"""融合 / 门禁 / 分类映射层测试。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §7
"""

from __future__ import annotations

import pytest

from app.services.diagnosis_operators.base import OperatorResult
from app.services.diagnosis_operators.classification import (
    DATA_INSUFFICIENT,
    INSTRUMENT,
    PROCESS,
    TUNING,
    UTILIZATION,
    VALVE,
    classify,
)
from app.services.diagnosis_operators.fusion import dempster_shafer, fuse_family
from app.services.diagnosis_operators.gate import GateResult, evaluate_gate

# ---------------------------------------------------------------------------
# D-S 融合
# ---------------------------------------------------------------------------


def test_dempster_shafer_empty_and_single() -> None:
    assert dempster_shafer([]) == 0.0
    assert dempster_shafer([0.7]) == pytest.approx(0.7)


def test_dempster_shafer_reinforcing() -> None:
    # 0.9×0.9 → 0.81/(0.81+0.01) ≈ 0.9878
    assert dempster_shafer([0.9, 0.9]) == pytest.approx(0.9878, abs=1e-3)


def test_dempster_shafer_conflicting_evidence() -> None:
    # 0.9 与 0.1 完全冲突 → 融合趋近 0.5
    assert dempster_shafer([0.9, 0.1]) == pytest.approx(0.5, abs=1e-6)


def test_dempster_shafer_degenerate() -> None:
    assert dempster_shafer([0.0, 0.0]) == 0.0
    assert dempster_shafer([1.0, 1.0]) == 1.0


def _op(name: str, detected: bool, conf: float, executed: bool = True) -> OperatorResult:
    return OperatorResult(operator=name, executed=executed, detected=detected, confidence=conf)


def test_fuse_family_requires_two_hits_for_fusion() -> None:
    single = fuse_family("stiction", "VALVE_STICTION", [_op("a", True, 0.7), _op("b", False, 0.0)])
    assert single.detected and not single.fused
    assert single.confidence == pytest.approx(0.7)

    both = fuse_family("stiction", "VALVE_STICTION", [_op("a", True, 0.8), _op("b", True, 0.8)])
    assert both.detected and both.fused
    assert both.confidence == pytest.approx(0.9412, abs=1e-3)


def test_fuse_family_no_hits() -> None:
    none = fuse_family("oscillation", "OSCILLATION", [_op("a", False, 0.0)])
    assert not none.detected and none.confidence == 0.0


# ---------------------------------------------------------------------------
# 数据门禁
# ---------------------------------------------------------------------------


def test_gate_fails_on_insufficient_points() -> None:
    g = evaluate_gate(point_count=10, expected_points=3600, valid_rate=0.99, confidence_level="A")
    assert not g.passed
    assert "不足" in (g.reason or "")


def test_gate_fails_on_confidence_e() -> None:
    g = evaluate_gate(point_count=3600, expected_points=3600, valid_rate=0.1, confidence_level="E")
    assert not g.passed
    assert "E 级" in (g.reason or "")


def test_gate_fails_on_gap_ratio() -> None:
    g = evaluate_gate(point_count=2000, expected_points=3600, valid_rate=0.99, confidence_level="B")
    assert not g.passed
    assert "断点" in (g.reason or "")


def test_gate_passes_normal() -> None:
    g = evaluate_gate(point_count=3500, expected_points=3600, valid_rate=0.97, confidence_level="A")
    assert g.passed and g.reason is None


# ---------------------------------------------------------------------------
# 分类决策表
# ---------------------------------------------------------------------------


def _fusion(detected: bool, conf: float, contributors: int = 1):
    from app.services.diagnosis_operators.fusion import FamilyFusion

    return FamilyFusion(
        family="x",
        symptom_tag="x",
        detected=detected,
        confidence=conf,
        contributors=[{"operator": f"op{i}", "confidence": conf} for i in range(contributors)],
    )


def _gate(passed: bool = True) -> GateResult:
    return GateResult(
        passed=passed,
        point_count=3500,
        expected_points=3600,
        valid_rate=0.97,
        confidence_level="A",
        gap_ratio=0.03,
        reason=None if passed else "有效数据率 43%，低于诊断门槛",
    )


def test_level0_gate_fail_short_circuits() -> None:
    r = classify({}, {}, {"auto_rate_avg": 1.0, "score_avg": 80}, _gate(passed=False))
    assert r.primary is not None
    assert r.primary.category == DATA_INSUFFICIENT
    assert r.recommendations[0].content.startswith("先通过数据管理")
    assert r.severity is None


