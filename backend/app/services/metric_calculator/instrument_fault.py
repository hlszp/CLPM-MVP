"""仪表故障率计算器（Phase 1，HiaMonitor 借鉴）.

公式：η_fault = N_fault / N_total × 100%

其中：
    N_fault：含仪表故障异常原因码的采样点数（不重复计数）
    N_total：评估时段总采样点数（point_count）

仪表故障对应的异常原因码（HiaMonitor 超限/冻结/突变 三类）：
    - OUT_OF_RANGE：超量程
    - FROZEN：信号冻结（复合判据，见下）
    - JUMP：信号突变

FROZEN 复合判据（P1 整改：FROZEN 改为仅标记不置 invalid 后的误报抑制）：
    控制良好的平稳回路 PV 长期低方差会被冻结检测大面积标记，直接计入
    仪表故障会误报。只有同时满足以下两个条件的 FROZEN 连续段才计故障：
        1. 段持续时间 ≥ frozen_fault_min_minutes（阈值配置，按控制类型）
        2. 同期 OP 有变化（std > frozen_std_pct × 100，归一化量纲）而 PV 不动
           ——控制器在调节而 PV 无响应，才是真仪表卡死的特征
    缺 OP 信号/时间戳/阈值配置时无法执行复合判据，回落旧口径
    （FROZEN 直接计故障），避免静默漏报。

复用既有 ``outlier_detection`` 预处理结果（DataBlock.outlier_reasons），
无需新增预处理步骤。SPIKE/NaN/QC_BAD/HF_NOISE/TS_ANOMALY 不计入仪表故障。

核心统计逻辑委托独立工具函数
``app.utils.instrument_fault_rate.calculate_instrument_fault_rate``：
本计算器先按复合判据过滤未确认的 FROZEN 标记，再调用工具函数统计，
确保计数逻辑单一来源、其他模块可直接复用。

定位：AGGREGATABLE 辅助指标，参与节点级聚合。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3
"""

from __future__ import annotations

import logging

import numpy as np

from app.contracts.data_types import DataBlock, MetricDataBundle, MetricResult, OutlierReason
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.preprocessing.thresholds import get_threshold_by_sampling_freq
from app.utils.instrument_fault_rate import calculate_instrument_fault_rate

logger = logging.getLogger(__name__)

_FROZEN = OutlierReason.FROZEN.value


class InstrumentFaultRateCalculator(MetricCalculatorBase):
    """仪表故障率计算器。

    复用 ``DataBlock.outlier_reasons["pv"]`` 统计三类仪表故障点占比。
    FROZEN 按复合判据确认（持续≥N 分钟且 OP 有变化），未确认的
    FROZEN 标记在统计前剔除（平稳回路误报抑制）。
    故障率用全量 ``point_count`` 做分母（非 masked_indices），确保
    故障点（pv_valid=False 被排除出 mask）也被统计。

    可信度仍用 mask ``valid_rate``：故障点 pv_valid=False → 排除出 mask
    → valid_rate 降低 → 可信度降级（合理：数据有效率低则可信度低）。

    核心统计委托 ``app.utils.instrument_fault_rate`` 工具函数，
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

        # 读取 PV 异常原因码，按复合判据过滤未确认的 FROZEN 标记
        pv_reasons = block.outlier_reasons.get("pv", [])
        filtered_reasons = self._filter_unconfirmed_frozen(block, pv_reasons)

        # 委托独立工具函数执行核心统计
        result = calculate_instrument_fault_rate(filtered_reasons, point_count=n)

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

    # ------------------------------------------------------------------
    # FROZEN 复合判据
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_unconfirmed_frozen(
        block: DataBlock,
        pv_reasons: list[list[str]],
    ) -> list[list[str]]:
        """剔除未通过复合判据的 FROZEN 标记（平稳回路误报抑制）.

        FROZEN 连续段同时满足「持续 ≥ frozen_fault_min_minutes」且
        「同期 OP std > frozen_std_pct × 100」才保留 FROZEN 计故障；
        否则从原因码中剔除（该点不再计入仪表故障）。

        无法执行复合判据（无 FROZEN 标记 / 缺 OP 信号 / 缺时间戳 /
        采样率无对应阈值配置）时原样返回，回落旧口径。

        Args:
            block: 预处理数据块（含 op 信号、timestamps、sampling_freq）
            pv_reasons: PV 每点异常原因码列表

        Returns:
            过滤后的原因码列表（与输入等长语义，未确认段的 FROZEN 被移除）
        """
        n = block.point_count
        # 补齐/截断到 n 点，与工具函数口径一致，保证索引对齐 timestamps/op
        reasons: list[list[str]] = [list(r) for r in pv_reasons[:n]]
        if len(reasons) < n:
            reasons.extend([] for _ in range(n - len(reasons)))

        frozen_indices = [i for i, r in enumerate(reasons) if _FROZEN in r]
        if not frozen_indices:
            return pv_reasons

        op = block.signals.get("op")
        timestamps = block.timestamps
        threshold = get_threshold_by_sampling_freq(block.sampling_freq)
        if not op or len(timestamps) < n or len(timestamps) < 2 or threshold is None:
            # 无法执行复合判据 → 回落旧口径（FROZEN 直接计故障），避免静默漏报
            return pv_reasons

        min_duration_s = threshold.frozen_fault_min_minutes * 60.0
        # OP 为归一化量纲（0~100）， epsilon 与冻结检测同尺度
        op_std_epsilon = threshold.frozen_std_pct * 100.0
        sample_interval_s = float(threshold.base_sampling_freq)

        confirmed: set[int] = set()
        for start, end in _contiguous_segments(frozen_indices):
            # 零阶保持：段时长 = 首尾时间差 + 一个采样间隔（末点也代表一段时长）
            duration_s = (timestamps[end] - timestamps[start]).total_seconds() + sample_interval_s
            if duration_s < min_duration_s:
                continue
            op_vals: list[float] = []
            for i in range(start, end + 1):
                if i >= len(op):
                    break
                try:
                    op_vals.append(float(op[i]))
                except (TypeError, ValueError):
                    continue
            if len(op_vals) >= 2 and float(np.std(op_vals)) > op_std_epsilon:
                confirmed.update(range(start, end + 1))

        if len(confirmed) == len(frozen_indices):
            return pv_reasons

        return [
            [r for r in point_reasons if r != _FROZEN or i in confirmed]
            for i, point_reasons in enumerate(reasons)
        ]


def _contiguous_segments(indices: list[int]) -> list[tuple[int, int]]:
    """将升序索引列表切分为连续段 [(start, end), ...]（含端点）."""
    if not indices:
        return []
    segments: list[tuple[int, int]] = []
    start = prev = indices[0]
    for idx in indices[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        segments.append((start, prev))
        start = prev = idx
    segments.append((start, prev))
    return segments


__all__ = ["InstrumentFaultRateCalculator"]
