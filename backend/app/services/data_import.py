"""历史数据导入服务 — 从远端 HTTP API 拉取历史数据写入本地 TDengine 宽表.

流程：
1. 查询回路信息 + tag 映射
2. 对每个回路：
   a. 按小时分块从远端 HTTP API 拉取历史数据
   b. 转换为宽表格式 (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
   c. 批量写入 TDengine（统一 UPSERT：同子表同 ts 行覆盖，落库唯一）
3. 更新导入进度（Redis）
4. (可选) 触发 KPI 回算

幂等口径（2026-09-07 导入策略收敛）：
- 不设 overwrite/skip 选择，统一按"回路号（子表） + 时间戳（ts）"幂等导入；
  TDengine 同子表同 ts 的 INSERT 即 UPSERT（覆盖写），保证一个落库数据在
  时间点上是唯一的，同时无需 DELETE 前置步骤、无数据缺口风险。
- gap backfill 复用本函数，同样走 UPSERT（原 skip 语义天然一致）。

设计依据：data-architecture-optimization-spec §5.3.2
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.redis import redis_client
from app.core.tdengine import make_subtable_name
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.schemas.loop_data import ImportStatus
from app.services.data_source.remote_api_provider import (
    RemoteApiCircuitOpenError,
    RemoteApiProvider,
)

logger = logging.getLogger(__name__)

# 目标时区（Asia/Shanghai）—— TDengine 服务器本地时区，所有写入时间戳统一转至此，
# 消除 naive datetime 在 TDengine 侧的 8h 偏移风险
_TARGET_TZ = timezone(timedelta(hours=8))

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

# 导入任务状态 CAS 脚本（参考 task_tracker._STATUS_CAS_LUA 改造）。
# 语义：若 old_status 已是终态且 new_status 不同 → BLOCKED（不覆盖），防止重投
# 任务用 RUNNING 覆盖已 SUCCESS 等终态；progress/imported_count/error_count 单调
# 递增；末尾 EXPIRE 刷新 TTL。
# ARGV 布局：[1]=new_status（空串表示不更新 status）, [2]=ttl_seconds, [3..]=field/value 对
_IMPORT_TASK_CAS_LUA = r"""
-- CLPM_IMPORT_TASK_CAS_V1
local task_key = KEYS[1]
if redis.call('EXISTS', task_key) == 0 then
  return {'MISSING', ''}
end

local new_status = ARGV[1]
local ttl_seconds = ARGV[2]
local old_status = redis.call('HGET', task_key, 'status') or ''
local terminal = {SUCCESS=true, FAILED=true, CANCELLED=true}

if new_status ~= '' then
  if terminal[old_status] and old_status ~= new_status then
    return {'BLOCKED', old_status}
  end
  redis.call('HSET', task_key, 'status', new_status)
end

for index = 3, #ARGV, 2 do
  local field = ARGV[index]
  local value = ARGV[index + 1]
  if field == 'progress' or field == 'imported_count' or field == 'error_count' then
    local current = tonumber(redis.call('HGET', task_key, field) or '')
    local incoming = tonumber(value)
    if current == nil or incoming == nil or incoming >= current then
      redis.call('HSET', task_key, field, value)
    end
  else
    redis.call('HSET', task_key, field, value)
  end
end

