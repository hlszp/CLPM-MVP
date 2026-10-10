"""附录 B.6 综合评分 公式级验证（任务 G2）.

公式事实来源：算法说明 §4.10（对齐 GB/T 44693.2-2024 附录 B.6）：
    P = (A·a + F·f + S·s)/(a+f+s) × R/100

权重模板（§4.10.3，对齐国标附录 C）：
    STABLE 稳定型 a=0.2 f=0.3 s=0.5
    SLOW   慢速型 a=0.3 f=0.1 s=0.6
    FAST   快速型 a=0.2 f=0.5 s=0.3
    LOGIC  逻辑型 a=0.0 f=0.4 s=0.6

D2 口径（2026-07-28 修订）：参与评分（weight>0）的核心指标任一
缺失/INCONCLUSIVE/E 级 → 整体 INCONCLUSIVE；weight=0 的核心指标缺失不熔断。

统一手算输入：A=80, F=60, S=90, R=75。
"""

from __future__ import annotations

import pytest

from app.services.confidence_evaluator import (
    DEFAULT_WEIGHTS,
    ConfidenceEvaluator,
)

from .g2_helpers import make_metric_result

#: 四套权重模板（算法说明 §4.10.3 / 国标附录 C）
WEIGHTS_STABLE = {"accuracy_rate": 0.2, "fast_rate": 0.3, "stability_rate": 0.5}
WEIGHTS_SLOW = {"accuracy_rate": 0.3, "fast_rate": 0.1, "stability_rate": 0.6}
WEIGHTS_FAST = {"accuracy_rate": 0.2, "fast_rate": 0.5, "stability_rate": 0.3}
WEIGHTS_LOGIC = {"accuracy_rate": 0.0, "fast_rate": 0.4, "stability_rate": 0.6}

A, F, S, R = 80.0, 60.0, 90.0, 75.0


def _inputs(a=A, f=F, s=S, r=R, conf="A"):
    return {
        "accuracy_rate": make_metric_result("accuracy_rate", a, conf),
        "fast_rate": make_metric_result("fast_rate", f, conf),
        "stability_rate": make_metric_result("stability_rate", s, conf),
        "effective_auto_rate": make_metric_result("effective_auto_rate", r, conf),
    }


class TestB6WeightTemplates:
    """附录 B.6：四套权重模板各验证一个手算例."""

    def test_stable_template(self):
        """附录 B.6：STABLE 0.2/0.3/0.5.

        base = (80×0.2 + 60×0.3 + 90×0.5)/1.0 = (16+18+45) = 79.0
        P = 79.0 × 0.75 = 59.25
        """
        result = ConfidenceEvaluator.compute_composite_score(_inputs(), weights=WEIGHTS_STABLE)
        assert result.value == pytest.approx(59.25, abs=1e-9)
        assert result.details["base_score"] == pytest.approx(79.0, abs=1e-9)

    def test_slow_template(self):
        """附录 B.6：SLOW 0.3/0.1/0.6.

        base = (80×0.3 + 60×0.1 + 90×0.6) = (24+6+54) = 84.0
        P = 84.0 × 0.75 = 63.00
        """
        result = ConfidenceEvaluator.compute_composite_score(_inputs(), weights=WEIGHTS_SLOW)
        assert result.value == pytest.approx(63.0, abs=1e-9)

    def test_fast_template(self):
        """附录 B.6：FAST 0.2/0.5/0.3.

        base = (80×0.2 + 60×0.5 + 90×0.3) = (16+30+27) = 73.0
        P = 73.0 × 0.75 = 54.75
        """
        result = ConfidenceEvaluator.compute_composite_score(_inputs(), weights=WEIGHTS_FAST)
        assert result.value == pytest.approx(54.75, abs=1e-9)

    def test_logic_template(self):
        """附录 B.6：LOGIC 0.0/0.4/0.6（准确率权重为 0，不参与评分）.

        base = (80×0.0 + 60×0.4 + 90×0.6)/1.0 = (0+24+54) = 78.0
        P = 78.0 × 0.75 = 58.50
        """
        result = ConfidenceEvaluator.compute_composite_score(_inputs(), weights=WEIGHTS_LOGIC)
        assert result.value == pytest.approx(58.5, abs=1e-9)

    def test_default_weights_equal_stable_template(self):
        """附录 B.6：ConfidenceEvaluator 默认权重 = 国标 STABLE 模板（v2.1 修正固化）."""
        assert DEFAULT_WEIGHTS == WEIGHTS_STABLE


