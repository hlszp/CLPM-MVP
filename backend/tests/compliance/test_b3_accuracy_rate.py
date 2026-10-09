"""附录 B.3 准确率 公式级验证（任务 G2）.

公式事实来源：算法说明 §4.4 v2.2（对齐 GB/T 44693.2-2024 附录 B.3）：
    A = [1 - r × (1 - 1/e^r)] × 100%
    r = |Ē| / |E|_max
    |Ē| = (1/n) Σ|E_i|
    |E|_max = e_max_tolerance_ratio × U   （v2.2：工程容限，默认 2% 量程）

v2.2 演进（2026-10-10 稳定回路误判整改）：原 v2.1 数据驱动
|E|_max = (1/n)Σ[max(|E_i|)-|E_i|] 是偏差序列自身离散度，PV 高度稳定的
回路离散度趋 0 → r 爆炸 → 余差仅量程万分之几被打 0~11 分且逐小时剧烈
跳变（生产实证 05TY05P0803_PIDA）；国标 |E|max 语义应为最大允许偏差。
恒定余差退化分支（e_max=0）随之移除，恒定余差直接走容限口径。
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

        |E| = [1, 2, 3, 2]（PV=[51,52,53,52]，SP=50），n=4：
        |Ē| = 8/4 = 2.0
        |E|_max = 2% × U = 2% × 100 = 2.0（默认容限，归一化量程）
        r = 2.0/2.0 = 1.0
        decay = 1 - e^(-1) = 0.6321205588285577
        A = (1 - 1×0.6321205588285577) × 100 = 36.7879441171442 → 36.79
        """
        bundle = make_bundle(
            {"pv": [51.0, 52.0, 53.0, 52.0], "sp": [50.0] * 4},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        expected = round((1.0 - (1.0 - math.exp(-1.0))) * 100.0, 2)
        assert expected == 36.79  # 手算核实锚点（禁止实现输出反推）
        assert result.value == expected
        assert result.details["r"] == pytest.approx(1.0, abs=1e-4)
        assert result.details["e_max"] == pytest.approx(2.0, abs=1e-4)

    def test_r_equals_four_thirds(self):
        """附录 B.3：r=4/3 非平凡点，A ≈ 1.81（CONFIG e_max 覆盖路径）.

        |E| = [1, 1.5, 2, 3.5]（PV=[51,51.5,52,53.5]，SP=50），n=4：
        |Ē| = 8/4 = 2.0
        CONFIG e_max = 1.5（管理员手工指定回路级基准）
        r = 2.0/1.5 = 4/3 ≈ 1.3333
        decay = 1 - e^(-4/3) = 0.7364028618842733
        A = (1 - (4/3)×0.7364028618842733) × 100 = 1.81295174876... → 1.81
        """
        bundle = make_bundle(
            {
                "pv": [51.0, 51.5, 52.0, 53.5],
                "sp": [50.0] * 4,
                "e_max": [1.5] * 4,
            },
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        expected = round((1.0 - (4.0 / 3.0) * (1.0 - math.exp(-4.0 / 3.0))) * 100.0, 2)
        assert expected == 1.81  # 手算核实锚点
        assert result.value == expected
        assert result.details["mean_abs_error"] == pytest.approx(2.0, abs=1e-4)
        assert result.details["e_max"] == pytest.approx(1.5, abs=1e-4)
        assert result.details["e_max_source"] == "config"

    def test_zero_error_full_score(self):
        """附录 B.3：零偏差（PV=SP）→ A = 100（§4.4.4 步骤 8）."""
        bundle = make_bundle(
            {"pv": [50.0] * 100, "sp": [50.0] * 100},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value == 100.0

    def test_constant_offset_not_full_score(self):
        """附录 B.3：恒定余差不得满分（容限口径）.

        PV=[55]*100，SP=[50]*100 → |Ē| = 5（恒定，无离散度）。
        U=200 → |E|_max = 2%×200 = 4 → r = 1.25
        A = (1 - 1.25×(1-e^-1.25)) × 100 ≈ 10.81
        """
        bundle = make_bundle(
            {"pv": [55.0] * 100, "sp": [50.0] * 100, "pv_range": [200.0]},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        expected = round((1.0 - 1.25 * (1.0 - math.exp(-1.25))) * 100.0, 2)
        assert result.value is not None
        assert result.value != 100.0  # 恒定余差禁止满分
        assert result.value == pytest.approx(expected, abs=0.01)
        assert result.details["e_max_source"] == "tolerance_ratio"

    def test_constant_offset_beyond_tolerance_scores_zero(self):
        """附录 B.3：恒定余差远超容限 → A = 0（公式下界截断）.

        |Ē|=10，U=200 → |E|_max=4，r=2.5 → A 为负 → clamp 至 0
        """
        bundle = make_bundle(
            {"pv": [60.0] * 100, "sp": [50.0] * 100, "pv_range": [200.0]},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(0.0, abs=1e-9)

    def test_constant_offset_missing_pv_range_fallback_normalized(self):
        """附录 B.3：恒定余差且量程缺失 → 回退归一化量程 100 计算.

        v2.2：数据块信号经预处理归一化为 0~100，量程缺失时回退 100 与
        信号量纲自洽（与 stability 同款），不再 INCONCLUSIVE。
        |Ē|=5 → |E|_max=2 → r=2.5 → A=0
        """
        bundle = make_bundle(
            {"pv": [55.0] * 100, "sp": [50.0] * 100},
            metric_code="accuracy_rate",
        )
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(0.0, abs=1e-9)
        assert result.details["e_max"] == pytest.approx(2.0, abs=1e-4)

    def test_empty_data_inconclusive(self):
        """附录 B.3：无有效 PV-SP 对 → INCONCLUSIVE（§4.4.4 步骤 2）."""
        bundle = make_bundle({}, metric_code="accuracy_rate")
        result = AccuracyRateCalculator().calculate(bundle)

        assert result.value is None
        assert result.confidence_level == "E"
