"""准确率计算器（算法说明 §4.4）.

公式：A = [1 - r × (1 - 1/e^r)] × 100%

其中：
    E_i = PV_i - SP_i
    |Ē| = (1/n) × Σ|E_i|
    r = |Ē| / |E|_max
    |E|_max = tolerance_ratio × U（工程容限 = 量程比例，默认 2%）

设计依据：算法说明 §4.4；GB/T 44693.2-2024 附录 B.3

v2.2 修正（2026-10-10，稳定回路误判整改）：|E|_max 归一化基准由
"数据驱动 Σ[max(|E_i|) - |E_i|]/n（偏差序列自身离散度）"改回国标语义
"最大允许偏差 = 量程比例容限"。原数据驱动基准下 r = mean/(max-mean)
是偏差均值相对自身离散度的倍数：PV 高度稳定的回路（COV 数据源常见）
离散度趋 0 → r 爆炸 → 余差仅量程万分之几的回路被打成 0~11 分，
且相邻小时分数剧烈跳变（0~85），与控制质量无关。
典型实证（05TY05P0803_PIDA，量程 0~1000℃）：余差 0.066% 量程 → 11 分。
修复后 r = |Ē|/(tolerance_ratio×U)，余差 2% 量程 ≈ 44 分、1% ≈ 73 分、
5% ≈ 0 分，梯度符合工程直觉；恒定余差退化分支（e_max=0）随之不再可达，
一并移除。CONFIG 信号覆盖入口（e_max / accuracy_e_max / error_max，
绝对值）保留最高优先级，供个别回路手工指定。
"""

from __future__ import annotations

import logging
import math

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 归一化量程（数据块信号经预处理 Step ③ 归一化为 0~100；CONFIG pv_range 可覆盖）
DEFAULT_PV_RANGE = 100.0

#: 工程容限比例默认值（占量程 2%，与 algorithm_config._DEFAULTS 一致）
_DEFAULT_E_MAX_TOLERANCE_RATIO = 0.02


class AccuracyRateCalculator(MetricCalculatorBase):
    """准确率计算器（算法说明 §4.4）.

    衡量 PV 达到 SP 的准确程度，反映回路的余差情况。
    采用国标指数型公式（含 1/e^r 项），归一化基准为量程比例工程容限。
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

        # 真零偏差 → A = 100%（对齐算法 v2.1 §4.4.4 步骤 8）
        if mean_abs_error <= 0:
            logger.debug("[准确率] 零偏差，A=100%%")
            return self._make_result(
                bundle,
                100.0,
                {
                    "mean_abs_error": 0.0,
                    "e_max": 0.0,
                    "r": 0.0,
                    "decay_factor": 0.0,
                    "sample_count": n,
                },
            )

        # |E|_max：优先从 CONFIG 信号读取（管理员手工指定绝对值），
        # 否则用工程容限 tolerance_ratio × U（国标"最大允许偏差"语义）
        e_max = self._read_e_max(bundle)
        e_max_source = "config"
        if e_max is None:
            params = get_algorithm_params("accuracy_rate", bundle.data_block.control_type)
            tolerance_ratio = float(
                params.get("e_max_tolerance_ratio", _DEFAULT_E_MAX_TOLERANCE_RATIO)
            )
            u = self._read_pv_range(bundle)
            e_max = tolerance_ratio * u
            e_max_source = "tolerance_ratio"

        if e_max <= 0:
            return self._make_inconclusive(
                bundle,
                "invalid_e_max",
                {"mean_abs_error": round(mean_abs_error, 4), "sample_count": n},
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
            "[准确率] mean_abs_error=%.6f, e_max=%.4f(%s), r=%.4f, decay=%.4f, A=%.2f",
            mean_abs_error,
            e_max,
            e_max_source,
            r,
            decay_factor,
            accuracy,
        )

        return self._make_result(
            bundle,
            accuracy,
            {
                "mean_abs_error": round(mean_abs_error, 6),
                "e_max": round(e_max, 4),
                "r": round(r, 4),
                "decay_factor": round(decay_factor, 4),
                "sample_count": n,
                "e_max_source": e_max_source,
            },
        )

    @staticmethod
    def _read_e_max(bundle: MetricDataBundle) -> float | None:
        """读取偏差最大允许基准 |E|_max 的 CONFIG 覆盖（绝对值）.

        优先级 1：CONFIG 信号覆盖（e_max / accuracy_e_max / error_max）——
        管理员手工指定回路级绝对容限（归一化量纲）。

        Args:
            bundle: 指标数据包

        Returns:
            |E|_max 覆盖值；未指定时返回 None（由调用方走容限比例路径）
        """
        signals = bundle.data_block.signals
        for key in ("e_max", "accuracy_e_max", "error_max"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    logger.debug("[准确率] e_max 从 CONFIG 读取: key=%s value=%s", key, val)
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _read_pv_range(bundle: MetricDataBundle) -> float:
        """读取 PV 量程范围 U（容限比例的基准）.

        数据块信号经预处理 Step ③ 归一化为 0~100，故默认 100 与信号量纲
        自洽（与 stability._read_pv_range 同款）；CONFIG pv_range 信号可覆盖。
        """
        val = MetricCalculatorBase._read_config_scalar(bundle.data_block.signals, "pv_range")
        if val is None:
            return DEFAULT_PV_RANGE
        try:
            v = float(val)
            return v if v > 0 else DEFAULT_PV_RANGE
        except (TypeError, ValueError):
            return DEFAULT_PV_RANGE


__all__ = ["AccuracyRateCalculator"]
