"""数据完整性检查服务.

对本地 TDengine 宽表做完整性检查（行级判定，简化版）：
- 整体完整度（百分比）
- 主要时间缺口（哪些时间段缺数据，小时粒度）
- 主要回路缺口（哪些回路缺数据）

设计依据：
- 数据架构决策：计算全本地，仅查 TDengine，不调远端 API
- 性能：单回路 1 次 INTERVAL(1h) 聚合查询（COUNT(*)），Semaphore(10) 并发限流
- 复用 data_import._batch_get_loop_data 获取回路→subtable 映射

缺失定义（2026-08-17 简化为行级判定）：
1. "缺失" = 该秒级时间戳没有行（即 COUNT(*) 统计的行数少于预期点数）
2. 不检查任何列的值是否为 NULL——列 NULL 属于数据质量范畴，
   由质量码和预处理 pipeline 处理，不在完整性检查中混合判定
3. 时间范围按筛选实际给定：不足整点的首尾桶用实际秒数算预期点数，不用固定 3600
4. TDengine 故障（连接失败/SQL 错误）与"真无数据"严格区分：
   查询失败抛 TDengineError，报告 dataSourceUnavailable=True，
   不判为缺失（避免误导用户在数据源宕机时重新导入）

检查算法：
- 对每个回路宽表按小时分桶，COUNT(*) 统计每桶实际行数
- expected_per_bucket = 桶实际秒数 / interval_s（首尾桶用实际跨度）
- 回路完整度 = 实际总行数 / 预期总行数
- 缺失小时桶：实际行数 < 预期行数 视为该小时有缺失

时区口径：
- 查询边界经 ``_to_utc_z`` 统一为带 Z 的 UTC 串（服务器按 +8 解释 naive 串）
- 期望桶枚举与 REST 返回桶键统一按 naive UTC（epoch 小时）对齐，
  带时区输入一律 astimezone(UTC) 后再去 tzinfo，不直接丢弃时区
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.tdengine import TDengineError, execute_sql
from app.models.loop import LoopLedger
from app.schemas.loop_data import IntegrityStatus
from app.services.data_import import _batch_get_loop_data

logger = logging.getLogger(__name__)

# 并发查询限流：tdengine.py 注释明确 >50 并发导致 REST API 排队，10 并发安全且快
_CONCURRENCY = 10

# 时间缺口返回上限（按影响回路数倒序取 Top N）
_MAX_TIME_GAPS = 50


async def check_integrity(
    db: Any,
    loop_ids: list[str] | None,
    ts_start: str,
    ts_end: str,
    expected_interval_s: int = 1,
) -> dict[str, Any]:
    """对本地 TDengine 数据做完整性检查（行级判定）.

    Args:
        db: 异步数据库会话
        loop_ids: 目标回路 ID 列表，None 时查全部 READY 回路
        ts_start: 检查时间范围起始（ISO 8601）
        ts_end: 检查时间范围结束（ISO 8601）
        expected_interval_s: 预期采样间隔（秒），用于计算预期点数

    Returns:
        IntegrityCheckResponse 兼容的 dict
    """
    # 1. 解析回路列表
    if not loop_ids:
        loop_ids = await _get_ready_loop_ids(db)
    if not loop_ids:
        logger.info("完整性检查: 无目标回路，返回空结果")
        return _empty_response(ts_start, ts_end, expected_interval_s)

    # 2. 批量预加载 subtable 映射（复用 data_import._batch_get_loop_data）
    loop_meta = await _batch_get_loop_data(db, loop_ids)
    tag_name_map = await _batch_get_tag_names(db, loop_ids)

    # 3. 过滤出有 subtable 的回路
    targets: list[tuple[str, str]] = []
    for lid in loop_ids:
        meta = loop_meta.get(lid, {})
        subtable = meta.get("subtable", "")
        if subtable:
            targets.append((lid, subtable))
        else:
            logger.debug("完整性检查: 回路 %s 无 subtable 映射，跳过", lid)

    if not targets:
        logger.info("完整性检查: 无有效 subtable，返回空结果")
        return _empty_response(ts_start, ts_end, expected_interval_s)

    # 4. 并发查询每个回路的小时分桶（COUNT(*) 行级统计）
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(
        *[_query_loop_bucket(sem, lid, sub, ts_start, ts_end) for lid, sub in targets],
        return_exceptions=True,
    )

    # 5. 聚合
    return _aggregate(
        results=results,
        loop_ids=loop_ids,
        tag_name_map=tag_name_map,
        ts_start=ts_start,
        ts_end=ts_end,
        expected_interval_s=expected_interval_s,
    )


async def _get_ready_loop_ids(db: Any) -> list[str]:
    """查询所有 READY 状态的活跃回路 ID."""
    result = await db.execute(
        select(LoopLedger.id).where(
            LoopLedger.status == "READY",
            LoopLedger.is_active.is_(True),
        )
    )
    return [str(row) for row in result.scalars().all()]


async def _batch_get_tag_names(db: Any, loop_ids: list[str]) -> dict[str, str]:
    """批量查询回路位号名（用于报告展示）."""
    from uuid import UUID

    result = await db.execute(
        select(LoopLedger).where(LoopLedger.id.in_([UUID(lid) for lid in loop_ids]))
    )
    return {str(loop.id): loop.tag_name or "" for loop in result.scalars().all()}


async def _query_loop_bucket(
    sem: asyncio.Semaphore,
    loop_id: str,
    subtable: str,
    ts_start: str,
    ts_end: str,
) -> dict[str, Any]:
    """单回路小时分桶查询，COUNT(*) 统计每桶实际行数.

    TDengine 故障时抛 TDengineError（raise_on_error=True），由聚合层
    统一报告"数据源不可用"，不降级为空结果误判缺失。
    """
    async with sem:
        # 查询边界统一归一化为带 Z 的 UTC 串：服务器按 +8 解释 naive 字符串，
        # 直接拼 naive 输入会使过滤窗口偏移 8 小时
        utc_start = _to_utc_z(ts_start)
        sql = (
            f"SELECT _wstart AS bucket_start, COUNT(*) AS cnt "
            f"FROM {settings.TDENGINE_DB}.{subtable} "
            f"WHERE ts >= '{utc_start}' AND ts <= '{_to_utc_z(ts_end)}' "
            f"INTERVAL(1h) ORDER BY bucket_start ASC"
        )
        try:
            rows = await execute_sql(sql, raise_on_error=True)
        except TDengineError as exc:
            exc.loop_id = loop_id  # type: ignore[attr-defined]  # 聚合层报告用
            raise

        # 总行数
        total_rows = sum(int(r.get("cnt", 0)) for r in rows)

        first_ts = str(rows[0]["bucket_start"]) if rows else None
        last_ts = str(rows[-1]["bucket_start"]) if rows else None
        return {
            "loop_id": loop_id,
            "subtable": subtable,
            "buckets": rows,
            "total_rows": total_rows,
            "first_ts": first_ts,
            "last_ts": last_ts,
        }


def _aggregate(
    results: list[Any],
    loop_ids: list[str],
    tag_name_map: dict[str, str],
    ts_start: str,
    ts_end: str,
    expected_interval_s: int,
) -> dict[str, Any]:
    """聚合所有回路的查询结果，计算完整度与缺口（行级判定）."""
    start_dt = _parse_dt(ts_start)
    end_dt = _parse_dt(ts_end)
    expected_points_per_sec = 1.0 / expected_interval_s  # 每秒预期点数
    total_seconds = (end_dt - start_dt).total_seconds()
    if total_seconds <= 0:
        return _empty_response(ts_start, ts_end, expected_interval_s)

    # 预期总行数 = 时间范围实际秒数 / 采样间隔
    expected_total = total_seconds * expected_points_per_sec

    # 枚举所有期望的小时桶，记录每桶的预期点数（首尾桶用实际秒数）
    expected_buckets = _enumerate_hour_buckets_with_expected(start_dt, end_dt, expected_interval_s)
    # expected_buckets: list[(bucket_start_str, bucket_expected_points)]

    loop_details: list[dict[str, Any]] = []
    # hour_gap_map: {bucket_start_str: [loop_ids with missing rows]}
    hour_gap_map: dict[str, list[str]] = {b[0]: [] for b in expected_buckets}

    total_actual_all = 0
    total_expected_all = 0
    # TDengine 故障的回路：数据源不可用，不参与缺失判定
    failed_loop_ids: list[str] = []

    for r in results:
        if isinstance(r, TDengineError):
            # TDengine 故障 ≠ 该时段无数据：报告数据源不可用，不判缺失
            failed_loop_ids.append(getattr(r, "loop_id", "unknown"))
            logger.warning(
                "完整性检查: 回路 %s 查询失败（数据源不可用）: %s",
                failed_loop_ids[-1],
                r,
            )
            continue
        if isinstance(r, Exception):
            logger.warning("完整性检查: 回路查询失败: %s", r)
            continue
        loop_id = r["loop_id"]

        # 构建桶查询索引：bucket_start_str -> actual_count
        bucket_map: dict[str, int] = {}
        for b in r["buckets"]:
            key = _normalize_bucket_key(str(b["bucket_start"]))
            bucket_map[key] = int(b.get("cnt", 0))

        # 回路完整度 = 实际行数 / 预期行数
        actual = r["total_rows"]
        completeness = min(actual / expected_total, 1.0) if expected_total > 0 else 0.0
        status = _classify_status(completeness)

        # 找该回路的缺失小时桶：实际行数 < 预期行数即缺失
        missing_hour_count = 0
        for bh_str, bh_expected in expected_buckets:
            bucket_actual = bucket_map.get(bh_str, 0)
            if bucket_actual < bh_expected:
                missing_hour_count += 1
                hour_gap_map[bh_str].append(loop_id)

        loop_details.append(
            {
                "loopId": loop_id,
                "tagName": tag_name_map.get(loop_id, ""),
                "subtable": r["subtable"],
                "expectedPoints": int(expected_total),
                "actualPoints": actual,
                "completeness": round(completeness, 4),
                "firstTs": r["first_ts"],
                "lastTs": r["last_ts"],
                "status": status,
                "missingHourCount": missing_hour_count,
            }
        )
        total_actual_all += actual
        total_expected_all += expected_total

    # 时间缺口：取 affected_loop_count > 0 的桶，按影响回路数倒序取 Top N
    time_gaps: list[dict[str, Any]] = []
    for bh_str, loops in hour_gap_map.items():
        if not loops:
            continue
        bh_dt = _parse_bucket_str(bh_str)
        # 桶结束 = 桶起点 + 1h，但不超过 end_dt
        bh_end = min(bh_dt + timedelta(hours=1), end_dt)
        time_gaps.append(
            {
                "startTs": bh_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "endTs": bh_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "affectedLoopCount": len(loops),
                "affectedLoopIds": loops,
            }
        )
    time_gaps.sort(key=lambda x: x["affectedLoopCount"], reverse=True)
    time_gaps = time_gaps[:_MAX_TIME_GAPS]

    overall = min(total_actual_all / total_expected_all, 1.0) if total_expected_all > 0 else 0.0

    return {
        "overallCompleteness": round(overall, 4),
        "loopCount": len(loop_details),
        "completeLoopCount": sum(
            1 for d in loop_details if d["status"] == IntegrityStatus.COMPLETE
        ),
        "partialLoopCount": sum(1 for d in loop_details if d["status"] == IntegrityStatus.PARTIAL),
        "missingLoopCount": sum(1 for d in loop_details if d["status"] == IntegrityStatus.MISSING),
        "loopDetails": loop_details,
        "timeGaps": time_gaps,
        # TDengine 故障标志：True 时上述完整度不反映真实数据缺失，
        # 前端应提示"数据源不可用"而非引导重新导入
        "dataSourceUnavailable": bool(failed_loop_ids),
        "failedLoopIds": failed_loop_ids,
        "tsStart": ts_start,
        "tsEnd": ts_end,
        "expectedInterval": expected_interval_s,
        "checkedAt": datetime.now().astimezone().isoformat(),
    }


def _classify_status(completeness: float) -> str:
    """根据完整度判定状态."""
    if completeness >= 0.95:
        return IntegrityStatus.COMPLETE
    if completeness >= 0.20:
        return IntegrityStatus.PARTIAL
    return IntegrityStatus.MISSING


def _parse_dt(ts_str: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive UTC datetime.

    时区口径：带时区的输入（含 Z / +08:00 偏移）先
    astimezone 到 UTC 再去 tzinfo，而非直接丢弃时区（直接丢弃会把
    +8 墙钟错当成 UTC，桶键与 TDengine REST 返回的 UTC 桶错位 8 小时）；
    naive 输入按本代码库约定视为 UTC。
    """
    s = ts_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _to_utc_z(ts_str: str) -> str:
    """将 ISO 8601 时间字符串归一化为带 Z 的 UTC 串（SQL 查询边界用）.

    TDengine 服务器按 +8 解释 naive 时间字符串，naive 输入直接拼 WHERE
    会使过滤窗口偏移 8 小时；统一输出带 Z 的 UTC 串让服务器按 UTC 解释。
    """
    dt = _parse_dt(ts_str)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _normalize_bucket_key(bucket_str: str) -> str:
    """归一化 TDengine 返回的 bucket_start 为 '%Y-%m-%d %H:%M:%S' 格式.

    TDengine REST 可能返回 '2026-07-22T00:00:00.000Z' 或
    '2026-07-22 00:00:00' 等多种格式，统一解析后格式化。
    """
    dt = _parse_bucket_str(bucket_str)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _enumerate_hour_buckets_with_expected(
    start_dt: datetime, end_dt: datetime, interval_s: int
) -> list[tuple[str, float]]:
    """枚举所有期望的小时桶，并计算每桶的预期点数.

    首尾不足整点的桶用实际秒数算预期点数，中间完整小时桶用 3600 秒算。

    例：start=10:30, end=13:10, interval=1s
    → [('10:00:00', 1800), ('11:00:00', 3600), ('12:00:00', 3600), ('13:00:00', 600)]
    （首桶 10:30-11:00 = 1800秒，末桶 13:00-13:10 = 600秒）
    """
    points_per_sec = 1.0 / interval_s
    # 对齐到整点
    bucket_start = start_dt.replace(minute=0, second=0, microsecond=0)
    buckets: list[tuple[str, float]] = []
    while bucket_start < end_dt:
        # 桶实际开始 = max(桶起点, start_dt)
        actual_start = max(bucket_start, start_dt)
        # 桶实际结束 = min(桶起点+1h, end_dt)
        actual_end = min(bucket_start + timedelta(hours=1), end_dt)
        bucket_seconds = (actual_end - actual_start).total_seconds()
        bucket_expected = bucket_seconds * points_per_sec
        buckets.append((bucket_start.strftime("%Y-%m-%d %H:%M:%S"), bucket_expected))
        bucket_start += timedelta(hours=1)
    return buckets


