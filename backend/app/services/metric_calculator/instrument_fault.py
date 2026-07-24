"""仪表故障率计算器（Phase 1，HiaMonitor 借鉴）.

公式：η_fault = N_fault / N_total × 100%

其中：
    N_fault：含仪表故障异常原因码的采样点数（不重复计数）
    N_total：评估时段总采样点数（point_count）

仪表故障对应的异常原因码（HiaMonitor 超限/冻结/突变 三类）：
    - OUT_OF_RANGE：超量程
    - FROZEN：信号冻结
    - JUMP：信号突变

复用既有 ``outlier_detection`` 预处理结果（DataBlock.outlier_reasons），
无需新增预处理步骤。SPIKE/NaN/QC_BAD/HF_NOISE/TS_ANOMALY 不计入仪表故障。

核心计算逻辑已抽取为独立工具函数
``app.utils.instrument_fault_rate.calculate_instrument_fault_rate``，
本计算器委托调用该函数，确保逻辑单一来源、其他模块可直接复用。

定位：AGGREGATABLE 辅助指标，参与节点级聚合。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3
"""

from __future__ import annotations

import logging

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase
from app.utils.instrument_fault_rate import calculate_instrument_fault_rate

logger = logging.getLogger(__name__)


class InstrumentFaultRateCalculator(MetricCalculatorBase):
    """仪表故障率计算器。

    复用 ``DataBlock.outlier_reasons["pv"]`` 统计三类仪表故障点占比。
    故障率用全量 ``point_count`` 做分母（非 masked_indices），确保
    故障点（pv_valid=False 被排除出 mask）也被统计。

    可信度仍用 mask ``valid_rate``：故障点 pv_valid=False → 排除出 mask
    → valid_rate 降低 → 可信度降级（合理：数据有效率低则可信度低）。

    核心计算委托 ``app.utils.instrument_fault_rate`` 工具函数，
    便于其他模块在脱离 MetricDataBundle 抽象时直接复用。
    """

    @property
    def metric_code(self) -> str:
        return "instrument_fault_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算仪表故障率.

        Args:
            bundle: 指标数据包（需含 pv 信号 + outlier_reasons）

        Returns:
            MetricResult：value 为故障率 0~100；
            details 含 freeze_count/mutation_count/overrange_count/
            fault_point_count/sample_count/source
        """
        block = bundle.data_block
        n = block.point_count

        logger.debug("[仪表故障率] 输入: point_count=%d", n)

        if n <= 0:
            return self._make_inconclusive(bundle, "empty_data_block")

        # 读取 PV 异常原因码
        pv_reasons = block.outlier_reasons.get("pv", [])

        # 委托独立工具函数执行核心计算
        result = calculate_instrument_fault_rate(pv_reasons, point_count=n)

        if result is None:
            return self._make_inconclusive(bundle, "empty_data_block")

        logger.debug(
            "[仪表故障率] fault_pts=%d/%d, rate=%.2f%%, freeze=%d, jump=%d, oor=%d",
            result.fault_point_count,
            result.sample_count,
            result.fault_rate,
            result.freeze_count,
            result.mutation_count,
            result.overrange_count,
        )

        return self._make_result(
            bundle,
            result.fault_rate,
            {
                "fault_rate": result.fault_rate,
                "freeze_count": result.freeze_count,
                "mutation_count": result.mutation_count,
                "overrange_count": result.overrange_count,
                "fault_point_count": result.fault_point_count,
                "sample_count": result.sample_count,
                "source": result.source,
            },
        )


__all__ = ["InstrumentFaultRateCalculator"]
