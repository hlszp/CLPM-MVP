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
        """OP 严格贴高限（100.0）→ 高饱和（ε=0 口径）."""
        calc = SaturationRateCalculator()
        result = calc.calculate(saturation_bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"

    def test_low_saturation(self):
        """OP 严格贴低限（0.0）→ 低饱和（ε=0 口径）."""
        n = 100
        mode = [1] * n
        op = [0.0] * n
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "LOW"

    def test_mixed_saturation(self):
        """50% 高饱和 + 50% 低饱和 → rate=100, type=BOTH。"""
        n = 100
        mode = [1] * n
        op = [100.0] * 50 + [0.0] * 50
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "BOTH"

    def test_manual_mode_not_counted(self):
        """手动模式（mode=0）不计入饱和分子；分母为总时长 → rate=0%.

        国标 F.3：Sa = AutoSaturateTime / AllTime。全程手动时
        AutoSaturateTime=0、AllTime>0 → Sa=0%（非 INCONCLUSIVE）。
        """
        n = 100
        mode = [0] * n  # 全手动
        op = [100.0] * n  # OP 饱和但非自控，不计入分子
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["saturation_type"] == "NONE"
        assert result.details["auto_duration_s"] == 0.0
        assert result.details["total_duration_s"] == 100.0

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_epsilon_from_algorithm_config(self):
        """ε 经配置链可配（2026-10-10 裁决）：algorithm_config 配 2.0 → OP=98 判饱和."""
        from unittest.mock import patch

        n = 100
        mode = [1] * n
        op = [98.0] * n  # 默认 ε=0 下不饱和
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        with (
            patch(
                "app.services.metric_calculator.saturation.get_algorithm_params",
                return_value={"saturation_epsilon": 2.0},
            ),
        ):
            result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"
        assert result.details["epsilon"] == 2.0

    def test_epsilon_config_signal_overrides_chain(self):
        """回路级 CONFIG saturation_epsilon 信号优先于配置链."""
        from unittest.mock import patch

        n = 100
        mode = [1] * n
        op = [50.0] * n
        signals = {"mode": mode, "op": op, "saturation_epsilon": [60.0]}
        bundle = make_bundle(signals, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        with (
            patch(
                "app.services.metric_calculator.saturation.get_algorithm_params",
                return_value={"saturation_epsilon": 2.0},
            ),
        ):
            result = calc.calculate(bundle)
        # ε=60 → 判定区间 [60, 40] 反转：OP=50 同时 ≤60+ 且 ≥40- → 低限侧先判
        assert result.details["epsilon"] == 60.0
        assert result.value == 100.0

    def test_custom_epsilon(self):
        """自定义 epsilon（从 CONFIG 信号读取）。"""
        n = 100
        mode = [1] * n
        op = [95.0] * n  # OP=95, 默认 epsilon=0 时未饱和（< 100）
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
        """OP 解析失败：计入分母总时长与自控时长，不计入饱和分子.

        分母=100s（全部点），auto_duration=100s（自控点含 OP 坏值），
        sat_high=50s（仅可解析的高限饱和点）→ rate=50%。
        """
        n = 100
        mode = [1] * n
        op = [100.0] * 50 + ["bad"] * 50  # 后半段 OP 无法解析（前半贴限饱和）
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        calc = SaturationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 50.0
        assert result.details["saturation_type"] == "HIGH"
        assert result.details["sat_high_duration_s"] == 50.0
        assert result.details["auto_duration_s"] == 100.0
        assert result.details["total_duration_s"] == 100.0

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
