"""Loop monitor service — list + detail with trend data (IDS v3.2 §2.2.14~2.2.15).

关键实现要点：
- 波形数据从 TDengine 查询，超过 1 万点触发 LTTB 降采样
- 开发环境 TDengine 可能无数据，返回空数组 + 明确状态标识，不报错
- PV 数据携带质量码数组
- 回路未就绪（PARTIAL/INACTIVE）返回明确状态标识
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly, LoopIntegritySnapshot
from app.models.plant_node import PlantNode
from app.models.tag import TagRegistry
from app.services.confidence_evaluator import ALGORITHM_VERSION
from app.services.data_source.realtime_subscriber import get_subscriber

logger = logging.getLogger(__name__)

# LTTB 降采样阈值（P3 #56：仅用于波形展示路径，KPI 计算路径不降采样）
# 触发降采样阈值：超过 10000 点触发 LTTB 算法
LTTB_THRESHOLD = 10000
# 降采样目标点数：对齐 AGENTS.md §性能边界 maxPoints=2000（波形渲染优化）
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


# 默认 MODE 值 → 控制模式映射（向后兼容，无 loop_mode_mapping 配置时使用）
# 与 node_performance.py 的 DEFAULT_AUTO_MODES={1,2,3} 语义一致
_DEFAULT_MODE_LABELS: dict[int, str] = {
    0: "Manual",
    1: "Auto",
    2: "Cascade",
    3: "Cascade",
}

# 数据库 mode_label（LoopModeMapping.mode_label，全大写）→ 前端 ControlMode（首字母大写）转换
# 前端 ControlMode 类型仅 'Auto' | 'Cascade' | 'Manual'，REMOTE/APC 归并为 Auto
_DB_MODE_LABEL_TO_FRONTEND: dict[str, str] = {
    "AUTO": "Auto",
    "CAS": "Cascade",
    "REMOTE": "Auto",  # 远程控制归并为 Auto（非手动）
    "APC": "Auto",  # 先进控制归并为 Auto（非手动）
    "MANUAL": "Manual",
}


def _mode_value_to_label(
    value: float | None,
    mapping: dict[int, str] | None = None,
) -> str | None:
    """MODE tag 值 → 控制模式标签。

    优先使用用户在 ``loop_mode_mapping`` 表中配置的映射（PRD §5.1.3 / FDS §5.3.1），
    无配置时回退到默认硬编码映射（向后兼容）。

    Args:
        value: MODE tag 当前值（int/float/None）
        mapping: 该回路的 MODE 值映射 ``{mode_value: frontend_label}``，
            由 ``_load_mode_mappings`` 预查并转换。None 时使用默认映射。

    Returns:
        控制模式标签（"Auto"/"Cascade"/"Manual"/"Unknown"），None 输入返回 None
    """
    if value is None:
        return None
    active_mapping = mapping if mapping is not None else _DEFAULT_MODE_LABELS
    return active_mapping.get(int(value), "Unknown")


async def _load_mode_mappings(db: AsyncSession, loop_ids: list[str]) -> dict[str, dict[int, str]]:
    """批量查询多个回路的 MODE 值映射配置。

    从 ``loop_mode_mapping`` 表读取每个回路的 (mode_value, mode_label) 配置，
    转换为前端 ControlMode 格式 ``{loop_id: {mode_value: frontend_label}}``。

    无配置的回路不在返回字典中（调用方回退到默认映射）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表

    Returns:
        ``{loop_id: {mode_value: frontend_label}}`` 字典
    """
    if not loop_ids:
        return {}
    from app.models.loop_config import LoopModeMapping

    result = await db.execute(
        select(
            LoopModeMapping.loop_id, LoopModeMapping.mode_value, LoopModeMapping.mode_label
        ).where(LoopModeMapping.loop_id.in_(loop_ids))
    )
    mappings: dict[str, dict[int, str]] = {}
    for row in result:
        loop_id = str(row.loop_id)
        frontend_label = _DB_MODE_LABEL_TO_FRONTEND.get(str(row.mode_label).upper(), "Unknown")
        mappings.setdefault(loop_id, {})[int(row.mode_value)] = frontend_label
    return mappings


def _ts_to_ms(ts: Any) -> int:
    """将时间戳（字符串或 datetime）转为毫秒数字（前端 ECharts time 轴要求）。

    TDengine REST API 返回的 ts 为字符串（如 '2026-06-25T17:29:22.000Z'），
    前端 MonitorTrend.timestamps 类型为 number[]（毫秒），需统一转换。
    naive（无时区）输入按 UTC 处理（补 Z 口径）：返回前端的毫秒时间戳
    与后端部署时区无关，避免 naive .timestamp() 被解释为本地墙钟。
    """
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            try:
                return int(float(ts))
            except (ValueError, TypeError):
                return 0
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return int(ts.timestamp() * 1000)
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp() * 1000)
    return 0


def _quality_code_to_label(q: Any) -> str:
    """质量码 → 前端标签（GOOD/BAD/UNCERTAIN）。

    Phase 10 UX 包：与 ``preprocessing/quality_code.py`` 的 ``_GOOD_CODES={1,2,3,192}``
    统一口径，修复"REST 路径把 2 当 UNCERTAIN、WS 路径把 2 当 UNCERTAIN"的语义冲突。

    兼容多种 schema：
    - TDengine: 1=Good, 0=Bad
    - OPC UA: 2=Good, 3=Good_Cascaded, 0=Bad
    - OPC DA: 192=Good
    - 已是 GOOD/BAD/UNCERTAIN 字符串则原样返回（向后兼容 MOCK 数据）
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
        # 与 preprocessing/quality_code.py 的 _GOOD_CODES={1,2,3,192} 对齐
        if q in (1, 2, 3, 192):
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
    """递归获取所有子孙节点 ID。

    Phase 10 性能优化：原 N 次 select 递归收敛为 1 次 ``WITH RECURSIVE`` CTE
    （``plant_node_tree.collect_descendant_node_ids``）。保留薄包装以维持公共 API，
    ``performance.py`` 通过此名间接复用。
    """
    from app.services.plant_node_tree import collect_descendant_node_ids

    return await collect_descendant_node_ids(db, parent_id)


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
    # 与统计卡片口径统一：仅统计/展示 is_active=True 的回路（WS-D 阶段5）
    conditions.append(LoopLedger.is_active.is_(True))
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
        # PostgreSQL DISTINCT ON：按 loop_id 取 ts_end 最大的一条
        # Phase 10 性能优化：原"ORDER BY + Python 层 if-not-in 取首条"会拉回全部行，
        # 现用真正 DISTINCT ON 让 PG 在数据库层直接去重，减少回传行数。
        s_stmt = (
            select(KpiSnapshotHourly)
            .where(KpiSnapshotHourly.loop_id.in_(loop_ids))
            .distinct(KpiSnapshotHourly.loop_id)
            .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_end.desc())
        )
        s_result = await db.execute(s_stmt)
        for snap in s_result.scalars().all():
            snapshot_map[str(snap.loop_id)] = snap

    # 批量查每个回路的 MODE 值映射配置（loop_mode_mapping 表）
    # 无配置的回路回退到默认映射（在 _mode_value_to_label 内处理）
    mode_mapping_map: dict[str, dict[int, str]] = (
        await _load_mode_mappings(db, loop_ids) if loop_ids else {}
    )

    # 批量查每个回路最新一次数据完整性巡检快照（DISTINCT ON 取每回路最新 check_date）
    # 用于列表页展示 PV 完整度，避免列表实时查 TDengine（27 回路 × 7 列 COUNT 需 ~3s）
    integrity_map: dict[str, LoopIntegritySnapshot] = {}
    if loop_ids:
        i_stmt = (
            select(LoopIntegritySnapshot)
            .where(LoopIntegritySnapshot.loop_id.in_(loop_ids))
            .distinct(LoopIntegritySnapshot.loop_id)
            .order_by(LoopIntegritySnapshot.loop_id, LoopIntegritySnapshot.check_date.desc())
        )
        i_result = await db.execute(i_stmt)
        for snap in i_result.scalars().all():
            integrity_map[str(snap.loop_id)] = snap

    # 批量从 Redis 读取实时值，优先于 PostgreSQL current_value
    redis_cache: dict[str, dict] = {}
    try:
        subscriber = get_subscriber()
        all_tag_names = [tag.tag_name for tag in tags_map.values() if tag.tag_name]
        if all_tag_names:
            cached_list = await subscriber.get_cached_values(all_tag_names)
            for item in cached_list:
                tc = item.get("tagCode")
                if tc:
                    redis_cache[tc] = item
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 Redis 读取实时值失败，回退到数据库值: %s", exc)

    items = []
    for loop in loops:
        loop_mappings = mappings_map.get(str(loop.id), {})
        snap = snapshot_map.get(str(loop.id))
        integrity = integrity_map.get(str(loop.id))
        # 构建当前值快照
        current_values: dict[str, Any] = {
            "pv": None,
            "sp": None,
            "op": None,
            "mode": None,
            "modeLabel": None,
            "pvQuality": None,
            "unit": None,
        }
        read_at = None
        control_mode = None
        for role in ("PV", "SP", "OP", "MODE"):
            mapping = loop_mappings.get(role)
            if mapping and str(mapping.tag_id) in tags_map:
                tag = tags_map[str(mapping.tag_id)]
                field = role.lower()
                # 优先从 Redis 实时缓存读取
                cached = redis_cache.get(tag.tag_name)
                if cached:
                    try:
                        current_values[field] = float(cached.get("value"))
                    except (TypeError, ValueError):
                        current_values[field] = tag.current_value
                    if role == "PV":
                        current_values["pvQuality"] = _quality_code_to_label(
                            cached.get("quality", tag.quality)
                        )
                    if cached.get("collectTime"):
                        read_at = cached["collectTime"]
                else:
                    current_values[field] = tag.current_value
                    if role == "PV":
                        current_values["pvQuality"] = _quality_code_to_label(tag.quality)
                if role == "MODE":
                    mode_val = current_values["mode"]
                    loop_mode_mapping = mode_mapping_map.get(str(loop.id))
                    current_values["modeLabel"] = _mode_value_to_label(mode_val, loop_mode_mapping)
                    control_mode = current_values["modeLabel"]
                if not cached and tag.last_sync_at:
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
                "fast_rate": _rate(snap.fast_rate),
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

        # v6.1：补充 PV/OP 量程与工程单位（从关联 Tag 引用，不冗余存储）
        pv_range_info: dict[str, float | None] | None = None
        pv_unit_val: str | None = None
        op_range_info: dict[str, float | None] | None = None
        op_unit_val: str | None = None
        loop_mappings = mappings_map.get(str(loop.id), {})
        for role_key, m in loop_mappings.items():
            tag = tags_map.get(str(m.tag_id))
            if not tag:
                continue
            if role_key == "PV":
                pv_range_info = {
                    "min": float(tag.range_min) if tag.range_min is not None else None,
                    "max": float(tag.range_max) if tag.range_max is not None else None,
                }
                pv_unit_val = tag.unit
            elif role_key == "OP":
                op_range_info = {
                    "min": float(tag.range_min) if tag.range_min is not None else None,
                    "max": float(tag.range_max) if tag.range_max is not None else None,
                }
                op_unit_val = tag.unit
        # WS-D 阶段5：currentValues.unit 派生自 PV Tag 工程单位（PV/SP 共享）
        if pv_unit_val is not None:
            current_values["unit"] = pv_unit_val

        items.append(
            {
                "loopId": str(loop.id),
                "tagName": loop.tag_name,
                "description": loop.description,
                "unitName": unit_map.get(str(loop.unit_id)) if loop.unit_id else None,
                "pvRange": pv_range_info,
                "pvUnit": pv_unit_val,
                "opRange": op_range_info,
                "opUnit": op_unit_val,
                "currentValues": current_values,
                "controlMode": control_mode,
                "score": list_score,
                # WS-D 阶段5：status 拆为 loopStatus（回路配置态）+ kpiStatus（评估态）
                # 避免前端 LoopStatus.PARTIAL 与 KpiStatus.PARTIAL 撞名无法区分
                "loopStatus": loop.status,
                "kpiStatus": list_status,
                "confidenceLevel": confidence_level,
                "effectiveAutoRate": _rate(snap.effective_auto_rate) if snap else None,
                "kpiSummary": kpi_summary,
                # 数据健康度（方案 A §5）：预处理 validRate + 可信度 + 完整度
                # 三者均来自最新 KPI 快照/每日巡检快照，列表页不实时查 TDengine
                "dataHealth": {
                    # 预处理：好值率/有效率（来自预处理管道 + ConfidenceEvaluator）
                    "validRate": _rate(snap.valid_rate) if snap else None,
                    # 回路可信度：A/B/C/D/E 等级（来自 ConfidenceEvaluator）
                    "confidenceLevel": confidence_level,
                    # 数据完整性：PV 完整度（来自每日 02:00 巡检快照）
                    "pvCompleteness": integrity.pv_completeness if integrity else None,
                    "overallCompleteness": integrity.overall_completeness if integrity else None,
                    "integrityStatus": integrity.status if integrity else None,
                    "missingColumns": integrity.missing_columns if integrity else None,
                    "lastIntegrityCheck": (
                        integrity.check_date.isoformat()
                        if integrity and integrity.check_date
                        else None
                    ),
                },
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
        BizError: ERR_LOOP_NOT_FOUND / ERR_VALIDATION（非法 trendWindow）
    """
    # WS-D 阶段5：非法 trendWindow 返回 400（原先静默回退到 24h）
    if trend_window not in TREND_WINDOWS:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的趋势时间窗: {trend_window}，支持: {', '.join(sorted(TREND_WINDOWS))}",
            status_code=400,
        )
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    tags_map, mappings = await _get_loop_tag_values(db, loop_id)

    # 查询该回路的 MODE 值映射配置（loop_mode_mapping 表）
    # 无配置时回退到默认映射（在 _mode_value_to_label 内处理）
    mode_mapping_dict = await _load_mode_mappings(db, [loop_id])
    loop_mode_mapping = mode_mapping_dict.get(loop_id)

    # 批量从 Redis 读取实时值，优先于 PostgreSQL current_value
    redis_cache: dict[str, dict] = {}
    try:
        all_tag_names = [tag.tag_name for tag in tags_map.values() if tag.tag_name]
        if all_tag_names:
            cached_list = await get_subscriber().get_cached_values(all_tag_names)
            for item in cached_list:
                tc = item.get("tagCode")
                if tc:
                    redis_cache[tc] = item
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 Redis 读取实时值失败，回退到数据库值: %s", exc)

    # 构建当前值快照
    current_values: dict[str, Any] = {
        "pv": None,
        "sp": None,
        "op": None,
        "mode": None,
        "modeLabel": None,
        "pvQuality": None,
        "unit": None,
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
            # WS-D 阶段5：PV Tag 工程单位派生 currentValues.unit（PV/SP 共享）
            if role == "PV" and tag.unit:
                current_values["unit"] = tag.unit
            # 优先从 Redis 实时缓存读取
            cached = redis_cache.get(tag.tag_name)
            if cached:
                try:
                    if role in ("PV", "SP", "OP", "MODE"):
                        current_values[role.lower()] = float(cached.get("value"))
                    elif role in ("PID_P", "PID_I", "PID_D"):
                        runtime_params[role.lower()] = float(cached.get("value"))
                except (TypeError, ValueError):
                    if role in ("PV", "SP", "OP", "MODE"):
                        current_values[role.lower()] = tag.current_value
                    elif role in ("PID_P", "PID_I", "PID_D"):
                        runtime_params[role.lower()] = tag.current_value
                if role == "PV":
                    current_values["pvQuality"] = _quality_code_to_label(
                        cached.get("quality", tag.quality)
                    )
                if role == "MODE":
                    mode_val = current_values["mode"]
                    current_values["modeLabel"] = _mode_value_to_label(mode_val, loop_mode_mapping)
                    runtime_params["controlMode"] = current_values["modeLabel"]
                if cached.get("collectTime"):
                    if read_at is None or cached["collectTime"] > read_at:
                        read_at = cached["collectTime"]
            else:
                if role == "PV":
                    current_values["pv"] = tag.current_value
                    current_values["pvQuality"] = tag.quality
                elif role == "SP":
                    current_values["sp"] = tag.current_value
                elif role == "OP":
                    current_values["op"] = tag.current_value
                elif role == "MODE":
                    current_values["mode"] = tag.current_value
                    current_values["modeLabel"] = _mode_value_to_label(
                        tag.current_value, loop_mode_mapping
                    )
                    runtime_params["controlMode"] = _mode_value_to_label(
                        tag.current_value, loop_mode_mapping
                    )
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

    # 查询趋势数据（并行 + 动态采样间隔，封装在 trend_service 中复用）
    trend_data: dict[str, Any] = {
        "timestamps": [],
        "pv": [],
        "sp": [],
        "op": [],
        "mode": [],
        "pvQuality": [],
        "sampleInterval": None,
        "downsampled": False,
    }
    trend_status = "EMPTY"  # EMPTY / OK / PARTIAL

    # 计算时间范围
    delta = TREND_WINDOWS.get(trend_window, timedelta(hours=24))
    now = datetime.now(UTC)
    start_dt = now - delta
    start_time = start_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{start_dt.microsecond // 1000:03d}Z"
    end_time = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    # 调用封装的趋势查询服务（并行查询 4 个 tag + 动态采样间隔）
    # 传入已加载的 tags_map / mappings，避免重复查询数据库
    from app.services.trend_service import fetch_loop_trend

    trend_result = await fetch_loop_trend(
        db,
        loop_id,
        start_time,
        end_time,
        target_points=3600,
        tags_map=tags_map,
        mappings=mappings,
    )

    if trend_result["pointCount"] > 0:
        trend_status = "OK"
        trend_data["timestamps"] = trend_result["timestamps"]
        trend_data["pv"] = trend_result["pv"]
        trend_data["sp"] = trend_result["sp"]
        trend_data["op"] = trend_result["op"]
        trend_data["mode"] = trend_result["mode"]
        trend_data["pvQuality"] = trend_result["pvQuality"]
        trend_data["sampleInterval"] = trend_result["sampleInterval"]
        trend_data["downsampled"] = trend_result["downsampled"]

    # TDengine 无数据时保持 EMPTY 状态，返回空数组（不再生成模拟数据）
    # 仿真脚本已持续向 TDengine 推送实时数据，趋势图直接展示真实历史数据

    # KPI 摘要：按 trend_window 时间范围聚合小时快照
    # last_1_hour → 取最新 1 条快照；last_N_hours → 聚合 N 小时内所有快照
    kpi_start = (now - delta).replace(tzinfo=None)
    snapshot = await db.execute(
        select(KpiSnapshotHourly)
        .where(
            KpiSnapshotHourly.loop_id == loop_id,
            KpiSnapshotHourly.ts_start >= kpi_start,
        )
        .order_by(KpiSnapshotHourly.ts_start.desc())
    )
    snaps = snapshot.scalars().all()

    if snaps:
        kpi_summary = _aggregate_kpi_snapshots(snaps, read_at)
    else:
        kpi_summary = {
            "composite_score": None,
            "auto_mode_rate": None,
            "effective_auto_rate": None,
            "steady_rate": None,
            "accuracy_rate": None,
            "fast_rate": None,
            "oscillation_rate": None,
            "saturation_rate": None,
            "good_value_rate": None,
            # WS-D 阶段5：无快照时 KPI 状态恒为 INCONCLUSIVE（对齐评估口径，
            # 原先 loop.status==READY 时返回 GOOD 是非法 KpiStatus 枚举值）
            "status": "INCONCLUSIVE",
            "algorithm_version": ALGORITHM_VERSION,
            "calculatedAt": read_at,
            # 可信度统一 Phase 2：回路级可信度（无快照时为 None）
            "confidence_level": None,
        }

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        # WS-D 阶段5：status 拆为 loopStatus（回路配置态）+ kpiStatus（评估态）
        "loopStatus": loop.status,
        "kpiStatus": kpi_summary.get("status"),
        "currentValues": current_values,
        "runtimeParams": runtime_params,
        "trend": trend_data,
        "trendStatus": trend_status,
        "kpiSummary": kpi_summary,
    }


def _aggregate_kpi_snapshots(snaps: list[KpiSnapshotHourly], read_at: str) -> dict[str, Any]:
    """聚合多条小时快照为一条 KPI 摘要。

    策略：按 valid_rate 加权平均（有效数据率高的小时权重更大）。
    - score / 各指标 rate：加权平均，None 值跳过
    - status：全 SUCCESS → SUCCESS；含 INCONCLUSIVE → PARTIAL
    - confidence_level：取最差等级
    - algorithm_version：取最新快照的版本
    - calculatedAt：取最新快照的 ts_end
    """
    if not snaps:
        return {}

    if len(snaps) == 1:
        snap = snaps[0]

        def _r(val):
            return float(val) if val is not None else None

        return {
            "composite_score": _r(snap.score),
            "auto_mode_rate": _r(snap.auto_mode_rate),
            "effective_auto_rate": _r(snap.effective_auto_rate),
            "steady_rate": _r(snap.steady_rate),
            "accuracy_rate": _r(snap.accuracy_rate),
            "fast_rate": _r(snap.fast_rate),
            "oscillation_rate": _r(snap.oscillation_rate),
            "saturation_rate": _r(snap.saturation_rate),
            "good_value_rate": _r(snap.good_value_rate),
            "status": snap.status,
            "algorithm_version": snap.algorithm_version or ALGORITHM_VERSION,
            "calculatedAt": snap.ts_end.isoformat() if snap.ts_end else read_at,
            # 可信度统一 Phase 2：回路级可信度（单条快照直接取）
            "confidence_level": snap.confidence_level,
        }

    # 多条快照：按 valid_rate 加权平均
    _CONFIDENCE_ORDER = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}

    def _r(val):
        return float(val) if val is not None else None

    # 计算权重（valid_rate），None 视为 0
    weights = [_r(s.valid_rate) or 0.0 for s in snaps]
    total_w = sum(weights)
    # 全部为 0 时退化为简单平均
    if total_w == 0:
        weights = [1.0] * len(snaps)
        total_w = float(len(snaps))

    def _weighted(key: str) -> float | None:
        """加权平均一个指标字段，全为 None 时返回 None。"""
        vals = []
        for s, w in zip(snaps, weights, strict=False):
            v = getattr(s, key, None)
            if v is not None:
                vals.append((float(v), w))
        if not vals:
            return None
        denom = sum(w for _, w in vals)
        if denom == 0:
            # 所有权重均为 0 时退化为简单平均
            return sum(v for v, _ in vals) / len(vals)
        return sum(v * w for v, w in vals) / denom

    # 状态：全 SUCCESS → SUCCESS；否则 PARTIAL
    all_success = all(s.status == "SUCCESS" for s in snaps)
    agg_status = "SUCCESS" if all_success else "PARTIAL"

    latest = snaps[0]  # snaps 已按 ts_start DESC 排序

    # 可信度统一 Phase 2：回路级可信度取最差等级（A 最好，E 最差）
    confidence_levels = [s.confidence_level for s in snaps if s.confidence_level]
    worst_confidence = (
        max(confidence_levels, key=lambda x: _CONFIDENCE_ORDER.get(x, 5))
        if confidence_levels
        else None
    )

    return {
        "composite_score": _weighted("score"),
        "auto_mode_rate": _weighted("auto_mode_rate"),
        "effective_auto_rate": _weighted("effective_auto_rate"),
        "steady_rate": _weighted("steady_rate"),
        "accuracy_rate": _weighted("accuracy_rate"),
        "fast_rate": _weighted("fast_rate"),
        "oscillation_rate": _weighted("oscillation_rate"),
        "saturation_rate": _weighted("saturation_rate"),
        "good_value_rate": _weighted("good_value_rate"),
        "status": agg_status,
        "algorithm_version": latest.algorithm_version or ALGORITHM_VERSION,
        "calculatedAt": latest.ts_end.isoformat() if latest.ts_end else read_at,
        "confidence_level": worst_confidence,
    }


__all__ = ["get_loop_monitor_detail", "list_loop_monitor", "lttb_downsample"]
