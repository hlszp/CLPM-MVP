"""PV/SP/OP/偏差 统计指标计算器（Phase 1，HiaMonitor 借鉴）.

8 个计算器：
    - PvMean / PvStd：PV 均值 / 总体标准差
    - SpMean / SpStd：SP 均值 / 总体标准差
    - OpMean / OpStd：OP 均值 / 总体标准差
    - ErrorMean / ErrorStd：偏差 E=PV-SP 的均值 / 总体标准差

标准差使用总体标准差（pstdev，除以 N），而非样本标准差（stdev，除以 N-1）。
控制回路评估时段的数据是完整总体，非抽样样本。

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

        return self._make_result(bundle, mean, {"mean": round(mean, 2), "n": n})


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

        return self._make_result(bundle, std, {"std": round(std, 2), "n": n})


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

        return self._make_result(bundle, mean, {"mean": round(mean, 2), "n": n})


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

        return self._make_result(bundle, std, {"std": round(std, 2), "n": n})


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
