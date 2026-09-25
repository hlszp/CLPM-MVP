"""趋势数据查询服务 — 统一封装并行查询 + 动态采样间隔.

供 monitor.py / 其他需要趋势数据的页面复用。

核心逻辑：
1. 根据时间范围动态计算采样间隔（固定目标点数 ~3600）
2. 并行查询 PV/SP/OP/MODE 四个 tag
3. 时间戳对齐 + 质量码归一化
4. 超过 target_points 时触发 LTTB 降采样

用法::

    from app.services.trend_service import fetch_loop_trend

    result = await fetch_loop_trend(
        db, loop_id,
        start_time="2026-07-09T00:00:00Z",
        end_time="2026-07-12T00:00:00Z",
        target_points=1800,
    )
    # result = {"timestamps": [...], "pv": [...], "sp": [...], ...}
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeparse import parse_iso_datetime, to_naive_utc
from app.models.loop import LoopTagMapping
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# 默认目标点数（趋势图展示上限）
#: 趋势显示网格上限（2026-09-19 用户口径：窗口最大 1800 点，超出按间隔取
#: 数确保刷新及时；builder 网格步长 = 窗口/该值，72h → 144s 步长）
DEFAULT_TARGET_POINTS = 1800


def lttb_downsample_multi_series(
    timestamps: list[int],
    series_map: dict[str, list[Any]],
    target_points: int,
) -> tuple[list[int], dict[str, list[Any]]]:
    """LTTB 降采样（多序列共享时间戳）。

    使用 PV 序列作为参考序列进行降采样，其他序列按相同索引采样。

    Args:
        timestamps: 时间戳数组（毫秒）
        series_map: {series_name: values} 字典
        target_points: 目标点数

    Returns:
        (降采样后的 timestamps, 降采样后的 series_map)
    """
    n = len(timestamps)
    if n <= target_points or n <= 2:
        return timestamps, series_map

    # 选择参考序列（优先 PV，否则第一个非空序列）
    ref_key = "pv"
    if ref_key not in series_map or not any(v is not None for v in series_map[ref_key]):
        for k, vals in series_map.items():
            if any(v is not None for v in vals):
                ref_key = k
                break

    ref_values = series_map.get(ref_key, [0.0] * n)
    # 将 None 替换为 0 用于计算
    ref_numeric = [v if v is not None else 0.0 for v in ref_values]
    ts_numeric = [float(t) for t in timestamps]

    sampled_indices: list[int] = [0]
    bucket_size = (n - 2) / (target_points - 2)
    a = 0

    for i in range(target_points - 2):
        bucket_start = int((i + 1) * bucket_size) + 1
        bucket_end = min(int((i + 2) * bucket_size) + 1, n)
        next_bucket_start = bucket_end
        next_bucket_end = min(int((i + 3) * bucket_size) + 1, n)

        # 计算下一个桶的平均点
        avg_x = 0.0
        avg_y = 0.0
        avg_count = 0
        for j in range(next_bucket_start, next_bucket_end):
            avg_x += ts_numeric[j]
            avg_y += ref_numeric[j]
            avg_count += 1
        if avg_count > 0:
            avg_x /= avg_count
            avg_y /= avg_count

        # 在当前桶中找到与三角形面积最大的点
        max_area = -1.0
        max_area_idx = bucket_start
        point_a_x = ts_numeric[a]
        point_a_y = ref_numeric[a]

        for j in range(bucket_start, bucket_end):
            area = (
                abs(
                    (point_a_x - avg_x) * (ref_numeric[j] - point_a_y)
                    - (point_a_x - ts_numeric[j]) * (avg_y - point_a_y)
                )
                * 0.5
            )
            if area > max_area:
                max_area = area
                max_area_idx = j

        sampled_indices.append(max_area_idx)
        a = max_area_idx

    sampled_indices.append(n - 1)

    new_timestamps = [timestamps[i] for i in sampled_indices]
    new_series_map: dict[str, list[Any]] = {}
    for k, vals in series_map.items():
        new_series_map[k] = [vals[i] for i in sampled_indices]

    return new_timestamps, new_series_map


# LTTB 降采样阈值（超过此值才触发降采样）
LTTB_THRESHOLD = 5000


def _parse_iso_datetime(s: str) -> datetime:
    """解析 ISO 8601 时间字符串（非法输入抛 400，不落到 500）。"""
    return parse_iso_datetime(s, field="startTime/endTime")


def _ts_to_millis(ts: Any) -> int | None:
    """将时间戳（字符串或 datetime）转为毫秒整数。

    naive（无时区）输入按 UTC 处理（补 Z 口径）：返回前端的毫秒时间戳
    与后端部署时区无关，避免 naive .timestamp() 被解释为本地墙钟。
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        return int(v * 1000) if v < 1e12 else int(v)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            try:
                v = float(ts)
                return int(v * 1000) if v < 1e12 else int(v)
            except (ValueError, TypeError):
                return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return int(ts.timestamp() * 1000)
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp() * 1000)
    return None


