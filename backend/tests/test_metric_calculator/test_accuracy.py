"""准确率计算器单元测试（算法说明 §4.4）.

测试用例覆盖：
- 零偏差（PV=SP → A=100）
- 小余差（工程容限口径，A 接近 100）
- 恒定小余差（稳定回路不再被自身离散度惩罚）
- 大余差（超容限 → 低分）
- 空数据（INCONCLUSIVE）
- CONFIG 中 e_max 绝对值覆盖
- 容限比例参数 e_max_tolerance_ratio 可调
- 可信度判定（valid_rate 影响）

v2.2 口径（2026-10-10 稳定回路误判整改）：|E|max = 容限比例×量程（默认 2%），
r = |Ē|/(tolerance×U)；恒定余差退化分支与数据驱动 e_max 已移除。

设计依据：算法说明 §4.4；GB/T 44693.2-2024 附录 B.3
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import ConfidenceLevel
from app.services.metric_calculator.accuracy import AccuracyRateCalculator

from .conftest import make_bundle


class TestAccuracyRate:
    """AccuracyRateCalculator 测试。"""

    def test_zero_error_returns_100(self, zero_error_bundle):
        """PV=SP 零偏差 → 准确率 100%。"""
        # v6.2 P2-2：可信度等级 = DataBlock.loop_confidence_level（回路级）
        zero_error_bundle.data_block.loop_confidence_level = ConfidenceLevel.A.value
        calc = AccuracyRateCalculator()
        result = calc.calculate(zero_error_bundle)
        assert result.value == 100.0
        assert result.confidence_level == ConfidenceLevel.A.value

    def test_small_error_high_accuracy(self):
        """小幅波动余差（mean=0.109，容限 2）→ A≈99.4。"""
        n = 100
        sp = [50.0] * n
        pv = [50.1] * (n - 1) + [51.0]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # r = 0.109/2 = 0.0545 → A ≈ 99.4
        assert 95.0 < result.value < 100.0
        assert result.details["mean_abs_error"] == 0.109
        assert result.details["e_max_source"] == "tolerance_ratio"

    def test_stable_tiny_residual_not_punished(self):
        """稳定回路微小恒定余差不再被自身离散度惩罚（整改核心回归用例）.

        实证场景（05TY05P0803_PIDA，归一化尺度）：余差 0.066（量程 0.066%）
        旧算法 r=mean/(max-mean) 爆炸 → A=11.1；新口径 r=0.066/2 → A≈96.7。
        """
        n = 721
        # 90% 点余差 0.066，10% 点 0.072（轻微离散度，旧口径 r≈5 → 0 分）
        pv = [50.066] * 650 + [50.072] * 71
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert 95.0 < result.value <= 100.0

    def test_constant_offset_uses_tolerance(self):
        """恒定余差走容限口径：|Ē|=2（量程 2%）→ r=1 → A≈36.8。"""
        n = 100
        pv = [52.0] * n  # 恒定偏离 SP 2.0
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value == 36.79  # 1 - 1×(1-e^-1) = e^-1 ≈ 0.3679
        assert result.details["e_max"] == 2.0

    def test_constant_offset_beyond_tolerance_zero(self):
        """恒定余差超过容限多倍 → A 扣到 0（不出现负值/满分）。"""
        n = 100
        pv = [60.0] * n  # |Ē|=10 = 5×容限
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_large_error_low_accuracy(self):
        """大偏差（mean=78，容限 2）→ r=39 → A=0。

        旧口径该用例 r=mean/(max-mean)=39 巧合同值；新口径 r 直接=39。
        """
        n = 100
        sp = [10.0] * n
        pv = [88.0, 89.0, 87.0, 90.0, 86.0] * 20
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value < 5.0  # 大偏差 → 低准确率
        assert result.details["r"] > 1.0

    def test_empty_data_inconclusive(self, empty_bundle):
        """空数据 → INCONCLUSIVE。"""
        calc = AccuracyRateCalculator()
        result = calc.calculate(empty_bundle)
        assert result.value is None
        assert result.confidence_level == ConfidenceLevel.E.value

    def test_custom_e_max_from_config(self):
        """CONFIG 信号中 e_max 绝对值覆盖生效（优先于容限比例）。"""
        n = 50
        pv = [55.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "e_max": [10.0] * n},
            metric_code="accuracy_rate",
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        # e_max=10, mean_abs_error=5, r=0.5
        assert result.value is not None
        assert result.details["e_max"] == 10.0
        assert result.details["e_max_source"] == "config"

    def test_tolerance_ratio_configurable(self):
        """容限比例经配置链可调：1% 容限下 |Ē|=1 → r=1 → A≈36.8。"""
        from unittest.mock import patch

        n = 100
        pv = [51.0] * n
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        with (
            patch(
                "app.services.metric_calculator.accuracy.get_algorithm_params",
                return_value={"e_max_tolerance_ratio": 0.01},
            ),
        ):
            result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value == 36.79  # r=1
        assert result.details["e_max"] == 1.0

    def test_confidence_level_based_on_loop_confidence(self):
        """可信度等级 = DataBlock.loop_confidence_level（回路级，P2-2）。

        v6.2 可信度统一 Phase 2：指标可信度不再由 metric-level valid_rate 决定，
        而是统一使用回路级 loop_confidence_level。
        valid_rate 仅用于可计算性判定（< 0.20 → INCONCLUSIVE）。
        """
        n = 100
        pv = [50.0] * n
        sp = [50.0] * n
        # 50% 有效（>= 0.20 阈值，可计算）
        validity = {"pv_valid": [True] * 50 + [False] * 50, "sp_valid": [True] * n}
        bundle = make_bundle(
            {"pv": pv, "sp": sp},
            validity,
            mask_expression="pv_valid && sp_valid",
            metric_code="accuracy_rate",
            loop_confidence_level=ConfidenceLevel.D.value,
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        # valid_rate = 50/100 = 0.5 >= 0.20 → 可计算，value 非 None
        assert result.value is not None
        # 可信度 = loop_confidence_level（D），不再由 metric-level vr 决定
        assert result.confidence_level == ConfidenceLevel.D.value

    def test_single_point(self):
        """单点数据可计算（不报错）；单点余差按容限口径扣分。"""
        bundle = make_bundle(
            {"pv": [52.0], "sp": [50.0]},
            metric_code="accuracy_rate",
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # |Ē|=2，容限 2 → r=1 → A≈36.8
        assert result.value == 36.79

    def test_pv_range_config_signal_overrides(self):
        """CONFIG pv_range 信号覆盖归一化默认 100（容限随量程缩放）。"""
        n = 50
        pv = [55.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "pv_range": [1000.0] * n},
            metric_code="accuracy_rate",
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        # e_max = 2%×1000 = 20, r=5/20=0.25 → A≈94.47
        assert result.value is not None
        assert result.value == pytest.approx(94.47, abs=0.01)
        assert result.details["e_max"] == 20.0
