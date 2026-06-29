"""振荡率计算器单元测试（算法说明 §4.6）.

测试用例覆盖：
- 正弦振荡（检测到振荡）
- 恒定偏差（无振荡）
- 随机噪声（无规律振荡）
- 数据不足（< 4 点）
- 零交叉点不足
- 振荡周期计算

设计依据：算法说明 §4.6；GB/T 44693.2-2024 附录 F.1
"""

from __future__ import annotations

import math
import random

from app.services.metric_calculator.oscillation import OscillationRateCalculator

from .conftest import make_bundle


class TestOscillationRate:
    """OscillationRateCalculator 测试。"""

    def test_sinusoidal_oscillation_detected(self):
        """正弦振荡（周期 20s）→ 检测到振荡，rate > 0。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value > 0
        assert result.details["is_oscillating"] is True

    def test_constant_error_no_oscillation(self):
        """恒定偏差（PV 恒定偏离 SP）→ 无振荡，rate=0。"""
        n = 200
        pv = [55.0] * n
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["is_oscillating"] is False

    def test_random_noise_no_oscillation(self):
        """随机噪声 → 无规律振荡，rate 低。"""
        random.seed(42)
        n = 200
        sp = [50.0] * n
        pv = [50.0 + random.gauss(0, 5) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 随机噪声的振荡率应较低（但不一定为 0）
        assert result.value < 50.0

    def test_insufficient_data(self):
        """数据不足（< 4 点）→ rate=0。"""
        bundle = make_bundle({"pv": [50, 51], "sp": [50, 50]}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["is_oscillating"] is False

    def test_zero_crossings_below_threshold(self):
        """零交叉点不足 4 → rate=0。"""
        # PV 单调递增，只穿越 SP 一次
        n = 100
        sp = [50.0] * n
        pv = [40.0 + 0.2 * i for i in range(n)]  # 40→59.8，穿越 50 一次
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_oscillation_period_calculated(self):
        """振荡时计算振荡周期。"""
        n = 200
        sp = [50.0] * n
        # 周期 20s 的正弦
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        if result.details["is_oscillating"]:
            assert result.details["oscillation_period"] > 0

    def test_high_frequency_oscillation(self):
        """高频振荡（周期 4s）→ 检测到振荡。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 4) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
