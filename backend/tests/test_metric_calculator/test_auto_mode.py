"""自控率计算器单元测试（算法说明 §4.0.3）.

测试用例覆盖：
- 全自动模式（rate=100）
- 全手动模式（rate=0）
- 混合模式
- 空数据 / 单点
- 包含 Cascade/Remote 模式

设计依据：算法说明 §4.0.3；GB/T 44693.2-2024 附录 B.1
"""

from __future__ import annotations

from app.services.metric_calculator.auto_mode import AutoModeRateCalculator

from .conftest import make_bundle


class TestAutoModeRate:
    """AutoModeRateCalculator 测试。"""

    def test_all_auto_mode(self, auto_mode_bundle):
        """全自动模式（mode=1）→ rate=100。"""
        calc = AutoModeRateCalculator()
        result = calc.calculate(auto_mode_bundle)
        assert result.value == 100.0

    def test_all_manual_mode(self):
        """全手动模式（mode=0）→ rate=0。"""
        n = 100
        mode = [0] * n
        bundle = make_bundle({"mode": mode, "op": [50.0] * n}, metric_code="auto_mode_rate")
        calc = AutoModeRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_mixed_mode(self):
        """50% Auto + 50% Manual → rate=50。"""
        n = 100
        mode = [1] * 50 + [0] * 50
        bundle = make_bundle({"mode": mode, "op": [50.0] * n}, metric_code="auto_mode_rate")
        calc = AutoModeRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 50.0

    def test_cascade_and_remote_count_as_auto(self):
        """Cascade(2) 和 Remote(3) 计入自控。"""
        n = 90
        mode = [1] * 30 + [2] * 30 + [3] * 30
        bundle = make_bundle({"mode": mode, "op": [50.0] * n}, metric_code="auto_mode_rate")
        calc = AutoModeRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="auto_mode_rate")
        calc = AutoModeRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_single_point_inconclusive(self):
        """单点数据 → INCONCLUSIVE（无法计算时长）。"""
        bundle = make_bundle({"mode": [1], "op": [50.0]}, metric_code="auto_mode_rate")
        calc = AutoModeRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_invalid_mode_values_ignored(self):
        """无效 mode 值（-1/99）不计入自控。"""
        n = 100
        mode = [1] * 50 + [99] * 50  # 99 不是有效自控模式
        bundle = make_bundle({"mode": mode, "op": [50.0] * n}, metric_code="auto_mode_rate")
        calc = AutoModeRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 50.0
