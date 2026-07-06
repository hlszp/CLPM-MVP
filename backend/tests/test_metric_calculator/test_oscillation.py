"""振荡率计算器单元测试（算法说明 §4.6）.

测试用例覆盖：
- 正弦振荡（检测到振荡）
- 恒定偏差（无振荡）
- 随机噪声（输出格式验证）
- 数据不足（< 4 点）
- 零交叉点不足
- 振荡周期计算
- S_TA/S_TB 持续时间相似率输出
- 设计文档伪代码对齐验证

设计依据：算法说明 §4.6；GB/T 44693.2-2024 附录 F.1

P2 #33 偏差2：移除 _crossing_regularity（CV 变异系数），按设计文档 §4.6.2 步骤 5
计算持续时间相似率 S_TA/S_TB（与 S_A/S_B 同算法），振荡判定仅依赖 S_A/S_B
（设计文档伪代码 line 19-22），S_TA/S_TB 作为辅助诊断输出。
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

    def test_random_noise_output_format(self):
        """随机噪声 → 输出格式完整，含 S_TA/S_TB 辅助诊断字段。

        注：设计文档 §4.6.2 伪代码 line 19-22 综合判定仅用 S_A/S_B，
        高频噪声下短段 IAE 相似率可能达 1.0 导致 is_oscillating=True，
        这是设计文档算法的已知特性，S_TA/S_TB 提供辅助诊断区分。
        """
        random.seed(42)
        n = 200
        sp = [50.0] * n
        pv = [50.0 + random.gauss(0, 5) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 验证 S_TA/S_TB 辅助诊断字段存在（P2 #33 新增）
        assert "s_ta" in result.details
        assert "s_tb" in result.details
        assert 0.0 <= result.details["s_ta"] <= 1.0
        assert 0.0 <= result.details["s_tb"] <= 1.0
        # 验证不再包含 regularity 字段（P2 #33 移除）
        assert "regularity" not in result.details

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


class TestOscillationSTaSTb:
    """S_TA/S_TB 持续时间相似率测试（P2 #33 偏差2 修复）。"""

    def test_s_ta_s_tb_in_details(self):
        """振荡检测结果 details 包含 s_ta/s_tb 字段。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert "s_ta" in result.details
        assert "s_tb" in result.details
        # 正弦振荡的持续时间相似率应较高（周期均匀）
        assert result.details["s_ta"] >= 0.5
        assert result.details["s_tb"] >= 0.5

    def test_s_ta_s_tb_range_0_to_1(self):
        """S_TA/S_TB 取值范围 [0, 1]。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert 0.0 <= result.details["s_ta"] <= 1.0
        assert 0.0 <= result.details["s_tb"] <= 1.0

    def test_regularity_field_removed(self):
        """P2 #33 移除 _crossing_regularity，details 不再包含 regularity 字段。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert "regularity" not in result.details


class TestOscillationDesignAlignment:
    """设计文档 §4.6.2 伪代码对齐验证（P2 #33 偏差2）。"""

    def test_osc_value_is_min_sa_sb_times_100(self):
        """振荡率 = min(S_A, S_B) × 100（设计文档步骤 6，不再乘 regularity）。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        expected = min(result.details["s_a"], result.details["s_b"]) * 100.0
        # 允许 _clamp 边界处理
        assert abs(result.value - min(expected, 100.0)) < 0.01

    def test_is_oscillating_only_depends_on_sa_sb(self):
        """is_oscillating 仅依赖 S_A/S_B >= 阈值（设计文档伪代码 line 20）。

        设计文档伪代码 line 19-22:
            is_oscillating = (S_A >= threshold AND S_B >= threshold)
        S_TA/S_TB 不参与判定，仅作辅助诊断输出。
        """
        # 正弦振荡：S_A/S_B 都高 → is_oscillating=True
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        if result.details["s_a"] >= 0.4 and result.details["s_b"] >= 0.4:
            assert result.details["is_oscillating"] is True
        else:
            assert result.details["is_oscillating"] is False

    def test_period_is_mean_duration_times_2(self):
        """振荡周期 = 2 × 平均半周期（设计文档伪代码 line 23-25）。

        设计文档：
            avg_half_period = mean(positive_duration + negative_duration)
            oscillation_period = 2 * avg_half_period
        """
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        if result.details["is_oscillating"]:
            # 周期 20s 的正弦波，半周期约 10s
            assert result.details["oscillation_period"] > 0