def _parse_bucket_str(bucket_str: str) -> datetime:
    """解析 TDengine 返回的 bucket_start 字符串为 naive UTC datetime.

    REST 返回的 INTERVAL 桶起点为 UTC（如 '2026-07-28T02:00:00.000Z'，
    已实证）；带时区输入先 astimezone 到 UTC 再去 tzinfo，naive 输入
    视为 UTC（与 _parse_dt 口径一致），保证桶键与期望枚举按 epoch 对齐。
    """
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(bucket_str, fmt)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(bucket_str.replace("Z", "+00:00"))
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        logger.warning("无法解析 bucket 时间 %r，使用当前时间兜底", bucket_str)
        return datetime.now(UTC).replace(tzinfo=None)


def _empty_response(ts_start: str, ts_end: str, expected_interval_s: int) -> dict[str, Any]:
    """无目标回路时的空响应."""
    return {
        "overallCompleteness": 0.0,
        "loopCount": 0,
        "completeLoopCount": 0,
        "partialLoopCount": 0,
        "missingLoopCount": 0,
        "loopDetails": [],
        "timeGaps": [],
        "dataSourceUnavailable": False,
        "failedLoopIds": [],
        "tsStart": ts_start,
        "tsEnd": ts_end,
        "expectedInterval": expected_interval_s,
        "checkedAt": datetime.now().astimezone().isoformat(),
    }


__all__ = ["check_integrity"]