class TestB6RDiscountFactor:
    """附录 B.6：R 折扣因子非加权验证（R 是乘数，不进入权重分配）."""

    def test_r_scales_score_linearly(self):
        """附录 B.6：R=75 → 59.25；R=50 → 39.50；比值恰为 75/50，证明 R 为线性乘数."""
        r75 = ConfidenceEvaluator.compute_composite_score(_inputs(r=75.0), weights=WEIGHTS_STABLE)
        r50 = ConfidenceEvaluator.compute_composite_score(_inputs(r=50.0), weights=WEIGHTS_STABLE)

        assert r75.value == pytest.approx(59.25, abs=1e-9)
        assert r50.value == pytest.approx(39.5, abs=1e-9)
        assert r75.value / r50.value == pytest.approx(75.0 / 50.0, abs=1e-9)

    def test_weights_normalization_r_excluded(self):
        """附录 B.6：权重未归一化（{2,3,5} 和为 10）结果不变，且 R 不进分母.

        base = (80×2 + 60×3 + 90×5)/10 = 790/10 = 79.0 → P = 59.25
        若 R 被误并入权重（分母 10+75 或类似），结果必 ≠ 59.25。
        """
        weights = {"accuracy_rate": 2.0, "fast_rate": 3.0, "stability_rate": 5.0}
        result = ConfidenceEvaluator.compute_composite_score(_inputs(), weights=weights)
        assert result.value == pytest.approx(59.25, abs=1e-9)

    def test_r_missing_inconclusive(self):
        """附录 B.6：R 缺失 → 整体 INCONCLUSIVE（P1 #18 口径，不再降级 60%）."""
        inputs = _inputs()
        del inputs["effective_auto_rate"]
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_STABLE)

        assert result.value is None
        assert result.confidence_level == "E"

    def test_r_e_level_inconclusive(self):
        """附录 B.6：R 可信度 E 级 → 整体 INCONCLUSIVE."""
        inputs = _inputs()
        inputs["effective_auto_rate"] = make_metric_result("effective_auto_rate", R, "E")
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_STABLE)

        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "effective_auto_rate INCONCLUSIVE"


class TestB6MissingCoreMetric:
    """附录 B.6：核心指标缺失 → 整体 INCONCLUSIVE（D2 口径）."""

    def test_missing_core_metric_inconclusive(self):
        """附录 B.6：fast_rate 缺失（weight=0.3>0）→ score=None，快照随之 INCONCLUSIVE."""
        inputs = _inputs()
        inputs["fast_rate"] = make_metric_result("fast_rate", None, "E")
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_STABLE)

        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "core metric INCONCLUSIVE"
        assert result.details["inconclusive_inputs"] == ["fast_rate"]

    def test_e_level_core_metric_inconclusive(self):
        """附录 B.6：核心指标 E 级（视同缺失）→ 整体 INCONCLUSIVE（D2）."""
        inputs = _inputs()
        inputs["stability_rate"] = make_metric_result("stability_rate", S, "E")
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_STABLE)

        assert result.value is None
        assert result.confidence_level == "E"

    def test_zero_weight_core_metric_missing_not_fused(self):
        """附录 B.6：weight=0 的核心指标缺失不熔断（LOGIC 模板 a=0，准确率缺失）.

        base = (60×0.4 + 90×0.6)/1.0 = 78.0 → P = 78.0 × 0.75 = 58.50
        v6.2 P2-3：综合评分需读取 accuracy_rate.confidence_level，故以 INCONCLUSIVE
        形式保留在字典中（weight=0 不触发核心指标熔断，仍可计算评分）。
        """
        inputs = _inputs()
        inputs["accuracy_rate"] = make_metric_result("accuracy_rate", None, "E")
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_LOGIC)

        assert result.value == pytest.approx(58.5, abs=1e-9)

    def test_d_level_core_metric_keeps_score_with_flag(self):
        """附录 B.6：核心指标 D 级保留评分，details 标注 low_confidence_inputs（D2）."""
        inputs = _inputs()
        inputs["accuracy_rate"] = make_metric_result("accuracy_rate", A, "D")
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_STABLE)

        assert result.value == pytest.approx(59.25, abs=1e-9)
        assert "accuracy_rate" in result.details["low_confidence_inputs"]
        assert result.confidence_level == "D"  # 可信度取核心指标 + R 中最低等级


