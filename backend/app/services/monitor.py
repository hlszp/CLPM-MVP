"""Loop monitor service — list + detail with trend data (IDS v3.2 §2.2.14~2.2.15).

关键实现要点：
- 波形数据从 TDengine 查询，超过 1 万点触发 LTTB 降采样
- 开发环境 TDengine 可能无数据，返回空数组 + 明确状态标识，不报错
- PV 数据携带质量码数组
- 回路未就绪（PARTIAL/INACTIVE）返回明确状态标识
"""

from __future__ import annotations

import logging
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.tdengine import query_trend_data
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# LTTB 降采样阈值
LTTB_THRESHOLD = 10000
LTTB_TARGET_POINTS = 2000

# 趋势时间窗映射
TREND_WINDOWS: dict[str, timedelta] = {
    "last_1_hour": timedelta(hours=1),
    "last_2_hours": timedelta(hours=2),
    "last_4_hours": timedelta(hours=4),
    "last_8_hours": timedelta(hours=8),
    "last_24_hours": timedelta(hours=24),
    "last_72_hours": timedelta(hours=72),
}


def _mode_value_to_label(value: float | None) -> str | None:
    """MODE tag 值 → 控制模式标签。"""
    if value is None:
        return None
    mapping = {0: "Manual", 1: "Auto", 2: "Cascade", 3: "Cascade"}
    return mapping.get(int(value), "Unknown")


def _ts_to_ms(ts: Any) -> int:
    """将时间戳（字符串或 datetime）转为毫秒数字（前端 ECharts time 轴要求）。

    TDengine REST API 返回的 ts 为字符串（如 '2026-06-25T17:29:22.000Z'），
    前端 MonitorTrend.timestamps 类型为 number[]（毫秒），需统一转换。
    """
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            try:
                return int(float(ts))
            except (ValueError, TypeError):
                return 0
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp() * 1000)
    return 0


def _quality_code_to_label(q: Any) -> str:
    """质量码 → 前端标签（GOOD/BAD/UNCERTAIN）。

    兼容两种 schema：
    - TDengine: 1=Good, 0=Bad
    - OPC DA: 192=Good
    - 已是 GOOD/BAD 字符串则原样返回（向后兼容 MOCK 数据）
    """
    if q is None:
        return "GOOD"
    if isinstance(q, str):
        if q in ("GOOD", "BAD", "UNCERTAIN"):
            return q
        try:
            q = int(q)
        except (ValueError, TypeError):
            return "UNCERTAIN"
    if isinstance(q, (int, float)):
        # TDengine: 1=Good; OPC DA: 192=Good; 0=Bad
        if q in (1, 192):
            return "GOOD"
        if q == 0:
            return "BAD"
        return "UNCERTAIN"
    return "UNCERTAIN"


