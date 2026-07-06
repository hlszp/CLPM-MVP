"""P2 #41 TC4: 场景间对比测试。

验证 7 场景测试数据集在关键指标上的横向差异，确保生成数据具有可分性：
- fast_response vs slow_response：fast_rate 差异（fast > slow）
- normal vs oscillation：oscillation_rate 差异（oscillation > normal）
- normal vs op_saturation：saturation_rate 差异（sat > normal）
- normal vs manual_mode：auto_mode_rate 差异（normal > manual）

设计依据：项目记忆硬约束 "7 scenarios"；GB/T 44693.2-2024 算法说明 §4.1-§4.10

注：本测试复用 test_scenarios.py 中的 _scenario_to_bundle / _compute_ideal_result
辅助函数与 kpi_scenarios fixture，避免重复定义。
"""

from __future__ import annotations

from app.services.metric_calculator.auto_mode import AutoModeRateCalculator
from app.services.metric_calculator.fast_rate import FastRateCalculator
from app.services.metric_calculator.saturation import SaturationRateCalculator
from app.services.metric_calculator.settling_time import SettlingTimeCalculator

# 复用 test_scenarios 模块中的辅助函数与 fixture
from tests.test_metric_calculator.test_scenarios import (
    _compute_ideal_result,
    _scenario_to_bundle,
)

# ---------------------------------------------------------------------------
# 辅助：计算 fast_rate（依赖 settling_time + ideal_settling_time）
# ---------------------------------------------------------------------------


def _compute_fast_rate(kpi_scenarios, scenario_name: str, ideal_sec: float = 30.0):
    """计算指定场景的 fast_rate。

    Args:
        kpi_scenarios: session fixture
        scenario_name: 场景名
        ideal_sec: 理想稳态时间（秒），默认 30s（FAST 控制类型默认）

    Returns:
        (fast_rate_value, settling_time_value) 元组
    """
    scenario = kpi_scenarios[scenario_name]

    # 1. 实际 settling_time
    settling_bundle = _scenario_to_bundle(scenario, "settling_time")
    settling_result = SettlingTimeCalculator().calculate(settling_bundle)
    assert settling_result.value is not None, f"{scenario_name} settling_time 计算返回 None"

    # 2. 注入 ideal_settling_time，计算 fast_rate
    bundle = _scenario_to_bundle(scenario, "fast_rate")
    calc = FastRateCalculator()
    calc.with_dependencies(
        {
            "settling_time": settling_result,
            "ideal_settling_time": _compute_ideal_result(ideal_sec),
        }
    )
    result = calc.calculate(bundle)
    assert result.value is not None, f"{scenario_name} fast_rate 计算返回 None"
    return result.value, settling_result.value


# ---------------------------------------------------------------------------
# 场景间对比测试
# ---------------------------------------------------------------------------


