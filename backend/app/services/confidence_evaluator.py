"""指标可信度评估器与综合评分计算（Phase 3 任务 3.3-3.4）.

提供：
1. 基于有效数据率（valid_rate）判定指标可信度等级 A/B/C/D/E（算法说明 §3.7.2）。
2. 构建数据血缘 DataLineage（算法说明 §3.7.1，8 字段）。
3. 综合评分 v2 计算：P = (A·a + F·f + S·s)/(a+f+s) × R（算法说明 §4.10）。

设计依据：算法说明 §3.7.1, §3.7.2, §4.10；GB/T 44693.2-2024 附录 B.6
"""

from __future__ import annotations

import logging

from app.contracts.data_types import (
    ConfidenceLevel,
    DataLineage,
    MetricDataBundle,
    MetricResult,
)

logger = logging.getLogger(__name__)

#: 算法版本号（与 kpi_calc / node_aggregation 对齐）
ALGORITHM_VERSION = "KPI_CALC_v2.0"

#: 质量策略标识（与预处理 Pipeline 对齐）
QUALITY_POLICY = "KEEP_ALL_WITH_VALIDITY"

#: 参与综合评分的核心指标代码
CORE_METRIC_CODES: tuple[str, ...] = (
    "accuracy_rate",
    "fast_rate",
    "stability_rate",
)

#: 折扣因子指标代码
DISCOUNT_METRIC_CODE = "effective_auto_rate"

#: 默认权重（无配置时回退，对齐国标附录 C 稳定型 STABLE 0.2/0.3/0.5）
#: v2.1 修正（对齐 FDS v5.1 §5.3.7.1 / 算法 v2.1 §4.10.3）：
#: 原 v2.0 权重 0.25/0.20/0.55 与国标默认值不一致，已修正为国标稳定型默认值。
#: 四套控制类型权重模板：
#:   - STABLE（稳定型）: a=0.2, f=0.3, s=0.5  —— 温度、压力控制
#:   - SLOW（慢速型）:   a=0.3, f=0.1, s=0.6  —— 缓慢调节回路
#:   - FAST（快速型）:   a=0.2, f=0.5, s=0.3  —— 副回路、流量控制
#:   - LOGIC（逻辑型）:  a=0.0, f=0.4, s=0.6  —— 防回流、防超温
#: effective_auto_rate 作为折扣因子 R 不参与权重分配。
DEFAULT_WEIGHTS: dict[str, float] = {
    "accuracy_rate": 0.2,
    "fast_rate": 0.3,
    "stability_rate": 0.5,
}