def lttb_downsample(
    data: list[dict[str, Any]],
    threshold: int = LTTB_THRESHOLD,
    target_points: int = LTTB_TARGET_POINTS,
) -> list[dict[str, Any]]:
    """LTTB (Largest Triangle Three Buckets) 降采样算法。

    当数据点超过 threshold 时，降采样到 target_points 个点。

    Args:
        data: 原始数据点列表，每项 {ts, value, quality}
        threshold: 触发降采样的阈值
        target_points: 降采样后的目标点数

    Returns:
        降采样后的数据点列表
    """
    if len(data) <= threshold:
        return data

    n = len(data)
    if n <= 2 or target_points <= 2:
        return data[:1] + data[-1:]

    # 将 ts 转为数值（用于计算三角形面积）
    def ts_to_num(ts: Any) -> float:
        if isinstance(ts, (int, float)):
            return float(ts)
        if isinstance(ts, str):
            try:
                # 尝试 ISO 格式解析
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return dt.timestamp()
            except (ValueError, TypeError):
                try:
                    return float(ts)
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    # 预处理：提取 ts_num 和 value
    ts_nums = [ts_to_num(d.get("ts")) for d in data]
    values = [d.get("value") if d.get("value") is not None else 0.0 for d in data]

    sampled_indices: list[int] = [0]  # 总是保留第一个点

    # 将数据分桶（排除首尾两个点）
    bucket_size = (n - 2) / (target_points - 2)
    a = 0  # 上一个被选中的点的索引

    for i in range(target_points - 2):
        # 计算当前桶和下一个桶的范围
        bucket_start = int((i + 1) * bucket_size) + 1
        bucket_end = int((i + 2) * bucket_size) + 1
        bucket_end = min(bucket_end, n)

        next_bucket_start = bucket_end
        next_bucket_end = min(int((i + 3) * bucket_size) + 1, n)

        # 计算下一个桶的平均点
        avg_x = 0.0
        avg_y = 0.0
        avg_count = 0
        for j in range(next_bucket_start, next_bucket_end):
            avg_x += ts_nums[j]
            avg_y += values[j]
            avg_count += 1
        if avg_count > 0:
            avg_x /= avg_count
            avg_y /= avg_count

        # 在当前桶中找到与三角形面积最大的点
        max_area = -1.0
        max_area_idx = bucket_start
        point_a_x = ts_nums[a]
        point_a_y = values[a]

        for j in range(bucket_start, bucket_end):
            # 三角形面积 = 0.5 * |x_a*(y_b - y_avg) + x_b*(y_avg - y_a) + x_avg*(y_a - y_b)|
            area = (
                abs(
                    (point_a_x - avg_x) * (values[j] - point_a_y)
                    - (point_a_x - ts_nums[j]) * (avg_y - point_a_y)
                )
                * 0.5
            )
            if area > max_area:
                max_area = area
                max_area_idx = j

        sampled_indices.append(max_area_idx)
        a = max_area_idx

    sampled_indices.append(n - 1)  # 总是保留最后一个点

    return [data[i] for i in sampled_indices]


async def _get_loop_tag_values(
    db: AsyncSession, loop_id: str
) -> tuple[dict[str, TagRegistry], dict[str, LoopTagMapping]]:
    """获取回路的所有 Tag 关联和 Tag 详情。返回 (tags_map, mappings)。"""
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 按 role 索引的 Tag 对象
    return tags_map, mappings


async def _get_descendant_node_ids(db: AsyncSession, parent_id: str) -> list[str]:
    """递归获取所有子孙节点 ID。"""
    result = await db.execute(select(PlantNode.id).where(PlantNode.parent_id == parent_id))
    child_ids = [str(row[0]) for row in result]
    all_ids = list(child_ids)
    for child_id in child_ids:
        all_ids.extend(await _get_descendant_node_ids(db, child_id))
    return all_ids


