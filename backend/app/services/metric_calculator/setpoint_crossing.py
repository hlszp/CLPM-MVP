"""设定值穿越次数 + 振荡幅值计算器（Phase 1，HiaMonitor 借鉴）.

2 个计算器：
    - SetpointCrossingCount：PV 穿越 SP 的次数（控制偏差 E=PV-SP 的符号变化数）
    - OscillationAmplitude：PV 偏离 SP 的平均绝对偏差（mean |PV-SP|）

P0 修复：``_get_masked_pair`` 返回的配对可能含 None（信号缺失），
必须过滤含 None 的整对后再计算，否则 ``float(None)`` 抛 TypeError。

穿越检测采用严格符号变化法：``diffs[i-1] * diffs[i] < 0``。
当 diff=0（PV 恰好等于 SP）时不计为穿越（0 * x = 0，不满足 < 0）。

定位：DISPLAY_ONLY 辅助指标，不参与节点级聚合。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4
"""

from __future__ import annotations

import logging
import statistics as stats

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 最少数据点数（穿越检测需 ≥ 3 点构成 ≥ 2 个差分；幅值需 ≥ 3 点统计意义）
MIN_POINTS = 3


def _filtered_diffs(pairs: list[tuple]) -> list[float]:
    """过滤含 None 的配对后计算偏差序列 E = PV - SP。

    P0 修复：必须先过滤含 None 的整对，否则 float(None) 抛 TypeError。
    """
    return [float(p) - float(s) for p, s in pairs if p is not None and s is not None]


class SetpointCrossingCountCalculator(MetricCalculatorBase):
    """设定值穿越次数计算器。

    统计控制偏差 E=PV-SP 的符号变化次数，衡量 PV 穿越设定值 SP 的频率。
    高穿越频率 + 低衰减 → 振荡趋势。

    严格符号变化法：``diffs[i-1] * diffs[i] < 0`` 表示一次穿越。
    diff=0（PV=SP）不计为穿越。
    """

    @property
    def metric_code(self) -> str:
        return "setpoint_crossing_count"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算设定值穿越次数.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为穿越次数（float）；
            details 含 crossing_count（int）和 n（有效点数）
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        diffs = _filtered_diffs(pairs)  # P0 fix
        n = len(diffs)

        logger.debug("[设定值穿越次数] 输入: valid_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        # 严格符号变化：diffs[i-1] * diffs[i] < 0
        crossings = sum(1 for i in range(1, n) if diffs[i - 1] * diffs[i] < 0)

        logger.debug("[设定值穿越次数] crossings=%d, n=%d", crossings, n)

        return self._make_result(
            bundle,
            float(crossings),
            {"crossing_count": crossings, "n": n},
        )


class OscillationAmplitudeCalculator(MetricCalculatorBase):
    """振荡幅值计算器。

    计算 PV 偏离 SP 的平均绝对偏差：amplitude = mean(|PV - SP|)。
    L2 指标（depends_on oscillation_rate），但计算器本身不读取
    oscillation_rate 的结果——依赖声明仅供三层编排保证执行顺序。
    """

    @property
    def metric_code(self) -> str:
        return "oscillation_amplitude"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算振荡幅值.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为平均绝对偏差；
            details 含 amplitude（2 位精度）和 n
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        diffs = _filtered_diffs(pairs)  # P0 fix
        n = len(diffs)

        logger.debug("[振荡幅值] 输入: valid_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        abs_errs = [abs(d) for d in diffs]
        amp = stats.mean(abs_errs)

        logger.debug("[振荡幅值] amplitude=%.4f, n=%d", amp, n)

        return self._make_result(
            bundle,
            amp,
            {"amplitude": round(amp, 2), "n": n},
        )


__all__ = [
    "OscillationAmplitudeCalculator",
    "SetpointCrossingCountCalculator",
]
