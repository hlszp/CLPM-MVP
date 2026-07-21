"""历史数据导入服务 — 从远端 HTTP API 拉取历史数据写入本地 TDengine 宽表.

流程：
1. 查询回路信息 + tag 映射
2. 对每个回路：
   a. (overwrite 策略) DELETE 目标时段旧数据
   b. 按小时分块从远端 HTTP API 拉取历史数据
   c. 转换为宽表格式 (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
   d. 批量写入 TDengine
3. 更新导入进度（Redis）
4. (可选) 触发 KPI 回算

冲突处理策略：
- overwrite: 先 DELETE 目标时段旧数据，再 INSERT（手工优先）
- skip: 直接 INSERT，依赖 TDengine UPSERT 自动覆盖

设计依据：data-architecture-optimization-spec §5.3.2
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.redis import redis_client
from app.core.tdengine import make_subtable_name
from app.core.tdengine_native import batch_insert, execute_native_effective
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.schemas.loop_data import ConflictStrategy, ImportStatus
from app.services.data_source.remote_api_provider import (
    RemoteApiCircuitOpenError,
    RemoteApiProvider,
)

logger = logging.getLogger(__name__)

# 共享熔断/限流守卫：手工导入（worker）与断点续传补数（API 进程）复用同一
# RemoteApiProvider 实例，进程内对远端历史 API 的并发（REMOTE_API_MAX_CONCURRENCY）
# 与熔断状态统一收口，避免多路并发叠加压垮边缘 API。
_remote_guard: RemoteApiProvider | None = None


def _get_remote_guard() -> RemoteApiProvider:
    """获取共享的远端 API 守卫单例（熔断器 + 全局限流信号量）."""
    global _remote_guard
    if _remote_guard is None:
        _remote_guard = RemoteApiProvider()
    return _remote_guard


# Redis key 前缀
_IMPORT_TASK_PREFIX = "import_task"
_IMPORT_TASK_INDEX = "import_task:index"

# 远端 API Good 质量码集合
_GOOD_QUALITY_CODES = frozenset({1, 192})

# 动态分块参数
_TARGET_CHUNKS = 30  # 目标分块数（每个回路最多发这么多 HTTP 请求）
# 单次请求最大时间跨度（h）：远端 API 在长跨度 + 高并发下易 504，
# 从 24h 降至 3h，单次请求数据量减小，瞬时压力降低。
_MAX_CHUNK_HOURS = 3
_MIN_CHUNK_HOURS = 1  # 单次请求最小时间跨度

# Chunk 级重试配置（应对远端 API 瞬时 504/超时，DERP 链路虽稳定但远端仍可能短时过载）
_MAX_RETRIES = 3  # 最大重试次数（不含首次请求）
_RETRY_BACKOFF_BASE = 1.0  # 指数退避基数（秒），重试间隔：1, 2, 4
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504, 429})  # 可重试的 HTTP 状态码
_RETRYABLE_HTTPX_EXCS = (httpx.TimeoutException, httpx.NetworkError)  # 可重试的网络异常

# 角色列名映射（与 tdengine.py 保持一致）
_ROLE_COLUMNS: dict[str, str] = {
    "PV": "pv",
    "SP": "sp",
    "OP": "op",
    "MODE": "mode",
    "PID_P": "pid_p",
    "PID_I": "pid_i",
    "PID_D": "pid_d",
}


class HistoryDataSourceError(RuntimeError):
    """远端历史数据源不可用或返回无效响应。"""


def _compute_chunk_hours(start_dt: datetime, end_dt: datetime) -> int:
    """根据导入时间范围动态计算分块大小（小时）.

    策略：以 _TARGET_CHUNKS 为锚点，均匀分割时间范围，
    同时受 _MIN_CHUNK_HOURS / _MAX_CHUNK_HOURS 约束。
    """
    total_hours = (end_dt - start_dt).total_seconds() / 3600
    if total_hours <= 0:
        return _MIN_CHUNK_HOURS
    chunk_hours = max(
        _MIN_CHUNK_HOURS,
        min(_MAX_CHUNK_HOURS, int(math.ceil(total_hours / _TARGET_CHUNKS))),
    )
    logger.info(
        "动态分块: 总时长=%.1fh, 分块=%dh, 预计%d次HTTP请求",
        total_hours,
        chunk_hours,
        math.ceil(total_hours / chunk_hours),
    )
    return chunk_hours


# ---------------------------------------------------------------------------
# Redis 任务跟踪
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _task_key(task_id: str) -> str:
    return f"{_IMPORT_TASK_PREFIX}:{task_id}"


async def _save_task(task_data: dict[str, str]) -> None:
    """保存导入任务到 Redis Hash 并更新索引."""
    task_id = task_data["task_id"]
    await redis_client.hset(_task_key(task_id), mapping=task_data)
    created_at = task_data.get("created_at", "")
    try:
        score = datetime.fromisoformat(created_at).timestamp()
    except (ValueError, TypeError):
        score = datetime.now(UTC).timestamp()
    await redis_client.zadd(_IMPORT_TASK_INDEX, {task_id: score})


async def _get_task(task_id: str) -> dict[str, str] | None:
    """从 Redis 读取导入任务."""
    data = await redis_client.hgetall(_task_key(task_id))
    return data if data else None


async def _update_task(task_id: str, **fields: Any) -> None:
    """更新导入任务字段."""
    mapping = {k: _to_str(v) for k, v in fields.items() if v is not None}
    if mapping:
        await redis_client.hset(_task_key(task_id), mapping=mapping)


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _to_str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _task_to_response(data: dict[str, Any]) -> dict[str, Any]:
    """将 Redis Hash 转换为响应字典."""
    return {
        "taskId": data.get("task_id", ""),
        "status": data.get("status", ImportStatus.PENDING.value),
        "progress": _to_float(data.get("progress")),
        "loopCount": _to_int(data.get("loop_count")),
        "importedCount": _to_int(data.get("imported_count")),
        "errorCount": _to_int(data.get("error_count")),
        "tsStart": data.get("ts_start", ""),
        "tsEnd": data.get("ts_end", ""),
        "createdAt": data.get("created_at", ""),
        "startedAt": _to_str_or_none(data.get("started_at")),
        "finishedAt": _to_str_or_none(data.get("finished_at")),
        "errorMessage": _to_str_or_none(data.get("error_message")),
        "createdBy": _to_str_or_none(data.get("created_by")),
        "conflictStrategy": data.get("conflict_strategy", "overwrite"),
        "triggerBackfill": data.get("trigger_backfill", "false") == "true",
    }


async def _is_task_cancelled(task_id: str) -> bool:
    """检查任务是否已被取消."""
    raw = await redis_client.hget(_task_key(task_id), "status")
    if raw is None:
        return False
    return str(raw).upper() == ImportStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# 创建导入任务记录
# ---------------------------------------------------------------------------


async def create_import_task(
    *,
    loop_ids: list[str],
    ts_start: str,
    ts_end: str,
    conflict_strategy: str,
    trigger_backfill: bool,
    created_by: str,
    celery_task_id: str,
) -> str:
    """创建导入任务记录，返回 task_id."""
    task_id = str(uuid4())
    now = _now_iso()
    task_data: dict[str, str] = {
        "task_id": task_id,
        "status": ImportStatus.PENDING.value,
        "progress": "0",
        "loop_count": str(len(loop_ids)),
        "imported_count": "0",
        "error_count": "0",
        "ts_start": ts_start,
        "ts_end": ts_end,
        "conflict_strategy": conflict_strategy,
        "trigger_backfill": "true" if trigger_backfill else "false",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_message": "",
        "created_by": created_by,
        "celery_task_id": celery_task_id,
        "loop_ids": json.dumps(loop_ids),
    }
    await _save_task(task_data)
    logger.info(
        "导入任务已创建: task_id=%s, loops=%d, range=%s~%s, strategy=%s",
        task_id,
        len(loop_ids),
        ts_start,
        ts_end,
        conflict_strategy,
    )
    return task_id


# ---------------------------------------------------------------------------
# 核心导入逻辑
# ---------------------------------------------------------------------------


async def import_history_data(
    loop_ids: list[str],
    ts_start: str,
    ts_end: str,
    interval: int = 1,
    conflict_strategy: str = "overwrite",
    trigger_backfill: bool = False,
    task_id: str | None = None,
) -> dict:
    """执行历史数据导入.

    Args:
        loop_ids: 回路 ID 列表
        ts_start: 开始时间 (ISO 8601)
        ts_end: 结束时间 (ISO 8601)
        interval: 采样间隔（秒），默认 1
        conflict_strategy: 冲突策略，overwrite 或 skip
        trigger_backfill: 是否在导入完成后触发 KPI 回算
        task_id: Redis 任务跟踪 ID

    Returns:
        导入结果 {total, succeeded, failed, errors}
    """
    from app.core.db import AsyncSessionLocal

    # 解析时间范围
    start_dt = _parse_dt(ts_start)
    end_dt = _parse_dt(ts_end)

    if task_id:
        await _update_task(
            task_id,
            status=ImportStatus.RUNNING.value,
            started_at=_now_iso(),
        )

    total = len(loop_ids)
    errors: list[str] = []

    # 批量预加载回路元数据（1 次 DB 会话，3 次 SQL 替代 3N 次）
    db_session = AsyncSessionLocal()
    try:
        loop_data_map = await _batch_get_loop_data(db_session, loop_ids)
    finally:
        await db_session.close()

    logger.info(
        "批量预加载完成: %d/%d 个回路有 tag 映射",
        sum(1 for v in loop_data_map.values() if v["role_tag_map"]),
        len(loop_ids),
    )

    # 计算动态分块大小
    chunk_hours = _compute_chunk_hours(start_dt, end_dt)

    import asyncio as _asyncio_sem

    sem = _asyncio_sem.Semaphore(2)  # 远端 API 易在高并发下 504，限制最多 2 个回路并发
    progress_lock = _asyncio_sem.Lock()
    # 共享计数器（并发安全）
    shared_succeeded = 0
    shared_failed = 0

    async def _record_progress() -> None:
        if not task_id:
            return
        async with progress_lock:
            cur_s, cur_f = shared_succeeded, shared_failed
        await _update_task(
            task_id,
            progress=round((cur_s + cur_f) / total, 4) if total > 0 else 1.0,
            imported_count=cur_s,
            error_count=cur_f,
        )

    async def _import_with_sem(i: int, lid: str) -> tuple[int, int, str]:
        """带信号量控制的单回路导入，返回 (index, count, error)."""
        nonlocal shared_succeeded, shared_failed
        async with sem:
            if task_id and await _is_task_cancelled(task_id):
                return (i, 0, "")

            loop_meta = loop_data_map.get(lid, {"role_tag_map": {}, "unit_id": "", "subtable": ""})
            if not loop_meta["role_tag_map"]:
                logger.warning("回路 %s 无有效 tag 映射，跳过", lid)
                async with progress_lock:
                    shared_failed += 1
                error = f"loop {lid}: 无有效 tag 映射"
                await _record_progress()
                return (i, 0, error)

            try:
                count = await _import_single_loop(
                    loop_id=lid,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    interval=interval,
                    conflict_strategy=conflict_strategy,
                    subtable=loop_meta["subtable"],
                    unit_id=loop_meta["unit_id"],
                    role_tag_map=loop_meta["role_tag_map"],
                    chunk_hours=chunk_hours,
                )
                if count <= 0:
                    raise HistoryDataSourceError("远端历史数据 API 未返回可导入数据")
                logger.info(
                    "回路导入完成: loop_id=%s, points=%d (%d/%d)",
                    lid,
                    count,
                    i + 1,
                    total,
                )
                async with progress_lock:
                    shared_succeeded += 1
            except Exception as exc:
                async with progress_lock:
                    shared_failed += 1
                error = f"loop {lid}: {exc}"
                logger.warning("回路导入失败: %s", error)
                await _record_progress()
                return (i, 0, error)

            # 更新进度
            await _record_progress()
            return (i, count, "")

    # 并发处理所有回路
    tasks = [_import_with_sem(i, lid) for i, lid in enumerate(loop_ids)]
    task_results = await _asyncio_sem.gather(*tasks, return_exceptions=True)
    for task_result in task_results:
        if isinstance(task_result, BaseException):
            errors.append(f"导入协程异常: {task_result}")
        elif task_result[2]:
            errors.append(task_result[2])

    succeeded = shared_succeeded
    failed = shared_failed

    result = {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors[:10],  # 只保留前 10 条错误
    }

    # 更新任务终态
    if task_id:
        if await _is_task_cancelled(task_id):
            final_status = ImportStatus.CANCELLED.value
        elif succeeded > 0:
            final_status = ImportStatus.SUCCESS.value
        else:
            final_status = ImportStatus.FAILED.value

        await _update_task(
            task_id,
            status=final_status,
            progress=(
                1.0
                if final_status in (ImportStatus.SUCCESS.value, ImportStatus.FAILED.value)
                else None
            ),
            finished_at=_now_iso(),
            error_message="; ".join(errors[:3]) if errors else "",
        )

    # 触发 KPI 回算
    if trigger_backfill and succeeded > 0:
        try:
            await _trigger_kpi_backfill(loop_ids, ts_start, ts_end)
        except Exception as exc:
            logger.warning("触发 KPI 回算失败: %s", exc)

    return result


async def _import_single_loop(
    loop_id: str,
    start_dt: datetime,
    end_dt: datetime,
    interval: int,
    conflict_strategy: str,
    subtable: str = "",
    unit_id: str = "",
    role_tag_map: dict[str, str] | None = None,
    chunk_hours: int = 1,
) -> int:
    """导入单个回路的历史数据.

    Args:
        subtable: 已构造好的 TDengine 子表名
        unit_id: 回路所属工艺单元 ID
        role_tag_map: {role → tag_name} 预加载的 tag 映射
        chunk_hours: 动态分块小时数

    Returns:
        导入的数据点数
    """
    if not role_tag_map:
        logger.warning("回路 %s 无有效 tag 映射，跳过", loop_id)
        return 0

    # 冲突处理：删除旧数据（overwrite 策略）
    if conflict_strategy == ConflictStrategy.OVERWRITE.value:
        await _delete_range(subtable, start_dt, end_dt)

    # 按动态分块拉取 + 写入
    total_count = 0
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(hours=chunk_hours), end_dt)

        # 从远端 API 拉取数据
        raw_data = await _fetch_remote_history(
            list(role_tag_map.values()),
            chunk_start.isoformat(),
            chunk_end.isoformat(),
            interval,
        )

        if raw_data:
            # 转换为宽表行
            rows = _convert_to_wide_rows(raw_data, role_tag_map)
            if rows:
                # 批量写入 TDengine
                count = await batch_insert(subtable, rows, loop_id=loop_id, unit_id=unit_id)
                total_count += count

        chunk_start = chunk_end

    return total_count


async def _get_loop_tag_mapping(db: Any, loop_id: str) -> dict[str, str]:
    """查询回路的 tag 映射（role → tag_name）."""
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role.upper(): m for m in m_result.scalars().all()}

    tag_ids = [str(m.tag_id) for m in mappings.values()]
    if not tag_ids:
        return {}

    t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
    tags_map = {str(t.id): t for t in t_result.scalars().all()}

    role_tag_map: dict[str, str] = {}
    for role_upper, mapping in mappings.items():
        tag = tags_map.get(str(mapping.tag_id))
        if tag and tag.tag_name:
            role_tag_map[role_upper] = tag.tag_name

    return role_tag_map


async def _batch_get_loop_data(
    db: Any,
    loop_ids: list[str],
) -> dict[str, dict]:
    """批量预加载回路元数据（tag 映射 + unit_id）.

    消除 N+1 查询问题：原本每个回路 3 次 SQL × N 个回路 = 3N 次，
    现在合并为 3 次总 SQL。

    Returns:
        {loop_id: {role_tag_map, unit_id, subtable}}
    """
    if not loop_ids:
        return {}

    # 1. 一次性加载所有回路的 tag 映射
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids)))
    loop_mappings: dict[str, dict[str, str]] = {}  # loop_id → {role → tag_id}
    all_tag_ids: list[str] = []
    for m in m_result.scalars().all():
        lid = str(m.loop_id)
        if lid not in loop_mappings:
            loop_mappings[lid] = {}
        loop_mappings[lid][m.tag_role.upper()] = str(m.tag_id)
        all_tag_ids.append(str(m.tag_id))

    if not all_tag_ids:
        return {lid: {"role_tag_map": {}, "unit_id": "", "subtable": ""} for lid in loop_ids}

    # 2. 一次性加载所有 tag 名称
    from uuid import UUID

    unique_tag_ids = list(set(all_tag_ids))
    t_result = await db.execute(
        select(TagRegistry).where(TagRegistry.id.in_([UUID(tid) for tid in unique_tag_ids]))
    )
    tag_name_map = {str(t.id): t.tag_name for t in t_result.scalars().all()}

    # 3. 一次性加载所有回路的 unit_id
    l_result = await db.execute(
        select(LoopLedger).where(LoopLedger.id.in_([UUID(lid) for lid in loop_ids]))
    )
    unit_map = {
        str(loop.id): str(loop.unit_id) if loop.unit_id else "" for loop in l_result.scalars().all()
    }

    # 4. 组装结果
    result: dict[str, dict] = {}
    for lid in loop_ids:
        role_tag_id_map = loop_mappings.get(lid, {})
        role_tag_map: dict[str, str] = {}
        for role, tag_id in role_tag_id_map.items():
            tag_name = tag_name_map.get(tag_id)
            if tag_name:
                role_tag_map[role] = tag_name

        unit_id = unit_map.get(lid, "")

        # 构造子表名
        subtable = ""
        if role_tag_map:
            first_tag_name = next(iter(role_tag_map.values()))
            loop_part = (
                first_tag_name.rsplit(".", 1)[0] if "." in first_tag_name else first_tag_name
            )
            subtable = make_subtable_name(loop_part)

        result[lid] = {
            "role_tag_map": role_tag_map,
            "unit_id": unit_id,
            "subtable": subtable,
        }

    return result


async def _delete_range(subtable: str, start_dt: datetime, end_dt: datetime) -> None:
    """删除指定时间范围的数据（overwrite 策略）."""
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    sql = (
        f"DELETE FROM {settings.TDENGINE_DB}.{subtable} "
        f"WHERE ts >= '{start_str}' AND ts <= '{end_str}'"
    )
    try:
        await execute_native_effective(sql)
        logger.debug("已删除 %s 范围 %s~%s 的数据", subtable, start_str, end_str)
    except Exception as exc:
        logger.warning("删除范围数据失败 (subtable=%s): %s", subtable, exc)


async def _fetch_remote_history(
    tag_codes: list[str],
    start_time: str,
    end_time: str,
    interval: int,
) -> tuple[list[str], dict[str, dict]]:
    """从远端 HTTP API 拉取历史数据.

    所有请求经共享守卫（``_get_remote_guard``）发出，复用 RemoteApiProvider 的
    熔断器与全局限流信号量；熔断中直接快速失败（不重试）。

    Returns:
        (timestamps, series_map) 其中 series_map = {tagCode: {values, qualities}}
        远端不可用或响应无效时抛出 ``HistoryDataSourceError``。

    重试策略（P0 改造，应对远端瞬时 504/超时）:
        - 可重试状态码：502/503/504/429
        - 可重试异常：httpx.TimeoutException / httpx.NetworkError
        - 指数退避：1s, 2s, 4s（最多重试 3 次）
        - 4xx（非 429）等不可重试错误直接抛出
        - 熔断中（RemoteApiCircuitOpenError）不可重试，直接抛出
    """
    if not settings.HISTORY_DATA_API_URL:
        raise HistoryDataSourceError("HISTORY_DATA_API_URL 未配置")

    request_body = {
        "tagCodes": tag_codes,
        "startTime": start_time,
        "endTime": end_time,
        "sampleInterval": interval,
    }

    import asyncio as _asyncio_retry

    guard = _get_remote_guard()
    last_exc: Exception | None = None
    last_status_code: int | None = None
    last_resp_text: str = ""

    # 首次请求 + 最多 _MAX_RETRIES 次重试
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await guard.fetch_history_guarded(request_body)

            # 200 OK：业务层校验后返回
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("code") not in (200, "200", "0", 0):
                    # 业务错误不可重试（远端已正常响应，只是业务逻辑拒绝）
                    raise HistoryDataSourceError(
                        f"远端历史数据 API 业务错误: {payload.get('message', '')}"
                    )

                data = payload.get("data") or {}
                timestamps = list(data.get("timestamps") or [])
                series_list = list(data.get("series") or [])

                # 构建 tagCode → series 映射
                series_map: dict[str, dict] = {}
                for series in series_list:
                    tc = str(series.get("tagCode") or "")
                    if tc:
                        series_map[tc] = {
                            "values": list(series.get("values") or []),
                            "qualities": list(series.get("qualities") or []),
                        }

                return timestamps, series_map

            # 非 200：判断是否可重试
            last_status_code = resp.status_code
            last_resp_text = resp.text[:200]

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "远端 API 返回 HTTP %d（可重试），%gs 后重试 (attempt %d/%d), "
                    "tag_codes=%s, range=%s~%s",
                    resp.status_code,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                    tag_codes[:2],
                    start_time,
                    end_time,
                )
                await _asyncio_retry.sleep(wait)
                continue

            # 不可重试状态码（4xx 等）或重试次数用完，直接抛出
            raise HistoryDataSourceError(
                f"远端历史数据 API 返回 HTTP {resp.status_code}: {last_resp_text}"
            )

        except HistoryDataSourceError:
            # 业务错误（如 code != 200）直接抛出，不重试
            raise
        except RemoteApiCircuitOpenError as exc:
            # 熔断中：退避秒级重试无意义（熔断持续数百秒），直接失败
            raise HistoryDataSourceError(f"远端历史数据 API 熔断中，快速失败: {exc}") from exc
        except _RETRYABLE_HTTPX_EXCS as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "远端 API %s（可重试），%gs 后重试 (attempt %d/%d), tag_codes=%s, range=%s~%s",
                    type(exc).__name__,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                    tag_codes[:2],
                    start_time,
                    end_time,
                )
                await _asyncio_retry.sleep(wait)
                continue
            # 重试次数用完
            if isinstance(exc, httpx.TimeoutException):
                raise HistoryDataSourceError(
                    f"远端历史数据 API 超时（{settings.HISTORY_DATA_API_TIMEOUT:g}s，"
                    f"已重试 {_MAX_RETRIES} 次）"
                ) from exc
            raise HistoryDataSourceError(
                f"远端历史数据 API 网络错误（已重试 {_MAX_RETRIES} 次）: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            # 其他 httpx 异常（非 Timeout/Network）不可重试
            raise HistoryDataSourceError(f"远端历史数据 API 请求失败: {exc}") from exc
        except (TypeError, ValueError) as exc:
            # JSON 解析等错误不可重试
            raise HistoryDataSourceError(f"远端历史数据 API 响应无效: {exc}") from exc

    # 理论上不会执行到这里（for 循环内所有路径都会 return 或 raise）
    raise HistoryDataSourceError(
        f"远端历史数据 API 重试失败（{last_status_code}）: {last_resp_text}"
    ) from last_exc


def _convert_to_wide_rows(
    raw_data: tuple[list[str], dict[str, dict]],
    role_tag_map: dict[str, str],
) -> list[tuple]:
    """将远端 API 响应转换为宽表行格式.

    行格式: (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
    """
    timestamps, series_map = raw_data
    rows: list[tuple] = []

    # 构建 role → series 反向映射
    role_series: dict[str, dict] = {}
    for role, tag_name in role_tag_map.items():
        series = series_map.get(tag_name) or series_map.get(tag_name.lower())
        if series:
            role_series[role] = series

    for i, ts_str in enumerate(timestamps):
        # 解析时间戳
        ts = _parse_ts_str(ts_str)
        if ts is None:
            continue

        # 提取各角色值
        row = _build_wide_row(i, role_series)
        rows.append((ts, *row))

    return rows


def _build_wide_row(index: int, role_series: dict[str, dict]) -> tuple:
    """构造单行数据（不含 ts）.

    返回: (pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
    """
    pv = _parse_float_val(_get_series_value(role_series, "PV", index))
    sp = _parse_float_val(_get_series_value(role_series, "SP", index))
    op = _parse_float_val(_get_series_value(role_series, "OP", index))
    mode = _parse_int_val(_get_series_value(role_series, "MODE", index))
    pid_p = _parse_float_val(_get_series_value(role_series, "PID_P", index))
    pid_i = _parse_float_val(_get_series_value(role_series, "PID_I", index))
    pid_d = _parse_float_val(_get_series_value(role_series, "PID_D", index))

    # PV 质量码
    pv_quality = _map_quality(_get_series_quality(role_series, "PV", index))

    return (pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)


def _get_series_value(role_series: dict[str, dict], role: str, index: int) -> Any:
    """获取指定角色在指定索引的值."""
    series = role_series.get(role)
    if not series:
        return None
    values = series.get("values", [])
    return values[index] if index < len(values) else None


def _get_series_quality(role_series: dict[str, dict], role: str, index: int) -> Any:
    """获取指定角色在指定索引的质量码."""
    series = role_series.get(role)
    if not series:
        return None
    qualities = series.get("qualities", [])
    return qualities[index] if index < len(qualities) else None


def _parse_float_val(value: Any) -> float | None:
    """安全解析 float."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_int_val(value: Any) -> int | None:
    """安全解析 int."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _map_quality(value: Any) -> int:
    """外部质量码 → CLPM 内部质量码（1=Good, 0=Bad）."""
    if value is None:
        return 0
    try:
        q_int = int(value)
    except (ValueError, TypeError):
        return 0
    return 1 if q_int in _GOOD_QUALITY_CODES else 0


def _parse_ts_str(ts_str: str) -> str | None:
    """解析时间戳字符串为 TDengine 可接受的格式."""
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (ValueError, TypeError):
        return None


def _parse_dt(ts_str: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive datetime."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    return dt


async def _trigger_kpi_backfill(loop_ids: list[str], ts_start: str, ts_end: str) -> None:
    """触发 KPI 回算任务."""
    from app.tasks.kpi_calc import backfill_kpi_range

    backfill_kpi_range.delay(ts_start, ts_end, loop_ids=loop_ids)
    logger.info("已触发 KPI 回算: loops=%d, range=%s~%s", len(loop_ids), ts_start, ts_end)


# ---------------------------------------------------------------------------
# 任务查询（供 API 层调用）
# ---------------------------------------------------------------------------


async def get_import_task(task_id: str) -> dict[str, Any] | None:
    """查询单个导入任务."""
    data = await _get_task(task_id)
    if not data:
        return None
    return _task_to_response(data)


async def list_import_tasks(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """查询导入任务列表（按创建时间倒序）."""
    task_ids = await redis_client.zrevrange(_IMPORT_TASK_INDEX, 0, -1)
    items: list[dict[str, Any]] = []
    for tid in task_ids:
        data = await _get_task(tid)
        if data:
            items.append(_task_to_response(data))

    total = len(items)
    offset = (page - 1) * page_size
    paginated = items[offset : offset + page_size]
    return {"items": paginated, "total": total}


async def cancel_import_task(task_id: str) -> dict[str, Any] | None:
    """取消导入任务（设置 CANCELLED 标志，Celery 任务检测后停止）."""
    data = await _get_task(task_id)
    if not data:
        return None

    # 撤销 Celery 任务
    celery_task_id = data.get("celery_task_id", "")
    if celery_task_id:
        try:
            from app.tasks.celery_app import celery_app

            celery_app.control.revoke(celery_task_id, terminate=True)
        except Exception:
            logger.warning("撤销 Celery 任务失败: %s", celery_task_id)

    await _update_task(
        task_id,
        status=ImportStatus.CANCELLED.value,
        finished_at=_now_iso(),
    )
    data["status"] = ImportStatus.CANCELLED.value
    return _task_to_response(data)


async def delete_import_task(task_id: str) -> bool:
    """删除导入任务记录（从 Redis Hash 与索引中移除）.

    仅允许删除非活跃任务（终态：SUCCESS/FAILED/CANCELLED）。
    活跃任务（PENDING/RUNNING）需先取消再删除。

    Returns:
        True 删除成功；None 表示任务不存在（供 API 区分 404）
    """
    data = await _get_task(task_id)
    if not data:
        return None  # type: ignore[return-value]

    status_val = str(data.get("status", "")).upper()
    if status_val in (ImportStatus.PENDING.value, ImportStatus.RUNNING.value):
        raise ValueError("任务正在执行中，请先取消再删除")

    await redis_client.delete(_task_key(task_id))
    await redis_client.zrem(_IMPORT_TASK_INDEX, task_id)
    logger.info("导入任务已删除: task_id=%s", task_id)
    return True


__all__ = [
    "cancel_import_task",
    "create_import_task",
    "delete_import_task",
    "get_import_task",
    "import_history_data",
    "list_import_tasks",
]