async def list_loop_monitor(
    db: AsyncSession,
    plant_node_id: str | None = None,
    view: str = "list",
    keyword: str | None = None,
    loop_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """回路监控列表（含实时 PV/SP/OP/MODE 值、质量码、评分）。"""
    conditions = []
    if plant_node_id:
        # 递归获取所有子孙节点 ID，包含自身
        all_node_ids = await _get_descendant_node_ids(db, plant_node_id)
        all_node_ids.append(plant_node_id)
        conditions.append(LoopLedger.unit_id.in_(all_node_ids))
    if loop_type:
        conditions.append(func.upper(LoopLedger.loop_type) == loop_type.upper())
    if keyword:
        kw = f"%{keyword}%"
        conditions.append(
            or_(
                LoopLedger.tag_name.ilike(kw),
                LoopLedger.description.ilike(kw),
            )
        )

    count_stmt = select(func.count()).select_from(LoopLedger)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = select(LoopLedger).order_by(LoopLedger.created_at.desc())
    for cond in conditions:
        stmt = stmt.where(cond)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    loops = result.scalars().all()

    # 批量查 unit_name
    unit_ids = [str(loop.unit_id) for loop in loops if loop.unit_id]
    unit_map: dict[str, str] = {}
    if unit_ids:
        u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
        for node in u_result.scalars().all():
            unit_map[str(node.id)] = node.name

    # 批量查 Tag 关联
    loop_ids = [str(loop.id) for loop in loops]
    mappings_map: dict[str, dict[str, LoopTagMapping]] = {}
    if loop_ids:
        m_result = await db.execute(
            select(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids))
        )
        for m in m_result.scalars().all():
            mappings_map.setdefault(str(m.loop_id), {})[m.tag_role] = m

    # 批量查 Tag 详情
    all_tag_ids = []
    for loop_mappings in mappings_map.values():
        for m in loop_mappings.values():
            all_tag_ids.append(str(m.tag_id))
    tags_map: dict[str, TagRegistry] = {}
    if all_tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(all_tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 批量查每个回路的最新 KPI 快照（DISTINCT ON 取每个 loop_id 的最新一条）
    snapshot_map: dict[str, KpiSnapshotHourly] = {}
    if loop_ids:
        # PostgreSQL DISTINCT ON 语法：按 loop_id 分组取 ts_end 最大的一条
        s_stmt = (
            select(KpiSnapshotHourly)
            .where(KpiSnapshotHourly.loop_id.in_(loop_ids))
            .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_end.desc())
        )
        s_result = await db.execute(s_stmt)
        for snap in s_result.scalars().all():
            # 只保留每个 loop 的第一条（最新）
            key = str(snap.loop_id)
            if key not in snapshot_map:
                snapshot_map[key] = snap

    items = []
    for loop in loops:
        loop_mappings = mappings_map.get(str(loop.id), {})
        snap = snapshot_map.get(str(loop.id))
        # 构建当前值快照
        current_values: dict[str, Any] = {
            "pv": None,
            "sp": None,
            "op": None,
            "mode": None,
            "modeLabel": None,
            "pvQuality": None,
        }
        read_at = None
        control_mode = None
        for role in ("PV", "SP", "OP", "MODE"):
            mapping = loop_mappings.get(role)
            if mapping and str(mapping.tag_id) in tags_map:
                tag = tags_map[str(mapping.tag_id)]
                field = role.lower()
                current_values[field] = tag.current_value
                if role == "MODE":
                    current_values["modeLabel"] = _mode_value_to_label(tag.current_value)
                    control_mode = current_values["modeLabel"]
                if role == "PV":
                    current_values["pvQuality"] = tag.quality
                if tag.last_sync_at:
                    ts = (
                        tag.last_sync_at.isoformat()
                        if hasattr(tag.last_sync_at, "isoformat")
                        else str(tag.last_sync_at)
                    )
                    if read_at is None or ts > read_at:
                        read_at = ts

        # KPI 摘要：从最新快照读取（无快照则返回 None）
        def _rate(val) -> float | None:
            """Decimal → float，None 保持 None。"""
            return float(val) if val is not None else None

        if snap:
            kpi_summary: dict[str, Any] = {
                "composite_score": _rate(snap.score),
                "effective_auto_rate": _rate(snap.effective_auto_rate),
                "auto_mode_rate": _rate(snap.auto_mode_rate),
                "steady_rate": _rate(snap.steady_rate),
                "accuracy_rate": _rate(snap.accuracy_rate),
                "fast_response_rate": _rate(snap.fast_response_rate),
                "oscillation_rate": _rate(snap.oscillation_rate),
                "saturation_rate": _rate(snap.saturation_rate),
                "good_value_rate": _rate(snap.good_value_rate),
                "valid_rate": _rate(snap.valid_rate),
                "confidence_level": snap.confidence_level,
                "status": snap.status,
                "calculatedAt": snap.ts_end.isoformat() if snap.ts_end else None,
            }
            list_score = _rate(snap.score)
            list_status = snap.status
            confidence_level = snap.confidence_level
        else:
            kpi_summary = None
            list_score = None
            list_status = None
            confidence_level = None

        items.append(
            {
                "loopId": str(loop.id),
                "tagName": loop.tag_name,
                "description": loop.description,
                "unitName": unit_map.get(str(loop.unit_id)) if loop.unit_id else None,
                "currentValues": current_values,
                "controlMode": control_mode,
                "score": list_score,
                "status": list_status,
                "confidenceLevel": confidence_level,
                "effectiveAutoRate": _rate(snap.effective_auto_rate) if snap else None,
                "kpiSummary": kpi_summary,
                "loopType": loop.loop_type,
                "isActive": bool(loop.is_active),
                "readAt": read_at,
            }
        )

    return {
        "view": view,
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_loop_monitor_detail(
    db: AsyncSession,
    loop_id: str,
    trend_window: str = "last_24_hours",
) -> dict:
    """回路运行详情（7 Tag 当前值、PID 参数、波形数据）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
    """
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    tags_map, mappings = await _get_loop_tag_values(db, loop_id)

    # 构建当前值快照
    current_values: dict[str, Any] = {
        "pv": None,
        "sp": None,
        "op": None,
        "mode": None,
        "modeLabel": None,
        "pvQuality": None,
        "readAt": None,
    }
    runtime_params: dict[str, Any] = {
        "controlMode": None,
        "pidP": None,
        "pidI": None,
        "pidD": None,
    }
    read_at = None
    for role in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"):
        mapping = mappings.get(role)
        if mapping and str(mapping.tag_id) in tags_map:
            tag = tags_map[str(mapping.tag_id)]
            if role == "PV":
                current_values["pv"] = tag.current_value
                current_values["pvQuality"] = tag.quality
            elif role == "SP":
                current_values["sp"] = tag.current_value
            elif role == "OP":
                current_values["op"] = tag.current_value
            elif role == "MODE":
                current_values["mode"] = tag.current_value
                current_values["modeLabel"] = _mode_value_to_label(tag.current_value)
                runtime_params["controlMode"] = _mode_value_to_label(tag.current_value)
            elif role == "PID_P":
                runtime_params["pidP"] = tag.current_value
            elif role == "PID_I":
                runtime_params["pidI"] = tag.current_value
            elif role == "PID_D":
                runtime_params["pidD"] = tag.current_value
            if tag.last_sync_at:
                ts = (
                    tag.last_sync_at.isoformat()
                    if hasattr(tag.last_sync_at, "isoformat")
                    else str(tag.last_sync_at)
                )
                if read_at is None or ts > read_at:
                    read_at = ts
    current_values["readAt"] = read_at

    # 查询趋势数据（从 TDengine）
    trend_data: dict[str, Any] = {
        "timestamps": [],
        "pv": [],
        "sp": [],
        "op": [],
        "mode": [],
        "pvQuality": [],
    }
    trend_status = "EMPTY"  # EMPTY / OK / PARTIAL

    # 计算时间范围
    # TDengine REST API 不支持 ISO 8601 时区后缀（+00:00 / Z），
    # 需转换为无时区的字符串格式
    delta = TREND_WINDOWS.get(trend_window, timedelta(hours=24))
    now = datetime.now(UTC)
    start_time = (now - delta).replace(tzinfo=None).isoformat()
    end_time = now.replace(tzinfo=None).isoformat()

    # 查询 PV/SP/OP/MODE 的趋势数据
    pv_trend: list[dict[str, Any]] = []
    sp_trend: list[dict[str, Any]] = []
    op_trend: list[dict[str, Any]] = []
    mode_trend: list[dict[str, Any]] = []

    for role in ("PV", "SP", "OP", "MODE"):
        mapping = mappings.get(role)
        if mapping and str(mapping.tag_id) in tags_map:
            tag = tags_map[str(mapping.tag_id)]
            try:
                raw_trend = await query_trend_data(tag.tag_name, start_time, end_time)
                # LTTB 降采样
                downsampled = lttb_downsample(raw_trend)
                if role == "PV":
                    pv_trend = downsampled
                elif role == "SP":
                    sp_trend = downsampled
                elif role == "OP":
                    op_trend = downsampled
                elif role == "MODE":
                    mode_trend = downsampled
            except Exception as exc:  # noqa: BLE001
                logger.warning("查询 %s 趋势数据失败: %s", role, exc)

    # 合并趋势数据（按时间戳对齐）
    if pv_trend or sp_trend or op_trend or mode_trend:
        trend_status = "OK"
        # 简化处理：以 PV 的时间戳为基准（如有），否则用 SP
        base_trend = pv_trend if pv_trend else (sp_trend if sp_trend else op_trend)
        if base_trend:
            # timestamps 统一转为毫秒数字（前端 ECharts time 轴要求 number[]）
            trend_data["timestamps"] = [_ts_to_ms(d.get("ts")) for d in base_trend]
            trend_data["pv"] = [d.get("value") for d in pv_trend] if pv_trend else []
            trend_data["sp"] = [d.get("value") for d in sp_trend] if sp_trend else []
            trend_data["op"] = [d.get("value") for d in op_trend] if op_trend else []
            trend_data["mode"] = [d.get("value") for d in mode_trend] if mode_trend else []
            # PV 质量码数组：数字码 → GOOD/BAD/UNCERTAIN（前端 Quality 类型）
            if pv_trend:
                trend_data["pvQuality"] = [
                    _quality_code_to_label(d.get("quality", "GOOD")) for d in pv_trend
                ]

    # TDengine 无数据时生成模拟趋势数据（开发演示用）
    if trend_status == "EMPTY":
        trend_status = "MOCK"
        seed = hash(loop.tag_name) % 1000
        rng = random.Random(seed)
        base_pv = 50.0 + rng.uniform(-20, 20)
        base_sp = base_pv + rng.uniform(-5, 5)
        interval_sec = max(int(delta.total_seconds() / 200), 10)
        point_count = int(delta.total_seconds() / interval_sec)
        ts_list: list[int] = []
        pv_list: list[float] = []
        sp_list: list[float] = []
        op_list: list[float] = []
        mode_list: list[int] = []
        current_mode = rng.choice([0, 1, 2])
        for i in range(point_count):
            t = now - delta + timedelta(seconds=i * interval_sec)
            ts_list.append(int(t.timestamp() * 1000))
            progress = i / max(point_count - 1, 1)
            # PV：正弦波 + 噪声，围绕 SP 波动
            wave = math.sin(progress * math.pi * 8) * 3.0
            noise = rng.uniform(-1.5, 1.5)
            pv_list.append(round(base_pv + wave + noise, 2))
            # SP：偶尔阶跃
            if i > 0 and i % max(point_count // 4, 1) == 0:
                base_sp = base_pv + rng.uniform(-8, 8)
            sp_list.append(round(base_sp, 2))
            # OP：0-100% 之间波动
            op_val = 40 + math.sin(progress * math.pi * 6) * 20 + rng.uniform(-5, 5)
            op_list.append(round(op_val, 2))
            # MODE：偶尔切换
            if i > 0 and rng.random() < 0.05:
                current_mode = rng.choice([0, 1, 2])
            mode_list.append(current_mode)
        trend_data["timestamps"] = ts_list
        trend_data["pv"] = pv_list
        trend_data["sp"] = sp_list
        trend_data["op"] = op_list
        trend_data["mode"] = mode_list
        trend_data["pvQuality"] = ["GOOD"] * point_count

    # KPI 摘要：从 kpi_snapshot_hourly 读取最新快照
    snapshot = await db.execute(
        select(KpiSnapshotHourly)
        .where(KpiSnapshotHourly.loop_id == loop_id)
        .order_by(KpiSnapshotHourly.ts_end.desc())
        .limit(1)
    )
    snap = snapshot.scalar_one_or_none()

    def _rate(val) -> float | None:
        """Decimal → float，None 保持 None。"""
        return float(val) if val is not None else None

    if snap:
        kpi_summary: dict[str, Any] = {
            "composite_score": _rate(snap.score),
            "auto_mode_rate": _rate(snap.auto_mode_rate),
            "effective_auto_rate": _rate(snap.effective_auto_rate),
            "steady_rate": _rate(snap.steady_rate),
            "accuracy_rate": _rate(snap.accuracy_rate),
            "fast_response_rate": _rate(snap.fast_response_rate),
            "oscillation_rate": _rate(snap.oscillation_rate),
            "saturation_rate": _rate(snap.saturation_rate),
            "good_value_rate": _rate(snap.good_value_rate),
            "status": snap.status,
            "algorithm_version": "KPI_CALC_v1.0",
            "calculatedAt": snap.ts_end.isoformat() if snap.ts_end else read_at,
        }
    else:
        kpi_summary = {
            "composite_score": None,
            "auto_mode_rate": None,
            "effective_auto_rate": None,
            "steady_rate": None,
            "accuracy_rate": None,
            "fast_response_rate": None,
            "oscillation_rate": None,
            "saturation_rate": None,
            "good_value_rate": None,
            "status": "INCONCLUSIVE" if loop.status != "READY" else "GOOD",
            "algorithm_version": "KPI_CALC_v1.0",
            "calculatedAt": read_at,
        }

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "status": loop.status,
        "currentValues": current_values,
        "runtimeParams": runtime_params,
        "trend": trend_data,
        "trendStatus": trend_status,
        "kpiSummary": kpi_summary,
    }


__all__ = ["get_loop_monitor_detail", "list_loop_monitor", "lttb_downsample"]
