"""趋势数据查询服务 — 统一封装并行查询 + 动态采样间隔.

供 monitor.py / waveform.py / 其他需要趋势数据的页面复用。

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
        target_points=3600,
    )
    # result = {"timestamps": [...], "pv": [...], "sp": [...], ...}
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import LoopTagMapping
from app.models.tag import TagRegistry
from app.services.waveform import lttb_downsample_multi_series

logger = logging.getLogger(__name__)

# 默认目标点数（趋势图展示上限）
DEFAULT_TARGET_POINTS = 3600

# LTTB 降采样阈值（超过此值才触发降采样）
LTTB_THRESHOLD = 5000


def _parse_iso_datetime(s: str) -> datetime:
    """解析 ISO 8601 时间字符串。"""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromisoformat(s)


def _ts_to_millis(ts: Any) -> int | None:
    """将时间戳（字符串或 datetime）转为毫秒整数。"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        return int(v * 1000) if v < 1e12 else int(v)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            try:
                v = float(ts)
                return int(v * 1000) if v < 1e12 else int(v)
            except (ValueError, TypeError):
                return None
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
    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)
    delta_seconds = int((end_dt - start_dt).total_seconds())
    if delta_seconds <= 0:
        return 1
    return max(1, delta_seconds // target_points)


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
        target_points: 目标数据点数（默认 3600）
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
    # 1. 动态计算采样间隔
    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)
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

    # 3. 并行查询各角色趋势数据
    from app.services.data_source.factory import get_provider

    provider = get_provider()

    async def _fetch_role(role: str) -> tuple[str, list[dict]]:
        mapping = mappings.get(role)
        if not mapping or str(mapping.tag_id) not in tags_map:
            return role, []
        tag = tags_map[str(mapping.tag_id)]
        try:
            raw = await provider.query_trend_data(
                tag.tag_name,
                start_time,
                end_time,
                sample_interval=sample_interval,
            )
            logger.debug(
                "趋势查询角色数据: loop=%s, role=%s, tag=%s, 返回点数=%d",
                loop_id,
                role,
                tag.tag_name,
                len(raw),
            )
            return role, raw
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询 %s 趋势数据失败: %s", role, exc)
            return role, []

    results = await asyncio.gather(*[_fetch_role(r) for r in roles])
    role_data: dict[str, list[dict]] = dict(results)

    # 记录各角色返回点数
    role_point_counts = {r: len(role_data.get(r, [])) for r in roles}
    logger.info(
        "趋势查询并行完成: loop=%s, 各角色点数=%s",
        loop_id,
        role_point_counts,
    )

    # 4. 以 PV 的时间戳为基准对齐
    pv_data = role_data.get("PV", [])
    sp_data = role_data.get("SP", [])
    op_data = role_data.get("OP", [])
    mode_data = role_data.get("MODE", [])

    base_data = pv_data if pv_data else (sp_data if sp_data else op_data)
    if not base_data:
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

    # 构建 ts → value 映射
    pv_map = {d.get("ts"): d.get("value") for d in pv_data}
    sp_map = {d.get("ts"): d.get("value") for d in sp_data}
    op_map = {d.get("ts"): d.get("value") for d in op_data}
    mode_map = {d.get("ts"): d.get("value") for d in mode_data}
    pv_quality_map = {d.get("ts"): d.get("quality", "GOOD") for d in pv_data}

    timestamps: list[int] = []
    pv_list: list[float | None] = []
    sp_list: list[float | None] = []
    op_list: list[float | None] = []
    mode_list: list[float | None] = []
    pv_quality_list: list[str] = []

    for d in base_data:
        ts = d.get("ts")
        ts_millis = _ts_to_millis(ts)
        if ts_millis is None:
            continue
        timestamps.append(ts_millis)

        quality_label = _quality_to_label(pv_quality_map.get(ts, "GOOD"))
        pv_quality_list.append(quality_label)

        # PV 质量码为 BAD 时，pv 值为 null
        if quality_label == "BAD":
            pv_list.append(None)
        else:
            pv_list.append(pv_map.get(ts))

        sp_list.append(sp_map.get(ts))
        op_list.append(op_map.get(ts))
        mode_list.append(mode_map.get(ts))

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
        "PV" if pv_data else ("SP" if sp_data else "OP"),
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


__all__ = [
    "fetch_loop_trend",
    "compute_sample_interval",
    "DEFAULT_TARGET_POINTS",
]
