"""阀门诊断指标计算器（Phase 1，HiaMonitor 借鉴）.

3 个计算器：
    - ValveLinearity：阀门线性度 = |r|（PV-OP 皮尔逊相关系数绝对值）
    - ValveNonlinearity：阀门非线性度 = 1 - |r|
    - ValveOperatingRange：阀门运行区间 = OP 的 [min, max]（value=span）

P0 修复：``_get_masked_pair`` 返回的配对可能含 None（信号缺失），
必须过滤"任一为 None"的整对后再计算，否则 xs/ys 长度不匹配或 TypeError。

定位：DISPLAY_ONLY 辅助指标，不参与节点级聚合。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4
"""

from __future__ import annotations

import logging
import math

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 相关性计算最少数据点数
MIN_POINTS_CORR = 3

#: 运行区间计算最少数据点数
MIN_POINTS_RANGE = 2


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """皮尔逊相关系数。

    Args:
        xs: x 值列表
        ys: y 值列表（长度须与 xs 相同）

    Returns:
        相关系数 r ∈ [-1, 1]；点数 < 2 或零方差时返回 None
    """
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denom = math.sqrt(sxx * syy)
    if denom == 0:
        return None
    return sxy / denom


def _filter_pairs(
    pairs: list[tuple[float | None, float | None]],
) -> tuple[list[float], list[float]]:
    """过滤含 None 的配对，返回 (xs, ys)。

    P0 修复：必须同时过滤"任一为 None"的整对，而非分别过滤，
    否则 xs/ys 长度不匹配导致后续计算崩溃。
    """
    xs = [float(p) for p, o in pairs if p is not None and o is not None]
    ys = [float(o) for p, o in pairs if p is not None and o is not None]
    return xs, ys


class ValveLinearityCalculator(MetricCalculatorBase):
    """阀门线性度计算器。

    基于 PV-OP 散点的皮尔逊相关系数绝对值，衡量阀门线性响应程度。
    linearity = |r|，r=1 表示完全线性，r=0 表示无线性关系。
    """

    @property
    def metric_code(self) -> str:
        return "valve_linearity"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算阀门线性度.

        Args:
            bundle: 指标数据包（需含 pv/op 信号，mask 为 pv_valid && op_valid）

        Returns:
            MetricResult：value 为 |r| ∈ [0, 1]；
            details 含 r（原始相关系数）和 n（有效点数）
        """
        pairs = self._get_masked_pair(bundle, "pv", "op")
        xs, ys = _filter_pairs(pairs)  # P0 fix
        n = len(xs)

        logger.debug("[阀门线性度] 输入: valid_pairs=%d", n)

        if n < MIN_POINTS_CORR:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS_CORR}
            )

        r = _pearson(xs, ys)
        linearity = abs(r) if r is not None else 0.0

        logger.debug("[阀门线性度] r=%.4f, linearity=%.4f, n=%d", r or 0, linearity, n)

        return self._make_result(
            bundle,
            linearity,
            {"r": round(r, 4) if r is not None else None, "n": n},
        )


class ValveNonlinearityCalculator(MetricCalculatorBase):
    """阀门非线性度计算器。

    nonlinearity = 1 - |r|，与 ValveLinearity 互补。
    r=1 → nonlinearity=0（完全线性），r=0 → nonlinearity=1（完全非线性）。

    依赖复用：编排层注入 valve_linearity 结果后直接取补值，
    避免对同一 PV-OP 数据重复计算皮尔逊 r（2026-08-27 去重）；
    依赖缺失（独立调用/单测）时回退自行计算。
    """

    #: 复用 valve_linearity 的 |r|，nonlinearity = 1 - |r|
    depends_on = ["valve_linearity"]

    @property
    def metric_code(self) -> str:
        return "valve_nonlinearity"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算阀门非线性度.

        Args:
            bundle: 指标数据包（需含 pv/op 信号，mask 为 pv_valid && op_valid）

        Returns:
            MetricResult：value 为 1-|r| ∈ [0, 1]；
            details 含 nonlinearity（4 位精度）和 n
        """
        # 依赖注入路径：nonlinearity 与 linearity 严格互补，
        # 零方差（r=None）时 linearity=0 → nonlinearity=1，口径一致
        lin_result = self.dependencies.get("valve_linearity")
        if lin_result is not None and lin_result.value is not None:
            nonlinearity = 1.0 - float(lin_result.value)
            n = lin_result.details.get("n") if lin_result.details else None
            logger.debug(
                "[阀门非线性度] 复用 valve_linearity: linearity=%.4f, nonlinearity=%.4f",
                lin_result.value,
                nonlinearity,
            )
            return self._make_result(
                bundle,
                nonlinearity,
                {"nonlinearity": round(nonlinearity, 4), "n": n},
            )

        # 独立调用回退路径：自行计算皮尔逊 r
        pairs = self._get_masked_pair(bundle, "pv", "op")
        xs, ys = _filter_pairs(pairs)  # P0 fix
        n = len(xs)

        logger.debug("[阀门非线性度] 输入: valid_pairs=%d", n)

        if n < MIN_POINTS_CORR:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS_CORR}
            )

        r = _pearson(xs, ys)
        nonlinearity = 1.0 - abs(r) if r is not None else 1.0

        logger.debug("[阀门非线性度] r=%.4f, nonlinearity=%.4f, n=%d", r or 0, nonlinearity, n)

        return self._make_result(
            bundle,
            nonlinearity,
            {"nonlinearity": round(nonlinearity, 4), "n": n},
        )


class ValveOperatingRangeCalculator(MetricCalculatorBase):
    """阀门运行区间计算器。

    计算 OP 信号的 [min, max] 区间。
    value = span（max - min），details 含 op_min / op_max / span。

    注意：metric_code 为 ``valve_operating_range``（calculator code），
    但 DB 列为 ``valve_op_min`` / ``valve_op_max``（Task 6 持久化时
    从 details 提取 op_min/op_max 单独传参）。
    """

    @property
    def metric_code(self) -> str:
        return "valve_operating_range"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算阀门运行区间.

        Args:
            bundle: 指标数据包（需含 op 信号，mask 为 op_valid）

        Returns:
            MetricResult：value 为 span（max-min）；
            details 含 op_min / op_max / span
        """
        vals = [float(v) for v in self._get_masked_values(bundle, "op") if v is not None]
        n = len(vals)

        logger.debug("[阀门运行区间] 输入: valid_points=%d", n)

        if n < MIN_POINTS_RANGE:
            return self._make_inconclusive(
                bundle, "insufficient_data", {"n": n, "min_required": MIN_POINTS_RANGE}
            )

        op_min = min(vals)
        op_max = max(vals)
        span = op_max - op_min

        logger.debug(
            "[阀门运行区间] min=%.4f, max=%.4f, span=%.4f, n=%d",
            op_min,
            op_max,
            span,
            n,
        )

        return self._make_result(
            bundle,
            span,
            {
                "op_min": round(op_min, 2),
                "op_max": round(op_max, 2),
                "span": round(span, 2),
            },
        )


__all__ = [
    "ValveLinearityCalculator",
    "ValveNonlinearityCalculator",
    "ValveOperatingRangeCalculator",
]
