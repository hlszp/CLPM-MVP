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

定位：AGGREGATABLE 辅助指标，参与节点级聚合。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3
"""

from __future__ import annotations

import logging

from app.contracts.data_types import MetricDataBundle, MetricResult, OutlierReason
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 仪表故障原因码集合（仅超限/冻结/突变 三类）
_FAULT_REASONS: frozenset[str] = frozenset(
    {
        OutlierReason.OUT_OF_RANGE.value,
        OutlierReason.FROZEN.value,
        OutlierReason.JUMP.value,
    }
)


class InstrumentFaultRateCalculator(MetricCalculatorBase):
    """仪表故障率计算器。

    复用 ``DataBlock.outlier_reasons["pv"]`` 统计三类仪表故障点占比。
    故障率用全量 ``point_count`` 做分母（非 masked_indices），确保
    故障点（pv_valid=False 被排除出 mask）也被统计。

    可信度仍用 mask ``valid_rate``：故障点 pv_valid=False → 排除出 mask
    → valid_rate 降低 → 可信度降级（合理：数据有效率低则可信度低）。
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

        # 读取 PV 异常原因码（可能缺失或长度不足，需补齐）
        pv_reasons = block.outlier_reasons.get("pv", [])
        if len(pv_reasons) < n:
            pv_reasons = pv_reasons + [[] for _ in range(n - len(pv_reasons))]

        freeze_count = 0
        mutation_count = 0
        overrange_count = 0
        fault_pts = 0

        for reasons in pv_reasons[:n]:
            reason_set = set(reasons)
            fault_reasons = reason_set & _FAULT_REASONS
            if fault_reasons:
                fault_pts += 1
                if OutlierReason.FROZEN.value in fault_reasons:
                    freeze_count += 1
                if OutlierReason.JUMP.value in fault_reasons:
                    mutation_count += 1
                if OutlierReason.OUT_OF_RANGE.value in fault_reasons:
                    overrange_count += 1

        fault_rate = self._clamp((fault_pts / n) * 100.0)

        logger.debug(
            "[仪表故障率] fault_pts=%d/%d, rate=%.2f%%, freeze=%d, jump=%d, oor=%d",
            fault_pts,
            n,
            fault_rate,
            freeze_count,
            mutation_count,
            overrange_count,
        )

        return self._make_result(
            bundle,
            fault_rate,
            {
                "fault_rate": round(fault_rate, 2),
                "freeze_count": freeze_count,
                "mutation_count": mutation_count,
                "overrange_count": overrange_count,
                "fault_point_count": fault_pts,
                "sample_count": n,
                "source": "outlier_reasons",
            },
        )


__all__ = ["InstrumentFaultRateCalculator"]
