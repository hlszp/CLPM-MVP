"""准确率计算器（算法说明 §4.4）.

公式：A = [1 - |Ē|/|E|_max × (1 - 1/e^r)] × 100%

其中：
    E_i = PV_i - SP_i
    |Ē| = (1/n) × Σ|E_i|
    r = |Ē| / |E|_max
    |E|_max = pv_range × 0.05（默认量程的 5%）

设计依据：算法说明 §4.4；GB/T 44693.2-2024 附录 B.3
"""

from __future__ import annotations

import logging
import math

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 默认偏差最大允许基准占量程比例
DEFAULT_E_MAX_RATIO = 0.05

#: 归一化信号量程（pv/sp/op 归一化到 0~100）
NORMALIZED_RANGE = 100.0


class AccuracyRateCalculator(MetricCalculatorBase):
    """准确率计算器（算法说明 §4.4）.

    衡量 PV 达到 SP 的准确程度，反映回路的余差情况。
    采用国标指数型公式（含 1/e^r 项），小偏差扣分少，大偏差扣分多。
    """

    @property
    def metric_code(self) -> str:
        return "accuracy_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算准确率.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为准确率 0~100，数据不足时 INCONCLUSIVE
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)

        logger.debug("[准确率] 输入: masked_points=%d", n)

        if n == 0:
            return self._make_inconclusive(bundle, "no_valid_pv_sp_pairs")

        # 计算偏差绝对值
        abs_errors = [abs(float(pv) - float(sp)) for pv, sp in pairs]
        mean_abs_error = sum(abs_errors) / n

        # |E|_max：从 CONFIG 信号读取，否则默认量程的 5%
        e_max = self._read_e_max(bundle)

        if e_max <= 0:
            logger.warning("[准确率] e_max=0，返回 0")
            return self._make_result(
                bundle, 0.0, {"mean_abs_error": mean_abs_error, "e_max": e_max}
            )

        # 归一化偏差 r = |Ē| / |E|_max
        r = mean_abs_error / e_max

        # 指数衰减因子：(1 - 1/e^r) = (1 - e^(-r))
        # P2 #39 TC2: 使用 e^(-r) 而非 1/e^r 避免大 r 时溢出（如 PV=1e6 → r=2e5）
        # math.exp(-r) 在 r→∞ 时返回 0.0（不抛 OverflowError），数学等价但数值稳定
        decay_factor = 1.0 - math.exp(-r)

        # 准确率 A = [1 - r × (1 - 1/e^r)] × 100
        accuracy = (1.0 - r * decay_factor) * 100.0
        accuracy = self._clamp(accuracy)

        logger.debug(
            "[准确率] mean_abs_error=%.4f, e_max=%.4f, r=%.4f, decay=%.4f, A=%.2f",
            mean_abs_error,
            e_max,
            r,
            decay_factor,
            accuracy,
        )

        return self._make_result(
            bundle,
            accuracy,
            {
                "mean_abs_error": round(mean_abs_error, 4),
                "e_max": e_max,
                "r": round(r, 4),
                "decay_factor": round(decay_factor, 4),
                "sample_count": n,
            },
        )

    @staticmethod
    def _read_e_max(bundle: MetricDataBundle) -> float:
        """读取偏差最大允许基准 |E|_max.

        优先从 CONFIG 信号读取（e_max / accuracy_e_max），
        否则默认归一化量程的 5%（100 × 0.05 = 5）。
        """
        signals = bundle.data_block.signals
        for key in ("e_max", "accuracy_e_max", "error_max"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return NORMALIZED_RANGE * DEFAULT_E_MAX_RATIO


__all__ = ["AccuracyRateCalculator"]
