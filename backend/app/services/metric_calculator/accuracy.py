"""准确率计算器（算法说明 §4.4）.

公式：A = [1 - |Ē|/|E|_max × (1 - 1/e^r)] × 100%

其中：
    E_i = PV_i - SP_i
    |Ē| = (1/n) × Σ|E_i|
    r = |Ē| / |E|_max
    |E|_max = (1/n) × Σ[max(|E_i|) - |E_i|]  （数据驱动，对齐 FDS v5.1 / 算法 v2.1）

设计依据：算法说明 §4.4 v2.1；GB/T 44693.2-2024 附录 B.3

v2.1 修正：|E|_max 由"外部输入参数（默认量程 5%）"改为"从数据计算
Σ[max(|E_i|) - |E_i|] / n"。原方案将 |E|_max 作为配置参数会导致同一偏差
在不同回路间不可比；改为数据驱动后，r = |Ē|/|E|_max ∈ [0, 1]，归一化更合理。
保留 CONFIG 覆盖入口（e_max / accuracy_e_max 信号），允许管理员手工指定。
"""

from __future__ import annotations

import logging
import math

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)


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

        # |E|_max：优先从 CONFIG 信号读取（管理员手工指定），
        # 否则从数据计算 Σ[max(|E_i|) - |E_i|] / n（对齐 FDS v5.1 / 算法 v2.1）
        e_max = self._read_e_max(bundle, abs_errors)

        # e_max == 0 表示所有偏差相等（无离散度），A = 100%（对齐算法 v2.1 §4.4.4 步骤 8）
        if e_max <= 0:
            logger.debug(
                "[准确率] e_max=0（所有偏差相等），A=100%%: mean_abs_error=%.4f",
                mean_abs_error,
            )
            return self._make_result(
                bundle,
                100.0,
                {
                    "mean_abs_error": round(mean_abs_error, 4),
                    "e_max": 0.0,
                    "r": 0.0,
                    "decay_factor": 0.0,
                    "sample_count": n,
                    "e_max_source": "data_degenerate",
                },
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
                "e_max": round(e_max, 4),
                "r": round(r, 4),
                "decay_factor": round(decay_factor, 4),
                "sample_count": n,
            },
        )

    @staticmethod
    def _read_e_max(
        bundle: MetricDataBundle, abs_errors: list[float] | None = None
    ) -> float:
        """读取偏差最大允许基准 |E|_max.

        优先级（对齐算法 v2.1 §4.4.4）：
        1. CONFIG 信号覆盖（e_max / accuracy_e_max / error_max）—— 管理员手工指定
        2. 数据驱动计算：e_max = Σ[max(|E_i|) - |E_i|] / n —— 默认行为

        Args:
            bundle: 指标数据包
            abs_errors: 偏差绝对值列表（数据驱动计算用）；None 时仅查 CONFIG

        Returns:
            |E|_max 值；CONFIG 未指定且 abs_errors 为 None 时返回 0（退化情形）
        """
        # 优先级 1：CONFIG 信号覆盖
        signals = bundle.data_block.signals
        for key in ("e_max", "accuracy_e_max", "error_max"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    logger.debug("[准确率] e_max 从 CONFIG 读取: key=%s value=%s", key, val)
                    return float(val)
                except (TypeError, ValueError):
                    continue

        # 优先级 2：数据驱动计算 Σ[max(|E_i|) - |E_i|] / n
        if not abs_errors:
            return 0.0
        max_abs_error = max(abs_errors)
        n = len(abs_errors)
        e_max = sum(max_abs_error - e for e in abs_errors) / n
        logger.debug(
            "[准确率] e_max 数据驱动计算: max_abs=%.4f, n=%d, e_max=%.4f",
            max_abs_error, n, e_max,
        )
        return e_max


__all__ = ["AccuracyRateCalculator"]
