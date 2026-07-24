"""仪表故障率独立工具函数单元测试.

测试覆盖：
- 无故障（rate=0）
- 全故障（rate=100）
- 混合故障
- 单点多原因码（不重复计数）
- 非故障原因码忽略
- 空数据返回 None
- 原因码列表长度不足/超出（自动补齐/截断）
- point_count 参数显式指定

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3
"""

from __future__ import annotations

from app.contracts.data_types import OutlierReason as OR
from app.utils.instrument_fault_rate import (
    FAULT_REASONS,
    InstrumentFaultRateResult,
    calculate_instrument_fault_rate,
)


class TestCalculateInstrumentFaultRate:
    """calculate_instrument_fault_rate 工具函数测试。"""

    def test_zero_fault(self):
        """全量点无故障原因码 → fault_rate=0%。"""
        reasons = [[] for _ in range(10)]
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 0.0
        assert result.fault_point_count == 0
        assert result.sample_count == 10

    def test_all_frozen(self):
        """全部冻结 → fault_rate=100%，freeze_count=n。"""
        reasons = [[OR.FROZEN.value]] * 10
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 100.0
        assert result.freeze_count == 10
        assert result.fault_point_count == 10

    def test_all_out_of_range(self):
        """全部超量程 → fault_rate=100%，overrange_count=n。"""
        reasons = [[OR.OUT_OF_RANGE.value]] * 10
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 100.0
        assert result.overrange_count == 10

    def test_all_jump(self):
        """全部跳变 → fault_rate=100%，mutation_count=n。"""
        reasons = [[OR.JUMP.value]] * 10
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 100.0
        assert result.mutation_count == 10

    def test_mixed_faults(self):
        """3/10 点分别有超限/跳变/冻结 → fault_rate=30%。"""
        reasons = [
            [OR.OUT_OF_RANGE.value],
            [],
            [OR.JUMP.value],
            [],
            [],
            [OR.FROZEN.value],
            [],
            [],
            [],
            [],
        ]
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 30.0
        assert result.overrange_count == 1
        assert result.mutation_count == 1
        assert result.freeze_count == 1
        assert result.fault_point_count == 3

    def test_multiple_reasons_per_point(self):
        """一点叠加 FROZEN+JUMP → 各 count 各+1，fault_point_count 只+1。"""
        reasons = [
            [],
            [],
            [OR.FROZEN.value, OR.JUMP.value],
            [],
            [],
        ]
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 20.0  # 1/5
        assert result.freeze_count == 1
        assert result.mutation_count == 1
        assert result.fault_point_count == 1  # 不重复计数

    def test_non_fault_reasons_ignored(self):
        """SPIKE/NaN/QC_BAD/HF_NOISE/TS_ANOMALY 不计入仪表故障率。"""
        reasons = [
            [OR.SPIKE.value],
            [OR.NAN.value],
            [OR.QC_BAD.value],
            [OR.HF_NOISE.value],
            [OR.TS_ANOMALY.value],
            [],
        ]
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 0.0
        assert result.fault_point_count == 0

    def test_empty_data_returns_none(self):
        """空数据（point_count=0）→ 返回 None。"""
        result = calculate_instrument_fault_rate([])
        assert result is None

    def test_zero_point_count_returns_none(self):
        """point_count=0 → 返回 None。"""
        result = calculate_instrument_fault_rate([], point_count=0)
        assert result is None

    def test_reasons_shorter_than_point_count(self):
        """原因码列表长度 < point_count → 尾部补空列表，不影响计算。"""
        # 只有 3 个点的原因码，但 point_count=5
        reasons = [
            [OR.FROZEN.value],
            [],
            [OR.OUT_OF_RANGE.value],
        ]
        result = calculate_instrument_fault_rate(reasons, point_count=5)
        assert result is not None
        assert result.sample_count == 5
        assert result.fault_point_count == 2
        assert result.fault_rate == 40.0  # 2/5

    def test_reasons_longer_than_point_count(self):
        """原因码列表长度 > point_count → 截断到 point_count。"""
        reasons = [
            [OR.FROZEN.value],
            [OR.FROZEN.value],
            [OR.FROZEN.value],
            [OR.FROZEN.value],
            [OR.FROZEN.value],
        ]
        result = calculate_instrument_fault_rate(reasons, point_count=3)
        assert result is not None
        assert result.sample_count == 3
        assert result.fault_point_count == 3
        assert result.fault_rate == 100.0

    def test_explicit_point_count_none_uses_list_length(self):
        """point_count=None → 使用 len(reasons)。"""
        reasons = [[OR.JUMP.value], [], [OR.JUMP.value]]
        result = calculate_instrument_fault_rate(reasons, point_count=None)
        assert result is not None
        assert result.sample_count == 3
        assert result.fault_rate == 66.67

    def test_result_is_frozen_dataclass(self):
        """返回结果为 InstrumentFaultRateResult 不可变数据类。"""
        result = calculate_instrument_fault_rate([[]])
        assert isinstance(result, InstrumentFaultRateResult)
        # frozen=True 确保不可变
        try:
            result.fault_rate = 999.0  # type: ignore[misc]
            raise AssertionError("InstrumentFaultRateResult should be frozen")
        except AttributeError:
            pass  # 预期：不可变

    def test_fault_reasons_constant(self):
        """FAULT_REASONS 常量包含正确的原因码。"""
        assert OR.OUT_OF_RANGE.value in FAULT_REASONS
        assert OR.FROZEN.value in FAULT_REASONS
        assert OR.JUMP.value in FAULT_REASONS
        assert OR.SPIKE.value not in FAULT_REASONS
        assert OR.NAN.value not in FAULT_REASONS
        assert OR.QC_BAD.value not in FAULT_REASONS
        assert len(FAULT_REASONS) == 3

    def test_source_field_default(self):
        """source 字段默认为 'outlier_reasons'。"""
        result = calculate_instrument_fault_rate([[]])
        assert result is not None
        assert result.source == "outlier_reasons"

    def test_half_fault_rate(self):
        """50% 故障率边界测试。"""
        reasons = [[OR.FROZEN.value]] * 50 + [[]] * 50
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 50.0
        assert result.fault_point_count == 50
        assert result.sample_count == 100

    def test_single_fault_point(self):
        """1/100 点故障 → fault_rate=1.0%。"""
        reasons = [[]] * 99 + [[OR.JUMP.value]]
        result = calculate_instrument_fault_rate(reasons)
        assert result is not None
        assert result.fault_rate == 1.0
        assert result.fault_point_count == 1
