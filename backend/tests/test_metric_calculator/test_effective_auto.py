"""有效自控率计算器单元测试（算法说明 §4.2）.

测试用例覆盖：
- 全自动且有效（rate=100）
- OP 饱和导致降级
- 偏差过大导致降级
- 手动模式
- 空数据
- auto_mode_rate 同时计算

设计依据：算法说明 §4.2；GB/T 44693.2-2024 附录 B.2
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import ConfidenceLevel
from app.services.metric_calculator.effective_auto import EffectiveAutoRateCalculator

from .conftest import make_bundle


class TestEffectiveAutoRate:
    """EffectiveAutoRateCalculator 测试。"""

    def test_full_effective_auto(self):
        """全自动、OP 未饱和、偏差合理 → R=100。"""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["auto_mode_rate"] == 100.0

    def test_op_saturation_reduces_rate(self):
        """OP 饱和 → 有效自控率 < 自控率。"""
        n = 100
        mode = [1] * n
        op = [99.5] * n  # 饱和
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        # auto_mode_rate=100, effective=0（全饱和）
        assert result.details["auto_mode_rate"] == 100.0
        assert result.value == 0.0

    def test_large_deviation_reduces_rate(self):
        """偏差超过 e_max → 有效自控率降为 0。"""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        pv = [90.0] * n  # 偏差 40 > e_max=5
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_manual_mode_zero_rate(self):
        """全手动 → auto_mode_rate=0, effective=0。"""
        n = 100
        mode = [0] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["auto_mode_rate"] == 0.0

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="effective_auto_rate")
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_partial_auto_partial_saturation(self):
        """50% Auto(有效) + 50% Auto(饱和) → R=50。"""
        n = 100
        mode = [1] * n
        op = [50.0] * 50 + [99.5] * 50
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 50.0

    def test_cascade_mode_counts_as_auto(self):
        """Cascade(2) 模式计入自控。"""
        n = 100
        mode = [2] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["auto_mode_rate"] == 100.0