redis.call('EXPIRE', task_key, ttl_seconds)
return {'UPDATED', old_status}
"""

# 远端 API Good 质量码集合
_GOOD_QUALITY_CODES = frozenset({1, 192})

# 动态分块参数
# 目标分块数（每个回路最多发这么多 HTTP 请求）
_TARGET_CHUNKS = 8
# 单次请求最大时间跨度（h）：2026-09-10 实测远端 24h 窗口 7 位号仅 0.5s
# （168h 也只 5.1s），大窗口单请求远快于多次小请求往返（56×3h≈13s vs 1×24h≈0.5s），
# 上限从 3h 放宽至 24h；请求次数从 56 次/回路降至 ~8 次/回路（7 天窗）。
_MAX_CHUNK_HOURS = 24
_MIN_CHUNK_HOURS = 1  # 单次请求最小时间跨度

# Chunk 级重试配置（应对远端 API 瞬时 504/超时，DERP 链路虽稳定但远端仍可能短时过载）
_MAX_RETRIES = 3  # 最大重试次数（不含首次请求）
_RETRY_BACKOFF_BASE = 1.0  # 指数退避基数（秒），重试间隔：1, 2, 4
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504, 429})  # 可重试的 HTTP 状态码
_RETRYABLE_HTTPX_EXCS = (httpx.TimeoutException, httpx.NetworkError)  # 可重试的网络异常

# ---------------------------------------------------------------------------
# 导入算法 v2（2026-09-09）：按角色信号频率分层拉取/写入
#
# 信号特征（zpdev 生产实测）：PV/OP 随工况连续变化（回路级 0.3~0.8Hz COV），
# SP/MODE/PID_P/I/D 为阶跃信号（分钟~小时级才变化一次）。旧算法 7 角色按同一
# interval 全量拉取+落库，低频角色被高频网格放大数倍冗余（PID 一周 60 万行里
# 99%+ 是重复值），是 shadow 双写下点表暴涨与导入缓慢的主因之一。
#
# 分层策略：
# - 高频流（PV/OP）：按用户 interval 分块拉取（沿用 1~3h 动态分块），逐点落库；
# - 低频流（SP/MODE/PID_*）：按粗粒度（默认 60s）大块（24h）拉取，本地 COV
#   去重（变化点 + 周期锚点 + 窗口末点）后稀疏写入点表——与实时 COV 写入口径
#   一致；宽表行由阶跃前向填充还原（读取侧本就按 COV 前向填充展开低频列，
#   见 tdengine_provider._legacy_query §6.5 与 LogicalWideBuilder，稀疏存储
#   天然兼容）。
# ---------------------------------------------------------------------------
_HIGH_FREQ_ROLES = frozenset({"PV", "OP"})
_LOW_FREQ_FETCH_INTERVAL = 60  # 低频拉取粒度（秒）；变化时刻定位精度与此对齐
_LOW_FREQ_CHUNK_HOURS = 24  # 低频分块（载荷小，用大块减少请求次数）
_LOW_FREQ_ANCHOR_SECONDS = 600.0  # 锚点间隔：值未变也周期落点，前向填充有界

_SENTINEL = object()  # COV 变性判定哨兵：与任何值（含 None）比较均不等


def _split_role_frequencies(
    role_tag_map: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """角色按信号频率分层：返回 (高频, 低频)。未知角色按高频处理."""
    high = {r: t for r, t in role_tag_map.items() if r in _HIGH_FREQ_ROLES}
    low = {r: t for r, t in role_tag_map.items() if r not in _HIGH_FREQ_ROLES}
    return high, low


def _cov_dedup_points(
    samples: list[tuple],
    anchor_seconds: float = _LOW_FREQ_ANCHOR_SECONDS,
) -> list[tuple]:
    """COV 去重：保留 值/质量变化点 + 周期锚点.

    samples: 升序 (ts_utc, value, quality_class, quality_raw)。
    变化判定用精确相等（阶跃信号经远端网格采样后稳定段逐点重复，精确匹配）；
    None（无效值）与任何数值视为变化；NaN 已在上游折 None。
    锚点保证稳定段也有周期落点——读取侧前向填充的扫描跨度有界，
    窗口末态由"末次变化/锚点"前向承载，无需额外末点封口。
    """
    out: list[tuple] = []
    last_v: object = _SENTINEL
    last_q: object = _SENTINEL
    last_emit: datetime | None = None
    for item in samples:
        ts, v, qc, _qr = item
        changed = v != last_v or qc != last_q
        anchored = last_emit is None or (ts - last_emit).total_seconds() >= anchor_seconds
        if changed or anchored:
            out.append(item)
            last_emit = ts
        last_v, last_q = v, qc
    return out


class _StepFill:
    """低频阶跃样本的前向取值器（游标随升序查询推进，均摊 O(1)）."""

    __slots__ = ("_samples", "_i")

    def __init__(self, samples: list[tuple]) -> None:
        self._samples = samples
        self._i = 0

    def value_at(self, ts: datetime) -> float | None:
        """取 ≤ ts 的最新样本值；窗口起点之前无样本返回 None."""
        s = self._samples
        if not s or ts < s[0][0]:
            return None
        while self._i + 1 < len(s) and s[self._i + 1][0] <= ts:
            self._i += 1
        return s[self._i][1]


async def _fetch_low_freq_points(
    role_tag_map_low: dict[str, str],
    start_dt: datetime,
    end_dt: datetime,
    base_interval: int,
    task_id: str | None,
    on_chunk_complete: callable | None,
) -> tuple[dict[str, list[tuple]], list[dict[str, str]], list[tuple[datetime, datetime]], bool]:
    """低频角色 COV 扫描（算法 v2 Phase A）.

    按 24h 大块、粗粒度（max(interval, 60s)）拉取，逐角色 COV 去重为
    变化/锚点样本。请求经共享守卫（限流/熔断/重试与高频流一致）。

    Returns:
        (samples_by_role, failed_windows, ok_windows, was_cancelled)
        samples 升序、已去重；(ts 为 aware UTC datetime)
    """
    from app.services.data_source.point_history_writer import (
        decode_history_quality,
        parse_source_ts,
    )

    interval = max(base_interval, _LOW_FREQ_FETCH_INTERVAL)
    samples_by_role: dict[str, list[tuple]] = {role: [] for role in role_tag_map_low}
    failed_windows: list[dict[str, str]] = []
    ok_windows: list[tuple[datetime, datetime]] = []
    cur = start_dt
    while cur < end_dt:
        if task_id and await _is_task_cancelled(task_id):
            return samples_by_role, failed_windows, ok_windows, True
        chunk_end = min(cur + timedelta(hours=_LOW_FREQ_CHUNK_HOURS), end_dt)
        try:
            raw = await _fetch_remote_history(
                list(role_tag_map_low.values()),
                cur.isoformat(),
                chunk_end.isoformat(),
                interval,
            )
            if raw and raw[0]:
                ts_list, series_map = raw
                ts_parsed = [parse_source_ts(t) for t in ts_list]
                for role, tag in role_tag_map_low.items():
                    series = series_map.get(tag) or series_map.get(tag.lower())
                    if not series:
                        continue
                    values = series.get("values", [])
                    qualities = series.get("qualities", [])
                    pts: list[tuple] = []
                    for i, ts in enumerate(ts_parsed):
                        if ts is None:
                            continue
                        v = _parse_float_val(values[i]) if i < len(values) else None
                        qc, qr = decode_history_quality(
                            qualities[i] if i < len(qualities) else None
                        )
                        pts.append((ts, v, qc, qr))
                    samples_by_role[role].extend(_cov_dedup_points(pts))
                ok_windows.append((cur, chunk_end))
        except Exception as exc:  # noqa: BLE001 — 分块级容错，与高频流口径一致
            failed_windows.append(
                {"start": cur.isoformat(), "end": chunk_end.isoformat(), "error": str(exc)[:200]}
            )
            logger.warning(
                "低频分块拉取失败（继续后续分块）: 窗口=%s ~ %s, err=%s",
                cur.isoformat(),
                chunk_end.isoformat(),
                exc,
            )
        if on_chunk_complete:
            await on_chunk_complete()
        cur = chunk_end
    return samples_by_role, failed_windows, ok_windows, False


async def _write_sparse_point_events(
    role_point_map_low: dict[str, tuple[str, str]],
    samples_by_role: dict[str, list[tuple]],
    *,
    source_task: str,
) -> int:
    """低频 COV 样本 → 点表稀疏点事件（shadow/point 布局）.

    与实时 COV 写入口径一致（阶跃信号只落变化点+锚点）；计数按去重后
    时间槽数（各角色 ts 并集），与 _write_point_events 的槽口径对齐。
    """
    from app.services.data_source.point_history_repository import (
        QSCHEMA_AAS,
        SOURCE_KIND_REMOTE_GRID,
        PointEvent,
        write_events,
    )

    events: list[PointEvent] = []
    for role, entry in role_point_map_low.items():
        _tag_name, point_id = entry
        if not point_id:
            continue
        for ts, v, qc, qr in samples_by_role.get(role) or []:
            events.append(
                PointEvent(
                    point_id=point_id,
                    ts=ts,
                    value=v,
                    quality_class=qc,
                    quality_raw=qr,
                    quality_schema=QSCHEMA_AAS,
                    source_kind=SOURCE_KIND_REMOTE_GRID,
                    received_at=datetime.now(UTC),
                    source_id=f"import:{source_task[:32]}",
                )
            )
    if not events:
        return 0
    result = await write_events(events, source_task=source_task or None)
    if result.failed:
        raise HistoryDataSourceError(f"点事件写入失败: {result.error}")
    return len({e.ts for e in events})


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
    """保存导入任务到 Redis Hash 并更新索引（含 TTL 修剪）."""
    task_id = task_data["task_id"]
    ttl = int(settings.IMPORT_TASK_TTL_DAYS) * 86400
    pipe = redis_client.pipeline()
    pipe.hset(_task_key(task_id), mapping=task_data)
    pipe.expire(_task_key(task_id), ttl)
    created_at = task_data.get("created_at", "")
    try:
        score = datetime.fromisoformat(created_at).timestamp()
    except (ValueError, TypeError):
        score = datetime.now(UTC).timestamp()
    pipe.zadd(_IMPORT_TASK_INDEX, {task_id: score})
    await pipe.execute()


async def _get_task(task_id: str) -> dict[str, str] | None:
    """从 Redis 读取导入任务."""
    data = await redis_client.hgetall(_task_key(task_id))
    return data if data else None


async def _update_task(task_id: str, **fields: Any) -> None:
    """更新导入任务字段（刷新 TTL，防止活跃任务过期）.

    非状态字段的普通更新（如 progress、celery_task_id 回填）。涉及状态变更
    （status=RUNNING/SUCCESS/FAILED/CANCELLED）应使用 ``_update_task_cas``，
    后者通过 Lua CAS 防止终态被重投任务覆盖。
    """
    mapping = {k: _to_str(v) for k, v in fields.items() if v is not None}
    if mapping:
        ttl = int(settings.IMPORT_TASK_TTL_DAYS) * 86400
        pipe = redis_client.pipeline()
        pipe.hset(_task_key(task_id), mapping=mapping)
        pipe.expire(_task_key(task_id), ttl)
        await pipe.execute()


async def _update_task_cas(
    task_id: str,
    *,
    new_status: str | None = None,
    **fields: Any,
) -> tuple[str, str]:
    """CAS 版本任务更新（状态变更专用，防终态被重投任务覆盖）.

    通过 ``_IMPORT_TASK_CAS_LUA`` 原子执行：若任务已处于终态（SUCCESS/FAILED/
    CANCELLED）且本次 new_status 不同 → 返回 BLOCKED 不更新；终态→同终态
    （如 SUCCESS→SUCCESS）允许更新其它字段。progress/imported_count/error_count
    单调递增。

    Args:
        task_id: 任务 ID
        new_status: 新状态；None/空串表示本次不更新 status（仅更新其它字段）
        **fields: 附带更新的字段（如 started_at/finished_at/result/error_message）

    Returns:
        (result_code, old_status) — code ∈ {'MISSING', 'BLOCKED', 'UPDATED'}
    """
    mapping = {k: v for k, v in fields.items() if v is not None}
    ttl = int(settings.IMPORT_TASK_TTL_DAYS) * 86400
    script_args: list[str] = [new_status or "", str(ttl)]
    for field, value in mapping.items():
        script_args.extend((_to_str(field), _to_str(value)))
    raw = await redis_client.eval(
        _IMPORT_TASK_CAS_LUA,
        1,
        _task_key(task_id),
        *script_args,
    )
    return str(raw[0]), str(raw[1])


def _build_cached_result(data: dict[str, Any]) -> dict[str, Any]:
    """从 Redis 任务记录重构导入返回结果（供幂等短路复用）.

    优先用持久化的 ``result`` JSON 字段；缺失时用 loop_count/imported_count/
    error_count 兜底重构。附加 ``skipped_redelivery=True`` 标记，供调用方区分
    正常执行结果与重投跳过结果。
    """
    raw = data.get("result", "")
    if raw:
        try:
            r = json.loads(raw)
            r["skipped_redelivery"] = True
            return r
        except (ValueError, TypeError):
            pass
    status = str(data.get("status", "")).upper()
    return {
        "total": _to_int(data.get("loop_count")),
        "succeeded": _to_int(data.get("imported_count"))
        if status == ImportStatus.SUCCESS.value
        else 0,
        "failed": _to_int(data.get("error_count")) if status == ImportStatus.FAILED.value else 0,
        "errors": [data.get("error_message", "")] if data.get("error_message") else [],
        "skipped_redelivery": True,
    }


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
    # result 为 JSON 串（total/succeeded/failed/errors），解析后透出供 UI 展示
    result_raw = data.get("result", "")
    try:
        result = json.loads(result_raw) if result_raw else None
    except (ValueError, TypeError):
        result = None
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
        "triggerBackfill": data.get("trigger_backfill", "false") == "true",
        "result": result,
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
        "trigger_backfill": "true" if trigger_backfill else "false",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_message": "",
        "created_by": created_by,
        "celery_task_id": celery_task_id,
        "loop_ids": json.dumps(loop_ids),
        "result": "",
    }
    await _save_task(task_data)
    logger.info(
        "导入任务已创建: task_id=%s, loops=%d, range=%s~%s",
        task_id,
        len(loop_ids),
        ts_start,
        ts_end,
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
    trigger_backfill: bool = False,
    point_only: bool = False,
    task_id: str | None = None,
) -> dict:
    """执行历史数据导入.

    Args:
        loop_ids: 回路 ID 列表
        ts_start: 开始时间 (ISO 8601)
        ts_end: 结束时间 (ISO 8601)
        interval: 采样间隔（秒），默认 1
        trigger_backfill: 是否在导入完成后触发 KPI 回算
        point_only: 仅写入位号点表、跳过宽表（历史回填独立表场景，复用
            storage_mode=point 语义）；False 时按 sys_config 的 storage_mode
        task_id: Redis 任务跟踪 ID

    Returns:
        导入结果 {total, succeeded, failed, errors}
    """
    from app.core.db import AsyncSessionLocal

    # 解析时间范围
    start_dt = _parse_dt(ts_start)
    end_dt = _parse_dt(ts_end)

    # 写入布局（2026-09-09 Phase 2：宽表已退役，历史导入恒只写点表）。
    # 此前 legacy/shadow/point 三态；现锁定 point——点事件即落库形态，
    # 与实时采集口径一致（COV 稀疏），读取侧由 LogicalWideBuilder 前向填充。
    storage_mode = "point"

    if task_id:
        cas_code, old_status = await _update_task_cas(
            task_id,
            new_status=ImportStatus.RUNNING.value,
            started_at=_now_iso(),
            # 进度心跳起点：清扫器以此判活（无心跳旧任务回退 started_at 口径）
            last_progress_at=_now_iso(),
        )
        if cas_code == "BLOCKED":
            # 已终态（入口预检查漏网的 TOCTOU 窗口兜底）— 不执行导入
            logger.warning(
                "CAS 拒绝覆盖终态: task_id=%s, existing=%s, 跳过执行",
                task_id,
                old_status,
            )
            existing = await _get_task(task_id)
            if existing:
                return _build_cached_result(existing)
            return {
                "total": len(loop_ids),
                "succeeded": 0,
                "failed": 0,
                "errors": [f"任务已处于终态 {old_status}"],
                "skipped_redelivery": True,
            }
        if cas_code == "MISSING":
            logger.warning("任务记录不存在: task_id=%s, 继续执行（无状态跟踪）", task_id)

    total = len(loop_ids)
    errors: list[str] = []
    terminal_set = False  # 正常流程是否已置终态（finally 兜底依据）

    try:
        # 前置探测（加固 0909）：批量导入启动前快检远端。
        # 仅"熔断打开"（电路确认为远端持续不可用）才阻断任务——其余连接层
        # 抖动（ConnectTimeout/ConnectError 等，0909 实测 httpx client 连接
        # 221.226.3.250 偶发 10s ConnectTimeout 而 urllib 直连 0.12s 即通）
        # 降级为警告放行：正式 _fetch_remote_history 自带 3 次指数退避重试
        # + 熔断器，由它自行处理瞬时抖动，前置探测不做二次拦截。
        try:
            await _probe_remote_history_api()
        except HistoryDataSourceError as _probe_err:
            reason = str(_probe_err)
            if "熔断" in reason:
                logger.warning("远端历史 API 熔断中，任务快速失败: %s", reason)
                errors.append(reason)
                if task_id:
                    await _update_task_cas(
                        task_id,
                        new_status=ImportStatus.FAILED.value,
                        finished_at=_now_iso(),
                        error_message=reason,
                        result={
                            "total": total,
                            "succeeded": 0,
                            "failed": total,
                            "errors": [f"远端熔断，任务未启动: {reason}"],
                        },
                    )
                return {"total": total, "succeeded": 0, "failed": total, "errors": errors}
            # 连接层抖动：降级放行，交正式拉取重试
            logger.warning("前置探测连接抖动（放行，交正式拉取重试）: %s", reason)

    except Exception:
        if task_id and not terminal_set:
            try:
                if await _is_task_cancelled(task_id):
                    fallback_status = ImportStatus.CANCELLED.value
                else:
                    fallback_status = ImportStatus.FAILED.value
                cas_code, _old = await _update_task_cas(
                    task_id,
                    new_status=fallback_status,
                    finished_at=_now_iso(),
                )
                if cas_code == "BLOCKED":
                    logger.info(
                        "异常兜底终态 CAS 被拒: task_id=%s, existing=%s, target=%s",
                        task_id,
                        _old,
                        fallback_status,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("异常中断兜底终态更新失败: task_id=%s", task_id)
        raise

    # ===== 正式导入 =====
    try:
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

        # 计算进度总量：进度回调按"分块"触发（每分块 1 次），
        # 总量必须是 回路数 × 每回路分块数。
        # 注意不能按小时数计量——chunk_hours>1 时（时间窗 >30h）分块数 < 小时数，
        # 按小时计量会导致进度只能爬到 1/chunk_hours（如 33%）后长期"停滞"，
        # 直到任务结束才跳变 100%。
        total_hours = math.ceil((end_dt - start_dt).total_seconds() / 3600)
        # v3 单相：每回路分块 = 时间窗 / chunk_hours（低频/高频两相已废弃）
        chunks_per_loop = max(1, math.ceil(total_hours / max(chunk_hours, 1)))
        total_units = total * chunks_per_loop  # 总进度单位 = 回路数 × 每回路分块数

        import asyncio as _asyncio_sem

        # 2026-09-10 实测：远端 24h 窗口 0.5s/请求很健康（见 _MAX_CHUNK_HOURS），
        # 并发从 2 放宽至 4（总对外并发 = 4 回路 × 单请求，远端可承受）。
        sem = _asyncio_sem.Semaphore(4)  # 限制最多 4 个回路并发拉取/写入
        progress_lock = _asyncio_sem.Lock()
        # 共享计数器（并发安全）
        shared_succeeded = 0
        shared_failed = 0
        shared_completed_units = 0  # 完成的小时窗口数

        async def _record_progress(chunk_complete: int = 0) -> None:
            if not task_id:
                return
            nonlocal shared_completed_units
            async with progress_lock:
                cur_s, cur_f = shared_succeeded, shared_failed
                if chunk_complete > 0:
                    shared_completed_units += chunk_complete
                cur_units = shared_completed_units
            # 进度 = 完成的分块数 / 总分块数；同时写 last_progress_at 心跳，
            # 供清扫器区分"执行中但停滞"（卡死）与"执行中且持续推进"（正常长任务）
            progress_value = round(cur_units / total_units, 4) if total_units > 0 else 1.0
            await _update_task(
                task_id,
                progress=progress_value,
                imported_count=cur_s,
                error_count=cur_f,
                last_progress_at=_now_iso(),
            )

        async def _import_with_sem(i: int, lid: str) -> tuple[int, int, str]:
            """带信号量控制的单回路导入，返回 (index, count, error)."""
            nonlocal shared_succeeded, shared_failed, shared_completed_units
            # 按回路记录已完成的小时窗口数（多回路并发交错时，
            # 不能用共享计数器取模推算本回路进度）
            loop_done = 0

            async def _on_chunk_complete() -> None:
                """小时分块完成时的进度回调（按回路计数）."""
                nonlocal loop_done
                loop_done += 1
                await _record_progress(chunk_complete=1)

            async with sem:
                if task_id and await _is_task_cancelled(task_id):
                    return (i, 0, "")

                loop_meta = loop_data_map.get(
                    lid, {"role_tag_map": {}, "unit_id": "", "subtable": ""}
                )
                if not loop_meta["role_tag_map"]:
                    logger.warning("回路 %s 无有效 tag 映射，跳过", lid)
                    async with progress_lock:
                        shared_failed += 1
                        # 无 tag 映射时按本回路全部分块数计入进度
                        shared_completed_units += chunks_per_loop
                    error = f"loop {lid}: 无有效 tag 映射"
                    await _record_progress()
                    return (i, 0, error)

                try:
                    count, failed_windows, loop_cancelled = await _import_single_loop(
                        loop_id=lid,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        interval=interval,
                        subtable=loop_meta["subtable"],
                        unit_id=loop_meta["unit_id"],
                        role_tag_map=loop_meta["role_tag_map"],
                        chunk_hours=chunk_hours,
                        task_id=task_id,
                        on_chunk_complete=_on_chunk_complete,
                        role_point_map=loop_meta.get("role_point_map", {}),
                        storage_mode=storage_mode,
                    )
                    if loop_cancelled:
                        # 取消中断：不计成功也不计失败（最终状态由任务级取消
                        # 检查置 CANCELLED）；主表数据保留（R12）
                        async with progress_lock:
                            shared_completed_units += max(chunks_per_loop - loop_done, 0)
                        await _record_progress()
                        return (i, 0, "")
                    if count <= 0 and not failed_windows:
                        raise HistoryDataSourceError("远端历史数据 API 未返回可导入数据")
                    if failed_windows:
                        # 分块级容错后仍存在失败窗口：已写入的部分数据保留，
                        # 回路计为失败（覆盖率按实际写入点数反馈），
                        # 缺口可用 skip 策略再导入补齐（overwrite 会误删实时行，禁止）
                        first = failed_windows[0]
                        error = (
                            f"loop {lid}: {len(failed_windows)} 个分块导入失败"
                            f"（已写入 {count} 点），首个失败窗口 "
                            f"{first['start']}~{first['end']}: {first['error']}"
                        )
                        logger.warning("回路部分分块导入失败: %s", error)
                        async with progress_lock:
                            shared_failed += 1
                            shared_completed_units += max(chunks_per_loop - loop_done, 0)
                        await _record_progress()
                        return (i, count, error)
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
                        # 失败时补齐本回路剩余分块（按本回路已完成 chunk 数计算，
                        # 不能用共享计数器取模——多回路并发交错时会失真）
                        shared_completed_units += max(chunks_per_loop - loop_done, 0)
                    error = f"loop {lid}: {exc}"
                    logger.warning("回路导入失败: %s", error)
                    await _record_progress()
                    return (i, 0, error)

                # 更新进度（回路完成时确保进度准确）
                await _record_progress()
                return (i, count, "")

        # 并发处理所有回路
        tasks = [_import_with_sem(i, lid) for i, lid in enumerate(loop_ids)]
        task_results = await _asyncio_sem.gather(*tasks, return_exceptions=True)

        # 收敛结果：按指定时间窗 + 回路号取数即可，不再量化覆盖率
        # （远端 COV 稀疏，按理想网格点数做分母会系统性误报"缺口"）
        for task_result in task_results:
            if isinstance(task_result, BaseException):
                errors.append(f"导入协程异常: {task_result}")
                continue
            i, count, error = task_result
            lid = loop_ids[i]
            if error:
                errors.append(error)
            if count > 0:
                # R13：本地 TD 已写入新数据 → 失效 realtime:history 与 L1/L2/L3
                # 计算缓存，避免残缺/陈旧缓存遮蔽新数据（失效失败只记日志）
                await _invalidate_loop_caches(lid, loop_data_map.get(lid, {}).get("loop_part", ""))

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

            cas_code, _old = await _update_task_cas(
                task_id,
                new_status=final_status,
                progress=(
                    1.0
                    if final_status in (ImportStatus.SUCCESS.value, ImportStatus.FAILED.value)
                    else None
                ),
                finished_at=_now_iso(),
                error_message="; ".join(errors[:3]) if errors else "",
                result=result,
            )
            if cas_code == "BLOCKED":
                logger.info(
                    "终态 CAS 被拒（已由先到者置终态）: task_id=%s, existing=%s, target=%s",
                    task_id,
                    _old,
                    final_status,
                )
            terminal_set = True

        # 触发 KPI 回算
        if trigger_backfill and succeeded > 0:
            try:
                await _trigger_kpi_backfill(loop_ids, ts_start, ts_end)
            except Exception as exc:
                logger.warning("触发 KPI 回算失败: %s", exc)

        return result
    except Exception:
        # 异常中断时兜底置终态（不卡 RUNNING），然后重新抛出供上层处理
        if task_id and not terminal_set:
            try:
                if await _is_task_cancelled(task_id):
                    fallback_status = ImportStatus.CANCELLED.value
                else:
                    fallback_status = ImportStatus.FAILED.value
                cas_code, _old = await _update_task_cas(
                    task_id,
                    new_status=fallback_status,
                    finished_at=_now_iso(),
                    # 携带中断时的部分进度，便于用户判断已导入规模并决定是否补数
                    error_message=(
                        f"导入任务异常中断（已完成分块 {shared_completed_units}/{total_units}，"
                        f"成功 {shared_succeeded} 回路 / 失败 {shared_failed} 回路）"
                    ),
                )
                if cas_code == "BLOCKED":
                    logger.info(
                        "异常兜底终态 CAS 被拒: task_id=%s, existing=%s, target=%s",
                        task_id,
                        _old,
                        fallback_status,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("异常中断兜底终态更新失败: task_id=%s", task_id)
        raise


async def _import_single_loop(
    loop_id: str,
    start_dt: datetime,
    end_dt: datetime,
    interval: int,
    subtable: str = "",
    unit_id: str = "",
    role_tag_map: dict[str, str] | None = None,
    chunk_hours: int = 1,
    task_id: str | None = None,
    on_chunk_complete: callable | None = None,
    role_point_map: dict[str, tuple[str, str]] | None = None,
    storage_mode: str = "point",
) -> tuple[int, list[dict[str, str]], bool]:
    """导入单个回路的历史数据（算法 v3：单相逐位号直导）.

    单点表结构下的自然形态：回路绑定的全部位号（PV/SP/OP/MODE/KP/TI/TD）
    一次拉取、一次批量写子表。TDengine 同 ts UPSERT 天然幂等，无需
    read_events 读回比对（那是实时路径的冲突登记语义，对批量导入是双倍
    IO）。SP/KP/TI/TD/MODE 的稀疏是远端 COV 存储的自然结果，不需要客户端
    低频/高频两相拆分（v2 已废弃）。

    分块级容错：单个分块失败记录窗口继续后续分块；进度按分块回调。

    Returns:
        (写入行数, 失败分块窗口列表[{start, end, error}], 是否因取消中断)
    """
    if not role_tag_map:
        logger.warning("回路 %s 无有效 tag 映射，跳过", loop_id)
        return 0, [], False

    role_point_map = role_point_map or {}
    total_count = 0
    failed_windows: list[dict[str, str]] = []
    was_cancelled = False
    point_ok_chunks: list[tuple[datetime, datetime]] = []
    point_import_batch_id: str | None = None

    async def _ensure_point_batch() -> str | None:
        """点级批次惰性创建（覆盖登记用）；失败不阻塞数据面."""
        nonlocal point_import_batch_id
        if point_import_batch_id is None:
            try:
                from app.core.db import AsyncSessionLocal as _ASL
                from app.services.data_source import point_history_metadata as _phm

                async with _ASL() as _bs:
                    _batch = await _phm.create_batch(
                        _bs,
                        window_start=start_dt.replace(tzinfo=UTC),
                        window_end=end_dt.replace(tzinfo=UTC),
                        source_task=f"import:{task_id or loop_id[:8]}",
                    )
                    await _bs.commit()
                    point_import_batch_id = _batch.batch_id
            except Exception as exc:  # noqa: BLE001 — 元数据失败不阻塞导入数据面
                logger.warning("点级批次创建失败（覆盖登记缺失）: %s", exc)
        return point_import_batch_id

    # 绑定位号（显式链接）：一次拉全部角色
    fetch_map = dict(role_tag_map)
    fetch_point_map = {r: v for r, v in role_point_map.items() if r in fetch_map}

    chunk_start = start_dt
    while chunk_start < end_dt:
        if task_id and await _is_task_cancelled(task_id):
            was_cancelled = True
            logger.info("回路 %s 导入被取消（已写入 %d 行），跳过剩余分块", loop_id, total_count)
            break

        chunk_end = min(chunk_start + timedelta(hours=chunk_hours), end_dt)

        try:
            raw_data = await _fetch_remote_history(
                list(fetch_map.values()),
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                interval,
            )
            if not raw_data or not raw_data[0]:
                logger.warning(
                    "远端该分块无数据: loop=%s, 窗口=%s ~ %s",
                    loop_id,
                    chunk_start.isoformat(),
                    chunk_end.isoformat(),
                )
            if raw_data and raw_data[0]:
                await _ensure_point_batch()
                point_ok_chunks.append((chunk_start, chunk_end))
                total_count += await _write_points_bulk(
                    fetch_point_map, raw_data, source_task=task_id or ""
                )
        except Exception as exc:  # noqa: BLE001 — 分块级容错：记录窗口后继续后续分块
            failed_windows.append(
                {
                    "start": chunk_start.isoformat(),
                    "end": chunk_end.isoformat(),
                    "error": str(exc)[:200],
                }
            )
            logger.warning(
                "分块导入失败（已重试仍失败，继续后续分块）: loop=%s, 窗口=%s ~ %s, err=%s",
                loop_id,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                exc,
            )

        chunk_start = chunk_end
        if on_chunk_complete:
            await on_chunk_complete()

    # 覆盖登记（v3 批量化）：每个 (位号, 分块窗口) 一条段——不再逐点，
    # 读取侧 anchor 语义保留（窗口级覆盖证明）
    if point_import_batch_id is not None and point_ok_chunks:
        try:
            from app.core.db import AsyncSessionLocal as _ASL
            from app.services.data_source import point_history_metadata as _phm

            async with _ASL() as _cs:
                unique_points = sorted({pid for _n, pid in role_point_map.values() if pid})
                for cs, ce in point_ok_chunks:
                    for pid in unique_points:
                        await _phm.register_coverage(
                            _cs,
                            seg_start=cs.replace(tzinfo=UTC),
                            seg_end=ce.replace(tzinfo=UTC),
                            point_id=pid,
                            batch_id=point_import_batch_id,
                            source_task=f"import:{task_id or ''}",
                        )
                await _phm.confirm_batch(
                    _cs,
                    point_import_batch_id,
                    stats={"slots": total_count, "failed_chunks": len(failed_windows)},
                    partial_reason=(
                        f"{len(failed_windows)} 个分块失败" if failed_windows else None
                    ),
                )
                await _cs.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("点级覆盖登记失败（数据已写，登记缺失）: %s", exc)

    if not failed_windows and total_count <= 0:
        logger.warning(
            "远端窗口无数据（loop=%s, 窗口=%s ~ %s），未写入任何行",
            loop_id,
            start_dt.isoformat(),
            end_dt.isoformat(),
        )

    return total_count, failed_windows, was_cancelled


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


# ---------------------------------------------------------------------------
# v3 直写路径（2026-09-09 重构）：单点表结构下的自然形态 —— 回路 → 绑定位号
# → 一次拉全部角色 → 过滤非有限值 → 批量 INSERT 子表（TDengine 同 ts UPSERT
# 天然幂等，无 read_events 读回比对——那是实时写入的冲突登记语义，对批量
# 导入是双倍 IO）。SP/KP/TI/TD/MODE 稀疏是远端 COV 存储的自然结果，
# 不需要客户端低频/高频两相拆分。
# ---------------------------------------------------------------------------


def _parse_remote_ts(ts_str: str):
    """远端时间串 → aware UTC datetime（复用 point_history_writer 契约）."""
    from app.services.data_source.point_history_writer import parse_source_ts

    return parse_source_ts(ts_str)


async def _write_points_bulk(
    role_point_map: dict[str, tuple[str, str]],
    raw_data: tuple[list[str], dict[str, dict]],
    *,
    source_task: str,
) -> int:
    """远端响应 → 点表批量直写（无读回比对；分批多子表 INSERT）.

    返回写入行数（物理行，非时间槽——v3 口径简化，进度/结果用行数）。
    """
    from app.core.tdengine_native import execute_native_effective
    from app.services.data_source.point_history_repository import (
        QSCHEMA_AAS,
        SOURCE_KIND_REMOTE_GRID,
        PointEvent,
        _build_insert_sql,
    )

    timestamps, series_map = raw_data
    ts_parsed = [_parse_remote_ts(t) for t in timestamps]
    events: list[PointEvent] = []
    now = datetime.now(UTC)
    for _role, entry in role_point_map.items():
        _tag_name, point_id = entry
        if not point_id:
            continue
        series = series_map.get(_tag_name) or series_map.get(_tag_name.lower())
        if not series:
            continue
        values = series.get("values", [])
        for i, ts in enumerate(ts_parsed):
            if ts is None:
                continue
            raw_v = values[i] if i < len(values) else None
            v = _parse_float_val(raw_v)
            if v is None:
                continue  # 非有限值/缺测跳过（质量与数值独立，但导入网格点无值不落）
            events.append(
                PointEvent(
                    point_id=point_id,
                    ts=ts,
                    value=v,
                    quality_class=1,
                    quality_raw=None,
                    quality_schema=QSCHEMA_AAS,
                    source_kind=SOURCE_KIND_REMOTE_GRID,
                    received_at=now,
                    source_id=f"import:{source_task[:32]}",
                )
            )
    if not events:
        return 0
    total = 0
    for i in range(0, len(events), 1000):
        chunk = [(ev, ev.payload_hash()) for ev in events[i : i + 1000]]
        sql = _build_insert_sql(chunk)
        await execute_native_effective(sql)
        total += len(chunk)
    return total


async def _batch_get_loop_data(
    db: Any,
    loop_ids: list[str],
) -> dict[str, dict]:
    """批量预加载回路元数据（tag 映射 + unit_id）.

    消除 N+1 查询问题：原本每个回路 3 次 SQL × N 个回路 = 3N 次，
    现在合并为 3 次总 SQL。

    Returns:
        {loop_id: {role_tag_map, unit_id, subtable, loop_part}}
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
        return {
            lid: {"role_tag_map": {}, "unit_id": "", "subtable": "", "loop_part": ""}
            for lid in loop_ids
        }

    # 2. 一次性加载所有 tag 名称
    from uuid import UUID

    unique_tag_ids = list(set(all_tag_ids))
    t_result = await db.execute(
        select(TagRegistry).where(TagRegistry.id.in_([UUID(tid) for tid in unique_tag_ids]))
    )
    tag_name_map = {str(t.id): t.tag_name for t in t_result.scalars().all()}
    name_to_id_map = {name: tid for tid, name in tag_name_map.items()}

    # 3. 一次性加载所有回路的 unit_id + tag_name（子表名唯一权威来源）
    l_result = await db.execute(
        select(LoopLedger).where(LoopLedger.id.in_([UUID(lid) for lid in loop_ids]))
    )
    loop_meta_map: dict[str, tuple[str, str]] = {}
    for loop in l_result.scalars().all():
        loop_meta_map[str(loop.id)] = (
            str(loop.unit_id) if loop.unit_id else "",
            loop.tag_name or "",
        )

    # 4. 组装结果
    result: dict[str, dict] = {}
    for lid in loop_ids:
        role_tag_id_map = loop_mappings.get(lid, {})
        role_tag_map: dict[str, str] = {}
        for role, tag_id in role_tag_id_map.items():
            tag_name = tag_name_map.get(tag_id)
            if tag_name:
                role_tag_map[role] = tag_name

        unit_id, loop_tag_name = loop_meta_map.get(lid, ("", ""))

        # 子表名：从回路台账 tag_name 生成（天然不含测点角色后缀）。
        # 历史 bug（2026-08-20 修复）：此前从「第一个测点名」r.split('.') 反推回路名，
        # 但本项目测点名用下划线分隔角色（xx_PV），剥离失败导致子表名带角色后缀、
        # 且不同批次第一测点不同 → 同一回路多张子表、数据分裂（235 张表 vs 应为 125 张）
        subtable = make_subtable_name(loop_tag_name) if loop_tag_name else ""

        result[lid] = {
            "role_tag_map": role_tag_map,
            "unit_id": unit_id,
            "subtable": subtable,
            # loop_part = 回路台账 tag_name（缓存失效时定位 realtime:history 键，R13）
            "loop_part": loop_tag_name,
            # 测点子表写入用：role → (tag_name, point_id=tag_registry.id)
            "role_point_map": {
                role: (name, name_to_id_map.get(name, "")) for role, name in role_tag_map.items()
            },
        }

    return result


