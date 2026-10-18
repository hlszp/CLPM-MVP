"""PV/SP/OP/偏差 统计指标计算器（Phase 1，HiaMonitor 借鉴）.

8 个计算器：
    - PvMean / PvStd：PV 均值 / 总体标准差
    - SpMean / SpStd：SP 均值 / 总体标准差
    - OpMean / OpStd：OP 均值 / 总体标准差
    - ErrorMean / ErrorStd：偏差 E=PV-SP 的均值 / 总体标准差

标准差使用总体标准差（pstdev，除以 N），而非样本标准差（stdev，除以 N-1）。
控制回路评估时段的数据是完整总体，非抽样样本。

P3-10c 新 profile（CANDIDATE_V3_RC1，10§3.4 描述统计行 + C16-EA4）：
    - ``value`` 恒为**样本口径**统计量（等权重逐点，保留可复算数值，
      不静默替换）；
    - ``details.statisticBasis`` 明确口径（sample_mean / population_std）；
    - **非均匀网格**且时间戳可用时另输出 ``details.timeWeightedMean``
      （零阶保持时间加权均值，与样本均值分开）——不规则样本 0@0/10@1/
      10@10：样本均值 6.67、[0,10) 保持时间均值 9，两者不混同；
      均匀网格时 ``uniformGrid=true``（样本均值即时间均值）。

定位：DISPLAY_ONLY 辅助指标，不参与节点级聚合（避免均值再平均失真）。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4
"""

from __future__ import annotations

import logging
import statistics as stats

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 统计指标最少数据点数（mean/std 均需 ≥ 2 点）
MIN_POINTS = 2

#: 均匀网格判定容差（秒）：相邻点时长与中位时长相对偏差超过该值视为非均匀
_NONUNIFORM_REL_TOL = 0.01


