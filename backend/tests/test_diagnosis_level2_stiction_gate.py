"""G22 相邻：粘滞族级 2 判定的必要条件（≥2 算子命中 × 融合置信 ≥0.7）。

事实来源：docs/MVP设计/07-诊断模块设计方案.md
- §7.1：「D-S 适合"同一假设的多证据"聚合（3 个算子交叉验证"是否粘滞"有意义）」；
  融合公式 C_fused = (Π cᵢ) / (Π cᵢ + Π (1-cᵢ))，完全冲突时趋近 0.5；
- §7.2 级 2：「粘滞族融合置信 ≥0.7（**≥2 算子命中**）」→ VALVE；
- §7.2 级 7：兜底「全部低置信（各算子置信均 <0.5）」→ DATA_INSUFFICIENT。

缺陷（修复前实测）：classification.py 级 2 原实现为
    confidence >= 0.7 OR len(contributors) >= 2
与文档不符——文档把"≥2 算子命中"写成**必要条件**，且本文件
_confidence_basis 自述"VALVE 粘滞方向：族内 ≥2 算子命中经 D-S 交叉验证融合"。
OR 的两个后果：
  ① 单算子命中（conf=0.8）即判 VALVE，无交叉验证；
  ② 两个低置信命中（0.3+0.3 → 融合 0.1552）亦判 VALVE，
     15.5% 置信输出"阀门问题"，并架空级 7 兜底。
"""

from __future__ import annotations

import pytest

from app.services.diagnosis_operators.base import OperatorResult
from app.services.diagnosis_operators.classification import (
    NO_SYMPTOM,
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
        """两个低置信命中（0.3+0.3 → 融合 0.1552）不得判 VALVE。

        修复前：len(contributors)==2 即命中 OR 分支 → VALVE @15.5%，
        架空级 7"各算子置信均 <0.5 → NO_SYMPTOM（原 DATA_INSUFFICIENT，D3 拆分）"。
        """
        fused = dempster_shafer([0.3, 0.3])
        assert fused == pytest.approx(0.1552, abs=1e-4)  # 确实远低于 0.7

        r = classify({"VALVE_STICTION": _fusion(True, fused, contributors=2)}, {}, _KPI, _gate())
        assert r.primary.category != VALVE, "低置信证据不得产出阀门结论"
        # D3（2026-09-25）：数据门禁通过且无症状命中 → NO_SYMPTOM（不再是 DATA_INSUFFICIENT）
        assert r.primary.category == NO_SYMPTOM

    def test_single_high_confidence_hit_must_not_trigger_level2(self) -> None:
        """单算子高置信命中不得判 VALVE（无 D-S 交叉验证）。"""
        r = classify({"VALVE_STICTION": _fusion(True, 0.8, contributors=1)}, {}, _KPI, _gate())
        assert r.primary.category != VALVE, "单算子命中不构成族内交叉验证"

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
