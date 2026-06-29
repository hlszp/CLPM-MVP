"""好值率计算器（算法说明 §4.1）.

公式：η_good = T_good / T_total × 100%

其中：
    T_good：PV 质量码为 Good 且数值在有效量程范围内的累计时长
    T_total：评估时段总时长

设计依据：算法说明 §4.1；GB/T 44693.2-2024 附录 F.6

定位：辅助诊断指标，不参与综合评分加权。
好值率 < 20% 时标记 INCONCLUSIVE（影响整体可信度）。
"""

from __future__ import annotations

import logging

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 好值率 INCONCLUSIVE 阈值（%）
INCONCLUSIVE_THRESHOLD = 20.0


class GoodValueRateCalculator(MetricCalculatorBase):
    """好值率计算器（算法说明 §4.1）.

    基于 PV 质量码统计，衡量评估时段内数据有效性的时长占比。
    好值率影响指标可信度：好值率低 → valid_rate 低 → 可信度降级。
    """

    @property
    def metric_code(self) -> str:
        return "good_value_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算好值率.

        Args:
            bundle: 指标数据包（mask 通常为空，全量数据参与）

        Returns:
            MetricResult：value 为好值率 0~100；
            好值率 < 20% 时 INCONCLUSIVE（value=None, confidence=E）
        """
        block = bundle.data_block
        n = block.point_count

        logger.debug("[好值率] 输入: point_count=%d", n)

        if n == 0:
            return self._make_inconclusive(bundle, "empty_data_block")

        # 优先使用预处理阶段计算的 quality_summary.good_value_rate
        if block.quality_summary.good_value_rate is not None:
            rate = block.quality_summary.good_value_rate * 100.0
            logger.debug("[好值率] 使用 quality_summary: rate=%.2f%%", rate)
        else:
            # 回退：基于 pv_valid 标记计算（valid 已含质量码判定）
            pv_valid = block.validity.get("pv_valid", [])
            good_count = sum(1 for v in pv_valid if v)
            rate = (good_count / n) * 100.0 if n > 0 else 0.0
            logger.debug("[好值率] 回退 pv_valid 计算: rate=%.2f%%", rate)

        rate = self._clamp(rate)

        # 好值率 < 20% → INCONCLUSIVE
        if rate < INCONCLUSIVE_THRESHOLD:
            return self._make_inconclusive(
                bundle,
                "good_value_rate_below_threshold",
                {"good_value_rate": round(rate, 2), "threshold": INCONCLUSIVE_THRESHOLD},
            )

        return self._make_result(
            bundle,
            rate,
            {
                "good_value_rate": round(rate, 2),
                "sample_count": n,
                "source": "quality_summary"
                if block.quality_summary.good_value_rate is not None
                else "pv_valid",
            },
        )


__all__ = ["GoodValueRateCalculator"]