async def _probe_remote_history_api() -> None:
    """批量导入前置探测：远端历史 API 连通性/熔断状态快检.

    0909 加固：961 回路批次启动时若远端恰在熔断/不可达，旧逻辑会让
    全部 78 分块走"熔断中快速失败"，任务 70s 内结束、0 点写入、errors
    超长（7 万条日志雪崩）。前置探测用最小请求（1 位号 1 窗口）快速
    判断——熔断打开/超时/网络错误时抛 HistoryDataSourceError，
    调用方直接置 FAILED 并返回"稍后重试"，不给用户错误地狱。

    - 探测走共享守卫（_get_remote_guard），复用熔断/限流，不会绕过；
    - 探测失败不重试（短超时即可），避免把熔断从"近开"推向"打开"。
    """
    if not settings.HISTORY_DATA_API_URL:
        raise HistoryDataSourceError("HISTORY_DATA_API_URL 未配置")

    now = datetime.now(UTC)
    probe_body = {
        "tagCodes": ["__CONNECTIVITY_PROBE__"],
        "startTime": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "endTime": now.isoformat().replace("+00:00", "Z"),
        "sampleInterval": 1,
    }
    guard = _get_remote_guard()
    try:
        resp = await guard.fetch_history_guarded(probe_body)
    except RemoteApiCircuitOpenError as exc:
        raise HistoryDataSourceError(f"远端历史数据 API 熔断中，稍后重试: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise HistoryDataSourceError("远端历史数据 API 探测超时，稍后重试") from exc
    except Exception as exc:  # noqa: BLE001 — RST 等网络错误
        raise HistoryDataSourceError(
            f"远端历史数据 API 不可达: {type(exc).__name__}: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise HistoryDataSourceError(f"远端历史数据 API 探测返回 HTTP {resp.status_code}，稍后重试")
    logger.info("远端历史数据 API 前置探测通过（HTTP 200）")


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


async def _write_point_events(
    role_point_map: dict[str, tuple[str, str]],
    raw_data: tuple[list[str], dict[str, dict]],
    *,
    source_task: str,
) -> tuple[int, dict[str, int]]:
    """远端历史样本 → 测点子表点事件（P2-3/P2-4）.

    - source_kind=REMOTE_GRID（4）：sampleInterval 重建样本，**不宣称原始事件**
      （设计 §6.1；HistoryData/Get 是否原始 COV 未确认——P0-5 U2）；
    - 质量经 ``decode_history_quality`` 解码（暂定 AAS 历史枚举；未知 → UNKNOWN
      绝不 Good）；
    - 返回 (时间槽计数, 内部物理计数)。**对外 imported_count 单位保持时间槽**
      （与宽表行口径一致，frontend/任务响应不感知布局）；
    - 幂等/冲突由仓储层分流（同 ts 同 payload skip；不同 payload 登记冲突）。
    """
    from app.services.data_source.point_history_repository import (
        QSCHEMA_AAS,
        SOURCE_KIND_REMOTE_GRID,
        PointEvent,
        write_events,
    )
    from app.services.data_source.point_history_writer import (
        decode_history_quality,
        parse_source_ts,
    )

    timestamps, series_map = raw_data
    events: list[PointEvent] = []
    ts_parsed: list[datetime | None] = []
    for ts_str in timestamps:
        # 点事件 ts = 真实源时刻（UTC）——不用 _parse_dt（那是 +8 墙钟落库口径）；
        # Z/带偏移串按原时区换算，naive 按 +8 墙钟解释（与宽表行时间同口径，
        # 见 parse_source_ts 的 2026-09-07 时区修复说明）
        ts_parsed.append(parse_source_ts(ts_str))
    slots_with_value = 0
    for _role, entry in role_point_map.items():
        tag_name, point_id = entry
        if not point_id:
            continue  # 无点身份的角色不入点表（原宽表 NULL 语义不变）
        series = series_map.get(tag_name) or series_map.get(tag_name.lower())
        if not series:
            continue
        values = series.get("values", [])
        qualities = series.get("qualities", [])
        for i, ts in enumerate(ts_parsed):
            if ts is None:
                continue
            v = _parse_float_val(values[i]) if i < len(values) else None
            qclass, qraw = decode_history_quality(qualities[i] if i < len(qualities) else None)
            events.append(
                PointEvent(
                    point_id=point_id,
                    ts=ts,
                    value=v,
                    quality_class=qclass,
                    quality_raw=qraw,
                    quality_schema=QSCHEMA_AAS,
                    source_kind=SOURCE_KIND_REMOTE_GRID,
                    received_at=datetime.now(UTC),
                    source_id=f"import:{source_task[:32]}",
                )
            )
    if not events:
        return 0, {"physical": 0, "identical": 0, "conflicts": 0}
    result = await write_events(events, source_task=source_task or None)
    # 时间槽计数：任一角色在某槽有事件即计一槽（与宽表行口径对齐）
    slot_ts = {e.ts for e in events}
    slots_with_value = len(slot_ts)
    stats = {
        "physical": result.inserted,
        "identical": result.identical_skipped,
        "conflicts": len(result.conflicts),
    }
    if result.failed:
        raise HistoryDataSourceError(f"点事件写入失败: {result.error}")
    return slots_with_value, stats


def _convert_to_wide_rows(
    raw_data: tuple[list[str], dict[str, dict]],
    role_tag_map: dict[str, str],
    low_fills: dict[str, _StepFill] | None = None,
) -> list[tuple]:
    """将远端 API 响应转换为宽表行格式（算法 v2：低频列阶跃前向填充）.

    行格式: (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)

    low_fills 提供时，SP/MODE/PID_* 列从低频 COV 样本按 ts 前向取值
    （与读取侧 COV 展开口径一致）；未提供时回退按 series 同索引取值
    （v1 行为，兼容全角色单流退化路径与既有单测）。
    """
    from app.services.data_source.point_history_writer import parse_source_ts

    timestamps, series_map = raw_data
    rows: list[tuple] = []

    # 构建 role → series 反向映射
    role_series: dict[str, dict] = {}
    for role, tag_name in role_tag_map.items():
        series = series_map.get(tag_name) or series_map.get(tag_name.lower())
        if series:
            role_series[role] = series

    for i, ts_str in enumerate(timestamps):
        # 解析时间戳：墙钟串落宽表行键；UTC 时刻供低频填充游标对齐
        ts = _parse_ts_str(ts_str)
        if ts is None:
            continue
        ts_utc = parse_source_ts(ts_str)
        # 提取各角色值
        row = _build_wide_row(i, role_series, ts_utc=ts_utc, low_fills=low_fills)
        rows.append((ts, *row))

    return rows


def _build_wide_row(
    index: int,
    role_series: dict[str, dict],
    ts_utc: datetime | None = None,
    low_fills: dict[str, _StepFill] | None = None,
) -> tuple:
    """构造单行数据（不含 ts）.

    返回: (pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
    低频角色（SP/MODE/PID_*）在 low_fills 提供时按阶跃前向取值，否则按
    series 同索引取值（v1 行为）。
    """

    def _high_float(role: str) -> float | None:
        return _parse_float_val(_get_series_value(role_series, role, index))

    def _role_value(role: str) -> Any:
        if low_fills is not None and role in low_fills and ts_utc is not None:
            return low_fills[role].value_at(ts_utc)
        return _get_series_value(role_series, role, index)

    pv = _high_float("PV")
    sp = _parse_float_val(_role_value("SP"))
    op = _high_float("OP")
    mode = _parse_int_val(_role_value("MODE"))
    pid_p = _parse_float_val(_role_value("PID_P"))
    pid_i = _parse_float_val(_role_value("PID_I"))
    pid_d = _parse_float_val(_role_value("PID_D"))

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
    """安全解析 float.

    NaN/Inf（如远端返回 ``"nan"``/``"inf"`` 字符串）一律置 None（写 NULL）：
    TDengine SQL 文本协议不接受 nan/inf 字面量，原样输出会导致整 chunk 写入失败。
    """
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _parse_int_val(value: Any) -> int | None:
    """安全解析 int（NaN/Inf 经 float 转换会抛 ValueError/OverflowError，一并拦截）."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return None


def _map_quality(value: Any) -> int | None:
    """外部质量码 → CLPM 内部质量码（1=Good, 0=Bad；缺失=None/NULL）.

    与实时写入口径对齐（realtime_subscriber._build_row）：远端未携带质量码
    （qualities 缺失/短于 values）或不可解析时写 NULL，不臆断为 Bad——
    Bad 会被诊断门禁剔除，误标会造成"导入后反而缺数"。
    """
    if value is None:
        return None
    try:
        q_int = int(value)
    except (ValueError, TypeError):
        return None
    return 1 if q_int in _GOOD_QUALITY_CODES else 0


def _parse_ts_str(ts_str: str) -> str | None:
    """解析时间戳字符串为 TDengine 可接受的格式（显式 astimezone 到目标时区）.

    - 带时区（含 Z 后缀）：astimezone 到 _TARGET_TZ（Asia/Shanghai）
    - naive（无时区）：视为已在 _TARGET_TZ
    - 返回 naive 字符串（TDengine 服务器本地时区解释）
    """
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TARGET_TZ)
        else:
            dt = dt.astimezone(_TARGET_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (ValueError, TypeError):
        return None


def _parse_dt(ts_str: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive datetime（显式 astimezone 到目标时区）.

    返回 naive datetime（已转换到 _TARGET_TZ，供 TDengine 本地时区解释）。
    """
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TARGET_TZ)
    else:
        dt = dt.astimezone(_TARGET_TZ)
    return dt.replace(tzinfo=None)


async def _invalidate_loop_caches(loop_id: str, loop_part: str) -> None:
    """导入/补数写入本地 TD 后失效相关缓存（R13）.

    - ``realtime:history:{loop_part}``：近 1 小时实时缓存。导入补齐/覆盖
      更正本地 TD 后必须 DEL，否则残缺或陈旧的缓存行会在完整性校验通过时
      遮蔽 TDengine 权威数据（key 前缀 lazy import 自 realtime_subscriber，
      与其 ``_REDIS_KEY_PREFIX`` 常量保持一致，避免循环依赖与字面量漂移）。
    - L1/L2/L3 计算缓存（``pdb*:{loop_id}:*``）：接入既有 CacheInvalidator
      .invalidate_loop（覆盖 DataBlock / Bundle / 聚合三层）。

    失效失败只记日志（不阻断导入结果上报），但必须显式暴露——缓存残留
    的后果是"已补齐仍读旧数据"，需要运维可见。
    """
    try:
        if loop_part:
            from app.services.data_source.realtime_subscriber import _REDIS_KEY_PREFIX

            await redis_client.delete(f"{_REDIS_KEY_PREFIX}history:{loop_part}")
        from app.services.cache.invalidation import CacheInvalidator

        await CacheInvalidator(redis_client).invalidate_loop(loop_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "导入后缓存失效失败（loop=%s, loop_part=%s，存在旧缓存遮蔽新数据风险）: %s",
            loop_id,
            loop_part,
            exc,
        )


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

    cas_code, old_status = await _update_task_cas(
        task_id,
        new_status=ImportStatus.CANCELLED.value,
        finished_at=_now_iso(),
    )
    if cas_code == "BLOCKED":
        # 任务已处于终态（SUCCESS/FAILED），无法取消已完成任务
        logger.info(
            "取消任务被 CAS 拒绝（已终态）: task_id=%s, existing=%s",
            task_id,
            old_status,
        )
        fresh = await _get_task(task_id) or data
        resp = _task_to_response(fresh)
        resp["was_blocked"] = True
        return resp
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


async def sweep_stale_running_tasks() -> dict[str, Any]:
    """清扫停滞/超时 RUNNING 导入任务（worker 卡死或被杀导致任务永久卡"执行中"）.

    遍历导入任务索引，找出 RUNNING 且判定已停滞的任务，置为 FAILED：
    - 有 last_progress_at 心跳（新版任务）：超过
      ``IMPORT_TASK_STALL_TIMEOUT_SECONDS`` 无进度推进 → 判卡死。
      长任务只要持续推进进度即视为存活，不会被误清扫。
    - 无心跳（旧版任务）：started_at 距今超过
      ``IMPORT_TASK_RUNNING_TIMEOUT_SECONDS`` → 兜底清扫。

    Returns:
        {"swept": N, "details": [...]}
    """
    timeout = int(settings.IMPORT_TASK_RUNNING_TIMEOUT_SECONDS)
    stall_timeout = int(settings.IMPORT_TASK_STALL_TIMEOUT_SECONDS)
    now_ts = datetime.now(UTC).timestamp()
    task_ids = await redis_client.zrange(_IMPORT_TASK_INDEX, 0, -1)
    swept: list[str] = []
    for tid in task_ids:
        data = await _get_task(tid)
        if not data:
            continue
        if data.get("status") != ImportStatus.RUNNING.value:
            continue
        last_progress_at = data.get("last_progress_at", "")
        started_at = data.get("started_at", "")
        ref_raw = last_progress_at or started_at
        if not ref_raw:
            continue
        try:
            ref_ts = datetime.fromisoformat(ref_raw).timestamp()
        except (ValueError, TypeError):
            continue
        # 有心跳按停滞阈值判活，无心跳（旧任务）按 RUNNING 总时长兜底
        threshold = stall_timeout if last_progress_at else timeout
        if now_ts - ref_ts < threshold:
            continue
        error_msg = (
            f"进度停滞超时（>{threshold}s 无进度推进），疑为 worker 卡死"
            if last_progress_at
            else f"RUNNING 超时（>{timeout}s），疑为 worker 异常终止"
        )
        cas_code, _old = await _update_task_cas(
            tid,
            new_status=ImportStatus.FAILED.value,
            finished_at=_now_iso(),
            error_message=error_msg,
        )
        if cas_code == "BLOCKED":
            # 原 worker 恰好同时完成（置 SUCCESS），CAS 拒绝覆盖终态
            logger.info(
                "清扫 RUNNING 任务 CAS 被拒（原 worker 已置终态）: task_id=%s, existing=%s",
                tid,
                _old,
            )
            continue
        swept.append(tid)
        logger.warning("导入任务 RUNNING 超时已清扫: task_id=%s", tid)
    return {"swept": len(swept), "details": swept}


async def prune_import_task_index() -> int:
    """修剪导入任务索引（移除已过期的任务 ID）.

    Redis Hash 自带 TTL 过期自动删除，但 Sorted Set 索引不会自动清理。
    本函数扫描索引中已不存在 Hash 的 task_id 并从索引移除。

    Returns:
        移除的条目数
    """
    task_ids = await redis_client.zrange(_IMPORT_TASK_INDEX, 0, -1)
    removed = 0
    for tid in task_ids:
        data = await _get_task(tid)
        if not data:
            await redis_client.zrem(_IMPORT_TASK_INDEX, tid)
            removed += 1
    if removed:
        logger.info("导入任务索引修剪: 移除 %d 个过期条目", removed)
    return removed


__all__ = [
    "cancel_import_task",
    "create_import_task",
    "delete_import_task",
    "get_import_task",
    "import_history_data",
    "list_import_tasks",
    "prune_import_task_index",
    "sweep_stale_running_tasks",
]