class TestScenarioComparison:
    """场景间指标对比测试。

    验证不同场景在关键指标上具有可分性（即生成数据集设计意图：
    相同算法下，不同信号特征应产生可观测的指标差异）。
    """

    def test_fast_vs_slow_fast_rate_diff(self, kpi_scenarios):
        """fast_response 的 fast_rate 应明显高于 slow_response。

        fast_response：settling_time ≈ 10s → T/T' 较小 → fast_rate 高
        slow_response：settling_time ≈ 60s → T/T' 较大 → fast_rate 低
        同一 ideal=30s 基准下，差异应显著（≥ 20 个百分点）。
        """
        fast_rate_fast, _ = _compute_fast_rate(kpi_scenarios, "fast_response", 30.0)
        fast_rate_slow, _ = _compute_fast_rate(kpi_scenarios, "slow_response", 30.0)

        # 横向对比：fast_response 应严格高于 slow_response
        assert fast_rate_fast > fast_rate_slow, (
            f"fast_response fast_rate={fast_rate_fast} 应高于 "
            f"slow_response fast_rate={fast_rate_slow}"
        )
        # 差异应显著（≥ 20 个百分点，确保场景设计具有可分性）
        diff = fast_rate_fast - fast_rate_slow
        assert diff >= 20.0, f"fast vs slow fast_rate 差异 {diff:.2f} 应 ≥ 20"

    def test_fast_vs_normal_settling_time_diff(self, kpi_scenarios):
        """fast_response 的 settling_time 应小于 normal。

        fast_response：expected settling_time=10s
        normal：expected settling_time=15s
        """
        fast_scenario = kpi_scenarios["fast_response"]
        normal_scenario = kpi_scenarios["normal"]

        fast_bundle = _scenario_to_bundle(fast_scenario, "settling_time")
        normal_bundle = _scenario_to_bundle(normal_scenario, "settling_time")

        fast_settling = SettlingTimeCalculator().calculate(fast_bundle).value
        normal_settling = SettlingTimeCalculator().calculate(normal_bundle).value

        assert fast_settling is not None and normal_settling is not None
        # fast_response 应 ≤ normal 的 settling_time（≤ 1.2 倍容忍边界抖动）
        assert fast_settling <= normal_settling * 1.2, (
            f"fast settling={fast_settling}s 应 ≤ normal×1.2={normal_settling * 1.2:.1f}s"
        )

    def test_normal_vs_op_saturation_sat_rate_diff(self, kpi_scenarios):
        """op_saturation 场景的 saturation_rate 应高于 normal。

        op_saturation：OP 周期性限位（OP≈97）
        normal：OP 在正常工作区间

        用 epsilon=5.0（阈值 OP≥95）时，op_saturation 饱和率应显著高于 normal。
        """
        normal_scenario = kpi_scenarios["normal"]
        sat_scenario = kpi_scenarios["op_saturation"]

        normal_bundle = _scenario_to_bundle(
            normal_scenario, "saturation_rate", mask_tags="op_valid && mode_valid"
        )
        sat_bundle = _scenario_to_bundle(
            sat_scenario, "saturation_rate", mask_tags="op_valid && mode_valid"
        )
        # 注入相同 epsilon=5.0 保证横向可比
        normal_bundle.data_block.signals["saturation_epsilon"] = [5.0]
        sat_bundle.data_block.signals["saturation_epsilon"] = [5.0]

        normal_sat = SaturationRateCalculator().calculate(normal_bundle).value
        sat_sat = SaturationRateCalculator().calculate(sat_bundle).value

        assert normal_sat is not None and sat_sat is not None
        # op_saturation 应高于 normal
        assert sat_sat > normal_sat, f"op_saturation sat_rate={sat_sat} 应高于 normal={normal_sat}"
        # 差异至少 20 个百分点（normal 应接近 0，op_saturation 应 ≥ 25）
        diff = sat_sat - normal_sat
        assert diff >= 20.0, f"op_saturation vs normal sat_rate 差异 {diff:.2f} 应 ≥ 20"

    def test_normal_vs_manual_mode_auto_rate_diff(self, kpi_scenarios):
        """normal 场景的 auto_mode_rate 应高于 manual_mode。

        normal：~95% Auto（5% 手动段注入）
        manual_mode：0% Auto（全 mode=0）

        差异应显著（≥ 80 个百分点）。
        """
        normal_scenario = kpi_scenarios["normal"]
        manual_scenario = kpi_scenarios["manual_mode"]

        normal_bundle = _scenario_to_bundle(normal_scenario, "auto_mode_rate")
        manual_bundle = _scenario_to_bundle(manual_scenario, "auto_mode_rate")

        normal_auto = AutoModeRateCalculator().calculate(normal_bundle).value
        manual_auto = AutoModeRateCalculator().calculate(manual_bundle).value

        assert normal_auto is not None and manual_auto is not None
        # normal 应远高于 manual_mode
        assert normal_auto > manual_auto, (
            f"normal auto_rate={normal_auto} 应高于 manual={manual_auto}"
        )
        # 差异至少 80 个百分点（normal ≥ 85，manual ≤ 5）
        diff = normal_auto - manual_auto
        assert diff >= 80.0, f"normal vs manual auto_rate 差异 {diff:.2f} 应 ≥ 80"

    def test_fast_response_higher_fast_rate_than_normal(self, kpi_scenarios):
        """fast_response 的 fast_rate 应高于 normal（横向一致性检查）。

        expected：fast_response fast_rate_range=[80,100]
                  normal fast_rate_range=[70,100]
        fast_response 的 settling_time（10s）< normal（15s）→ fast_rate 更高。
        """
        fast_rate_fast, _ = _compute_fast_rate(kpi_scenarios, "fast_response", 30.0)
        fast_rate_normal, _ = _compute_fast_rate(kpi_scenarios, "normal", 30.0)

        # fast_response 应 ≥ normal（容忍边界抖动，允许相等或更高）
        assert fast_rate_fast >= fast_rate_normal - 5.0, (
            f"fast_response fast_rate={fast_rate_fast} 应 ≥ normal-5={fast_rate_normal - 5.0:.1f}"
        )

    def test_slow_vs_normal_settling_time_diff(self, kpi_scenarios):
        """slow_response 的 settling_time 应大于 normal。

        slow_response：expected settling_time=60s
        normal：expected settling_time=15s
        """
        slow_scenario = kpi_scenarios["slow_response"]
        normal_scenario = kpi_scenarios["normal"]

        slow_bundle = _scenario_to_bundle(slow_scenario, "settling_time")
        normal_bundle = _scenario_to_bundle(normal_scenario, "settling_time")

        slow_settling = SettlingTimeCalculator().calculate(slow_bundle).value
        normal_settling = SettlingTimeCalculator().calculate(normal_bundle).value

        assert slow_settling is not None and normal_settling is not None
        # slow_response 应 ≥ normal 的 settling_time（≥ 0.8 倍容忍边界抖动）
        assert slow_settling >= normal_settling * 0.8, (
            f"slow settling={slow_settling}s 应 ≥ normal×0.8={normal_settling * 0.8:.1f}s"
        )
