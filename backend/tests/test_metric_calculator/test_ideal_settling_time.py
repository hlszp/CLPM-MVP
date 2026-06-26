"""理想稳态时间计算器单元测试（算法说明 §4.5）.

测试用例覆盖：
- 手动配置（最高优先级）
- 模型参数计算（T' = α·(τ+θ)）
- 控制类型默认值
- 未知控制类型（回退 120）
- 无配置（回退默认）

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import ControlType
from app.services.metric_calculator.ideal_settling_time import (
    IdealSettlingTimeCalculator,
    DEFAULT_IDEAL_SETTLING,
    FALLBACK_DEFAULT,
)

from .conftest import make_bundle


class TestIdealSettlingTime:
    """IdealSettlingTimeCalculator 测试。"""

    def test_manual_config_highest_priority(self, config_bundle):
        """手动配置（CONFIG 信号）优先级最高。"""
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(config_bundle)
        assert result.value == 45.0
        assert result.details["source"] == "manual"

    def test_model_based_calculation(self):
        """基于过程模型参数 T' = α·(τ+θ)。"""
        # FC: α=1.5, τ=10, θ=5 → T' = 1.5 × 15 = 22.5
        bundle = make_bundle(
            {
                "process_time_constant": [10.0],
                "process_dead_time": [5.0],
                "control_type": ["FC"],
            },
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == 22.5
        assert result.details["source"] == "model"

    def test_default_by_control_type_fc(self):
        """FC 默认 30 秒。"""
        bundle = make_bundle(
            {"control_type": ["FC"]},
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == DEFAULT_IDEAL_SETTLING[ControlType.FLOW.value]
        assert result.details["source"] == "default"

    def test_default_by_control_type_tc(self):
        """TC 默认 300 秒。"""
        bundle = make_bundle(
            {"control_type": ["TC"]},
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == 300.0

    def test_default_by_control_type_cc(self):
        """CC 默认 600 秒。"""
        bundle = make_bundle(
            {"control_type": ["CC"]},
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == 600.0

    def test_unknown_control_type_fallback(self):
        """未知控制类型 → 回退 120 秒。"""
        bundle = make_bundle(
            {"control_type": ["XX"]},
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == FALLBACK_DEFAULT

    def test_empty_config_uses_fallback(self):
        """无任何配置 → 回退默认 120。"""
        bundle = make_bundle(
            {},
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        assert result.value == FALLBACK_DEFAULT

    def test_manual_zero_falls_through_to_model(self):
        """手动配置为 0 → 转向模型/默认。"""
        bundle = make_bundle(
            {"ideal_settling_time": [0.0], "control_type": ["FC"]},
            tag_group="CONFIG",
            metric_code="ideal_settling_time",
            n=1,
        )
        calc = IdealSettlingTimeCalculator()
        result = calc.calculate(bundle)
        # 手动 0 无效 → 默认 FC=30
        assert result.value == 30.0
        assert result.details["source"] == "default"
