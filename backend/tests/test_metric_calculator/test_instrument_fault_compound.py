"""仪表故障率 FROZEN 复合判据单元测试（P1 整改）.

背景：FROZEN 改为仅标记不置 invalid 后，控制良好的平稳回路 PV 低方差
会被冻结检测大面积标记，若直接计入仪表故障会误报。复合判据：
    FROZEN 连续段持续 ≥ frozen_fault_min_minutes（阈值配置）
    且同期 OP 有变化（std > frozen_std_pct × 100）而 PV 不动
才计为仪表故障；缺 OP/时间戳/阈值配置时回落旧口径（FROZEN 直接计）。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3
"""

from __future__ import annotations

import dataclasses

import pytest

from app.contracts.data_types import ControlType
from app.contracts.data_types import OutlierReason as OR
from app.services.metric_calculator.instrument_fault import (
    InstrumentFaultRateCalculator,
    _contiguous_segments,
)
from app.services.preprocessing.thresholds import get_threshold

from .conftest import make_bundle


@pytest.fixture
def low_frozen_fault_minutes(monkeypatch):
    """将 frozen_fault_min_minutes 压到 0.05 分钟（3 秒），便于短数据测试。

    frozen_fault_min_minutes 未纳入 sys_config 运行时覆盖（PARAM_FIELDS），
    测试直接 monkeypatch 计算器内的阈值反查函数。
    """
    patched = dataclasses.replace(get_threshold(ControlType.FLOW), frozen_fault_min_minutes=0.05)
    monkeypatch.setattr(
        "app.services.metric_calculator.instrument_fault.get_threshold_by_sampling_freq",
        lambda _label: patched,
    )


class TestContiguousSegments:
    """_contiguous_segments 连续段切分。"""

    def test_empty(self):
        assert _contiguous_segments([]) == []

    def test_single(self):
        assert _contiguous_segments([3]) == [(3, 3)]

    def test_multiple_segments(self):
        assert _contiguous_segments([0, 1, 2, 5, 6, 9]) == [(0, 2), (5, 6), (9, 9)]


class TestFrozenCompoundCriterion:
    """FROZEN 复合判据：持续≥N 分钟且 OP 有变化才计仪表故障。"""

    def test_stuck_sensor_with_moving_op_counted(self, low_frozen_fault_minutes):
        """传感器真卡死：FROZEN 持续 10s ≥ 3s 且 OP 大幅变化 → 计故障。"""
        n = 10  # 1s 采样，零阶保持时长 = 9 + 1 = 10s ≥ 3s
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [10.0, 90.0, 10.0, 90.0, 10.0, 90.0, 10.0, 90.0, 10.0, 90.0]},
            outlier_reasons={"pv": [[OR.FROZEN.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["freeze_count"] == n

    def test_steady_loop_with_static_op_not_counted(self, low_frozen_fault_minutes):
        """平稳良好回路：FROZEN 持续够长但 OP 也不动（控制良好）→ 不计故障。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [52.0] * n},  # OP 恒定，std=0 ≤ epsilon
            outlier_reasons={"pv": [[OR.FROZEN.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["freeze_count"] == 0
        assert result.details["fault_point_count"] == 0

    def test_short_frozen_segment_not_counted(self):
        """FROZEN 持续不足 N（默认 5 分钟）→ 不计故障（即使 OP 在变化）。"""
        n = 10  # 10s << 300s 默认阈值
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [10.0, 90.0] * 5},
            outlier_reasons={"pv": [[OR.FROZEN.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["freeze_count"] == 0

    def test_only_long_segment_counted(self, low_frozen_fault_minutes):
        """两段 FROZEN：长段（OP 变化）计故障，短段不计。"""
        n = 10
        reasons = [[OR.FROZEN.value]] * 3 + [[]] * 2 + [[OR.FROZEN.value]] * 5
        # 短段 [0,2] 时长 3s ≥ 3s 但 OP std=0 → 不计；
        # 长段 [5,9] 时长 5s ≥ 3s 且 OP 变化 → 计
        op = [50.0] * 3 + [50.0, 50.0] + [10.0, 90.0, 10.0, 90.0, 10.0]
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": op},
            outlier_reasons={"pv": reasons},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        # 短段 OP std=0 不计，长段 5 点计
        assert result.details["freeze_count"] == 5
        assert result.details["fault_point_count"] == 5
        assert result.value == 50.0

    def test_missing_op_falls_back_to_legacy(self):
        """缺 OP 信号 → 回落旧口径：FROZEN 直接计故障（避免静默漏报）。"""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n},
            outlier_reasons={"pv": [[OR.FROZEN.value]] * n},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["freeze_count"] == n

    def test_other_fault_reasons_unaffected(self, low_frozen_fault_minutes):
        """OUT_OF_RANGE/JUMP 不走复合判据，仍直接计故障。"""
        n = 10
        reasons = [[OR.OUT_OF_RANGE.value]] + [[]] * 8 + [[OR.JUMP.value]]
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [50.0] * n},
            outlier_reasons={"pv": reasons},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.value == 20.0
        assert result.details["overrange_count"] == 1
        assert result.details["mutation_count"] == 1

    def test_frozen_mixed_with_jump_still_counts_jump(self, low_frozen_fault_minutes):
        """未确认 FROZEN 被剔除后，同点的 JUMP 仍计故障。"""
        n = 10
        reasons = [[OR.FROZEN.value, OR.JUMP.value]] + [[OR.FROZEN.value]] * 9
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [50.0] * n},  # OP 不动 → FROZEN 不确认
            outlier_reasons={"pv": reasons},
            metric_code="instrument_fault_rate",
        )
        result = InstrumentFaultRateCalculator().calculate(bundle)
        assert result.details["freeze_count"] == 0
        assert result.details["mutation_count"] == 1
        assert result.details["fault_point_count"] == 1