def test_level1_instrument_primary() -> None:
    fusions = {"QUALITY_ABNORMAL": _fusion(True, 0.85)}
    op_results = {
        "sensor_fault": OperatorResult(
            "sensor_fault",
            True,
            detected=True,
            confidence=0.85,
            features={"sensor_subtype": "frozen"},
        ),
    }
    r = classify(fusions, op_results, {"auto_rate_avg": 1.0, "score_avg": 70}, _gate())
    assert r.primary.category == INSTRUMENT
    assert r.primary.status == "primary"


def test_level2_valve_stiction_primary() -> None:
    fusions = {"VALVE_STICTION": _fusion(True, 0.88, contributors=2)}
    r = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 55}, _gate())
    assert r.primary.category == VALVE
    assert r.severity == "MEDIUM"  # score<60


def test_level3_valve_saturation_primary() -> None:
    op_results = {
        "output_saturation": OperatorResult(
            "output_saturation",
            True,
            detected=True,
            confidence=1.0,
            features={"saturation_rate": 0.98},
        ),
    }
    r = classify({}, op_results, {"auto_rate_avg": 1.0, "score_avg": 90}, _gate())
    assert r.primary.category == VALVE
    assert any("贴工程限位" in b for b in r.primary.basis)


def test_level4_process_primary() -> None:
    fusions = {"EXTERNAL_DISTURBANCE": _fusion(True, 0.75)}
    op_results = {
        "disturbance_burst": OperatorResult(
            "disturbance_burst",
            True,
            detected=True,
            confidence=0.75,
            features={"shift_frequency": 8.0},
        ),
    }
    r = classify(fusions, op_results, {"auto_rate_avg": 1.0, "score_avg": 70}, _gate())
    assert r.primary.category == PROCESS


def test_level4_process_via_oscillation_exclusion() -> None:
    # 振荡且无粘滞且无过激 → PROCESS
    fusions = {"OSCILLATION": _fusion(True, 0.8)}
    r = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 70}, _gate())
    assert r.primary.category == PROCESS


def test_level5_tuning_primary() -> None:
    fusions = {"OVERAGGRESSIVE": _fusion(True, 0.9)}
    op_results = {
        "step_response_overshoot": OperatorResult(
            "step_response_overshoot",
            True,
            detected=True,
            confidence=0.9,
            features={"overshoot": 0.35, "decay_ratio": 0.6},
        ),
    }
    r = classify(fusions, op_results, {"auto_rate_avg": 1.0, "score_avg": 35}, _gate())
    assert r.primary.category == TUNING
    assert r.severity == "HIGH"  # score<40 且置信≥0.8


def test_level6_utilization_primary() -> None:
    r = classify({}, {}, {"auto_rate_avg": 0.31, "score_avg": 70}, _gate())
    assert r.primary.category == UTILIZATION


def test_level7_fallback_data_insufficient() -> None:
    r = classify({}, {}, {"auto_rate_avg": 0.9, "score_avg": 70}, _gate())
    assert r.primary.category == DATA_INSUFFICIENT


def test_contamination_instrument_downgrades_tuning() -> None:
    """仪表主因 + 过激命中 → 过激进待复核，且不生成参数处置建议。"""
    fusions = {
        "QUALITY_ABNORMAL": _fusion(True, 0.85),
        "OVERAGGRESSIVE": _fusion(True, 0.9),
    }
    r = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 70}, _gate())
    assert r.primary.category == INSTRUMENT
    assert not r.secondary
    assert len(r.pending_review) == 1
    assert r.pending_review[0].category == TUNING
    assert r.pending_review[0].contamination_note is not None
    # R4：不出现"整定/Kp"类建议
    assert not any("整定" in rec.content or "Kp" in rec.content for rec in r.recommendations)


# ---------------------------------------------------------------------------
# 通信链路分类（COMMUNICATION，独立 link 族）
# ---------------------------------------------------------------------------


def _quality_op(pattern: str = "Q001", conf: float = 0.9, max_bad: int = 1200):
    return OperatorResult(
        "quality_code_rules",
        True,
        detected=True,
        confidence=conf,
        features={
            "quality_pattern": pattern,
            "max_consecutive_bad": max_bad,
            "bad_rate": 0.014,
        },
    )


def test_communication_primary_on_q001() -> None:
    """Q001 连续断流 → COMMUNICATION 主分类，basis 含断流与缺口佐证。"""
    fusions = {"LINK_ABNORMAL": _fusion(True, 0.9)}
    gate = GateResult(
        passed=True,
        point_count=64000,
        expected_points=86400,
        valid_rate=0.99,
        confidence_level="A",
        gap_ratio=0.26,
    )
    r = classify(
        fusions,
        {"quality_code_rules": _quality_op()},
        {"auto_rate_avg": 1.0, "score_avg": 70},
        gate,
    )
    assert r.primary is not None
    assert r.primary.category == "COMMUNICATION"
    assert any("连续 Bad" in b for b in r.primary.basis)
    assert any("落库缺口率" in b for b in r.primary.basis)
    assert r.recommendations[0].content.startswith("检查通信链路")