class ConfidenceEvaluator:
    """指标可信度评估器.

    提供可信度等级判定、数据血缘构建、综合评分计算三类能力。
    所有指标计算器与编排层共享本类的静态方法，保证可信度判定一致性。

    设计依据：算法说明 §3.7.1, §3.7.2, §4.10
    """

    @staticmethod
    def evaluate(valid_rate: float) -> ConfidenceLevel:
        """根据有效数据率判定可信度等级（算法说明 §3.7.2）.

        Args:
            valid_rate: 有效数据率 0~1

        Returns:
            可信度等级枚举 A/B/C/D/E

        等级阈值：
            - A: valid_rate >= 0.95
            - B: 0.80 <= valid_rate < 0.95
            - C: 0.60 <= valid_rate < 0.80
            - D: 0.20 <= valid_rate < 0.60
            - E: valid_rate < 0.20 → INCONCLUSIVE
        """
        if valid_rate >= 0.95:
            return ConfidenceLevel.A
        if valid_rate >= 0.80:
            return ConfidenceLevel.B
        if valid_rate >= 0.60:
            return ConfidenceLevel.C
        if valid_rate >= 0.20:
            return ConfidenceLevel.D
        return ConfidenceLevel.E

    @staticmethod
    def build_lineage(
        bundle: MetricDataBundle,
        valid_rate: float,
        algorithm_version: str = ALGORITHM_VERSION,
    ) -> DataLineage:
        """构建数据血缘（算法说明 §3.7.1，8 字段）.

        Args:
            bundle: 指标数据包
            valid_rate: 有效数据率 0~1
            algorithm_version: 算法版本号

        Returns:
            DataLineage 对象，含采样频率/聚合策略/质量策略/tagGroup/
            data_block_ids/valid_rate/预处理版本/算法版本
        """
        block = bundle.data_block
        return DataLineage(
            sampling_freq=block.sampling_freq,
            aggregation_policy="LAST",
            quality_policy=QUALITY_POLICY,
            tag_group=block.tag_group,
            data_block_ids=[block.data_block_id],
            valid_rate=round(valid_rate, 4),
            data_policy_version=block.preprocess_version,
            algorithm_version=algorithm_version,
        )

    @staticmethod
    def compute_composite_score(
        metric_results: dict[str, MetricResult],
        weights: dict[str, float] | None = None,
    ) -> MetricResult:
        """计算综合评分 P = (A·a + F·f + S·s)/(a+f+s) × R（算法说明 §4.10）.

        Args:
            metric_results: 各指标结果字典 ``{metric_code: MetricResult}``，
                至少含 accuracy_rate/fast_rate/stability_rate/effective_auto_rate
            weights: 权重配置 ``{accuracy_rate: a, fast_rate: f, stability_rate: s}``；
                None 时使用默认权重

        Returns:
            综合评分 MetricResult：
                - 有效自控率为 E 级（INCONCLUSIVE）→ value=None
                - 所有权重为 0 → value=0.0
                - 正常 → value=round2(基础评分 × R/100)

        设计依据：算法说明 §4.10.2, GB/T 44693.2-2024 附录 B.6

        缺失指标处理：
            核心指标缺失（value=None）时按 0 计入分子，但其权重仍计入分母
            （对齐 kpi_calc._compute_composite_score_v2 实现）。
        """
        w = weights if weights else DEFAULT_WEIGHTS
        a = float(w.get("accuracy_rate", 0.0))
        f = float(w.get("fast_rate", 0.0))
        s = float(w.get("stability_rate", 0.0))

        # 折扣因子 R
        r_result = metric_results.get(DISCOUNT_METRIC_CODE)
        r_value = r_result.value if r_result else None

        logger.info(
            "[综合评分] 输入: A=%s, F=%s, S=%s, R=%s, weights(a=%.3f, f=%.3f, s=%.3f)",
            metric_results.get("accuracy_rate").value
            if metric_results.get("accuracy_rate")
            else None,
            metric_results.get("fast_rate").value if metric_results.get("fast_rate") else None,
            metric_results.get("stability_rate").value
            if metric_results.get("stability_rate")
            else None,
            r_value,
            a,
            f,
            s,
        )

        # R 缺失或可信度 E 级 → 评分留空（INCONCLUSIVE）
        # P1 #18: 移除原"R 缺失降级 60%"无依据逻辑，统一视为 INCONCLUSIVE
        if (
            r_result is None
            or r_result.value is None
            or r_result.confidence_level == ConfidenceLevel.E.value
        ):
            logger.info(
                "[综合评分] R 缺失或可信度 E 级，评分留空（INCONCLUSIVE）: r_result=%s",
                "None"
                if r_result is None
                else f"value={r_result.value}, conf={r_result.confidence_level}",
            )
            if r_result and r_result.lineage:
                lineage = r_result.lineage
            else:
                # R 缺失时回退到 accuracy_rate 的血缘
                acc_result = metric_results.get("accuracy_rate")
                lineage = (
                    acc_result.lineage
                    if acc_result and acc_result.lineage
                    else DataLineage(algorithm_version=ALGORITHM_VERSION)
                )
            return MetricResult(
                metric_code="composite_score",
                value=None,
                confidence_level=ConfidenceLevel.E.value,
                lineage=lineage,
                details={"reason": "effective_auto_rate INCONCLUSIVE"},
            )

        # 加权分子：(A*a + F*f + S*s)，缺失指标按 0 计入
        weighted_sum = 0.0
        for code, weight in (("accuracy_rate", a), ("fast_rate", f), ("stability_rate", s)):
            result = metric_results.get(code)
            val = result.value if result else None
            if val is not None and weight > 0:
                # 归一化到 [0,1] 再加权
                eta = max(0.0, min(1.0, val / 100.0))
                weighted_sum += weight * eta
                logger.info(
                    "[综合评分] 加权项 %s: value=%.4f eta=%.4f weight=%.3f 贡献=%.4f",
                    code,
                    float(val),
                    eta,
                    weight,
                    weight * eta,
                )
            else:
                logger.info(
                    "[综合评分] 加权项 %s: value=%s weight=%.3f 跳过（值缺失或权重为0）",
                    code,
                    val,
                    weight,
                )

        total_weight = a + f + s
        logger.info("[综合评分] weighted_sum=%.4f, total_weight=%.3f", weighted_sum, total_weight)
        if total_weight <= 0:
            logger.warning("[综合评分] 所有权重总和为 0，返回 0")
            return MetricResult(
                metric_code="composite_score",
                value=0.0,
                confidence_level=ConfidenceLevel.A.value,
                lineage=DataLineage(algorithm_version=ALGORITHM_VERSION),
                details={"reason": "zero total weight"},
            )

        # 基础评分 = (A*a + F*f + S*s) / (a+f+s) * 100
        base_score = weighted_sum / total_weight * 100.0

        # R 作为乘数：P = base_score * R/100
        # R 缺失或 INCONCLUSIVE 已在前面提前返回，此处 r_value 必为有效值
        r_norm = max(0.0, min(1.0, r_value / 100.0))
        score = base_score * r_norm
        logger.info(
            "[综合评分] R 折扣: R=%.4f r_norm=%.4f, base_score=%.4f → score=%.4f",
            float(r_value),
            r_norm,
            base_score,
            score,
        )

        score = max(0.0, min(100.0, score))
        score = round(score, 2)

        # 可信度取核心指标中最低等级
        confidence = _min_confidence(metric_results)

        # 血缘取准确率的血缘（若存在）
        lineage_ref = metric_results.get("accuracy_rate")
        lineage = (
            lineage_ref.lineage
            if lineage_ref and lineage_ref.lineage
            else DataLineage(algorithm_version=ALGORITHM_VERSION)
        )

        logger.info(
            "[综合评分] 最终结果: base_score=%.4f, R=%s, score=%.2f, confidence=%s",
            base_score,
            r_value,
            score,
            confidence,
        )

        return MetricResult(
            metric_code="composite_score",
            value=score,
            confidence_level=confidence,
            lineage=lineage,
            details={
                "base_score": round(base_score, 2),
                "effective_auto_rate": r_value,
                "weights": {"a": a, "f": f, "s": s},
            },
        )


def _min_confidence(metric_results: dict[str, MetricResult]) -> str:
    """取核心指标 + R 中最低的可信度等级（A 最高，E 最低）.

    Args:
        metric_results: 指标结果字典

    Returns:
        最低可信度等级字符串
    """
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
    codes = (*CORE_METRIC_CODES, DISCOUNT_METRIC_CODE)
    worst = "A"
    worst_rank = 0
    for code in codes:
        result = metric_results.get(code)
        if result is None:
            continue
        rank = order.get(result.confidence_level, 4)
        if rank > worst_rank:
            worst_rank = rank
            worst = result.confidence_level
    return worst


__all__ = [
    "ALGORITHM_VERSION",
    "ConfidenceEvaluator",
    "CORE_METRIC_CODES",
    "DEFAULT_WEIGHTS",
    "DISCOUNT_METRIC_CODE",
    "QUALITY_POLICY",
]