def _time_weighted_mean_details_from_ts(
    vals: list[float],
    ts: list,
) -> dict[str, object]:
    """新 profile 均值类指标口径与时间均值增量 details（C16-EA4）核心.

    - ``statisticBasis``：value 的口径（样本均值）；
    - 非均匀网格且时间戳可用 → 另报 ``timeWeightedMean``：零阶保持，
      每点持有 ``[ts_i, ts_{i+1})``，**末点持有期未知按 0 计**（半开窗口
      语义，不造时长越过最后观测——C16-EA4：0@0/10@1/10@10 的
      [0,10) 时间均值 9）；
    - 均匀网格 → ``uniformGrid=true``（样本均值即时间均值，不重复输出）；
      均匀性按**相邻采样间隔**判定（与末点持有期无关）。

    Returns:
        details 增量字典（时间戳不可用时仅报样本口径）。
    """
    details: dict[str, object] = {"statisticBasis": "sample_mean"}
    if len(ts) != len(vals) or len(ts) < 2:
        return details
    try:
        intervals = [(b - a).total_seconds() for a, b in zip(ts[:-1], ts[1:], strict=True)]
    except (AttributeError, TypeError):
        return details
    if not intervals or any(d <= 0 for d in intervals):
        return details
    med = sorted(intervals)[len(intervals) // 2]
    uniform = all(abs(d - med) <= _NONUNIFORM_REL_TOL * max(med, 1e-9) for d in intervals)
    if uniform:
        details["uniformGrid"] = True
        return details
    # 非均匀：零阶保持时间均值（末点持有 0）
    durations = intervals + [0.0]
    total = sum(durations)
    twm = sum(v * d for v, d in zip(vals, durations, strict=True)) / total
    details["uniformGrid"] = False
    details["timeWeightedMean"] = round(twm, 4)
    details["timeWeightedBasis"] = "zero_order_hold_half_open_last_slot_zero"
    return details


def _time_weighted_mean_details(
    calc: MetricCalculatorBase,
    bundle: MetricDataBundle,
    vals: list[float],
) -> dict[str, object]:
    """单信号版本：masked 时间戳自动对齐（长度不一致=对齐失败，仅样本口径）."""
    ts = calc._get_masked_timestamps(bundle)
    if len(ts) != len(vals):
        ts = []
    return _time_weighted_mean_details_from_ts(vals, ts)


def _std_basis_details() -> dict[str, object]:
    """新 profile 标准差类指标口径 details（样本口径正名）."""
    return {"statisticBasis": "population_std_over_samples"}


# ---------------------------------------------------------------------------
# 单信号均值/标准差基类
# ---------------------------------------------------------------------------


class _SingleTagMeanCalculator(MetricCalculatorBase):
    """单信号均值计算器基类。

    子类需设置 ``_tag`` 和 ``_code`` 类属性。
    """

    _tag: str = ""
    _code: str = ""

    @property
    def metric_code(self) -> str:
        return self._code

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        vals = [float(v) for v in self._get_masked_values(bundle, self._tag) if v is not None]
        n = len(vals)

        logger.debug("[%s] 输入: tag=%s, valid_points=%d", self._code, self._tag, n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        mean = stats.mean(vals)
        logger.debug("[%s] mean=%.4f, n=%d", self._code, mean, n)

        details: dict = {"mean": round(mean, 2), "n": n}
        if self.new_semantics:
            details.update(_time_weighted_mean_details(self, bundle, vals))
            details["assessmentState"] = "COMPLETE"
        return self._make_result(bundle, mean, details)


class _SingleTagStdCalculator(MetricCalculatorBase):
    """单信号总体标准差计算器基类。

    子类需设置 ``_tag`` 和 ``_code`` 类属性。
    """

    _tag: str = ""
    _code: str = ""

    @property
    def metric_code(self) -> str:
        return self._code

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        vals = [float(v) for v in self._get_masked_values(bundle, self._tag) if v is not None]
        n = len(vals)

        logger.debug("[%s] 输入: tag=%s, valid_points=%d", self._code, self._tag, n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        std = stats.pstdev(vals)
        logger.debug("[%s] pstdev=%.4f, n=%d", self._code, std, n)

        details: dict = {"std": round(std, 2), "n": n}
        if self.new_semantics:
            details.update(_std_basis_details())
            details["assessmentState"] = "COMPLETE"
        return self._make_result(bundle, std, details)


# ---------------------------------------------------------------------------
# PV 统计
# ---------------------------------------------------------------------------


class PvMeanCalculator(_SingleTagMeanCalculator):
    """PV 均值。"""

    _tag = "pv"
    _code = "pv_mean"


class PvStdCalculator(_SingleTagStdCalculator):
    """PV 总体标准差。"""

    _tag = "pv"
    _code = "pv_std"


# ---------------------------------------------------------------------------
# SP 统计
# ---------------------------------------------------------------------------


class SpMeanCalculator(_SingleTagMeanCalculator):
    """SP 均值。"""

    _tag = "sp"
    _code = "sp_mean"


class SpStdCalculator(_SingleTagStdCalculator):
    """SP 总体标准差。"""

    _tag = "sp"
    _code = "sp_std"


# ---------------------------------------------------------------------------
# OP 统计
# ---------------------------------------------------------------------------


class OpMeanCalculator(_SingleTagMeanCalculator):
    """OP 均值。"""

    _tag = "op"
    _code = "op_mean"


class OpStdCalculator(_SingleTagStdCalculator):
    """OP 总体标准差。"""

    _tag = "op"
    _code = "op_std"


# ---------------------------------------------------------------------------
# 偏差统计 E = PV - SP
# ---------------------------------------------------------------------------


class ErrorMeanCalculator(MetricCalculatorBase):
    """偏差 E=PV-SP 的均值。"""

    @property
    def metric_code(self) -> str:
        return "error_mean"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        # P0 fix：过滤含 None 的整对，避免 float(None) TypeError
        errs = [float(p) - float(s) for p, s in pairs if p is not None and s is not None]
        n = len(errs)

        logger.debug("[偏差均值] 输入: valid_pairs=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        mean = stats.mean(errs)
        logger.debug("[偏差均值] mean=%.4f, n=%d", mean, n)

        details: dict = {"mean": round(mean, 2), "n": n}
        if self.new_semantics:
            # 偏差序列时间戳与 pv/sp masked 对齐（pairs 过滤 None 后可能不等长）
            ts = self._get_masked_timestamps(bundle)
            if len(ts) != n:
                ts = []
            details.update(_time_weighted_mean_details_from_ts(errs, ts))
            details["assessmentState"] = "COMPLETE"
        return self._make_result(bundle, mean, details)


class ErrorStdCalculator(MetricCalculatorBase):
    """偏差 E=PV-SP 的总体标准差。"""

    @property
    def metric_code(self) -> str:
        return "error_std"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        # P0 fix：过滤含 None 的整对
        errs = [float(p) - float(s) for p, s in pairs if p is not None and s is not None]
        n = len(errs)

        logger.debug("[偏差标准差] 输入: valid_pairs=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS}
            )

        std = stats.pstdev(errs)
        logger.debug("[偏差标准差] pstdev=%.4f, n=%d", std, n)

        details: dict = {"std": round(std, 2), "n": n}
        if self.new_semantics:
            details.update(_std_basis_details())
            details["assessmentState"] = "COMPLETE"
        return self._make_result(bundle, std, details)


__all__ = [
    "ErrorMeanCalculator",
    "ErrorStdCalculator",
    "OpMeanCalculator",
    "OpStdCalculator",
    "PvMeanCalculator",
    "PvStdCalculator",
    "SpMeanCalculator",
    "SpStdCalculator",
]
