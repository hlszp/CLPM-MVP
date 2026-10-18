"""指标计算器抽象接口（Phase 3 任务 3.1）.

定义所有指标计算器必须实现的契约：输入 MetricDataBundle，输出 MetricResult。
指标计算器只消费 MetricDataBundle（含 DataBlock + mask + lineage），
不直接查询数据库，保证算法层与数据层解耦。

设计依据：ADS §10.2, 数据流程图 §7.5, 算法说明 §3.6
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.contracts.data_types import MetricDataBundle, MetricResult


class MetricCalculator(ABC):
    """指标计算器抽象接口（设计依据：ADS §10.2, 数据流程图 §7.5）.

    所有指标计算器（准确率/快速率/稳定率等 12 个）均实现此接口。
    计算器只消费 MetricDataBundle，输出含 value/confidence_level/lineage/details
    的 MetricResult。

    依赖注入：
        部分指标依赖其他指标的计算结果（如稳定率依赖振荡率）。
        编排层（Phase 4）在调用 ``calculate`` 前，通过 ``dependencies``
        属性注入前置指标结果，计算器从 ``self.dependencies`` 读取。
    """

    #: 依赖的其他指标代码列表（如 ``["oscillation_rate"]``），子类按需覆盖。
    depends_on: list[str] = []

    def __init__(self) -> None:
        #: 前置指标结果字典 ``{metric_code: MetricResult}``，由编排层注入。
        self.dependencies: dict[str, MetricResult] = {}

    @property
    @abstractmethod
    def metric_code(self) -> str:
        """指标代码，如 ``"accuracy_rate"``."""

    @abstractmethod
    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算指标值.

        Args:
            bundle: 指标数据包，含 DataBlock + mask_expression + masked_indices + lineage

        Returns:
            MetricResult，含 value/confidence_level/lineage/details；
            数据不足（E 级可信度）时 value=None（INCONCLUSIVE）。

        设计依据：数据流程图 §7.5
        """

    def with_dependencies(self, deps: dict[str, MetricResult]) -> MetricCalculator:
        """注入前置指标结果（链式调用）.

        Args:
            deps: ``{metric_code: MetricResult}`` 前置指标结果

        Returns:
            self（支持链式调用）
        """
        self.dependencies = dict(deps)
        return self


__all__ = ["MetricCalculator"]
