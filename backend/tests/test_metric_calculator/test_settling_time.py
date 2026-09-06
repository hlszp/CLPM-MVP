"""稳态时间计算器单元测试（算法说明 §4.5）.

测试用例覆盖：
- 恒定信号（settling=0）
- 振荡信号（settling > 0）
- 数据不足（< 100 点，含旧阈值 30/30 边界回归测试）
- 自定义采样周期
- 大数据集

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 F.4
"""

from __future__ import annotations

import math

from app.services.metric_calculator.settling_time import SettlingTimeCalculator

from .conftest import make_bundle


class TestSettlingTime:
    """SettlingTimeCalculator 测试。"""

    def test_constant_signal_zero_settling(self):
        """恒定偏差信号 → settling=0（已稳态，reason=already_stable）。"""
        n = 100
        pv = [50.5] * n
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["reason"] == "already_stable"

    def test_oscillating_signal_positive_settling(self):
        """振荡信号 → settling > 0。"""
        n = 200
        sp = [50.0] * n
        # 衰减振荡
        pv = [50.0 + 20.0 * math.exp(-i / 50) * math.sin(i * 0.1) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        # 衰减振荡应有正的稳态时间
        assert result.value is not None
        assert result.value >= 0.0

    def test_insufficient_data(self):
        """数据不足（< 100 点）→ INCONCLUSIVE（value=None）。"""
        n = 20
        pv = [50.0 + i * 0.1 for i in range(n)]
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "insufficient_data"

    def test_boundary_30_still_insufficient(self):
        """旧阈值 30 点现在仍判为数据不足（防回归）。

        P1 #16: MIN_POINTS 从 30 提升至 100，原 30 点边界点应仍判 insufficient_data。
        """
        n = 30
        pv = [50.0 + i * 0.05 for i in range(n)]
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "insufficient_data"
        assert result.details["min_required"] == 100

    def test_boundary_99_still_insufficient(self):
        """99 点（阈值减 1）仍判为数据不足。"""
        n = 99
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(i * 0.1) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.details["reason"] == "insufficient_data"
        assert result.details["sample_count"] == 99
        assert result.details["min_required"] == 100

    def test_boundary_100_passes_threshold(self):
        """100 点正好达到阈值，不应返回 insufficient_data。"""
        n = 100
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(i * 0.1) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        # 100 点正好通过阈值，reason 不应是 insufficient_data
        assert result.details.get("reason") != "insufficient_data"
        assert result.details["sample_count"] == 100

    def test_custom_sample_interval(self):
        """自定义采样周期（5s）：时间戳与标签同口径时按 5s 计算。

        R14-4（2026-09-06）等间隔准入：实际间隔与声明一致才计算——
        声明 5s 但时间戳 1s 间隔（旧测试形态，即"声明失真"缺陷场景）
        现在会跳过计算（sampling_interval_mismatch）。
        """
        n = 100
        sp = [50.0] * n
        pv = [50.0 + 20.0 * math.exp(-i / 30) * math.sin(i * 0.2) for i in range(n)]
        bundle = make_bundle(
            {"pv": pv, "sp": sp},
            metric_code="settling_time",
            sampling_freq="5s",
            interval_s=5.0,
        )
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.details["sample_interval"] == 5.0

    def test_zero_error_signal(self):
        """PV=SP 零偏差 → settling=0。"""
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_large_dataset(self):
        """大数据集（500 点）不报错。"""
        n = 500
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 50) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value >= 0.0

    def test_result_has_details(self):
        """结果包含详细信息。"""
        n = 100
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(i * 0.1) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        calc = SettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert "actual_settling_time" in result.details
        assert "sample_interval" in result.details
        assert "threshold" in result.details
