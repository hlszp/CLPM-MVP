"""数据完整性检查服务.

对本地 TDengine 宽表做完整性检查，输出：
- 整体完整度（百分比）
- 主要时间缺口（哪些时间段缺数据，小时粒度）
- 主要回路缺口（哪些回路缺数据）

设计依据：
- 数据架构决策：计算全本地，仅查 TDengine，不调远端 API
- 性能：单回路 1 次 INTERVAL(1h) 聚合查询，Semaphore(10) 并发限流
- 复用 data_import._batch_get_loop_data 获取回路→subtable 映射

检查算法：
- 对每个回路宽表按小时分桶 COUNT(*)
- expected_per_hour = 3600 // interval_s
- 回路完整度 = actual_total / (total_hours × expected_per_hour)
- 缺失小时桶：cnt < expected_per_hour × 0.5（低于预期 50%）
- 时间缺口：跨回路聚合每小时桶的 affected_loop_count，按影响回路数倒序取 Top 50
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.tdengine import execute_sql
from app.models.loop import LoopLedger
from app.schemas.loop_data import IntegrityStatus
from app.services.data_import import _batch_get_loop_data

logger = logging.getLogger(__name__)

# 并发查询限流：tdengine.py 注释明确 >50 并发导致 REST API 排队，10 并发安全且快
_CONCURRENCY = 10

# 缺失小时桶判定阈值：桶内点数低于预期的 50% 视为该小时缺数据
_MISSING_BUCKET_RATIO = 0.5

# 时间缺口返回上限（按影响回路数倒序取 Top N）
_MAX_TIME_GAPS = 50


async def check_integrity(
    db: Any,
    loop_ids: list[str] | None,
    ts_start: str,
    ts_end: str,
    expected_interval_s: int = 1,
) -> dict[str, Any]:
    """对本地 TDengine 数据做完整性检查.

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
    # 补充 tagName 用于报告展示
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

    # 4. 并发查询每个回路的小时分桶
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
    """单回路小时分桶查询.

    单条 SQL 同时拿到：分桶计数 + 总计数 + 首/末时间。
    TDengine INTERVAL(1h) 只返回有数据的桶，空桶不出现。
    """
    async with sem:
        sql = (
            f"SELECT _wstart as bucket_start, COUNT(*) as cnt "
            f"FROM {settings.TDENGINE_DB}.{subtable} "
            f"WHERE ts >= '{ts_start}' AND ts <= '{ts_end}' "
            f"INTERVAL(1h) ORDER BY bucket_start ASC"
        )
        rows = await execute_sql(sql)
        total_count = sum(int(r.get("cnt", 0)) for r in rows)
        first_ts = str(rows[0]["bucket_start"]) if rows else None
        last_ts = str(rows[-1]["bucket_start"]) if rows else None
        return {
            "loop_id": loop_id,
            "subtable": subtable,
            "buckets": rows,
            "total_count": total_count,
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
    """聚合所有回路的查询结果，计算完整度与缺口."""
    start_dt = _parse_dt(ts_start)
    end_dt = _parse_dt(ts_end)
    expected_per_hour = max(1, 3600 // expected_interval_s)
    total_hours = max(1, math.ceil((end_dt - start_dt).total_seconds() / 3600))
    expected_total_per_loop = total_hours * expected_per_hour

    # 枚举所有期望的小时桶（对齐到整点）
    expected_buckets = _enumerate_hour_buckets(start_dt, end_dt)

    loop_details: list[dict[str, Any]] = []
    # hour_gap_map: {bucket_start_str: [loop_ids with insufficient data]}
    hour_gap_map: dict[str, list[str]] = {b: [] for b in expected_buckets}

    total_actual_all = 0
    total_expected_all = 0

    # 按结果顺序匹配 loop_id（results 顺序与 gather 入参一致）
    for r in results:
        if isinstance(r, Exception):
            logger.warning("完整性检查: 回路查询失败: %s", r)
            continue
        loop_id = r["loop_id"]
        # 归一化 bucket key：TDengine 返回 "2026-07-22T00:00:00.000Z"，
        # 统一转成 "%Y-%m-%d %H:%M:%S" 与 expected_buckets 对齐
        bucket_map = {
            _normalize_bucket_key(str(b["bucket_start"])): int(b["cnt"]) for b in r["buckets"]
        }
        actual_total = r["total_count"]

        completeness = (
            actual_total / expected_total_per_loop if expected_total_per_loop > 0 else 0.0
        )
        completeness = min(completeness, 1.0)  # 超过 100% 截断
        status = _classify_status(completeness)

        # 找该回路的缺失小时桶
        missing_hour_count = 0
        missing_threshold = expected_per_hour * _MISSING_BUCKET_RATIO
        for bh in expected_buckets:
            cnt = bucket_map.get(bh, 0)
            if cnt < missing_threshold:
                missing_hour_count += 1
                hour_gap_map[bh].append(loop_id)

        loop_details.append(
            {
                "loopId": loop_id,
                "tagName": tag_name_map.get(loop_id, ""),
                "subtable": r["subtable"],
                "expectedPoints": expected_total_per_loop,
                "actualPoints": actual_total,
                "completeness": round(completeness, 4),
                "firstTs": r["first_ts"],
                "lastTs": r["last_ts"],
                "status": status,
                "missingHourCount": missing_hour_count,
            }
        )
        total_actual_all += actual_total
        total_expected_all += expected_total_per_loop

    # 时间缺口：取 affected_loop_count > 0 的桶，按影响回路数倒序取 Top N
    time_gaps: list[dict[str, Any]] = []
    for bh_str, loops in hour_gap_map.items():
        if not loops:
            continue
        # 计算 endTs = bucket_start + 1h
        bh_dt = _parse_bucket_str(bh_str)
        end_str = (bh_dt + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        time_gaps.append(
            {
                "startTs": bh_str,
                "endTs": end_str,
                "affectedLoopCount": len(loops),
                "affectedLoopIds": loops,
            }
        )
    time_gaps.sort(key=lambda x: x["affectedLoopCount"], reverse=True)
    time_gaps = time_gaps[:_MAX_TIME_GAPS]

    overall = total_actual_all / total_expected_all if total_expected_all > 0 else 0.0
    overall = min(overall, 1.0)  # 超过 100% 截断（actual 可能略多于 expected）

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
    """解析 ISO 8601 时间字符串为 naive datetime."""
    s = ts_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    # 统一为 naive（去掉时区，对齐 TDengine TIMESTAMP 存储口径）
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _normalize_bucket_key(bucket_str: str) -> str:
    """归一化 TDengine 返回的 bucket_start 为 '%Y-%m-%d %H:%M:%S' 格式.

    TDengine REST 可能返回 '2026-07-22T00:00:00.000Z' 或
    '2026-07-22 00:00:00' 等多种格式，统一解析后格式化。
    """
    dt = _parse_bucket_str(bucket_str)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _enumerate_hour_buckets(start_dt: datetime, end_dt: datetime) -> list[str]:
    """枚举所有期望的小时桶起始时间（对齐到整点）.

    例：start=2026-07-15 10:30, end=2026-07-15 13:10
    → ['2026-07-15 10:00:00', '2026-07-15 11:00:00', '2026-07-15 12:00:00',
       '2026-07-15 13:00:00']
    """
    # 对齐到整点
    bucket_start = start_dt.replace(minute=0, second=0, microsecond=0)
    buckets: list[str] = []
    while bucket_start < end_dt:
        buckets.append(bucket_start.strftime("%Y-%m-%d %H:%M:%S"))
        bucket_start += timedelta(hours=1)
    return buckets


def _parse_bucket_str(bucket_str: str) -> datetime:
    """解析 TDengine 返回的 bucket_start 字符串为 datetime."""
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(bucket_str, fmt)
        except ValueError:
            continue
    # 兜底：ISO 解析
    try:
        return datetime.fromisoformat(bucket_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        logger.warning("无法解析 bucket 时间 %r，使用当前时间兜底", bucket_str)
        return datetime.now()


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
        "tsStart": ts_start,
        "tsEnd": ts_end,
        "expectedInterval": expected_interval_s,
        "checkedAt": datetime.now().astimezone().isoformat(),
    }


__all__ = ["check_integrity"]