class TestB6C01ZeroWeightAccuracyKeyMissing:
    """C01（04 验收基线 / 台账 CAL-01）：LOGIC 零准确率权重，accuracy 键完全缺失.

    独立手算（F60/S90/R75，冻结权重 0/0.4/0.6）：
        base = (60×0.4 + 90×0.6)/1.0 = 78.0
        P = 78.0 × 0.75 = 58.50
    修复前：compute_composite_score 末尾无条件下标
    metric_results["accuracy_rate"] → KeyError（零权重必需性检查已跳过，
    键可整体缺失）。修复后按首个存在的核心指标读取回路级可信度。
    """

    def test_logic_accuracy_key_absent_computes_58_5(self):
        """C01：accuracy 键完全缺失（非 value=None）→ 不抛异常，P=58.5."""
        inputs = {
            "fast_rate": make_metric_result("fast_rate", 60.0, "A"),
            "stability_rate": make_metric_result("stability_rate", 90.0, "A"),
            "effective_auto_rate": make_metric_result("effective_auto_rate", 75.0, "A"),
        }
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_LOGIC)

        assert result.value == pytest.approx(58.5, abs=1e-9)
        assert result.details["base_score"] == pytest.approx(78.0, abs=1e-9)
        assert result.confidence_level == "A"  # 回路级可信度取自首个存在的核心指标

    def test_logic_accuracy_key_absent_confidence_from_present_core(self):
        """C01：accuracy 缺失时可信度取自在场核心指标（回路级，同值透传）."""
        inputs = {
            "fast_rate": make_metric_result("fast_rate", 60.0, "C"),
            "stability_rate": make_metric_result("stability_rate", 90.0, "C"),
            "effective_auto_rate": make_metric_result("effective_auto_rate", 75.0, "C"),
        }
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_LOGIC)

        assert result.value == pytest.approx(58.5, abs=1e-9)
        assert result.confidence_level == "C"

    def test_logic_accuracy_value_null_still_58_5(self):
        """C01：accuracy 以 value=None/E 级留在字典中 → 同样 P=58.5（与键缺失分别测）.

        与 test_zero_weight_core_metric_missing_not_fused 互补：该用例证明
        value=None 不熔断，本用例对齐 C01 手算锚点（F60/S90/R75）。
        """
        inputs = _inputs(f=60.0, s=90.0)
        inputs["accuracy_rate"] = make_metric_result("accuracy_rate", None, "E")
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_LOGIC)

        assert result.value == pytest.approx(58.5, abs=1e-9)

    def test_nonzero_weight_accuracy_key_absent_fuses(self):
        """C01 对照：a>0 时 accuracy 键缺失 → 整体 INCONCLUSIVE（不静默丢权重）."""
        inputs = {
            "fast_rate": make_metric_result("fast_rate", 60.0, "A"),
            "stability_rate": make_metric_result("stability_rate", 90.0, "A"),
            "effective_auto_rate": make_metric_result("effective_auto_rate", 75.0, "A"),
        }
        result = ConfidenceEvaluator.compute_composite_score(inputs, weights=WEIGHTS_STABLE)

        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "core metric INCONCLUSIVE"
        assert result.details["inconclusive_inputs"] == ["accuracy_rate"]
