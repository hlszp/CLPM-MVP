"""Node-level daily/monthly aggregation service (GB/T 44693.2-2024 §6.4).

按 loop_count 加权聚合节点级小时/日快照，生成日级/月级聚合快照。

聚合规则：
- 取该节点当天/当月所有 kpi_node_snapshot_hourly / kpi_node_snapshot_daily 记录
- 按 loop_count 加权平均各 KPI 字段
- realtime_auto_rate 取当天/当月最后一次小时快照的值（非聚合）
- loop_count 取最大值（反映节点规模）
- status 由聚合后 score 重新定级（_score_to_status）
- 幂等：相同 plant_node_id + stat_date / stat_month 不重复写入（先删后建）

权重体系说明（P2 #28 R4 修复时澄清）：

本模块的"日/月聚合"与 node_performance.py 的"小时聚合"使用**不同的权重体系**，
这是有意为之的设计选择，并非缺陷：

1. **小时聚合**（node_performance.py:aggregate_node_snapshot）：
   - 数据流：回路级快照 → 节点级小时快照
   - 权重：LoopLevelWeight（按回路级别 1:3, 2:2, 3:1）
   - 依据：FDS §5.3.7 "装置级聚合评分" + GB/T 44693.2-2024 附录 E.2
   - 目的：让重要回路（level=1）在节点级评分中占更高权重

2. **日/月聚合**（本模块）：
   - 数据流：节点级小时快照 → 节点级日/月快照
   - 权重：loop_count（每小时/日参与聚合的回路数）
   - 目的：让回路数多的小时/日（代表性更强）在日/月聚合中占更高权重
   - 结构性约束：KpiNodeSnapshotHourly/Daily 表不含 level 字段，
     节点级快照已无回路维度，无法再按 LoopLevelWeight 加权

两套权重体系处理不同维度的聚合：小时聚合按"回路重要性"，日/月聚合按"节点规模"。
项目记忆约束 "Plant-level KPI aggregation must use weighted average based on
loop importance levels" 仅适用于回路 → 节点的小时聚合（plant-level），不覆盖日/月层。

核心函数：
- aggregate_daily_snapshot: 单节点日聚合
- aggregate_monthly_snapshot: 单节点月聚合
- aggregate_all_nodes_daily: 全节点批量日聚合
- aggregate_all_nodes_monthly: 全节点批量月聚合
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.data_types import DataLineage, MetricResult
from app.models.node_kpi import (
    KpiNodeSnapshotDaily,
    KpiNodeSnapshotHourly,
    KpiNodeSnapshotMonthly,
)
from app.models.plant_node import PlantNode
from app.services.performance import _score_to_status

logger = logging.getLogger(__name__)

# 参与加权聚合的 KPI 字段（与小时快照对齐，realtime_auto_rate 单独处理）
AGGREGATE_FIELDS = (
    "score",
    "good_value_rate",
    "auto_mode_rate",
    "effective_auto_rate",
    "steady_rate",
    "accuracy_rate",
    "fast_response_rate",
    "oscillation_rate",
    "saturation_rate",
    "auto_loop_ratio",
)

# 算法版本号（与 node_performance.py 对齐，复用回路级版本）
ALGORITHM_VERSION = "KPI_CALC_v2.0"


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _weighted_average(snaps: list, fields: tuple[str, ...]) -> dict[str, Decimal | None]:
    """按 loop_count 加权平均指定字段。

    Args:
        snaps: 快照对象列表（需有 loop_count 属性和 fields 中的字段）
        fields: 需要加权平均的字段名元组

    Returns:
        {field: Decimal | None} — 加权平均值；所有源值都为 None 时返回 None

    说明：
        - 分母仅累加有值（非 None）快照的 loop_count，避免 None 值稀释加权结果
        - 所有 loop_count=0 时退化为简单平均（避免除零）
    """
    result: dict[str, Decimal | None] = {}
    for f in fields:
        weighted_sum = Decimal("0")
        weight_total = Decimal("0")
        has_value = False
        for s in snaps:
            val = getattr(s, f)
            if val is None:
                continue
            has_value = True
            lc = Decimal(str(getattr(s, "loop_count", 0) or 0))
            weighted_sum += Decimal(str(val)) * lc
            weight_total += lc

        if not has_value:
            result[f] = None
        elif weight_total == 0:
            # 所有 loop_count=0，退化为简单平均
            non_none = [getattr(s, f) for s in snaps if getattr(s, f) is not None]
            avg = sum(Decimal(str(v)) for v in non_none) / Decimal(len(non_none))
            result[f] = avg.quantize(Decimal("0.01"))
        else:
            result[f] = (weighted_sum / weight_total).quantize(Decimal("0.01"))
    return result


def _max_loop_count(snaps: list) -> int:
    """取快照列表中最大的 loop_count。"""
    return max((getattr(s, "loop_count", 0) or 0) for s in snaps) if snaps else 0


def _latest_algorithm_version(snaps: list) -> str | None:
    """取快照列表中最后一条的 algorithm_version。"""
    if not snaps:
        return None
    return getattr(snaps[-1], "algorithm_version", None)


# ---------------------------------------------------------------------------
# 日级聚合
# ---------------------------------------------------------------------------


async def aggregate_daily_snapshot(
    db: AsyncSession,
    plant_node_id: str,
    stat_date,
) -> dict | None:
    """聚合指定节点某天的所有小时快照，生成日级快照。

    Args:
        db: 异步数据库会话
        plant_node_id: 工厂节点 ID
        stat_date: 统计日期（date 或 datetime）

    Returns:
        日级快照字典，无数据时返回 None
    """
    # 规范化 stat_date 为 date 类型
    if isinstance(stat_date, datetime):
        stat_date = stat_date.date()
    elif hasattr(stat_date, "year") and hasattr(stat_date, "month") and hasattr(stat_date, "day"):
        stat_date = stat_date  # 已是 date

    # 时间窗：[stat_date 00:00, stat_date+1 00:00)
    start_dt = datetime.combine(stat_date, time.min)
    end_dt = start_dt + timedelta(days=1)

    # 查询当天所有小时快照（按 ts_start 升序，便于取最后一条）
    result = await db.execute(
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
            KpiNodeSnapshotHourly.ts_start >= start_dt,
            KpiNodeSnapshotHourly.ts_start < end_dt,
        )
        .order_by(KpiNodeSnapshotHourly.ts_start.asc())
    )
    hourly_snaps = list(result.scalars().all())

    if not hourly_snaps:
        logger.debug(
            "[日聚合] plant_node_id=%s, stat_date=%s 无小时快照",
            plant_node_id,
            stat_date,
        )
        return None

    # 按 loop_count 加权平均
    agg = _weighted_average(hourly_snaps, AGGREGATE_FIELDS)

    # realtime_auto_rate 取当天最后一次小时快照的值（非聚合）
    realtime_auto_rate = hourly_snaps[-1].realtime_auto_rate

    # loop_count 取最大值
    loop_count = _max_loop_count(hourly_snaps)

    # algorithm_version 取最后一条
    algorithm_version = _latest_algorithm_version(hourly_snaps) or ALGORITHM_VERSION

    # status 由聚合后 score 重新定级
    status = _score_to_status(agg.get("score"))

    snap_data = {
        "plant_node_id": plant_node_id,
        "stat_date": stat_date,
        "score": agg.get("score"),
        "good_value_rate": agg.get("good_value_rate"),
        "auto_mode_rate": agg.get("auto_mode_rate"),
        "effective_auto_rate": agg.get("effective_auto_rate"),
        "steady_rate": agg.get("steady_rate"),
        "accuracy_rate": agg.get("accuracy_rate"),
        "fast_response_rate": agg.get("fast_response_rate"),
        "oscillation_rate": agg.get("oscillation_rate"),
        "saturation_rate": agg.get("saturation_rate"),
        "auto_loop_ratio": agg.get("auto_loop_ratio"),
        "realtime_auto_rate": realtime_auto_rate,
        "loop_count": loop_count,
        "status": status,
        "algorithm_version": algorithm_version,
    }

    saved = await _save_daily_snapshot(db, snap_data)
    logger.info(
        "[日聚合] plant_node_id=%s, stat_date=%s, 小时快照数=%d, "
        "loop_count=%d, score=%s, status=%s",
        plant_node_id,
        stat_date,
        len(hourly_snaps),
        loop_count,
        snap_data["score"],
        status,
    )
    return saved


async def _save_daily_snapshot(db: AsyncSession, snap_data: dict) -> dict:
    """保存日级快照（幂等：相同 plant_node_id + stat_date 覆盖更新）。"""
    plant_node_id = snap_data["plant_node_id"]
    stat_date = snap_data["stat_date"]

    existing_result = await db.execute(
        select(KpiNodeSnapshotDaily).where(
            KpiNodeSnapshotDaily.plant_node_id == plant_node_id,
            KpiNodeSnapshotDaily.stat_date == stat_date,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        for key, val in snap_data.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        existing.created_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
        logger.debug(
            "[日快照] 覆盖更新 plant_node_id=%s, stat_date=%s",
            plant_node_id,
            stat_date,
        )
    else:
        snap = KpiNodeSnapshotDaily(
            id=str(uuid4()),
            **snap_data,
        )
        db.add(snap)
        await db.flush()
        logger.debug(
            "[日快照] 新增 plant_node_id=%s, stat_date=%s",
            plant_node_id,
            stat_date,
        )

    return snap_data


# ---------------------------------------------------------------------------
# 月级聚合
# ---------------------------------------------------------------------------


async def aggregate_monthly_snapshot(
    db: AsyncSession,
    plant_node_id: str,
    stat_month,
) -> dict | None:
    """聚合指定节点某月的所有日快照，生成月级快照。

    Args:
        db: 异步数据库会话
        plant_node_id: 工厂节点 ID
        stat_month: 统计月份（date 或 datetime，月初）

    Returns:
        月级快照字典，无数据时返回 None
    """
    # 规范化 stat_month 为月初 date
    if isinstance(stat_month, datetime):
        stat_month = stat_month.date().replace(day=1)
    elif hasattr(stat_month, "replace"):
        stat_month = stat_month.replace(day=1)

    # 时间窗：[stat_month, 下个月初)
    if stat_month.month == 12:
        next_month = stat_month.replace(year=stat_month.year + 1, month=1, day=1)
    else:
        next_month = stat_month.replace(month=stat_month.month + 1, day=1)

    # 查询当月所有日快照（按 stat_date 升序）
    result = await db.execute(
        select(KpiNodeSnapshotDaily)
        .where(
            KpiNodeSnapshotDaily.plant_node_id == plant_node_id,
            KpiNodeSnapshotDaily.stat_date >= stat_month,
            KpiNodeSnapshotDaily.stat_date < next_month,
        )
        .order_by(KpiNodeSnapshotDaily.stat_date.asc())
    )
    daily_snaps = list(result.scalars().all())

    if not daily_snaps:
        logger.debug(
            "[月聚合] plant_node_id=%s, stat_month=%s 无日快照",
            plant_node_id,
            stat_month,
        )
        return None

    # 按 loop_count 加权平均
    agg = _weighted_average(daily_snaps, AGGREGATE_FIELDS)

    # realtime_auto_rate 取当月最后一次小时快照的值（非聚合）
    # 查询当月最后一条小时快照
    hourly_result = await db.execute(
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
            KpiNodeSnapshotHourly.ts_start >= datetime.combine(stat_month, time.min),
            KpiNodeSnapshotHourly.ts_start < datetime.combine(next_month, time.min),
        )
        .order_by(KpiNodeSnapshotHourly.ts_start.desc())
        .limit(1)
    )
    last_hourly = hourly_result.scalar_one_or_none()
    realtime_auto_rate = last_hourly.realtime_auto_rate if last_hourly else None

    # loop_count 取最大值
    loop_count = _max_loop_count(daily_snaps)

    # algorithm_version 取最后一条
    algorithm_version = _latest_algorithm_version(daily_snaps) or ALGORITHM_VERSION

    # status 由聚合后 score 重新定级
    status = _score_to_status(agg.get("score"))

    snap_data = {
        "plant_node_id": plant_node_id,
        "stat_month": stat_month,
        "score": agg.get("score"),
        "good_value_rate": agg.get("good_value_rate"),
        "auto_mode_rate": agg.get("auto_mode_rate"),
        "effective_auto_rate": agg.get("effective_auto_rate"),
        "steady_rate": agg.get("steady_rate"),
        "accuracy_rate": agg.get("accuracy_rate"),
        "fast_response_rate": agg.get("fast_response_rate"),
        "oscillation_rate": agg.get("oscillation_rate"),
        "saturation_rate": agg.get("saturation_rate"),
        "auto_loop_ratio": agg.get("auto_loop_ratio"),
        "realtime_auto_rate": realtime_auto_rate,
        "loop_count": loop_count,
        "status": status,
        "algorithm_version": algorithm_version,
    }

    saved = await _save_monthly_snapshot(db, snap_data)
    logger.info(
        "[月聚合] plant_node_id=%s, stat_month=%s, 日快照数=%d, loop_count=%d, score=%s, status=%s",
        plant_node_id,
        stat_month,
        len(daily_snaps),
        loop_count,
        snap_data["score"],
        status,
    )
    return saved


async def _save_monthly_snapshot(db: AsyncSession, snap_data: dict) -> dict:
    """保存月级快照（幂等：相同 plant_node_id + stat_month 覆盖更新）。"""
    plant_node_id = snap_data["plant_node_id"]
    stat_month = snap_data["stat_month"]

    existing_result = await db.execute(
        select(KpiNodeSnapshotMonthly).where(
            KpiNodeSnapshotMonthly.plant_node_id == plant_node_id,
            KpiNodeSnapshotMonthly.stat_month == stat_month,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        for key, val in snap_data.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        existing.created_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
        logger.debug(
            "[月快照] 覆盖更新 plant_node_id=%s, stat_month=%s",
            plant_node_id,
            stat_month,
        )
    else:
        snap = KpiNodeSnapshotMonthly(
            id=str(uuid4()),
            **snap_data,
        )
        db.add(snap)
        await db.flush()
        logger.debug(
            "[月快照] 新增 plant_node_id=%s, stat_month=%s",
            plant_node_id,
            stat_month,
        )

    return snap_data


# ---------------------------------------------------------------------------
# 批量聚合（遍历所有 is_kpi_enabled 节点）
# ---------------------------------------------------------------------------


async def aggregate_all_nodes_daily(stat_date) -> dict:
    """遍历所有 is_kpi_enabled 节点，批量执行日聚合。

    Args:
        stat_date: 统计日期（date 或 datetime）

    Returns:
        汇总结果 {total, success, skipped, failed, stat_date}
    """
    from app.core.db import AsyncSessionLocal

    if isinstance(stat_date, datetime):
        stat_date = stat_date.date()

    async with AsyncSessionLocal() as db:
        node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
        nodes = list(node_result.scalars().all())

        if not nodes:
            logger.info("[批量日聚合] 无启用 KPI 评估的节点，跳过")
            return {
                "total": 0,
                "success": 0,
                "skipped": 0,
                "failed": 0,
                "stat_date": str(stat_date),
            }

        logger.info("[批量日聚合] 待聚合节点数: %d, stat_date=%s", len(nodes), stat_date)

        success_count = 0
        skipped_count = 0
        failed_count = 0
        for node in nodes:
            try:
                snap = await aggregate_daily_snapshot(
                    db=db,
                    plant_node_id=str(node.id),
                    stat_date=stat_date,
                )
                if snap is None:
                    skipped_count += 1
                    logger.debug("[批量日聚合] 节点 %s 无数据，跳过", node.name)
                else:
                    success_count += 1
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                logger.warning("[批量日聚合] 节点 %s 聚合失败: %s", node.name, exc)

        await db.commit()

    return {
        "total": len(nodes),
        "success": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "stat_date": str(stat_date),
    }


async def aggregate_all_nodes_monthly(stat_month) -> dict:
    """遍历所有 is_kpi_enabled 节点，批量执行月聚合。

    Args:
        stat_month: 统计月份（date 或 datetime，月初）

    Returns:
        汇总结果 {total, success, skipped, failed, stat_month}
    """
    from app.core.db import AsyncSessionLocal

    if isinstance(stat_month, datetime):
        stat_month = stat_month.date().replace(day=1)
    elif hasattr(stat_month, "replace"):
        stat_month = stat_month.replace(day=1)

    async with AsyncSessionLocal() as db:
        node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
        nodes = list(node_result.scalars().all())

        if not nodes:
            logger.info("[批量月聚合] 无启用 KPI 评估的节点，跳过")
            return {
                "total": 0,
                "success": 0,
                "skipped": 0,
                "failed": 0,
                "stat_month": str(stat_month),
            }

        logger.info("[批量月聚合] 待聚合节点数: %d, stat_month=%s", len(nodes), stat_month)

        success_count = 0
        skipped_count = 0
        failed_count = 0
        for node in nodes:
            try:
                snap = await aggregate_monthly_snapshot(
                    db=db,
                    plant_node_id=str(node.id),
                    stat_month=stat_month,
                )
                if snap is None:
                    skipped_count += 1
                    logger.debug("[批量月聚合] 节点 %s 无数据，跳过", node.name)
                else:
                    success_count += 1
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                logger.warning("[批量月聚合] 节点 %s 聚合失败: %s", node.name, exc)

        await db.commit()

    return {
        "total": len(nodes),
        "success": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "stat_month": str(stat_month),
    }


# ---------------------------------------------------------------------------
# 内存级回路聚合（Phase 3 任务 3.6）
# ---------------------------------------------------------------------------


class NodeAggregator:
    """节点级回路聚合器（算法说明 §4.11）.

    将回路级 MetricResult 按级别权重聚合为节点级 MetricResult。
    与 ``aggregate_daily_snapshot`` / ``aggregate_monthly_snapshot`` 的区别：
    后两者基于 DB 快照按 loop_count 加权做日/月聚合；本类在内存中
    按回路级别权重（1/2/3 → 3/2/1）做实时聚合，供编排层直接调用。

    设计依据：算法说明 §4.11；GB/T 44693.2-2024 附录 E.2

    聚合公式：
        Score_unit = Σ(w_i^level · Score_i) / Σ(w_i^level)

    回路级别权重（GB/T 44693.2-2024 附录 E.2）：
        - 一级（level=1）：权重 3（对装置整体性能/安全/经济/环保具决定性影响）
        - 二级（level=2）：权重 2（对装置运行稳定性或主要设备安全有较大影响）
        - 三级（level=3）：权重 1（相对次要的辅助控制回路）

    INCONCLUSIVE 回路处理：
        value=None 或 confidence_level='E' 的回路不参与聚合，单独统计数量。
    """

    #: 回路级别 → 权重映射（level 1→3, 2→2, 3→1）
    LEVEL_WEIGHTS: dict[int, int] = {1: 3, 2: 2, 3: 1}

    #: 默认级别（未指定时）
    DEFAULT_LEVEL = 3

    def aggregate(
        self,
        loop_scores: list[MetricResult],
        loop_weights: dict[str, int] | None = None,
    ) -> MetricResult:
        """聚合回路级评分为节点级评分.

        Args:
            loop_scores: 回路级指标结果列表（metric_code 应一致，通常为 composite_score）
            loop_weights: ``{loop_id: level}`` 映射，level 为 1/2/3；
                None 时所有回路按默认级别 3 处理

        Returns:
            节点级 MetricResult：
                - 所有回路 INCONCLUSIVE → value=None, confidence_level='E'
                - 正常 → value=round2(加权平均), confidence_level 取最低

        设计依据：算法说明 §4.11.2, §4.11.3
        """
        loop_weights = loop_weights or {}
        valid_results: list[tuple[MetricResult, int]] = []
        inconclusive_count = 0

        for result in loop_scores:
            # INCONCLUSIVE 判定：value=None 或 confidence_level='E'
            if result.value is None or result.confidence_level == "E":
                inconclusive_count += 1
                logger.debug(
                    "[节点聚合] 跳过 INCONCLUSIVE 回路: confidence=%s",
                    result.confidence_level,
                )
                continue
            # 回路级别（从 details.loop_level 读取，或从 loop_weights 读取）
            level = self._resolve_level(result, loop_weights)
            weight = self.LEVEL_WEIGHTS.get(level, self.LEVEL_WEIGHTS[self.DEFAULT_LEVEL])
            valid_results.append((result, weight))

        total_loops = len(loop_scores)
        logger.debug(
            "[节点聚合] total=%d, valid=%d, inconclusive=%d",
            total_loops,
            len(valid_results),
            inconclusive_count,
        )

        # 所有回路 INCONCLUSIVE → 节点评分留空
        if not valid_results:
            return MetricResult(
                metric_code="composite_score",
                value=None,
                confidence_level="E",
                lineage=DataLineage(algorithm_version=ALGORITHM_VERSION),
                details={
                    "reason": "all_loops_inconclusive",
                    "total_loops": total_loops,
                    "inconclusive_count": inconclusive_count,
                },
            )

        # 加权平均：Score_unit = Σ(w_i · Score_i) / Σ(w_i)
        weighted_sum = sum(r.value * w for r, w in valid_results)
        weight_total = sum(w for _, w in valid_results)
        node_score = weighted_sum / weight_total if weight_total > 0 else 0.0
        node_score = max(0.0, min(100.0, node_score))
        node_score = round(node_score, 2)

        # 可信度取有效回路中最低等级
        confidence = self._min_confidence([r for r, _ in valid_results])

        # 血缘取第一条有效回路（若有）
        lineage = valid_results[0][0].lineage

        logger.debug(
            "[节点聚合] node_score=%.2f, confidence=%s, weight_total=%d",
            node_score,
            confidence,
            weight_total,
        )

        return MetricResult(
            metric_code="composite_score",
            value=node_score,
            confidence_level=confidence,
            lineage=lineage,
            details={
                "total_loops": total_loops,
                "valid_loops": len(valid_results),
                "inconclusive_count": inconclusive_count,
                "weight_total": weight_total,
            },
        )

    def _resolve_level(self, result: MetricResult, loop_weights: dict[str, int]) -> int:
        """解析回路级别.

        优先级：loop_weights[loop_id] > result.details.loop_level > DEFAULT_LEVEL
        """
        # 从 loop_weights 读取（需 loop_id，但 MetricResult 无 loop_id 字段）
        # MetricResult.details 中可能存有 loop_id
        loop_id = result.details.get("loop_id") if result.details else None
        if loop_id and loop_id in loop_weights:
            return loop_weights[loop_id]
        # 从 details.loop_level 读取
        if result.details:
            level = result.details.get("loop_level")
            if level is not None:
                try:
                    return int(level)
                except (TypeError, ValueError):
                    pass
        return self.DEFAULT_LEVEL

    @staticmethod
    def _min_confidence(results: list[MetricResult]) -> str:
        """取结果列表中最低的可信度等级（A 最高，E 最低）."""
        order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
        worst = "A"
        worst_rank = 0
        for r in results:
            rank = order.get(r.confidence_level, 4)
            if rank > worst_rank:
                worst_rank = rank
                worst = r.confidence_level
        return worst


__all__ = [
    "AGGREGATE_FIELDS",
    "NodeAggregator",
    "aggregate_all_nodes_daily",
    "aggregate_all_nodes_monthly",
    "aggregate_daily_snapshot",
    "aggregate_monthly_snapshot",
]
