"""设定值穿越次数 + 振荡幅值计算器（Phase 1，HiaMonitor 借鉴）.

2 个计算器：
    - SetpointCrossingCount：PV 穿越 SP 的次数（控制偏差 E=PV-SP 的符号变化数）
    - OscillationAmplitude：PV 偏离 SP 的平均绝对偏差（mean |PV-SP|）

P0 修复：``_get_masked_pair`` 返回的配对可能含 None（信号缺失），
必须过滤含 None 的整对后再计算，否则 ``float(None)`` 抛 TypeError。

legacy 穿越检测采用严格符号变化法：``diffs[i-1] * diffs[i] < 0``。
当 diff=0（PV 恰好等于 SP）时不计为穿越（0 * x = 0，不满足 < 0）。

P3-10c 新 profile（CANDIDATE_V3_RC1，10§3.4 穿越次数/"振荡幅值"行 +
C16-EA2/C16-EA3）：
    - **零平台穿越**：连续段内用最近非零符号处理零平台——[-1,0,+1] 记
      1 次穿越（legacy 严格乘积法记 0）；diff=0 是"恰在 SP 上"的平台，
      不是符号不变；
    - **gap 不连接**：按 masked 原始索引邻接切连续段，段间不比较——
      [-1, gap, +1] 记 0 次穿越（legacy 过滤 None 后记 1）；
    - **可配置死区**（``crossing_deadband``，默认 0=保留精确统计）：
      |diff| ≤ 死区按零平台吸收；死区由工程分辨率/噪声依据确定，
      值为 0 时也不跨缺口连接；
    - **振荡幅值正名**：保留旧 key/数值，``details.quantity`` 明确为
      **平均绝对偏差**（mean |PV-SP|，工程单位），不是振荡振幅——真实
      振幅须来自已验证振荡证据（oscillation 偏置/谱分析），不复用旧 key
      偷换定义。

定位：DISPLAY_ONLY 辅助指标，不参与节点级聚合。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4
"""

from __future__ import annotations

import logging
import statistics as stats
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 最少数据点数（穿越检测需 ≥ 3 点构成 ≥ 2 个差分；幅值需 ≥ 3 点统计意义）
MIN_POINTS = 3

#: 穿越死区默认值（0=保留精确统计；>0 由工程分辨率/噪声依据配置）
DEFAULT_CROSSING_DEADBAND = 0.0

#: 新 profile 振荡幅值口径标注（P6 文案按此展示，不冒充真实振幅）
_AMPLITUDE_QUANTITY = "mean_absolute_deviation"
_AMPLITUDE_NOTE = (
    "mean |PV-SP| in engineering units, NOT oscillation amplitude; true "
    "amplitude requires verified oscillation evidence (oscillation operator)"
)


def _filtered_diffs(pairs: list[tuple]) -> list[float]:
    """过滤含 None 的配对后计算偏差序列 E = PV - SP。

    P0 修复：必须先过滤含 None 的整对，否则 float(None) 抛 TypeError。
    """
    return [float(p) - float(s) for p, s in pairs if p is not None and s is not None]


def _masked_position_runs(bundle: MetricDataBundle) -> list[tuple[int, int]]:
    """masked 序列的连续游程（masked 位置空间闭区间对，跨缺口断开）.

    判据：相邻 masked 点的**原始索引**必须连续（``idx[k] == idx[k-1]+1``），
    中间存在被 mask 排除的点（无效/未知槽）即视为缺口，两侧不连接
    （C16-EA2/C16-EA3：gap 不连接）。

    注意：这是纯索引口径（假设 masked 序列不含 None 值）；含 None 过滤
    的对齐场景用 :func:`_diffs_and_runs`（diffs 位置空间，None 同样断开）。
    """
    idx = bundle.masked_indices
    runs: list[tuple[int, int]] = []
    if not idx:
        return runs
    start = 0
    for k in range(1, len(idx)):
        if idx[k] != idx[k - 1] + 1:
            runs.append((start, k - 1))
            start = k
    runs.append((start, len(idx) - 1))
    return runs


def _diffs_and_runs(
    pairs: list[tuple],
    masked_indices: list[int],
) -> tuple[list[float], list[tuple[int, int]]]:
    """偏差序列 + 连续游程（diffs 位置空间；None 过滤与缺口都断开游程）.

    游程断开条件（任一，C16-EA2）：
    - 相邻可用配对的原始索引不邻接（中间有被排除的槽 = 缺口）；
    - 配对之间夹 None（信号缺失点被 P0 过滤，同样不连接）。

    Returns:
        (diffs, runs)：diffs 为过滤 None 后的 E=PV-SP 序列；runs 为 diffs
        位置空间的闭区间对，可直接 ``diffs[a:b+1]`` 切段。
    """
    diffs: list[float] = []
    pos_of_pair: list[int | None] = []
    for p, s in pairs:
        if p is None or s is None:
            pos_of_pair.append(None)
        else:
            pos_of_pair.append(len(diffs))
            diffs.append(float(p) - float(s))
    if not diffs:
        return diffs, []

    runs: list[tuple[int, int]] = []
    start = 0
    prev_usable: int | None = None
    for k, pos in enumerate(pos_of_pair):
        if pos is None:
            continue
        if prev_usable is not None:
            adjacent = (
                k == prev_usable + 1
                and k < len(masked_indices)
                and prev_usable < len(masked_indices)
                and masked_indices[k] == masked_indices[prev_usable] + 1
            )
            if not adjacent:
                runs.append((start, pos - 1))
                start = pos
        prev_usable = k
    runs.append((start, len(diffs) - 1))
    return diffs, runs


