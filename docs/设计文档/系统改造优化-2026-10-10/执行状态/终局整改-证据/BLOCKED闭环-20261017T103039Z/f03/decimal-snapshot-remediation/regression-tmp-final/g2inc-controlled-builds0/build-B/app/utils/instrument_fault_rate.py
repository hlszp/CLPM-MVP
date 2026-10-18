"""仪表故障率独立计算工具函数.

公式：η_fault = N_fault / N_total × 100%

其中：
    N_fault：含仪表故障异常原因码的采样点数（不重复计数）
    N_total：评估时段总采样点数（point_count）

仪表故障对应的异常原因码（仅 PV 的超限/冻结 两类，
2026-10-10 用户裁决收窄）：
    - OUT_OF_RANGE：超量程
    - FROZEN：信号冻结

不计入仪表故障（含曾计入后移除的）：
    - JUMP：信号突变——COV 数据源/量纲异常/SP 阶跃场景误报多，
      突变更接近数据毛刺而非仪表硬件故障（标记本身保留，仍参与
      数据有效性判定，只是不再计入仪表故障率）
    - SPIKE/NaN/QC_BAD/HF_NOISE/TS_ANOMALY：数据质量/噪声范畴

本模块是**独立工具函数**，不依赖 MetricDataBundle/DataBlock 抽象，
任何持有 PV 异常原因码列表的模块均可直接调用。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3；
2026-10-10 故障码范围修订（用户裁决：仅超量程+冻结）
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts.data_types import OutlierReason

#: 仪表故障原因码集合（仅 PV 超限/冻结 两类）
FAULT_REASONS: frozenset[str] = frozenset(
    {
        OutlierReason.OUT_OF_RANGE.value,
        OutlierReason.FROZEN.value,
    }
)


@dataclass(frozen=True)
class InstrumentFaultRateResult:
    """仪表故障率计算结果.

    Attributes:
        fault_rate: 故障率百分比（0~100），保留 2 位小数
        fault_point_count: 含故障原因码的采样点数（不重复计数）
        sample_count: 总采样点数
        freeze_count: 冻结（FROZEN）点数（一个点可同时叠加多种原因码）
        overrange_count: 超量程（OUT_OF_RANGE）点数
        source: 数据来源标识
    """

    fault_rate: float
    fault_point_count: int
    sample_count: int
    freeze_count: int
    overrange_count: int
    source: str = "outlier_reasons"


def calculate_instrument_fault_rate(
    pv_outlier_reasons: list[list[str]],
    point_count: int | None = None,
) -> InstrumentFaultRateResult | None:
    """计算仪表故障率（独立工具函数）.

    复用预处理阶段 ``outlier_detection`` 的结果，统计仪表故障点占比
    （仅 PV 的 OUT_OF_RANGE/FROZEN 两类）。
    故障率用全量 ``point_count`` 做分母（非 masked_indices），确保
    故障点（pv_valid=False 被排除出 mask）也被统计。

    Args:
        pv_outlier_reasons: PV 信号每个采样点的异常原因码列表，
            外层长度应等于 point_count；不足时尾部自动补空列表，
            超出时截断到 point_count。
        point_count: 评估时段总采样点数。``None`` 时取
            ``len(pv_outlier_reasons)``。

    Returns:
        InstrumentFaultRateResult：含 fault_rate 及各类故障点计数；
        ``point_count <= 0`` 时返回 ``None``（空数据，调用方应标记 INCONCLUSIVE）。

    Examples:
        >>> from app.utils.instrument_fault_rate import calculate_instrument_fault_rate
        >>> reasons = [
        ...     ["OUT_OF_RANGE"],
        ...     [],
        ...     ["FROZEN", "JUMP"],
        ...     [],
        ... ]
        >>> result = calculate_instrument_fault_rate(reasons)
        >>> result.fault_rate
        50.0
        >>> result.fault_point_count
        2
        >>> result.freeze_count
        1
        >>> result.overrange_count
        1
    """
    n = point_count if point_count is not None else len(pv_outlier_reasons)

    if n <= 0:
        return None

    # 补齐或截断原因码列表到 n 个点
    reasons_list = pv_outlier_reasons[:n]
    if len(reasons_list) < n:
        reasons_list.extend([] for _ in range(n - len(reasons_list)))

    freeze_count = 0
    overrange_count = 0
    fault_pts = 0

    for reasons in reasons_list:
        reason_set = set(reasons)
        fault_reasons = reason_set & FAULT_REASONS
        if fault_reasons:
            fault_pts += 1
            if OutlierReason.FROZEN.value in fault_reasons:
                freeze_count += 1
            if OutlierReason.OUT_OF_RANGE.value in fault_reasons:
                overrange_count += 1

    fault_rate = max(0.0, min(100.0, (fault_pts / n) * 100.0))

    return InstrumentFaultRateResult(
        fault_rate=round(fault_rate, 2),
        fault_point_count=fault_pts,
        sample_count=n,
        freeze_count=freeze_count,
        overrange_count=overrange_count,
    )


__all__ = [
    "FAULT_REASONS",
    "InstrumentFaultRateResult",
    "calculate_instrument_fault_rate",
]
