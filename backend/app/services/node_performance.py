"""Node-level performance evaluation service (GB/T 44693.2-2024 §6.4).

按 plant_node 递归收集下属回路，以 score_weight 加权聚合回路级快照，
支持企业级/装置级/单元级 KPI 持久化与查询。

核心功能：
- 递归收集节点下属回路（PostgreSQL CTE）
- 加权聚合回路级快照（score_weight 加权均值）
- 节点级快照读写（幂等：相同 plant_node_id + ts_start 不重复写入）
- 查询服务（最新快照、历史趋势、节点间排名、全厂总览）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import COMPLEX_ROLE_MAIN, LoopLedger
from app.models.loop_config import LoopLevelWeight
from app.models.metric import KpiSnapshotHourly
from app.models.node_kpi import (
    KpiNodeSnapshotDaily,
    KpiNodeSnapshotHourly,
    KpiNodeSnapshotMonthly,
)
from app.models.plant_node import PlantNode
from app.models.unit_kpi_summary import UnitKpiSummary
from app.services.performance import ALGORITHM_VERSION, KPI_NAME_MAP, _score_to_status

logger = logging.getLogger(__name__)

# 参与加权聚合的 KPI 字段（与回路级快照对齐）
KPI_FIELDS = (
    "good_value_rate",
    "auto_mode_rate",
    "effective_auto_rate",
    "steady_rate",
    "accuracy_rate",
    "fast_rate",
    "oscillation_rate",
    "saturation_rate",
    "instrument_fault_rate",  # Phase 1 新增：仪表故障率（AGGREGATABLE）
    "score",
    # P1 #14: 4 个诊断字段（与 KpiNodeSnapshotHourly 模型对齐）
    "stiction_index",
    "settling_time",
    "output_trip_index",
    "ideal_settling_time",
)


# ---------------------------------------------------------------------------
# 递归收集节点下属回路
# ---------------------------------------------------------------------------


async def collect_descendant_loop_ids(
    db: AsyncSession,
    plant_node_id: str,
) -> list[str]:
    """递归收集指定节点及其所有子孙节点下挂载的回路 ID。

    使用 PostgreSQL 递归 CTE 遍历 plant_node 树，再 JOIN loop_ledger.unit_id。

    Returns:
        回路 ID 列表（仅 is_active=True 的回路）
    """
    # 递归 CTE：从 plant_node_id 出发，向下遍历所有子节点
    cte_sql = text("""
        WITH RECURSIVE node_tree AS (
            SELECT id FROM plant_node WHERE id = :node_id
            UNION ALL
            SELECT child.id FROM plant_node child
            JOIN node_tree ON child.parent_id = node_tree.id
        )
        SELECT l.id AS loop_id
        FROM loop_ledger l
        WHERE l.unit_id IN (SELECT id FROM node_tree)
          AND l.is_active = TRUE
    """)
    result = await db.execute(cte_sql, {"node_id": plant_node_id})
    return [str(row.loop_id) for row in result.all()]


# ---------------------------------------------------------------------------
# 实时自控率查询
# ---------------------------------------------------------------------------


async def query_realtime_auto_rate(
    db: AsyncSession,
    loop_ids: list[str],
) -> dict | None:
    """查询当前时刻处于自动模式的回路占比（实时自控率）。

    MODE 最新值优先读 Redis 实时缓存（SignalR 订阅器维护的 ``realtime:{tagCode}``），
    缓存缺失时回退 PostgreSQL ``tag_registry.current_value``（仅 AAS 同步写入，可能过期），
    然后根据该回路的投用定义（loop_mode_mapping）判断是否算自动模式。
    无投用定义的回路回退到默认 {1, 2, 3}（向后兼容）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表

    Returns:
        实时自控率统计结果 dict：
        - rate: 自控率百分比（Decimal）
        - auto_count: 自动模式回路数
        - manual_count: 手动模式回路数
        - total_count: 有效回路总数
        - read_at: 数据最新时间（所读实时缓存 collectTime 的最大值，ISO 字符串；
          全部回退 DB 值时为 None，表示实时流中断/数据可能过期）
        TDengine 不可用或无数据时返回 None
    """
    if not loop_ids:
        return None

    from app.models.loop import LoopTagMapping
    from app.models.loop_config import LoopModeMapping
    from app.models.tag import TagRegistry

    # --- 1. 批量查询投用定义，构建 {loop_id: set(auto_mode_values)} ---
    mm_result = await db.execute(
        select(LoopModeMapping.loop_id, LoopModeMapping.mode_value).where(
            LoopModeMapping.loop_id.in_(loop_ids),
            LoopModeMapping.is_auto.is_(True),
        )
    )
    auto_mode_map: dict[str, set[int]] = {}
    for row in mm_result.all():
        auto_mode_map.setdefault(row.loop_id, set()).add(row.mode_value)

    # --- 2. 查询每个回路的 MODE tag 映射 ---
    result = await db.execute(
        select(LoopTagMapping.loop_id, TagRegistry.tag_name)
        .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
        .where(
            LoopTagMapping.loop_id.in_(loop_ids),
            LoopTagMapping.tag_role == "MODE",
        )
    )
    rows = result.all()

    if not rows:
        logger.debug("[实时自控率] 无 MODE tag 映射，跳过")
        return None

    # --- 3. 读取最新 MODE 值：优先 Redis 实时缓存（SignalR 订阅器维护），
    #        缺失的 tag 回退 tag_registry.current_value（AAS 同步写入，可能过期）---
    tag_names = [row.tag_name for row in rows]

    tag_mode_map: dict[str, object] = {}
    latest_collect_time: str | None = None
    try:
        from app.services.data_source.realtime_subscriber import get_subscriber

        cached_list = await get_subscriber().get_cached_values(tag_names)
        for item in cached_list:
            tc = item.get("tagCode")
            if tc:
                tag_mode_map[tc] = item.get("value")
                # 记录数据最新时间（collectTime 为 ISO 字符串，同格式可直接比较）
                ct = item.get("collectTime")
                if ct and (latest_collect_time is None or ct > latest_collect_time):
                    latest_collect_time = ct
    except Exception:
        logger.warning("[实时自控率] 从 Redis 读取实时 MODE 值失败，回退数据库值", exc_info=True)

    missing = [name for name in tag_names if tag_mode_map.get(name) in (None, "")]
    if missing:
        tag_result = await db.execute(
            select(TagRegistry.tag_name, TagRegistry.current_value).where(
                TagRegistry.tag_name.in_(missing)
            )
        )
        for row in tag_result.all():
            tag_mode_map[row.tag_name] = row.current_value

    # --- 4. 按回路投用定义判断是否算自动 ---
    DEFAULT_AUTO_MODES = {1, 2, 3}  # 向后兼容默认值
    auto_count = 0
    valid_count = 0
    mode_counts: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

    for row in rows:
        mode_val = tag_mode_map.get(row.tag_name)
        if mode_val is None or mode_val == "":
            continue
        try:
            mode_int = int(float(mode_val))  # type: ignore[arg-type]
        except (ValueError, TypeError):
            continue
        valid_count += 1
        # 统计各 MODE 值的数量（仅统计 0-4 标准模式）
        if mode_int in mode_counts:
            mode_counts[mode_int] += 1
        # 取该回路的自动 MODE 集合，无配置时回退到默认
        auto_modes = auto_mode_map.get(row.loop_id, DEFAULT_AUTO_MODES)
        if mode_int in auto_modes:
            auto_count += 1

    if valid_count == 0:
        logger.debug("[实时自控率] 无可用 MODE 数据（Redis 缓存与数据库值均为空）")
        return None

    rate = round(auto_count / valid_count * 100, 2)
    logger.debug(
        "[实时自控率] 有效回路=%d, 自动模式=%d, 实时自控率=%.2f%%",
        valid_count,
        auto_count,
        rate,
    )
    return {
        "rate": Decimal(str(rate)),
        "auto_count": auto_count,
        "manual_count": valid_count - auto_count,
        "total_count": valid_count,
        "mode_counts": mode_counts,
        "read_at": latest_collect_time,
    }


# ---------------------------------------------------------------------------
# 节点级快照聚合与持久化
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P4 S3：复杂回路组去重（Python 层，方案 B）
# ---------------------------------------------------------------------------

#: confidence_level 排序键（A 最佳 → E 最差，None 视为最低）
_CONFIDENCE_RANK: dict[str, int] = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}


def _confidence_rank(level: str | None) -> int:
    """confidence_level → 排序键，越小越优；None 视为最低。"""
    if level is None:
        return 99
    return _CONFIDENCE_RANK.get(level, 99)


def _pick_group_representative(members: list) -> object:
    """从复杂回路组成员中选代表（RFC 决策点 2）。

    - 有 complex_role=MAIN 的成员 → 取 MAIN（多个 MAIN 取首个，业务层应保证唯一）
    - MAIN 缺席 → 取 confidence 最高（A>B>C>D>E，None 最低）的成员
    """
    mains = [m for m in members if m.complex_role == COMPLEX_ROLE_MAIN]
    if mains:
        return mains[0]
    return min(members, key=lambda m: _confidence_rank(m.confidence_level))


def _dedup_complex_groups(rows: list) -> list:
    """复杂回路组去重（RFC 决策点 2）。

    - complex_loop_group_id 为空（普通单回路）：全部保留
    - 同 complex_loop_group_id 的组：仅保留一个代表（MAIN 优先，否则 confidence 最高）

    Returns:
        去重后的回路行列表（单回路 + 每组代表）
    """
    singles = [r for r in rows if r.complex_loop_group_id is None]
    groups: dict[str, list] = {}
    for r in rows:
        gid = r.complex_loop_group_id
        if gid is not None:
            groups.setdefault(gid, []).append(r)

    representatives = list(singles)
    for members in groups.values():
        representatives.append(_pick_group_representative(members))
    logger.debug(
        "[节点级聚合-S3] 输入回路=%d, 去重后代表=%d, 复杂组=%d",
        len(rows),
        len(representatives),
        len(groups),
    )
    return representatives


async def _fetch_and_aggregate_loops(
    db: AsyncSession,
    loop_ids: list[str],
    ts_start: datetime,
    ts_end: datetime,
) -> dict | None:
    """P4 S3：获取回路级 SUCCESS 快照 → 复杂组去重 → Python 加权聚合。

    替代原单 SQL 聚合：先查每回路最新 SUCCESS 快照 + 复杂分组/角色/权重，
    再 Python 按 complex_loop_group_id 去重（MAIN 代表，缺席退化 confidence 最高），
    最后按 importance_level 权重加权平均。

    Returns:
        聚合结果 dict（含各 KPI 字段加权均值 / loop_count / auto_loop_count /
        auto_loop_ratio），无 SUCCESS 快照或权重为 0 返回 None。
    """
    # 子查询：每个回路在时间窗内最新一条 SUCCESS 快照（DISTINCT ON loop_id）
    subq = (
        select(KpiSnapshotHourly)
        .where(
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= ts_start,
            KpiSnapshotHourly.ts_start <= ts_end,
            KpiSnapshotHourly.status == "SUCCESS",
        )
        .distinct(KpiSnapshotHourly.loop_id)
        .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_start.desc())
    ).subquery()

    # 外层 JOIN loop_ledger + loop_level_weight，取回路级字段 + 复杂分组/角色 + 权重
    # level=NULL 时 OUTER JOIN 不匹配，COALESCE 到 1.0（等同 level=3）
    weight_col = func.coalesce(LoopLevelWeight.weight, Decimal("1.0")).label("weight")
    fields = [getattr(subq.c, f).label(f) for f in KPI_FIELDS]

    # S1：仅聚合 include_in_evaluation=True 的回路
    stmt = (
        select(
            subq.c.loop_id.label("loop_id"),
            subq.c.confidence_level.label("confidence_level"),
            *fields,
            LoopLedger.complex_loop_group_id,
            LoopLedger.complex_role,
            weight_col,
        )
        .select_from(
            subq.join(LoopLedger, subq.c.loop_id == LoopLedger.id).outerjoin(
                LoopLevelWeight, LoopLedger.importance_level == LoopLevelWeight.level
            )
        )
        .where(LoopLedger.include_in_evaluation.is_(True))
    )
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return None

    # P4 S3：复杂回路组去重（MAIN 代表 / confidence 回退）
    representatives = _dedup_complex_groups(rows)

    weight_total = sum(Decimal(str(r.weight or 0)) for r in representatives)
    if weight_total == 0:
        logger.warning("[节点级聚合-S3] SUM(weight)=0，无法计算加权平均")
        return None

    def avg_value(field: str) -> Decimal | None:
        # 与原 SQL 一致：SUM(field*weight) / SUM(weight)，NULL 字段跳过（不参与分子）
        numerator = Decimal("0")
        for r in representatives:
            val = getattr(r, field)
            if val is not None:
                numerator += Decimal(str(val)) * Decimal(str(r.weight or 0))
        return (numerator / weight_total).quantize(Decimal("0.01"))

    auto_loop_count_val = sum(
        1
        for r in representatives
        if r.auto_mode_rate is not None and Decimal(str(r.auto_mode_rate)) > 0
    )
    loop_count = len(representatives)
    auto_loop_ratio = round(auto_loop_count_val / loop_count * 100, 2) if loop_count else 0.0

    # 仪表故障率诊断日志：逐回路打印值与权重，便于排查数据为空问题
    ifr_details = [
        {
            "loop_id": str(r.loop_id),
            "instrument_fault_rate": r.instrument_fault_rate,
            "weight": float(r.weight or 0),
        }
        for r in representatives
    ]
    ifr_non_null = [d for d in ifr_details if d["instrument_fault_rate"] is not None]
    ifr_avg = avg_value("instrument_fault_rate")
    logger.info(
        "[节点级聚合-仪表故障率] 代表回路=%d, 有值=%d, 无值=%d, 加权均值=%s, 详情=%s",
        len(representatives),
        len(ifr_non_null),
        len(ifr_details) - len(ifr_non_null),
        ifr_avg,
        ifr_details,
    )

    return {
        "score": avg_value("score"),
        "good_value_rate": avg_value("good_value_rate"),
        "auto_mode_rate": avg_value("auto_mode_rate"),
        "effective_auto_rate": avg_value("effective_auto_rate"),
        "steady_rate": avg_value("steady_rate"),
        "accuracy_rate": avg_value("accuracy_rate"),
        "fast_rate": avg_value("fast_rate"),
        "oscillation_rate": avg_value("oscillation_rate"),
        "saturation_rate": avg_value("saturation_rate"),
        "instrument_fault_rate": ifr_avg,
        "stiction_index": avg_value("stiction_index"),
        "settling_time": avg_value("settling_time"),
        "output_trip_index": avg_value("output_trip_index"),
        "ideal_settling_time": avg_value("ideal_settling_time"),
        "loop_count": loop_count,
        "auto_loop_count": auto_loop_count_val,
        "auto_loop_ratio": Decimal(str(auto_loop_ratio)).quantize(Decimal("0.01")),
    }


async def aggregate_node_snapshot(
    db: AsyncSession,
    plant_node_id: str,
    ts_start: datetime,
    ts_end: datetime,
) -> dict | None:
    """聚合指定节点在时间窗内的回路级快照，生成节点级快照。

    聚合规则（对齐 GB/T 44693.2-2024 §6.4 + 附表2）：
    - 递归收集节点下属所有 active 回路
    - 取每个回路在该时间窗内最新一条 SUCCESS 快照
    - 按回路级别加权平均（level=1→3.0, level=2→2.0, level=3→1.0）
    - 投自动回路占比 = auto_mode_rate > 0 的回路数 / 总回路数

    Returns:
        节点级快照字典，若无数据返回 None
    """
    loop_ids = await collect_descendant_loop_ids(db, plant_node_id)
    if not loop_ids:
        logger.debug("[节点级聚合] plant_node_id=%s 无下属回路", plant_node_id)
        return None

    # P4 S3：复杂组去重 + Python 加权聚合（替代原单 SQL 聚合）
    agg = await _fetch_and_aggregate_loops(db, loop_ids, ts_start, ts_end)
    if agg is None:
        logger.debug(
            "[节点级聚合] plant_node_id=%s, 时间窗 %s~%s 无 SUCCESS 快照",
            plant_node_id,
            ts_start,
            ts_end,
        )
        return None

    score_avg = agg["score"]
    auto_loop_count_val = agg["auto_loop_count"]

    status = _score_to_status(score_avg)

    # 查询实时自控率（TDengine 不可用时返回 None，不影响聚合流程）
    _realtime_result = await query_realtime_auto_rate(db, loop_ids)
    realtime_auto_rate = _realtime_result["rate"] if _realtime_result else None

    # v6.1.2 修复：补充 UnitKpiSummary 所需的回路计数字段
    # P4 S3：loop_count 为去重后回路组数（单回路 + 每复杂组代表）
    total_loops_count = len(loop_ids)
    evaluated_loops_count = agg["loop_count"]

    # excluded_loops: include_in_evaluation=False 的回路数
    ex_result = await db.execute(
        select(func.count())
        .select_from(LoopLedger)
        .where(
            LoopLedger.id.in_(loop_ids),
            LoopLedger.include_in_evaluation.is_(False),
        )
    )
    excluded_loops_count = int(ex_result.scalar() or 0)

    # inconclusive_loops: 只有 INCONCLUSIVE 快照但没有 SUCCESS 快照的回路数
    ic_result = await db.execute(
        select(func.count(func.distinct(KpiSnapshotHourly.loop_id)))
        .select_from(KpiSnapshotHourly)
        .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
        .where(
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= ts_start,
            KpiSnapshotHourly.ts_start <= ts_end,
            KpiSnapshotHourly.status == "INCONCLUSIVE",
            LoopLedger.include_in_evaluation.is_(True),
            ~KpiSnapshotHourly.loop_id.in_(
                select(KpiSnapshotHourly.loop_id).where(
                    KpiSnapshotHourly.loop_id.in_(loop_ids),
                    KpiSnapshotHourly.ts_start >= ts_start,
                    KpiSnapshotHourly.ts_start <= ts_end,
                    KpiSnapshotHourly.status == "SUCCESS",
                )
            ),
        )
    )
    inconclusive_loops_count = int(ic_result.scalar() or 0)

    logger.info(
        "[节点级聚合] plant_node_id=%s, 回路数=%d, 投自动回路数=%d, "
        "投自动占比=%.2f%%, 实时自控率=%s, 加权综合评分=%s, "
        "加权仪表故障率=%s, 定级=%s",
        plant_node_id,
        agg["loop_count"],
        auto_loop_count_val,
        agg["auto_loop_ratio"],
        realtime_auto_rate,
        score_avg,
        agg["instrument_fault_rate"],
        status,
    )

    return {
        "plant_node_id": plant_node_id,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "score": score_avg,
        "good_value_rate": agg["good_value_rate"],
        "auto_mode_rate": agg["auto_mode_rate"],
        "effective_auto_rate": agg["effective_auto_rate"],
        "steady_rate": agg["steady_rate"],
        "accuracy_rate": agg["accuracy_rate"],
        "fast_rate": agg["fast_rate"],
        "oscillation_rate": agg["oscillation_rate"],
        "saturation_rate": agg["saturation_rate"],
        "instrument_fault_rate": agg["instrument_fault_rate"],
        # P1 #14: 4 个诊断字段（与 KpiNodeSnapshotHourly 模型对齐）
        "stiction_index": agg["stiction_index"],
        "settling_time": agg["settling_time"],
        "output_trip_index": agg["output_trip_index"],
        "ideal_settling_time": agg["ideal_settling_time"],
        "auto_loop_ratio": agg["auto_loop_ratio"],
        "realtime_auto_rate": realtime_auto_rate,
        "loop_count": agg["loop_count"],
        "status": status,
        "algorithm_version": ALGORITHM_VERSION,
        # v6.1.2: UnitKpiSummary 所需的回路计数字段
        "total_loops": total_loops_count,
        "evaluated_loops": evaluated_loops_count,
        "inconclusive_loops": inconclusive_loops_count,
        "excluded_loops": excluded_loops_count,
        # unit_status: 聚合状态（SUCCESS/PARTIAL/EMPTY），不是性能定级
        "unit_status": "PARTIAL" if inconclusive_loops_count > 0 else "SUCCESS",
    }


async def save_node_snapshot(db: AsyncSession, snap_data: dict) -> dict:
    """保存节点级快照（幂等：相同 plant_node_id + ts_start 覆盖更新）。

    v5.3：并行写入 KpiNodeSnapshotHourly + UnitKpiSummary（装置级汇总）。
    UnitKpiSummary 仅写入装置类型节点（type=UNIT），其他类型节点跳过。

    Args:
        db: 异步数据库会话
        snap_data: aggregate_node_snapshot 返回的快照字典

    Returns:
        保存后的快照字典
    """
    plant_node_id = snap_data["plant_node_id"]
    ts_start = snap_data["ts_start"]

    # v5.3 字段分离：UnitKpiSummary 专用字段不写入 KpiNodeSnapshotHourly
    _UNIT_FIELDS = {
        "total_loops",
        "evaluated_loops",
        "excluded_loops",
        "inconclusive_loops",
        "unit_status",
    }
    node_snap_data = {k: v for k, v in snap_data.items() if k not in _UNIT_FIELDS}

    # 查询是否已存在（幂等）
    existing_result = await db.execute(
        select(KpiNodeSnapshotHourly).where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
            KpiNodeSnapshotHourly.ts_start == ts_start,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        # 覆盖更新
        for key, val in node_snap_data.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        existing.created_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
        logger.debug("[节点级快照] 覆盖更新 plant_node_id=%s, ts_start=%s", plant_node_id, ts_start)
    else:
        # 新增
        snap = KpiNodeSnapshotHourly(
            id=str(uuid4()),
            **node_snap_data,
        )
        db.add(snap)
        await db.flush()
        logger.debug("[节点级快照] 新增 plant_node_id=%s, ts_start=%s", plant_node_id, ts_start)

    # v5.3：并行写入 UnitKpiSummary（装置级汇总）
    await _save_unit_kpi_summary(db, snap_data)

    return snap_data


async def _save_unit_kpi_summary(db: AsyncSession, snap_data: dict) -> None:
    """写入装置级 KPI 汇总（UnitKpiSummary）。

    仅当 snap_data 包含 v5.3 聚合字段时写入，否则跳过。
    幂等：相同 node_id + snapshot_time 覆盖更新。
    """
    node_id = snap_data["plant_node_id"]
    snapshot_time = snap_data["ts_start"]

    existing_result = await db.execute(
        select(UnitKpiSummary).where(
            UnitKpiSummary.node_id == node_id,
            UnitKpiSummary.snapshot_time == snapshot_time,
        )
    )
    existing = existing_result.scalar_one_or_none()

    unit_data = {
        "node_id": node_id,
        "snapshot_time": snapshot_time,
        "avg_score": snap_data.get("score"),
        "auto_mode_rate": snap_data.get("auto_mode_rate"),
        "effective_auto_rate": snap_data.get("effective_auto_rate"),
        "stability_rate": snap_data.get("steady_rate"),
        "accuracy_rate": snap_data.get("accuracy_rate"),
        "fast_rate": snap_data.get("fast_rate"),
        "good_value_rate": snap_data.get("good_value_rate"),
        "oscillation_rate": snap_data.get("oscillation_rate"),
        "saturation_rate": snap_data.get("saturation_rate"),
        "instrument_fault_rate": snap_data.get("instrument_fault_rate"),
        "total_loops": snap_data.get("total_loops"),
        "evaluated_loops": snap_data.get("evaluated_loops"),
        "inconclusive_loops": snap_data.get("inconclusive_loops"),
        "excluded_loops": snap_data.get("excluded_loops", 0),
        "status": snap_data.get("unit_status", "SUCCESS"),
        "algorithm_version": snap_data.get("algorithm_version"),
    }

    if existing:
        for key, val in unit_data.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        await db.flush()
    else:
        summary = UnitKpiSummary(
            id=str(uuid4()),
            **unit_data,
        )
        db.add(summary)
        await db.flush()
        logger.debug("[装置级汇总] 新增 node_id=%s, snapshot_time=%s", node_id, snapshot_time)


async def calculate_and_save_node_snapshot(
    db: AsyncSession,
    plant_node_id: str,
    ts_start: datetime,
    ts_end: datetime,
) -> dict | None:
    """聚合并保存节点级快照（一步完成）。"""
    snap_data = await aggregate_node_snapshot(db, plant_node_id, ts_start, ts_end)
    if snap_data is None:
        return None
    return await save_node_snapshot(db, snap_data)


# ---------------------------------------------------------------------------
# 查询服务
# ---------------------------------------------------------------------------


def _snapshot_to_dict(snap: KpiNodeSnapshotHourly, node_name: str | None = None) -> dict:
    """快照对象转字典。"""

    def to_float(v):
        return float(v) if v is not None else None

    return {
        "plantNodeId": str(snap.plant_node_id),
        "plantNodeName": node_name,
        "tsStart": snap.ts_start.isoformat() if snap.ts_start else None,
        "tsEnd": snap.ts_end.isoformat() if snap.ts_end else None,
        "score": to_float(snap.score),
        "goodValueRate": to_float(snap.good_value_rate),
        "autoModeRate": to_float(snap.auto_mode_rate),
        "effectiveAutoRate": to_float(snap.effective_auto_rate),
        "steadyRate": to_float(snap.steady_rate),
        "accuracyRate": to_float(snap.accuracy_rate),
        "fastResponseRate": to_float(snap.fast_rate),
        "oscillationRate": to_float(snap.oscillation_rate),
        "saturationRate": to_float(snap.saturation_rate),
        "instrumentFaultRate": to_float(snap.instrument_fault_rate),
        "autoLoopRatio": to_float(snap.auto_loop_ratio),
        "realtimeAutoRate": to_float(snap.realtime_auto_rate),
        "loopCount": snap.loop_count,
        "status": snap.status,
        "algorithmVersion": snap.algorithm_version,
    }


async def get_node_latest_snapshot(
    db: AsyncSession,
    plant_node_id: str,
) -> dict | None:
    """获取节点最新一条快照。"""
    result = await db.execute(
        select(KpiNodeSnapshotHourly)
        .where(KpiNodeSnapshotHourly.plant_node_id == plant_node_id)
        .order_by(KpiNodeSnapshotHourly.ts_start.desc())
        .limit(1)
    )
    snap = result.scalar_one_or_none()
    if snap is None:
        return None

    # 查节点名
    node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = node_result.scalar_one_or_none()
    node_name = node.name if node else None

    return _snapshot_to_dict(snap, node_name)


async def get_node_trend(
    db: AsyncSession,
    plant_node_id: str,
    start: datetime,
    end: datetime,
) -> dict:
    """获取节点历史趋势。"""
    result = await db.execute(
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
        )
        .order_by(KpiNodeSnapshotHourly.ts_start.asc())
    )
    snaps = result.scalars().all()

    # 查节点名
    node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = node_result.scalar_one_or_none()
    node_name = node.name if node else None

    timestamps = [s.ts_start.isoformat() for s in snaps]

    def series(field: str, name: str) -> dict:
        return {
            "metricKey": field,
            "metricName": name,
            "values": [
                float(getattr(s, field)) if getattr(s, field) is not None else None for s in snaps
            ],
        }

    return {
        "plantNodeId": plant_node_id,
        "plantNodeName": node_name,
        "timestamps": timestamps,
        "series": [
            series("score", KPI_NAME_MAP.get("composite_score", "综合评分")),
            series("auto_loop_ratio", KPI_NAME_MAP.get("auto_loop_ratio", "投自动回路占比")),
            series("realtime_auto_rate", "实时自控率"),
            series("steady_rate", KPI_NAME_MAP.get("steady_rate", "平稳率")),
            series("effective_auto_rate", KPI_NAME_MAP.get("effective_auto_rate", "有效自控率")),
        ],
    }


async def get_node_ranking(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    node_type: str | None = None,
    sort_by: str = "score",
    sort_order: str = "desc",
    limit: int = 50,
) -> list[dict]:
    """获取节点间性能排名。

    Args:
        node_type: 筛选节点类型（FACTORY/UNIT/EQUIPMENT），None 表示所有启用节点
        sort_by: 排序字段 score/steady_rate/auto_loop_ratio
        sort_order: asc/desc（默认 desc，分数最高的在前）
    """
    # 子查询：每个节点在时间窗内最新一条快照
    base = (
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
        )
        .distinct(KpiNodeSnapshotHourly.plant_node_id)
        .order_by(KpiNodeSnapshotHourly.plant_node_id, KpiNodeSnapshotHourly.ts_start.desc())
    ).subquery()

    # JOIN plant_node 过滤类型
    stmt = select(base, PlantNode.name, PlantNode.type).join(
        PlantNode, base.c.plant_node_id == PlantNode.id
    )

    if node_type:
        stmt = stmt.where(PlantNode.type == node_type)

    # 排序
    sort_field_map = {
        "score": base.c.score,
        "steady_rate": base.c.steady_rate,
        "auto_loop_ratio": base.c.auto_loop_ratio,
        "effective_auto_rate": base.c.effective_auto_rate,
        "realtime_auto_rate": base.c.realtime_auto_rate,
    }
    sort_col = sort_field_map.get(sort_by, base.c.score)
    if sort_order.lower() == "asc":
        stmt = stmt.order_by(sort_col.asc().nulls_last())
    else:
        stmt = stmt.order_by(sort_col.desc().nulls_last())

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for idx, row in enumerate(rows, start=1):

        def to_float(v):
            return float(v) if v is not None else None

        items.append(
            {
                "rank": idx,
                "plantNodeId": str(row.plant_node_id),
                "plantNodeName": row.name,
                "plantNodeType": row.type,
                "tsStart": row.ts_start.isoformat() if row.ts_start else None,
                "score": to_float(row.score),
                "goodValueRate": to_float(row.good_value_rate),
                "autoModeRate": to_float(row.auto_mode_rate),
                "effectiveAutoRate": to_float(row.effective_auto_rate),
                "steadyRate": to_float(row.steady_rate),
                "accuracyRate": to_float(row.accuracy_rate),
                "fastResponseRate": to_float(row.fast_rate),
                "oscillationRate": to_float(row.oscillation_rate),
                "saturationRate": to_float(row.saturation_rate),
                "instrumentFaultRate": to_float(row.instrument_fault_rate),
                "autoLoopRatio": to_float(row.auto_loop_ratio),
                "realtimeAutoRate": to_float(row.realtime_auto_rate),
                "loopCount": row.loop_count,
                "status": row.status,
                "algorithmVersion": row.algorithm_version,
            }
        )
    return items


async def get_nodes_overview(
    db: AsyncSession,
    start: datetime,
    end: datetime,
) -> dict:
    """全厂总览：所有启用 KPI 评估的节点最新快照汇总。"""
    # 查询所有 is_kpi_enabled 的节点
    node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
    enabled_nodes = node_result.scalars().all()

    if not enabled_nodes:
        return {
            "totalNodes": 0,
            "nodes": [],
            "statusDistribution": {},
        }

    node_ids = [str(n.id) for n in enabled_nodes]
    node_name_map = {str(n.id): n.name for n in enabled_nodes}
    node_type_map = {str(n.id): n.type for n in enabled_nodes}

    # 子查询：每个节点最新快照
    base = (
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
            KpiNodeSnapshotHourly.plant_node_id.in_(node_ids),
        )
        .distinct(KpiNodeSnapshotHourly.plant_node_id)
        .order_by(KpiNodeSnapshotHourly.plant_node_id, KpiNodeSnapshotHourly.ts_start.desc())
    ).subquery()

    stmt = select(base)
    result = await db.execute(stmt)
    rows = result.all()

    nodes = []
    status_dist: dict[str, int] = {}
    for row in rows:
        nid = str(row.plant_node_id)

        def to_float(v):
            return float(v) if v is not None else None

        status = row.status
        status_dist[status] = status_dist.get(status, 0) + 1

        nodes.append(
            {
                "plantNodeId": nid,
                "plantNodeName": node_name_map.get(nid),
                "plantNodeType": node_type_map.get(nid),
                "score": to_float(row.score),
                "autoLoopRatio": to_float(row.auto_loop_ratio),
                "realtimeAutoRate": to_float(row.realtime_auto_rate),
                "steadyRate": to_float(row.steady_rate),
                "effectiveAutoRate": to_float(row.effective_auto_rate),
                "loopCount": row.loop_count,
                "status": status,
                "tsStart": row.ts_start.isoformat() if row.ts_start else None,
            }
        )

    # 按评分降序
    nodes.sort(key=lambda x: -(x["score"] or 0))

    return {
        "totalNodes": len(enabled_nodes),
        "nodesWithSnapshot": len(nodes),
        "nodes": nodes,
        "statusDistribution": status_dist,
    }


# ---------------------------------------------------------------------------
# 多维度监控查询（hour / day / month）
# ---------------------------------------------------------------------------


def _monitor_snapshot_to_dict(snap, dimension: str, node_name: str | None = None) -> dict:
    """监控快照对象转字典（兼容 hour/day/month 三种维度）。"""

    def to_float(v):
        return float(v) if v is not None else None

    # 时间字段：hour 用 ts_start/ts_end，day 用 stat_date，month 用 stat_month
    if dimension == "hour":
        time_label = snap.ts_start.isoformat() if snap.ts_start else None
        time_end = snap.ts_end.isoformat() if snap.ts_end else None
    elif dimension == "day":
        time_label = snap.stat_date.isoformat() if snap.stat_date else None
        time_end = None
    else:  # month
        time_label = snap.stat_month.isoformat() if snap.stat_month else None
        time_end = None

    return {
        "plantNodeId": str(snap.plant_node_id),
        "plantNodeName": node_name,
        "dimension": dimension,
        "tsStart": time_label,
        "tsEnd": time_end,
        "score": to_float(snap.score),
        "goodValueRate": to_float(snap.good_value_rate),
        "autoModeRate": to_float(snap.auto_mode_rate),
        "effectiveAutoRate": to_float(snap.effective_auto_rate),
        "steadyRate": to_float(snap.steady_rate),
        "accuracyRate": to_float(snap.accuracy_rate),
        "fastResponseRate": to_float(snap.fast_rate),
        "oscillationRate": to_float(snap.oscillation_rate),
        "saturationRate": to_float(snap.saturation_rate),
        "instrumentFaultRate": to_float(snap.instrument_fault_rate),
        "autoLoopRatio": to_float(snap.auto_loop_ratio),
        "realtimeAutoRate": to_float(snap.realtime_auto_rate),
        "loopCount": snap.loop_count,
        "status": snap.status,
        "algorithmVersion": snap.algorithm_version,
    }


async def get_node_monitor_data(
    db: AsyncSession,
    plant_node_id: str,
    dimension: str,
    start: datetime,
    end: datetime,
) -> dict:
    """获取节点多维度监控数据（hour/day/month）。

    Args:
        db: 异步数据库会话
        plant_node_id: 工厂节点 ID
        dimension: 维度 hour/day/month
        start: 起始时间（datetime，day/month 维度会取 .date()）
        end: 结束时间（datetime，day/month 维度会取 .date()）

    Returns:
        {plantNodeId, plantNodeName, dimension, start, end, snapshots: [...]}
    """
    # 查节点名
    node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = node_result.scalar_one_or_none()
    node_name = node.name if node else None

    if dimension == "hour":
        stmt = (
            select(KpiNodeSnapshotHourly)
            .where(
                KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
                KpiNodeSnapshotHourly.ts_start >= start,
                KpiNodeSnapshotHourly.ts_start <= end,
            )
            .order_by(KpiNodeSnapshotHourly.ts_start.asc())
        )
    elif dimension == "day":
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end
        stmt = (
            select(KpiNodeSnapshotDaily)
            .where(
                KpiNodeSnapshotDaily.plant_node_id == plant_node_id,
                KpiNodeSnapshotDaily.stat_date >= start_date,
                KpiNodeSnapshotDaily.stat_date <= end_date,
            )
            .order_by(KpiNodeSnapshotDaily.stat_date.asc())
        )
    elif dimension == "month":
        start_month = (
            start.date().replace(day=1) if isinstance(start, datetime) else start.replace(day=1)
        )
        end_month = end.date().replace(day=1) if isinstance(end, datetime) else end.replace(day=1)
        stmt = (
            select(KpiNodeSnapshotMonthly)
            .where(
                KpiNodeSnapshotMonthly.plant_node_id == plant_node_id,
                KpiNodeSnapshotMonthly.stat_month >= start_month,
                KpiNodeSnapshotMonthly.stat_month <= end_month,
            )
            .order_by(KpiNodeSnapshotMonthly.stat_month.asc())
        )
    else:
        raise ValueError(f"不支持的维度: {dimension}，可选值: hour/day/month")

    result = await db.execute(stmt)
    snaps = result.scalars().all()

    snapshots = [_monitor_snapshot_to_dict(s, dimension, node_name) for s in snaps]

    return {
        "plantNodeId": plant_node_id,
        "plantNodeName": node_name,
        "dimension": dimension,
        "start": start.isoformat() if isinstance(start, datetime) else str(start),
        "end": end.isoformat() if isinstance(end, datetime) else str(end),
        "snapshots": snapshots,
    }


# ---------------------------------------------------------------------------
# 批量优化（Phase 4：节点级聚合性能优化）
# ---------------------------------------------------------------------------


async def batch_collect_descendant_loop_ids(
    db: AsyncSession,
    node_ids: list[str],
) -> dict[str, list[str]]:
    """批量递归收集多个节点的下属回路 ID（1 次递归 CTE 替代 N 次）.

    Args:
        db: 异步数据库会话
        node_ids: 需要收集回路的节点 ID 列表

    Returns:
        ``{node_id: [loop_id, ...]}`` 映射；无回路的节点对应空列表

    设计依据：Phase 4 优化措施 1，将 N 次 ``collect_descendant_loop_ids`` 合并为
    1 次递归 CTE + 1 次批量 loop 查询。
    """
    if not node_ids:
        return {}

    # 1 次递归 CTE：返回 (ancestor_node_id, descendant_node_id) 对
    # 对每个 node_id 展开 its entire subtree
    cte_sql = text("""
        WITH RECURSIVE node_tree AS (
            SELECT id AS root_id, id AS descendant_id
            FROM plant_node
            WHERE id = ANY(:node_ids)
            UNION ALL
            SELECT nt.root_id, child.id
            FROM plant_node child
            JOIN node_tree nt ON child.parent_id = nt.descendant_id
        )
        SELECT nt.root_id, l.id AS loop_id
        FROM node_tree nt
        JOIN loop_ledger l ON l.unit_id = nt.descendant_id
        WHERE l.is_active = TRUE
    """)

    result = await db.execute(cte_sql, {"node_ids": node_ids})
    mapping: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for row in result.all():
        root_id = str(row.root_id)
        if root_id in mapping:
            mapping[root_id].append(str(row.loop_id))

    total_loops = sum(len(v) for v in mapping.values())
    logger.info(
        "[批量树遍历] %d 节点 → %d 回路（1 次 CTE 替代 %d 次）",
        len(node_ids),
        total_loops,
        len(node_ids),
    )
    return mapping


async def batch_query_realtime_auto_rate(
    db: AsyncSession,
    node_to_loop_ids: dict[str, list[str]],
) -> dict[str, dict | None]:
    """批量查询多节点的实时自控率（3 次批量查询替代 3N 次）.

    Args:
        db: 异步数据库会话
        node_to_loop_ids: ``{node_id: [loop_id, ...]}`` 映射

    Returns:
        ``{node_id: realtime_auto_rate_dict | None}`` 映射

    设计依据：Phase 4 优化措施 2，将 N 次 ``query_realtime_auto_rate`` 合并为
    3 次批量查询 + 内存计算。
    """
    from app.models.loop import LoopTagMapping
    from app.models.loop_config import LoopModeMapping
    from app.models.tag import TagRegistry

    # 收集所有 loop_id（去重）
    all_loop_ids = list({lid for lids in node_to_loop_ids.values() for lid in lids})
    if not all_loop_ids:
        return dict.fromkeys(node_to_loop_ids)

    # 1. 批量查询投用定义
    mm_result = await db.execute(
        select(LoopModeMapping.loop_id, LoopModeMapping.mode_value).where(
            LoopModeMapping.loop_id.in_(all_loop_ids),
            LoopModeMapping.is_auto.is_(True),
        )
    )
    auto_mode_map: dict[str, set[int]] = {}
    for row in mm_result.all():
        auto_mode_map.setdefault(str(row.loop_id), set()).add(row.mode_value)

    # 2. 批量查询 MODE tag 映射
    mt_result = await db.execute(
        select(LoopTagMapping.loop_id, TagRegistry.tag_name)
        .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
        .where(
            LoopTagMapping.loop_id.in_(all_loop_ids),
            LoopTagMapping.tag_role == "MODE",
        )
    )
    mode_rows = mt_result.all()
    if not mode_rows:
        return dict.fromkeys(node_to_loop_ids)

    tag_names = [row.tag_name for row in mode_rows]
    # loop_id → tag_name 映射
    loop_tag_map: dict[str, str] = {str(row.loop_id): row.tag_name for row in mode_rows}

    # 3. 批量查询 tag_registry.current_value
    tag_result = await db.execute(
        select(TagRegistry.tag_name, TagRegistry.current_value).where(
            TagRegistry.tag_name.in_(tag_names)
        )
    )
    tag_mode_map: dict[str, float | None] = {
        row.tag_name: row.current_value for row in tag_result.all()
    }

    # 4. 按节点计算实时自控率
    DEFAULT_AUTO_MODES = {1, 2, 3}
    now = datetime.now(UTC)
    result_map: dict[str, dict | None] = {}

    for node_id, loop_ids in node_to_loop_ids.items():
        auto_count = 0
        valid_count = 0
        mode_counts: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

        for loop_id in loop_ids:
            tag_name = loop_tag_map.get(loop_id)
            if tag_name is None:
                continue
            mode_val = tag_mode_map.get(tag_name)
            if mode_val is None:
                continue
            try:
                mode_int = int(mode_val)
            except (ValueError, TypeError):
                continue
            valid_count += 1
            if mode_int in mode_counts:
                mode_counts[mode_int] += 1
            auto_modes = auto_mode_map.get(loop_id, DEFAULT_AUTO_MODES)
            if mode_int in auto_modes:
                auto_count += 1

        if valid_count == 0:
            result_map[node_id] = None
        else:
            rate = round(auto_count / valid_count * 100, 2)
            result_map[node_id] = {
                "rate": Decimal(str(rate)),
                "auto_count": auto_count,
                "manual_count": valid_count - auto_count,
                "total_count": valid_count,
                "mode_counts": mode_counts,
                "read_at": now.isoformat(),
            }

    logger.info(
        "[批量实时自控率] %d 节点, %d 回路（3 次查询替代 %d 次）",
        len(node_to_loop_ids),
        len(all_loop_ids),
        len(node_to_loop_ids) * 3,
    )
    return result_map


async def batch_query_loop_counts(
    db: AsyncSession,
    node_to_loop_ids: dict[str, list[str]],
    ts_start: datetime,
    ts_end: datetime,
) -> dict[str, dict]:
    """批量查询多节点的 excluded/inconclusive 回路计数（2 次分组查询替代 2N 次）.

    Args:
        db: 异步数据库会话
        node_to_loop_ids: ``{node_id: [loop_id, ...]}`` 映射
        ts_start: 时间窗起始
        ts_end: 时间窗结束

    Returns:
        ``{node_id: {excluded_loops, inconclusive_loops}}`` 映射

    设计依据：Phase 4 优化措施 3
    """
    all_loop_ids = list({lid for lids in node_to_loop_ids.values() for lid in lids})
    if not all_loop_ids:
        return {nid: {"excluded_loops": 0, "inconclusive_loops": 0} for nid in node_to_loop_ids}

    # 构造 loop_id → node_id 反向映射（一个 loop 可能属于多个 node 的后代）
    loop_to_nodes: dict[str, list[str]] = {}
    for node_id, loop_ids in node_to_loop_ids.items():
        for lid in loop_ids:
            loop_to_nodes.setdefault(lid, []).append(node_id)

    # 1. 批量查询 excluded loops（include_in_evaluation=False）
    ex_result = await db.execute(
        select(LoopLedger.id).where(
            LoopLedger.id.in_(all_loop_ids),
            LoopLedger.include_in_evaluation.is_(False),
        )
    )
    excluded_counts: dict[str, int] = dict.fromkeys(node_to_loop_ids, 0)
    for row in ex_result.all():
        lid = str(row.id)
        for nid in loop_to_nodes.get(lid, []):
            excluded_counts[nid] = excluded_counts.get(nid, 0) + 1

    # 2. 批量查询 inconclusive loops
    # 有 INCONCLUSIVE 快照但没有 SUCCESS 快照的回路
    # 使用两个子查询：先找有 SUCCESS 的 loop_ids，再找有 INCONCLUSIVE 但不在 SUCCESS 列表中的
    success_loops_result = await db.execute(
        select(KpiSnapshotHourly.loop_id)
        .where(
            KpiSnapshotHourly.loop_id.in_(all_loop_ids),
            KpiSnapshotHourly.ts_start >= ts_start,
            KpiSnapshotHourly.ts_start <= ts_end,
            KpiSnapshotHourly.status == "SUCCESS",
        )
        .distinct()
    )
    success_loop_ids = {str(row.loop_id) for row in success_loops_result.all()}

    ic_result = await db.execute(
        select(KpiSnapshotHourly.loop_id)
        .where(
            KpiSnapshotHourly.loop_id.in_(all_loop_ids),
            KpiSnapshotHourly.ts_start >= ts_start,
            KpiSnapshotHourly.ts_start <= ts_end,
            KpiSnapshotHourly.status == "INCONCLUSIVE",
        )
        .distinct()
    )
    inconclusive_counts: dict[str, int] = dict.fromkeys(node_to_loop_ids, 0)
    for row in ic_result.all():
        lid = str(row.loop_id)
        if lid not in success_loop_ids:
            for nid in loop_to_nodes.get(lid, []):
                inconclusive_counts[nid] = inconclusive_counts.get(nid, 0) + 1

    result = {
        nid: {
            "excluded_loops": excluded_counts.get(nid, 0),
            "inconclusive_loops": inconclusive_counts.get(nid, 0),
        }
        for nid in node_to_loop_ids
    }
    logger.info(
        "[批量回路计数] %d 节点, %d 回路（2 次查询替代 %d 次）",
        len(node_to_loop_ids),
        len(all_loop_ids),
        len(node_to_loop_ids) * 2,
    )
    return result


async def aggregate_node_snapshot_with_presets(
    db: AsyncSession,
    plant_node_id: str,
    ts_start: datetime,
    ts_end: datetime,
    loop_ids: list[str],
    realtime_result: dict | None,
    counts: dict,
) -> dict | None:
    """聚合节点快照（使用预加载数据，避免重复 DB 查询）.

    与 ``aggregate_node_snapshot`` 的区别：跳过 collect_descendant_loop_ids /
    query_realtime_auto_rate / excluded/inconclusive count 查询，
    直接使用传入的预加载数据。仅保留 1 次主聚合 SQL 查询。

    Args:
        db: 异步数据库会话
        plant_node_id: 节点 ID
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        loop_ids: 预加载的下属回路 ID 列表
        realtime_result: 预加载的实时自控率结果（None 表示无数据）
        counts: 预加载的计数 ``{excluded_loops, inconclusive_loops}``

    Returns:
        节点级快照字典，无数据返回 None
    """
    if not loop_ids:
        logger.debug("[节点级聚合-批量] plant_node_id=%s 无下属回路", plant_node_id)
        return None

    # P4 S3：复杂组去重 + Python 加权聚合（与 aggregate_node_snapshot 共用）
    agg = await _fetch_and_aggregate_loops(db, loop_ids, ts_start, ts_end)
    if agg is None:
        logger.debug(
            "[节点级聚合-批量] plant_node_id=%s, 时间窗 %s~%s 无 SUCCESS 快照",
            plant_node_id,
            ts_start,
            ts_end,
        )
        return None

    score_avg = agg["score"]
    auto_loop_count_val = agg["auto_loop_count"]

    status = _score_to_status(score_avg)

    realtime_auto_rate = realtime_result["rate"] if realtime_result else None

    # P4 S3：loop_count 为去重后回路组数
    total_loops_count = len(loop_ids)
    evaluated_loops_count = agg["loop_count"]
    excluded_loops_count = counts.get("excluded_loops", 0)
    inconclusive_loops_count = counts.get("inconclusive_loops", 0)

    logger.info(
        "[节点级聚合-批量] plant_node_id=%s, 回路数=%d, 投自动回路数=%d, "
        "投自动占比=%.2f%%, 实时自控率=%s, 加权综合评分=%s, 定级=%s",
        plant_node_id,
        agg["loop_count"],
        auto_loop_count_val,
        agg["auto_loop_ratio"],
        realtime_auto_rate,
        score_avg,
        status,
    )

    return {
        "plant_node_id": plant_node_id,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "score": score_avg,
        "good_value_rate": agg["good_value_rate"],
        "auto_mode_rate": agg["auto_mode_rate"],
        "effective_auto_rate": agg["effective_auto_rate"],
        "steady_rate": agg["steady_rate"],
        "accuracy_rate": agg["accuracy_rate"],
        "fast_rate": agg["fast_rate"],
        "oscillation_rate": agg["oscillation_rate"],
        "saturation_rate": agg["saturation_rate"],
        "instrument_fault_rate": agg["instrument_fault_rate"],
        "stiction_index": agg["stiction_index"],
        "settling_time": agg["settling_time"],
        "output_trip_index": agg["output_trip_index"],
        "ideal_settling_time": agg["ideal_settling_time"],
        "auto_loop_ratio": agg["auto_loop_ratio"],
        "realtime_auto_rate": realtime_auto_rate,
        "loop_count": agg["loop_count"],
        "status": status,
        "algorithm_version": ALGORITHM_VERSION,
        "total_loops": total_loops_count,
        "evaluated_loops": evaluated_loops_count,
        "inconclusive_loops": inconclusive_loops_count,
        "excluded_loops": excluded_loops_count,
        "unit_status": "PARTIAL" if inconclusive_loops_count > 0 else "SUCCESS",
    }


async def batch_calculate_and_save_node_snapshots(
    nodes: list,
    ts_start: datetime,
    ts_end: datetime,
    concurrency: int = 10,
) -> dict:
    """批量聚合并保存多节点快照（预加载 + 并发处理）.

    Phase 4 优化入口：将 N 个节点的聚合流程从 ~9N 次 DB 查询优化为
    ~6 次批量查询 + 3N 次单节点查询（主聚合 SQL + 保存），并通过
    asyncio.Semaphore 并发处理。

    Args:
        nodes: PlantNode 对象列表（is_kpi_enabled=True）
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        concurrency: 最大并发数

    Returns:
        ``{total, success, skipped, failed, ts_start, ts_end}``
    """
    import asyncio

    from app.core.db import AsyncSessionLocal

    if not nodes:
        return {"total": 0, "success": 0, "skipped": 0, "failed": 0}

    node_ids = [str(n.id) for n in nodes]

    # Phase 1: 批量预加载（共享 session）
    async with AsyncSessionLocal() as db:
        # 措施 1：批量树遍历
        node_to_loop_ids = await batch_collect_descendant_loop_ids(db, node_ids)

        # 措施 2：批量实时自控率
        node_to_realtime = await batch_query_realtime_auto_rate(db, node_to_loop_ids)

        # 措施 3：批量回路计数
        node_to_counts = await batch_query_loop_counts(db, node_to_loop_ids, ts_start, ts_end)

    logger.info(
        "[批量节点聚合] 预加载完成: %d 节点, 开始并发聚合（并发数=%d）",
        len(nodes),
        concurrency,
    )

    # Phase 2: 并发聚合 + 保存（每节点独立 session）
    sem = asyncio.Semaphore(concurrency)

    async def _process_node(node) -> dict | None:
        async with sem:
            node_id = str(node.id)
            loop_ids = node_to_loop_ids.get(node_id, [])
            realtime_result = node_to_realtime.get(node_id)
            counts = node_to_counts.get(node_id, {"excluded_loops": 0, "inconclusive_loops": 0})

            if not loop_ids:
                logger.debug("[批量节点聚合] 节点 %s 无下属回路，跳过", node.name)
                return None

            async with AsyncSessionLocal() as worker_db:
                try:
                    snap_data = await aggregate_node_snapshot_with_presets(
                        db=worker_db,
                        plant_node_id=node_id,
                        ts_start=ts_start,
                        ts_end=ts_end,
                        loop_ids=loop_ids,
                        realtime_result=realtime_result,
                        counts=counts,
                    )
                    if snap_data is None:
                        return None
                    await save_node_snapshot(worker_db, snap_data)
                    await worker_db.commit()
                    return snap_data
                except Exception:
                    await worker_db.rollback()
                    raise

    tasks = [asyncio.create_task(_process_node(node)) for node in nodes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = 0
    skipped_count = 0
    failed_count = 0
    for r in results:
        if isinstance(r, Exception):
            failed_count += 1
            logger.warning("[批量节点聚合] 节点聚合失败: %s", r)
        elif r is None:
            skipped_count += 1
        else:
            success_count += 1

    return {
        "total": len(nodes),
        "success": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }


__all__ = [
    "aggregate_node_snapshot",
    "aggregate_node_snapshot_with_presets",
    "batch_calculate_and_save_node_snapshots",
    "batch_collect_descendant_loop_ids",
    "batch_query_loop_counts",
    "batch_query_realtime_auto_rate",
    "calculate_and_save_node_snapshot",
    "collect_descendant_loop_ids",
    "get_node_latest_snapshot",
    "get_node_monitor_data",
    "get_node_ranking",
    "get_node_trend",
    "get_nodes_overview",
    "query_realtime_auto_rate",
    "save_node_snapshot",
]