def _quality_to_label(q: Any) -> str:
    """质量码 → 前端标签（GOOD/BAD/UNCERTAIN，大写对齐前端 Quality 类型）。

    兼容两种 schema：
    - TDengine: 1=Good, 0=Bad
    - OPC DA: 192=Good
    - 已是字符串则原样归一化
    """
    if q is None:
        return "GOOD"
    if isinstance(q, str):
        q_upper = q.upper()
        if q_upper == "GOOD":
            return "GOOD"
        if q_upper == "BAD":
            return "BAD"
        if q_upper == "UNCERTAIN":
            return "UNCERTAIN"
        try:
            q = int(q)
        except (ValueError, TypeError):
            return "UNCERTAIN"
    if isinstance(q, (int, float)):
        if q in (1, 192):
            return "GOOD"
        if q == 0:
            return "BAD"
        return "UNCERTAIN"
    return "UNCERTAIN"


def compute_sample_interval(
    start_time: str, end_time: str, target_points: int = DEFAULT_TARGET_POINTS
) -> int:
    """根据时间范围动态计算采样间隔（秒）。

    确保返回的数据点数不超过 target_points。

    Examples:
        1h → 1s   (3600s / 3600 = 1)
        2h → 2s   (7200s / 3600 = 2)
        4h → 4s   (14400s / 3600 = 4)
        24h → 24s (86400s / 3600 = 24)
        72h → 72s (259200s / 3600 = 72)
    """
    start_dt = to_naive_utc(_parse_iso_datetime(start_time))
    end_dt = to_naive_utc(_parse_iso_datetime(end_time))
    delta_seconds = int((end_dt - start_dt).total_seconds())
    if delta_seconds <= 0:
        return 1
    return max(1, delta_seconds // target_points)


async def _fetch_trend_fast(
    db: AsyncSession,
    loop_id: str,
    start_dt: Any,
    end_dt: Any,
    sample_interval: int,
) -> dict[str, Any]:
    """显示快路径：点表布局下用 TD 服务端降采样组装趋势.

    每角色位号一条 PARTITION BY point_id + INTERVAL + FILL(PREV) 聚合查询
    （≤target_points 桶，空窗前向补齐），只回 ≤1800 桶而非全窗原始事件
    （数万~数十万行）——查询负载与传输量降 1~2 个数量级（0920 性能优化）。
    """
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    role_point: dict[str, str] = {}
    for r in ("PV", "SP", "OP", "MODE"):
        m = mappings.get(r)
        if m is not None:
            role_point[r] = str(m.tag_id)
    if not role_point:
        raise LookupError("loop 无角色位号绑定")

    from app.services.data_source.point_history_repository import (
        read_trend_buckets,
    )

    # 2026-09-24 修复：此处曾传 (start_ms, end_ms, step_ms) 三个毫秒整数，
    # 与 read_trend_buckets(point_ids, start: datetime, end: datetime, interval_s: int)
    # 签名不符 —— start/end 被当 datetime 调 .timestamp() 抛 AttributeError，
    # 且 step_ms 会被当作"秒"再 ×1000，桶宽放大 1000 倍。
    # 快路径因此自 0920 优化上线起从未生效（异常被下方 except 静默回退）。
    buckets = await read_trend_buckets(list(role_point.values()), start_dt, end_dt, sample_interval)

    pv_point = role_point.get("PV")
    pv_map = buckets.get(pv_point, {}) if pv_point else {}
    starts = sorted({b for bm in buckets.values() for b in bm})

    # 2026-09-24 修复：原实现用角色名（"SP"/"OP"/"MODE"）去索引以小写为键的
    # 累加器字典，抛 KeyError —— 与上面的签名错位叠加，使快路径 100% 失败。
    # 三个角色映射循环外预取，避免每个桶重复 dict 查询（快路径自身的目标即省开销）。
    smap_by_dst = {
        "sp": buckets.get(role_point.get("SP", ""), {}),
        "op": buckets.get(role_point.get("OP", ""), {}),
        "mode": buckets.get(role_point.get("MODE", ""), {}),
    }

    timestamps: list[int] = []
    pv: list[float | None] = []
    sp: list[float | None] = []
    op: list[float | None] = []
    mode: list[float | None] = []
    ql: list[str] = []
    for b in starts:
        timestamps.append(b)
        qe = pv_map.get(b)
        q = int(qe[1]) if qe and qe[1] is not None else -1
        ql.append(_quality_to_label(q))
        # PV 质量码 BAD → null（对齐既有显示语义）
        pv.append(None if (qe is None or q == 0) else qe[0])
        for dst, src in (("sp", "SP"), ("op", "OP"), ("mode", "MODE")):
            e = smap_by_dst[dst].get(b)
            if e is not None and e[0] is not None:
                v = e[0]
                if src == "MODE":
                    v = int(round(float(v)))
                {"sp": sp, "op": op, "mode": mode}[dst].append(v)
            else:
                {"sp": sp, "op": op, "mode": mode}[dst].append(None)

    return {
        "timestamps": timestamps,
        "pv": pv,
        "sp": sp,
        "op": op,
        "mode": mode,
        "pvQuality": ql,
        "sampleInterval": sample_interval,
        "pointCount": len(timestamps),
        "downsampled": True,
    }


async def fetch_loop_trend(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    *,
    target_points: int = DEFAULT_TARGET_POINTS,
    roles: tuple[str, ...] = ("PV", "SP", "OP", "MODE"),
    tags_map: dict[str, TagRegistry] | None = None,
    mappings: dict[str, LoopTagMapping] | None = None,
) -> dict[str, Any]:
    """查询回路多角色趋势数据（并行 + 动态采样间隔）.

    Args:
        db: 异步数据库会话
        loop_id: 回路 ID
        start_time: 开始时间（ISO 8601 字符串）
        end_time: 结束时间（ISO 8601 字符串）
        target_points: 目标数据点数（默认 1800）
        roles: 要查询的角色列表
        tags_map: 预加载的 Tag 详情 ``{tag_id: TagRegistry}``，
            若为 None 则内部查询数据库。调用方已加载时传入可避免重复查询。
        mappings: 预加载的角色映射 ``{tag_role: LoopTagMapping}``，
            若为 None 则内部查询数据库。

    Returns:
       ::

            {
                "timestamps": list[int],       # 毫秒时间戳
                "pv": list[float|None],
                "sp": list[float|None],
                "op": list[float|None],
                "mode": list[float|None],
                "pvQuality": list[str],        # GOOD/BAD/UNCERTAIN
                "sampleInterval": int,         # 实际使用的采样间隔（秒）
                "pointCount": int,
                "downsampled": bool,           # 是否触发了 LTTB 降采样
            }
    """
    # 1. 动态计算采样间隔（统一归一为 naive UTC，避免 aware/naive 相减抛
    # TypeError → 500；时间窗语义与 DB/TDengine 的 naive UTC 口径一致）
    start_dt = to_naive_utc(_parse_iso_datetime(start_time))
    end_dt = to_naive_utc(_parse_iso_datetime(end_time))
    delta_seconds = int((end_dt - start_dt).total_seconds())
    sample_interval = compute_sample_interval(start_time, end_time, target_points)
    logger.info(
        "趋势查询开始: loop=%s, range=%s~%s (%ds), targetPoints=%d → sampleInterval=%ds "
        "(计算: %ds / %d = %ds)",
        loop_id,
        start_time,
        end_time,
        delta_seconds,
        target_points,
        sample_interval,
        delta_seconds,
        target_points,
        max(1, delta_seconds // target_points),
    )

    # 1.5 显示快路径（0920 性能优化）：点表布局时用 TD 服务端降采样
    # （PARTITION BY point_id + INTERVAL + FILL(PREV)），只回 ≤target_points
    # 个聚合桶（慢变信号前向补齐），不再拉全窗原始事件在 Python 建网格。
    # 布局非 point（legacy 兜底部署）或快路径异常时回退既有 Provider 路径。
    try:
        from app.services.data_source.point_history_metadata import resolve_layout

        if await resolve_layout(db, loop_id=loop_id, at=end_dt) == "point":
            return await _fetch_trend_fast(db, loop_id, start_dt, end_dt, sample_interval)
    except Exception as exc:  # noqa: BLE001 — 快路径失败回退，不阻塞显示
        logger.warning("趋势快路径失败，回退 Provider 查询: %s", exc)

    # 2. 查询回路 Tag 关联（若调用方已预加载则直接复用，避免重复查询）
    if mappings is None:
        m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
        mappings = {m.tag_role: m for m in m_result.scalars().all()}

    if tags_map is None:
        tag_ids = [str(m.tag_id) for m in mappings.values()]
        tags_map = {}
        if tag_ids:
            t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
            for t in t_result.scalars().all():
                tags_map[str(t.id)] = t

    available_roles = [r for r in roles if r in mappings and str(mappings[r].tag_id) in tags_map]
    logger.info(
        "趋势查询 Tag 映射: loop=%s, 请求角色=%s, 有效角色=%s",
        loop_id,
        list(roles),
        available_roles,
    )

    # 3. 宽表查询（一次查询所有角色）。无有效角色时直接返回空数据，
    # 避免 Provider 重复查询映射，也避免对远端数据源发出无意义请求。
    raw_series = None
    if available_roles:
        from app.services.data_source.factory import get_provider

        provider = get_provider()
        query_wide_fn = provider.make_query_fn(db)

        try:
            raw_series = await query_wide_fn(
                loop_id=loop_id,
                tag_roles=available_roles,
                start=start_time,
                end=end_time,
                interval_s=sample_interval,
            )
            logger.debug(
                "宽表查询成功: loop=%s, 返回点数=%d",
                loop_id,
                len(raw_series.timestamps),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("宽表查询失败 (loop=%s): %s", loop_id, exc)

    if not raw_series or not raw_series.timestamps:
        return {
            "timestamps": [],
            "pv": [],
            "sp": [],
            "op": [],
            "mode": [],
            "pvQuality": [],
            "sampleInterval": sample_interval,
            "pointCount": 0,
            "downsampled": False,
        }

    timestamps: list[int] = []
    pv_list: list[float | None] = []
    sp_list: list[float | None] = []
    op_list: list[float | None] = []
    mode_list: list[float | None] = []
    pv_quality_list: list[str] = []

    pv_qualities = raw_series.quality_codes.get("pv_quality", [])

    for i, ts in enumerate(raw_series.timestamps):
        ts_millis = _ts_to_millis(ts)
        if ts_millis is None:
            continue
        timestamps.append(ts_millis)

        q = pv_qualities[i] if i < len(pv_qualities) else "GOOD"
        quality_label = _quality_to_label(q)
        pv_quality_list.append(quality_label)

        pv_sig = raw_series.signals.get("pv")
        sp_sig = raw_series.signals.get("sp")
        op_sig = raw_series.signals.get("op")
        mode_sig = raw_series.signals.get("mode")

        # PV 质量码为 BAD 时，pv 值为 null
        if quality_label == "BAD":
            pv_list.append(None)
        else:
            pv_list.append(pv_sig[i] if pv_sig and i < len(pv_sig) else None)

        sp_list.append(sp_sig[i] if sp_sig and i < len(sp_sig) else None)
        op_list.append(op_sig[i] if op_sig and i < len(op_sig) else None)
        mode_list.append(mode_sig[i] if mode_sig and i < len(mode_sig) else None)

    # 5. LTTB 降采样（如果点数仍超过阈值）
    downsampled = False
    pre_downsample_count = len(timestamps)
    if len(timestamps) > LTTB_THRESHOLD:
        logger.info(
            "趋势查询触发 LTTB 降采样: loop=%s, 原始点数=%d > 阈值=%d → 目标点数=%d",
            loop_id,
            pre_downsample_count,
            LTTB_THRESHOLD,
            target_points,
        )
        series_map = {
            "pv": pv_list,
            "sp": sp_list,
            "op": op_list,
            "mode": mode_list,
            "pvQuality": pv_quality_list,
        }
        timestamps, series_map = lttb_downsample_multi_series(timestamps, series_map, target_points)
        pv_list = series_map["pv"]
        sp_list = series_map["sp"]
        op_list = series_map["op"]
        mode_list = series_map["mode"]
        pv_quality_list = series_map["pvQuality"]
        downsampled = True

    logger.info(
        "趋势查询完成: loop=%s, sampleInterval=%ds, 最终点数=%d, 降采样=%s%s, 基准角色=%s",
        loop_id,
        sample_interval,
        len(timestamps),
        "是" if downsampled else "否",
        f"(原始={pre_downsample_count}→{len(timestamps)})" if downsampled else "",
        "PV" if pv_list else ("SP" if sp_list else "OP"),
    )

    return {
        "timestamps": timestamps,
        "pv": pv_list,
        "sp": sp_list,
        "op": op_list,
        "mode": mode_list,
        "pvQuality": pv_quality_list,
        "sampleInterval": sample_interval,
        "pointCount": len(timestamps),
        "downsampled": downsampled,
    }


def _as_utc(dt: datetime) -> datetime:
    """naive（按 UTC 解释）或 aware → aware UTC。

    统一用 aware 比较：``history_coverage_segment`` 的 seg_start/seg_end 是
    ``DateTime(timezone=True)``，把 naive 值传给 asyncpg 会抛"can't subtract
    offset-naive and offset-aware datetimes"（本项目已多次踩到）。
    """
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def attach_gap_info(db: AsyncSession, loop_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """把「已登记缺口」附加到趋势结果上（1b，2026-09-26）。

    为什么必须查 PG 而不能从时间戳判断：趋势的 point 快路径走 TD 端
    ``PARTITION BY + FILL(PREV)``，缺口会被前向填充成平台线且时间戳均匀，
    序列本身看不出缺口。缺口事实由 1a 落在 ``history_coverage_segment``
    （``status='gap'``）。

    作用域说明：缺口段是**采集会话级**的（``point_id`` 为 NULL、``session_id``
    非空），因此这里**只按时间重叠过滤**，不按 ``loop_id`` / ``point_id`` 过滤；
    参数 ``loop_id`` 仅用于日志定位。

    失败语义：任何异常都返回 ``gaps=[]`` + ``observedRatio=None``，绝不抛错
    （缺口信息是附加项，不得影响趋势主体）。
    """
    result["gaps"] = []
    result["observedRatio"] = None
    timestamps = result.get("timestamps") or []
    if len(timestamps) < 2:
        return result
    try:
        win_start = _as_utc(parse_iso_datetime(str(timestamps[0]), field="timestamps[0]"))
        win_end = _as_utc(parse_iso_datetime(str(timestamps[-1]), field="timestamps[-1]"))
        window_s = (win_end - win_start).total_seconds()
        if window_s <= 0:
            return result

        from app.models.point_history import HistoryCoverageSegment

        rows = (
            await db.execute(
                select(
                    HistoryCoverageSegment.seg_start,
                    HistoryCoverageSegment.seg_end,
                ).where(
                    HistoryCoverageSegment.status == "gap",
                    HistoryCoverageSegment.seg_end >= win_start,
                    HistoryCoverageSegment.seg_start <= win_end,
                )
            )
        ).all()

        gaps: list[dict[str, Any]] = []
        total_gap_s = 0.0
        for seg_start, seg_end in rows:
            start = max(_as_utc(seg_start), win_start)
            end = min(_as_utc(seg_end), win_end)
            seconds = (end - start).total_seconds()
            if seconds <= 0:
                continue
            total_gap_s += seconds
            gaps.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "seconds": round(seconds, 3),
                }
            )
        gaps.sort(key=lambda g: str(g["start"]))
        result["gaps"] = gaps
        result["observedRatio"] = max(0.0, min(1.0, 1.0 - total_gap_s / window_s))
    except Exception as exc:  # noqa: BLE001 — 缺口信息是附加项，绝不抛错
        logger.warning("趋势缺口附加失败（loop=%s，返回空缺口）: %s", loop_id, exc)
        result["gaps"] = []
        result["observedRatio"] = None
    return result


__all__ = [
    "fetch_loop_trend",
    "compute_sample_interval",
    "attach_gap_info",
    "DEFAULT_TARGET_POINTS",
]
