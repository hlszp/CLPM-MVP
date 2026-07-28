"""饱和率计算器单元测试（算法说明 §4.7）.

测试用例覆盖：
- 无饱和（rate=0）
- 高限饱和
- 低限饱和
- 混合饱和
- 手动模式不计入
- 空数据 / 自定义 epsilon

设计依据：算法说明 §4.7；GB/T 44693.2-2024 附录 F.3
"""

from __future__ import annotations

from app.services.metric_calculator.saturation import SaturationRateCalculator

from .conftest import make_bundle


class TestSaturationRate:
    """SaturationRateCalculator 测试。"""

    def test_no_saturation(self, auto_mode_bundle):
        """OP 在中间区域（50%）→ 无饱和。"""
        calc = SaturationRateCalculator()
        result = calc.calculate(auto_mode_bundle)
        assert result.value == 0.0
        assert result.details["saturation_type"] == "NONE"

    def test_high_saturation(self, saturation_bundle):
        """OP 接近高限（99.5）→ 高饱和。"""
        calc = SaturationRateCalculator()
        result = calc.calculate(saturation_bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"

    def test_low_saturation(self):
        """OP 接近低限（0.5）→ 低饱和。"""
        n = 100
        mode = [1] * n
        op = [0.5] * n
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "LOW"

    def test_mixed_saturation(self):
        """50% 高饱和 + 50% 低饱和 → rate=100, type=BOTH。"""
        n = 100
        mode = [1] * n
        op = [99.5] * 50 + [0.5] * 50
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "BOTH"

    def test_manual_mode_not_counted(self):
        """手动模式（mode=0）不计入饱和。"""
        n = 100
        mode = [0] * n  # 全手动
        op = [99.5] * n  # OP 饱和但不计入
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None  # 无自控时长 → INCONCLUSIVE

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_custom_epsilon(self):
        """自定义 epsilon（从 CONFIG 信号读取）。"""
        n = 100
        mode = [1] * n
        op = [95.0] * n  # OP=95, 默认 epsilon=2 时未饱和（< 98）
        # 设置 epsilon=10 → 95 >= 100-10=90 → 饱和
        bundle = make_bundle(
            {"mode": mode, "op": op, "saturation_epsilon": [10.0] * n},
            metric_code="saturation_rate",
        )
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["epsilon"] == 10.0

    def test_unparseable_op_skipped(self):
        """OP 解析失败跳过该点（不计入分子分母），不按 0.0 误计为低限饱和。"""
        n = 100
        mode = [1] * n
        op = [99.5] * 50 + ["bad"] * 50  # 后半段 OP 无法解析
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        # 仅 50 个可解析点计入，全为高限饱和
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"
        assert result.details["auto_duration_s"] == 50.0

    def test_mismatched_lengths_no_index_error(self):
        """mode 信号长于时间戳时按最短数组截断，不抛 IndexError。"""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        # 时间戳截断到 50 点，模拟数组长度不一致
        bundle.data_block.timestamps = bundle.data_block.timestamps[:50]
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["auto_duration_s"] == 50.0
