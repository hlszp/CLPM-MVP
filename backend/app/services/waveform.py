"""Waveform query service (IDS v3.2 §2.4.5 — S4-DIAG-004).

业务逻辑：
- 从 TDengine 拉取波形数据（PV/SP/OP/MODE）
- PV 质量码为 Bad 时，pv 值设为 null
- 超过 maxPoints 触发 LTTB 降采样
- 时间窗超过 30 天返回 ERR_TS_001
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.tdengine import query_trend_data
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# 最大时间窗（30 天）
MAX_TIME_WINDOW_DAYS = 30

# 默认降采样阈值
DEFAULT_MAX_POINTS = 5000


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


def _ts_to_millis(ts: Any) -> int | None:
    """将时间戳转为毫秒整数。"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # 如果是秒级，转为毫秒
        v = float(ts)
        if v < 1e12:
            return int(v * 1000)
        return int(v)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            try:
                v = float(ts)
                if v < 1e12:
                    return int(v * 1000)
                return int(v)
            except (ValueError, TypeError):
                return None
    return None


async def get_waveform(
    db: AsyncSession,
    loop_id: str,
    *,
    start_time: str,
    end_time: str,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict:
    """波形数据查询。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TS_001
    """
    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 解析时间
    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)

    # 校验时间窗不超过 30 天
    if end_dt - start_dt > timedelta(days=MAX_TIME_WINDOW_DAYS):
        raise BizError(
            code="ERR_TS_001",
            message=f"时间窗不能超过 {MAX_TIME_WINDOW_DAYS} 天",
            status_code=400,
        )

    # TDengine 存储的时间带 Z 后缀（ISO 8601 UTC），查询时需保持一致
    td_start = start_time
    td_end = end_time

    # 查询回路 Tag 关联
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 查询各角色的趋势数据（S3-A5: 并行查询 4 个 Tag）
    async def _fetch_role(role: str) -> tuple[str, list[dict]]:
        mapping = mappings.get(role)
        if not mapping or str(mapping.tag_id) not in tags_map:
            return role, []
        tag = tags_map[str(mapping.tag_id)]
        try:
            raw = await query_trend_data(tag.tag_name, td_start, td_end)
            return role, raw
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询 %s 趋势数据失败: %s", role, exc)
            return role, []

    results = await asyncio.gather(*[_fetch_role(r) for r in ("PV", "SP", "OP", "MODE")])
    role_data: dict[str, list[dict]] = dict(results)

    # 以 PV 的时间戳为基准对齐
    pv_data = role_data.get("PV", [])
    sp_data = role_data.get("SP", [])
    op_data = role_data.get("OP", [])
    mode_data = role_data.get("MODE", [])

    base_data = pv_data if pv_data else (sp_data if sp_data else op_data)
    if not base_data:
        return {
            "loopId": loop_id,
            "tagName": loop.tag_name,
            "timeRange": {"startTime": start_time, "endTime": end_time},
            "timestamps": [],
            "pv": [],
            "sp": [],
            "op": [],
            "mode": [],
            "pvQuality": [],
            "downsampled": False,
            "pointCount": 0,
        }

    # 构建 ts → value 映射
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

    pv_map = {d.get("ts"): d.get("value") for d in pv_data}

    for d in base_data:
        ts = d.get("ts")
        ts_millis = _ts_to_millis(ts)
        if ts_millis is None:
            continue
        timestamps.append(ts_millis)

        quality = pv_quality_map.get(ts, "GOOD")
        quality_norm = str(quality).upper() if quality else "GOOD"
        pv_quality_list.append(_quality_normalize(quality_norm))

        # PV 质量码为 Bad 时，pv 值为 null
        if quality_norm == "BAD":
            pv_list.append(None)
        else:
            pv_value = pv_map.get(ts)
            pv_list.append(pv_value)

        sp_list.append(sp_map.get(ts))
        op_list.append(op_map.get(ts))
        mode_list.append(mode_map.get(ts))

    # LTTB 降采样
    downsampled = False
    if len(timestamps) > max_points:
        series_map = {
            "pv": pv_list,
            "sp": sp_list,
            "op": op_list,
            "mode": mode_list,
            "pvQuality": pv_quality_list,
        }
        timestamps, series_map = lttb_downsample_multi_series(timestamps, series_map, max_points)
        pv_list = series_map["pv"]
        sp_list = series_map["sp"]
        op_list = series_map["op"]
        mode_list = series_map["mode"]
        pv_quality_list = series_map["pvQuality"]
        downsampled = True

    return {
        "loopId": loop_id,
        "tagName": loop.tag_name,
        "timeRange": {"startTime": start_time, "endTime": end_time},
        "timestamps": timestamps,
        "pv": pv_list,
        "sp": sp_list,
        "op": op_list,
        "mode": mode_list,
        "pvQuality": pv_quality_list,
        "downsampled": downsampled,
        "pointCount": len(timestamps),
    }


def _quality_normalize(quality: str) -> str:
    """质量码归一化：Good/Bad/Unknown。"""
    q = quality.upper()
    if q == "GOOD":
        return "Good"
    if q == "BAD":
        return "Bad"
    if q == "UNCERTAIN":
        return "Unknown"
    return "Good" if q == "GOOD" else "Unknown"


def _parse_iso_datetime(s: str) -> datetime:
    """解析 ISO 8601 时间字符串。"""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromisoformat(s)


__all__ = ["get_waveform", "lttb_downsample_multi_series"]
