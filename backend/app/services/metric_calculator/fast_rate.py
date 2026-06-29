"""快速率计算器（算法说明 §4.5）.

公式：
    F = 100%                          当 T ≤ T'
    F = 1/e^((T-T')/T') × 100%        当 T > T'

其中：
    T：实际稳态时间（秒，由 settling_time 计算器提供）
    T'：理想稳态时间（秒，由 ideal_settling_time 计算器提供）

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4

定位：核心质量指标，参与综合评分加权。
依赖：settling_time（实际稳态时间）、ideal_settling_time（理想稳态时间）。
"""

from __future__ import annotations

import logging
import math

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)


class FastRateCalculator(MetricCalculatorBase):
    """快速率计算器（算法说明 §4.5）.

    基于 ARMA 模型辨识的实际稳态时间与理想稳态时间对比，
    采用分段指数映射：T≤T' 时满分，T>T' 时指数衰减。
    """

    #: 依赖稳态时间和理想稳态时间计算器
    depends_on = ["settling_time", "ideal_settling_time"]

    @property
    def metric_code(self) -> str:
        return "fast_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算快速率.

        Args:
            bundle: 指标数据包

        Returns:
            MetricResult：value 为快速率 0~100，
            actual_settling_time 和 ideal_settling_time 在 details 中
        """
        # 从依赖中读取实际稳态时间 T
        settling_result = self.dependencies.get("settling_time")
        actual_t = self._extract_settling_time(settling_result)

        # 从依赖中读取理想稳态时间 T'
        ideal_result = self.dependencies.get("ideal_settling_time")
        ideal_t = ideal_result.value if ideal_result else None

        logger.debug(
            "[快速率] actual_settling=%.2f, ideal_settling=%s",
            actual_t,
            ideal_t,
        )

        # 理想稳态时间无效 → 返回 INCONCLUSIVE
        if ideal_t is None or ideal_t <= 0:
            return self._make_inconclusive(
                bundle,
                "invalid_ideal_settling_time",
                {"actual_settling_time": actual_t, "ideal_settling_time": ideal_t},
            )

        # 实际稳态时间 ≤ 0（已稳态或辨识失败）→ 快速率 100%
        if actual_t <= 0:
            logger.debug("[快速率] actual_settling ≤ 0，返回 100")
            return self._make_result(
                bundle,
                100.0,
                {
                    "actual_settling_time": 0.0,
                    "ideal_settling_time": round(ideal_t, 2),
                    "reason": "already_stable",
                },
            )

        # T ≤ T' → 快速率 100%
        if actual_t <= ideal_t:
            logger.debug("[快速率] T=%.1f ≤ T'=%.1f，返回 100", actual_t, ideal_t)
            return self._make_result(
                bundle,
                100.0,
                {
                    "actual_settling_time": round(actual_t, 2),
                    "ideal_settling_time": round(ideal_t, 2),
                    "ratio": round(actual_t / ideal_t, 4),
                },
            )

        # T > T' → F = 1/e^((T-T')/T') × 100
        ratio = (actual_t - ideal_t) / ideal_t
        fast_rate = (1.0 / math.exp(ratio)) * 100.0
        fast_rate = self._clamp(fast_rate)

        logger.debug(
            "[快速率] T=%.1f > T'=%.1f, ratio=%.4f, F=%.2f",
            actual_t,
            ideal_t,
            ratio,
            fast_rate,
        )

        return self._make_result(
            bundle,
            fast_rate,
            {
                "actual_settling_time": round(actual_t, 2),
                "ideal_settling_time": round(ideal_t, 2),
                "ratio": round(ratio, 4),
            },
        )

    @staticmethod
    def _extract_settling_time(result: MetricResult | None) -> float:
        """从 settling_time 结果中提取实际稳态时间.

        优先从 details.actual_settling_time 读取，回退到 value。
        """
        if result is None:
            return 0.0
        if result.details:
            val = result.details.get("actual_settling_time")
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        if result.value is not None:
            try:
                return float(result.value)
            except (TypeError, ValueError):
                pass
        return 0.0


__all__ = ["FastRateCalculator"]
