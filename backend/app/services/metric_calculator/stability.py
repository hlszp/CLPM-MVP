"""稳定率计算器（算法说明 §4.3）.

公式：S = 1/e^(σ/(0.05·U)) × (1-Osc) × 100%

其中：
    E_i = PV_i - SP_i
    σ = sqrt((1/n) × Σ(E_i - Ē)²)
    U = PV 量程范围（归一化后为 100）
    Osc = 振荡率（0~1，由 oscillation_rate 计算器提供）

设计依据：算法说明 §4.3；GB/T 44693.2-2024 附录 B.5

定位：核心质量指标，参与综合评分加权。
依赖：oscillation_rate（通过 dependencies 注入）。
"""

from __future__ import annotations

import logging
import math

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 归一化量程
DEFAULT_PV_RANGE = 100.0

#: 最少数据点数
MIN_POINTS = 2


class StabilityRateCalculator(MetricCalculatorBase):
    """稳定率计算器（算法说明 §4.3）.

    采用控制偏差标准差衡量 PV 波动平稳程度，结合振荡率修正。
    指数型公式：σ=0 时 S=100%，σ 增大时 S 指数衰减。
    """

    #: 依赖振荡率计算器
    depends_on = ["oscillation_rate"]

    @property
    def metric_code(self) -> str:
        return "stability_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算稳定率.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为稳定率 0~100，
            oscillation_rate 从 dependencies 读取
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)

        logger.debug("[稳定率] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(bundle, "insufficient_data")

        # 计算控制偏差
        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)
        mean_error = float(np.mean(errors))
        std_error = float(np.std(errors))

        # 振荡率（0~1）
        osc_result = self.dependencies.get("oscillation_rate")
        osc_rate_pct = osc_result.value if osc_result and osc_result.value is not None else 0.0
        osc_factor = 1.0 - (osc_rate_pct / 100.0)

        if osc_factor <= 0:
            logger.debug("[稳定率] 振荡率 %.2f%% ≥ 100%%，稳定率返回 0", osc_rate_pct)
            return self._make_result(
                bundle,
                0.0,
                {
                    "std_error": round(std_error, 4),
                    "oscillation_rate": round(osc_rate_pct, 2),
                    "reason": "osc_too_high",
                },
            )

        # U = PV 量程范围
        u = self._read_pv_range(bundle)
        if u <= 0:
            return self._make_inconclusive(bundle, "zero_pv_range")

        # 指数衰减：S = 1/e^(σ/(0.05·U)) × (1-Osc) × 100
        normalized_std = std_error / (0.05 * u)
        stability = (1.0 / math.exp(normalized_std)) * osc_factor * 100.0
        stability = self._clamp(stability)

        logger.debug(
            "[稳定率] mean_error=%.4f, std=%.4f, U=%.1f, norm_std=%.4f, osc=%.2f%%, S=%.2f",
            mean_error,
            std_error,
            u,
            normalized_std,
            osc_rate_pct,
            stability,
        )

        return self._make_result(
            bundle,
            stability,
            {
                "mean_error": round(mean_error, 4),
                "std_error": round(std_error, 4),
                "pv_range": u,
                "normalized_std": round(normalized_std, 4),
                "oscillation_rate": round(osc_rate_pct, 2),
                "osc_factor": round(osc_factor, 4),
                "sample_count": n,
            },
        )

    @staticmethod
    def _read_pv_range(bundle: MetricDataBundle) -> float:
        """读取 PV 量程范围."""
        val = bundle.data_block.signals.get("pv_range")
        if val is None:
            return DEFAULT_PV_RANGE
        try:
            v = float(val)
            return v if v > 0 else DEFAULT_PV_RANGE
        except (TypeError, ValueError):
            return DEFAULT_PV_RANGE


__all__ = ["StabilityRateCalculator"]
