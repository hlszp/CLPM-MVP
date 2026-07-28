"""附录 B.3 准确率 公式级验证（任务 G2）.

公式事实来源：算法说明 §4.4 v2.1（对齐 GB/T 44693.2-2024 附录 B.3）：
    A = [1 - r × (1 - 1/e^r)] × 100%
    r = |Ē| / |E|_max
    |Ē| = (1/n) Σ|E_i|
    |E|_max = (1/n) Σ[max(|E_i|) - |E_i|]   （v2.1：数据驱动，非外部输入）

退化分支（Phase 1 P0 修复，此处固化为国标用例）：
    |E|_max = 0 且 |Ē| > 0（恒定余差）→ 不得满分，
    A = max(0, 1 - |Ē|/(0.05·U)) × 100
"""

from __future__ import annotations

import math

import pytest

from app.services.metric_calculator.accuracy import AccuracyRateCalculator

from .g2_helpers import make_bundle


class TestB3AccuracyRate:
    """附录 B.3 准确率：已知 |Ē|、|E|_max 合成序列验证公式精确值."""

    def test_r_equals_one(self):
        """附录 B.3：r=1 基准点，A = (1 - (1 - 1/e)) × 100 = 100/e ≈ 36.79.

        |E| = [1, 2, 3, 6]（PV=[51,52,53,56]，SP=50），n=4：
        |Ē| = 12/4 = 3.0
        max(|E|) = 6 → |E|_max = (5+4+3+0)/4 = 3.0
        r = 3.0/3.0 = 1.0
        decay = 1 - e^(-1) = 0.6321205588285577
        A = (1 - 1×0.6321205588285577) × 100 = 36.7879441171442 → 36.79
        """
        bundle = make_bundle(
            {"pv": [51.0, 52.0, 53.0, 56.0], "sp": [50.0] * 4},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        expected = round((1.0 - (1.0 - math.exp(-1.0))) * 100.0, 2)
        assert expected == 36.79  # 手算核实锚点（禁止实现输出反推）
        assert result.value == expected
        assert result.details["r"] == pytest.approx(1.0, abs=1e-4)
        assert result.details["e_max"] == pytest.approx(3.0, abs=1e-4)

    def test_r_equals_four_thirds(self):
        """附录 B.3：r=4/3 非平凡点，A ≈ 1.81.

        |E| = [1, 1.5, 2, 3.5]（PV=[51,51.5,52,53.5]，SP=50），n=4：
        |Ē| = 8/4 = 2.0
        max(|E|) = 3.5 → |E|_max = (2.5+2+1.5+0)/4 = 1.5
        r = 2.0/1.5 = 4/3 ≈ 1.3333
        decay = 1 - e^(-4/3) = 0.7364028618842733
        A = (1 - (4/3)×0.7364028618842733) × 100 = 1.81295174876... → 1.81
        """
        bundle = make_bundle(
            {"pv": [51.0, 51.5, 52.0, 53.5], "sp": [50.0] * 4},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        expected = round((1.0 - (4.0 / 3.0) * (1.0 - math.exp(-4.0 / 3.0))) * 100.0, 2)
        assert expected == 1.81  # 手算核实锚点
        assert result.value == expected
        assert result.details["mean_abs_error"] == pytest.approx(2.0, abs=1e-4)
        assert result.details["e_max"] == pytest.approx(1.5, abs=1e-4)

    def test_zero_error_full_score(self):
        """附录 B.3：零偏差（PV=SP）→ A = 100（§4.4.4 步骤 8）."""
        bundle = make_bundle(
            {"pv": [50.0] * 100, "sp": [50.0] * 100},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value == 100.0

    def test_constant_offset_not_full_score(self):
        """附录 B.3：恒定余差不得满分（Phase 1 修复固化为国标用例）.

        PV=[55]*100，SP=[50]*100 → |E| 恒为 5，max=|Ē|=5，|E|_max=0（退化）。
        国标指数公式在 |E|_max=0 时不可归一化，按量程百分比扣分：
        U=200 → A = (1 - 5/(0.05×200)) × 100 = (1 - 0.5) × 100 = 50.0
        """
        bundle = make_bundle(
            {"pv": [55.0] * 100, "sp": [50.0] * 100, "pv_range": [200.0]},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value is not None
        assert result.value != 100.0  # 恒定余差禁止满分
        assert result.value == pytest.approx(50.0, abs=1e-9)
        assert result.details["e_max_source"] == "data_degenerate"

    def test_constant_offset_at_tolerance_boundary_scores_zero(self):
        """附录 B.3：恒定余差恰为量程 5% 容限 → A = 0（扣分公式下界）.

        |Ē|=10，U=200 → A = (1 - 10/10) × 100 = 0.0
        """
        bundle = make_bundle(
            {"pv": [60.0] * 100, "sp": [50.0] * 100, "pv_range": [200.0]},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(0.0, abs=1e-9)

    def test_constant_offset_missing_pv_range_inconclusive(self):
        """附录 B.3：恒定余差且量程缺失 → INCONCLUSIVE（量程是扣分必需基准）."""
        bundle = make_bundle(
            {"pv": [55.0] * 100, "sp": [50.0] * 100},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "degenerate_offset_missing_pv_range"

    def test_empty_data_inconclusive(self):
        """附录 B.3：无有效 PV-SP 对 → INCONCLUSIVE（§4.4.4 步骤 2）."""
        bundle = make_bundle({}, metric_code="accuracy_rate")
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value is None
        assert result.confidence_level == "E"