def test_communication_priority_over_instrument() -> None:
    """链路 + 仪表波形同时命中 → 先通信后仪表：COMMUNICATION 主，仪表转待复核。"""
    fusions = {
        "LINK_ABNORMAL": _fusion(True, 0.9),
        "QUALITY_ABNORMAL": _fusion(True, 0.85),
    }
    op_results = {
        "quality_code_rules": _quality_op(),
        "sensor_fault": OperatorResult(
            "sensor_fault",
            True,
            detected=True,
            confidence=0.85,
            features={"sensor_subtype": "frozen"},
        ),
    }
    r = classify(fusions, op_results, {"auto_rate_avg": 1.0, "score_avg": 70}, _gate())
    assert r.primary.category == "COMMUNICATION"
    # 仪表被链路污染 → 待复核而非次分类
    assert any(j.category == INSTRUMENT for j in r.pending_review)
    assert not any(j.category == INSTRUMENT for j in r.secondary)


def test_contamination_communication_downgrades_tuning() -> None:
    """链路主因 + 过激命中 → 参数判定被污染转待复核。"""
    fusions = {
        "LINK_ABNORMAL": _fusion(True, 0.75),
        "OVERAGGRESSIVE": _fusion(True, 0.9),
    }
    r = classify(
        fusions,
        {"quality_code_rules": _quality_op(conf=0.75)},
        {"auto_rate_avg": 1.0, "score_avg": 70},
        _gate(),
    )
    assert r.primary.category == "COMMUNICATION"
    assert any(j.category == TUNING for j in r.pending_review)


def test_get_confidence_definitions_structure() -> None:
    """置信度显式定义：分类级 + 算子级 + 融合规则齐全。"""
    from app.services.diagnosis_operators.classification import (
        get_confidence_definitions,
    )

    defs = get_confidence_definitions()
    assert "COMMUNICATION" in defs["categories"]
    assert defs["categories"]["COMMUNICATION"]
    # 算子级：注册表全覆盖
    from app.services.diagnosis_operators import OPERATOR_REGISTRY

    assert set(defs["operators"]) == set(OPERATOR_REGISTRY)
    assert all(defs["operators"].values())  # 每个算子都有口径文案
    assert "Dempster-Shafer" in defs["fusion"]


def test_contamination_valve_downgrades_tuning() -> None:
    fusions = {
        "VALVE_STICTION": _fusion(True, 0.88, contributors=2),
        "OVERAGGRESSIVE": _fusion(True, 0.9),
    }
    r = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 70}, _gate())
    assert r.primary.category == VALVE
    assert r.pending_review and r.pending_review[0].category == TUNING


def test_utilization_is_independent_dimension() -> None:
    """参数主因 + 投用命中 → 投用进次分类（不被污染）。"""
    fusions = {"OVERAGGRESSIVE": _fusion(True, 0.9)}
    r = classify(fusions, {}, {"auto_rate_avg": 0.4, "score_avg": 70}, _gate())
    assert r.primary.category == TUNING
    assert any(j.category == UTILIZATION for j in r.secondary)


def test_recommendation_order_r2_utilization_priority() -> None:
    """自动投用率 <30% 时，恢复自动投用升至第 2 位。"""
    fusions = {"OVERAGGRESSIVE": _fusion(True, 0.9)}
    r = classify(fusions, {}, {"auto_rate_avg": 0.2, "score_avg": 70}, _gate())
    assert r.recommendations[0].priority == 1  # 主分类建议
    assert r.recommendations[1].content.startswith("优先恢复自动投用")


def test_every_recommendation_has_basis() -> None:
    fusions = {"OVERAGGRESSIVE": _fusion(True, 0.9)}
    r = classify(fusions, {}, {"auto_rate_avg": 0.2, "score_avg": 70}, _gate())
    for rec in r.recommendations:
        assert rec.basis
        assert rec.direction


def test_severity_levels() -> None:
    fusions = {"OVERAGGRESSIVE": _fusion(True, 0.9)}
    high = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 35}, _gate())
    assert high.severity == "HIGH"
    med = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 55}, _gate())
    assert med.severity == "MEDIUM"
    low = classify(fusions, {}, {"auto_rate_avg": 1.0, "score_avg": 85}, _gate())
    assert low.severity == "MEDIUM"  # 置信 0.9 ≥ 0.6 → MEDIUM
