"""G22 相邻：粘滞族级 2 判定的必要条件（≥2 算子命中 × 支持度 ≥0.7）。

事实来源：docs/MVP设计/07-诊断模块设计方案.md + 08 号文 §4.4（D5 A 裁决）
- §7.2 级 2：「粘滞族融合置信 ≥0.7（**≥2 算子命中**）」→ VALVE；
- D5（2026-10-12）：融合数值改保守规则（conservative-max/1，命中取最大，
  同依赖组不增强）；未达原因门槛时**症状保留+原因未决**（primary=None），
  不再落 NO_SYMPTOM"未发现异常"（08§4.4.1）。dempster_shafer 数值断言
  属历史 v1 规则锚定（现行链路已不调用）。

缺陷（修复前实测）：classification.py 级 2 原实现为
    confidence >= 0.7 OR len(contributors) >= 2
OR 的两个后果：
  ① 单算子命中（conf=0.8）即判 VALVE，无交叉验证；
  ② 两个低置信命中（0.3+0.3 → 融合 0.1552）亦判 VALVE，
     15.5% 置信输出"阀门问题"，且架空级 7 兜底。
"""

from __future__ import annotations

import pytest

from app.services.diagnosis_operators.base import OperatorResult
from app.services.diagnosis_operators.classification import (
    VALVE,
    classify,
)
from app.services.diagnosis_operators.fusion import FamilyFusion, dempster_shafer
from app.services.diagnosis_operators.gate import GateResult


def _fusion(detected: bool, conf: float, contributors: int = 1) -> FamilyFusion:
    return FamilyFusion(
        family="stiction",
        symptom_tag="VALVE_STICTION",
        detected=detected,
        confidence=conf,
        contributors=[{"operator": f"op{i}", "confidence": conf} for i in range(contributors)],
    )


def _gate() -> GateResult:
    return GateResult(
        passed=True,
        point_count=3500,
        expected_points=3600,
        valid_rate=0.97,
        confidence_level="A",
        gap_ratio=0.03,
        reason=None,
    )


#: 无其它级命中：auto_rate 1.0 不触发级 6，故未判级 2 时应落到级 7 兜底
_KPI = {"auto_rate_avg": 1.0, "score_avg": 55}


class TestLevel2StictionRequiresCrossValidation:
    """级 2 必须同时满足"多算子交叉验证"与"融合置信达阈"。"""

    def test_two_low_confidence_hits_must_not_trigger_level2(self) -> None:
        """两个低置信命中（0.3+0.3）不得判 VALVE。

        D5（08-A 保守融合）：两低置信命中支持度取最大 0.3 < 0.7 → 无 VALVE
        候选；症状已检出但原因证据不足 → **症状保留+原因未决**（primary=None，
        08§4.4.1——不再是 NO_SYMPTOM"未发现异常"，D5 拆出未决态）。
        """
        r = classify({"VALVE_STICTION": _fusion(True, 0.3, contributors=2)}, {}, _KPI, _gate())
        assert r.primary is None, "低置信证据不得产出阀门结论"
        assert any("症状已检出" in x for x in r.rationale)
        assert any("原因未决" in x for x in r.rationale)

    def test_single_high_confidence_hit_must_not_trigger_level2(self) -> None:
        """单算子高置信命中不得判 VALVE（无交叉验证）→ 症状保留+原因未决。"""
        r = classify({"VALVE_STICTION": _fusion(True, 0.8, contributors=1)}, {}, _KPI, _gate())
        assert r.primary is None, "单算子命中不构成族内交叉验证"
        assert any("症状已检出" in x for x in r.rationale)

    def test_two_high_confidence_hits_still_trigger_level2(self) -> None:
        """回归护栏：多算子交叉验证且融合置信达标仍判 VALVE（文档级 2 正例）。"""
        fused = dempster_shafer([0.9, 0.8])
        assert fused == pytest.approx(0.9730, abs=1e-4)

        r = classify({"VALVE_STICTION": _fusion(True, fused, contributors=2)}, {}, _KPI, _gate())
        assert r.primary.category == VALVE
        assert r.primary.confidence == pytest.approx(fused, abs=1e-4)


class TestFusionFormulaMatchesDesignDoc:
    """融合公式与设计文档 §7.1 一致（澄清 G22「非 D-S」之疑）。"""

    def test_formula_matches_doc(self) -> None:
        """C_fused = (Π cᵢ) / (Π cᵢ + Π (1-cᵢ))。"""
        for confs in ([0.9, 0.8], [0.6, 0.7], [0.3, 0.9], [0.5, 0.5]):
            prod_c = 1.0
            prod_not = 1.0
            for c in confs:
                prod_c *= c
                prod_not *= 1.0 - c
            assert dempster_shafer(confs) == pytest.approx(prod_c / (prod_c + prod_not), abs=1e-9)

    def test_conflicting_evidence_approaches_half(self) -> None:
        """文档 §7.1：「族内证据完全冲突时融合置信度趋近 0.5，按低置信处理」。"""
        assert dempster_shafer([0.9, 0.1]) == pytest.approx(0.5, abs=1e-6)
        assert dempster_shafer([0.99, 0.01]) == pytest.approx(0.5, abs=1e-6)

    def test_family_only_takes_detections(self) -> None:
        """族内融合只取命中算子（文档 §7.2 语义为"命中"计数）。"""
        from app.services.diagnosis_operators.fusion import fuse_family

        results = [
            OperatorResult("a", True, detected=True, confidence=0.8),
            OperatorResult("b", True, detected=False, confidence=0.0),
            OperatorResult("c", False, detected=True, confidence=0.9),  # 未执行
        ]
        f = fuse_family("stiction", "VALVE_STICTION", results)
        assert [c["operator"] for c in f.contributors] == ["a"]
        assert f.fused is False  # 单条命中不构成融合

    def test_zero_hits_not_detected(self) -> None:
        from app.services.diagnosis_operators.fusion import fuse_family

        f = fuse_family("stiction", "VALVE_STICTION", [])
        assert f.detected is False and f.confidence == 0.0