def _count_crossings_segment(
    diffs: list[float],
    deadband: float,
) -> tuple[int, int]:
    """段内零平台穿越计数：最近非零符号法.

    |diff| ≤ deadband 的点作为零平台吸收（不计符号），穿越发生在相邻
    两个**非平台**点符号相反时。deadband=0 时仅精确 0 是平台（保留
    精确统计，C16-EA2）。

    Returns:
        (crossing_count, zero_platform_points)
    """
    signs: list[int] = []
    platform = 0
    for d in diffs:
        if abs(d) <= deadband:
            platform += 1
            continue
        signs.append(1 if d > 0 else -1)
    crossings = sum(1 for i in range(1, len(signs)) if signs[i] * signs[i - 1] < 0)
    return crossings, platform


class SetpointCrossingCountCalculator(MetricCalculatorBase):
    """设定值穿越次数计算器。

    统计控制偏差 E=PV-SP 的符号变化次数，衡量 PV 穿越设定值 SP 的频率。
    高穿越频率 + 低衰减 → 振荡趋势。

    legacy：严格符号变化法（``diffs[i-1] * diffs[i] < 0``，diff=0 不计）；
    新 profile：连续段内最近非零符号（零平台吸收）+ 死区 + gap 不连接。
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
            details 含 crossing_count（int）和 n（有效点数）；
            新 profile 另含 deadband/segment_count/zero_platform_points/
            cross_gap_excluded 与 assessmentState
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        diffs = _filtered_diffs(pairs)  # P0 fix
        n = len(diffs)

        logger.debug("[设定值穿越次数] 输入: valid_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        if self.new_semantics:
            return self._calculate_new_profile(bundle, pairs, n)

        # legacy 严格符号变化：diffs[i-1] * diffs[i] < 0
        crossings = sum(1 for i in range(1, n) if diffs[i - 1] * diffs[i] < 0)

        logger.debug("[设定值穿越次数] crossings=%d, n=%d", crossings, n)

        return self._make_result(
            bundle,
            float(crossings),
            {"crossing_count": crossings, "n": n},
        )

    def _calculate_new_profile(
        self, bundle: MetricDataBundle, pairs: list[tuple], n: int
    ) -> MetricResult:
        """新 profile 穿越计数：段内最近非零符号 + 死区 + gap 不连接（C16-EA2）."""
        params = self._effective_params("setpoint_crossing_count", bundle)
        deadband = float(params.get("crossing_deadband", DEFAULT_CROSSING_DEADBAND))

        diffs, runs = _diffs_and_runs(pairs, bundle.masked_indices)
        crossings = 0
        platform_total = 0
        for a, b in runs:
            seg = diffs[a : b + 1]
            if len(seg) < 2:
                continue
            c, p = _count_crossings_segment(seg, deadband)
            crossings += c
            platform_total += p

        gap_excluded = len(runs) - 1 if len(runs) > 1 else 0
        logger.debug(
            "[设定值穿越次数|新] crossings=%d, n=%d, segments=%d, deadband=%.4g, platform=%d",
            crossings,
            n,
            len(runs),
            deadband,
            platform_total,
        )
        return self._make_result(
            bundle,
            float(crossings),
            {
                "crossing_count": crossings,
                "n": n,
                "deadband": deadband,
                "segment_count": len(runs),
                "zero_platform_points": platform_total,
                "cross_gap_excluded": gap_excluded > 0,
                "gap_excluded_pairs": gap_excluded,
                "assessmentState": "COMPLETE",
            },
        )


class OscillationAmplitudeCalculator(MetricCalculatorBase):
    """振荡幅值计算器。

    计算 PV 偏离 SP 的平均绝对偏差：amplitude = mean(|PV - SP|)。
    L1 指标（无依赖），计算器独立计算，不依赖 oscillation_rate 结果。

    新 profile：保留旧 key/数值，展示口径明确为**平均绝对偏差**
    （固定偏差 5 → 5；正弦峰值 10 → 2A/π ≈ 6.37），不冒充真实振幅
    （C16-EA3）。
    """

    @property
    def metric_code(self) -> str:
        return "oscillation_amplitude"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算振荡幅值.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为平均绝对偏差（工程单位）；
            details 含 amplitude（2 位精度）和 n；新 profile 另含
            quantity/quantityNote（平均绝对偏差正名，非真实振幅）
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

        details: dict[str, Any] = {"amplitude": round(amp, 2), "n": n}
        if self.new_semantics:
            details.update(
                {
                    "assessmentState": "COMPLETE",
                    "quantity": _AMPLITUDE_QUANTITY,
                    "quantityNote": _AMPLITUDE_NOTE,
                }
            )
        return self._make_result(bundle, amp, details)


__all__ = [
    "OscillationAmplitudeCalculator",
    "SetpointCrossingCountCalculator",
]
