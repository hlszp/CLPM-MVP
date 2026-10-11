"""D5 融合与建议（P3-01 第五子卡，08 号文 §4.4 A 裁决 + 11§5 R11）。

覆盖任务书【一】【4】核验项：
- 保守融合反例：多算子部分支持 → 症状保留+原因未决，非强行归类（08§4.4.1）；
- 支持度不冒称概率（supportKind=RULE_SUPPORT/supportRuleVersion 标识）；
- C15-D05：同一特征两条方法重复进入融合不虚增独立证据数（同依赖组归并）；
- C15-D06：振荡阳性、粘滞/过激未执行 → 不因缺少替代原因检查自动确认 PROCESS；
- C30-D01：未决不引导 PID 整定；建议-证据绑定可溯（算子/支持度/run）；
- 取消/失败/未评估 run 不产 SYSTEM 建议（08§4.2 行 7/8）；
- 08§4.2 组合决定表消费（全部 ERROR→FAILED 等由 D2 既有测试锚定，此处只验
  supportRuleVersion 增量字段）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.diagnosis_run import DiagnosisRun
from app.services.diagnosis_assessment import (
    CAUSE_UNRESOLVED,
    JUDGEMENT_INCONCLUSIVE,
    JUDGEMENT_NOT_ASSESSED,
    JUDGEMENT_SYMPTOM_DETECTED,
    build_assessment_meta,
)
from app.services.diagnosis_operators.base import (
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_POSITIVE,
    OperatorResult,
)
from app.services.diagnosis_operators.classification import (
    NO_SYMPTOM,
    VALVE,
    classify,
)
from app.services.diagnosis_operators.fusion import (
    CONFLICT_POSITIVE_NEGATIVE_COEXIST,
    DEPENDENCY_GROUPS,
    SUPPORT_RULE_VERSION,
    FamilyFusion,
    fuse_family,
)
from app.services.diagnosis_operators.gate import GateResult
from app.services.diagnosis_system_actions import generate_system_actions

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _op(
    name: str,
    state: str | None,
    conf: float = 0.0,
    detected: bool = False,
    executed: bool = True,
) -> OperatorResult:
    return OperatorResult(
        operator=name,
        executed=executed,
        detected=detected,
        confidence=conf,
        assessment_state=state,
    )


def _fusion(detected: bool, conf: float, contributors: int = 1, **kw) -> FamilyFusion:
    return FamilyFusion(
        family="x",
        symptom_tag=kw.pop("symptom_tag", "X"),
        detected=detected,
        confidence=conf,
        contributors=[{"operator": f"op{i}", "confidence": conf} for i in range(contributors)],
        **kw,
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


_KPI = {"auto_rate_avg": 1.0, "score_avg": 70}


def _mock_db(existing_count: int = 0) -> tuple[MagicMock, MagicMock]:
    exec_result = MagicMock()
    exec_result.scalar.return_value = existing_count
    db = MagicMock()
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db, exec_result


def _run_row(
    status: str = "SUCCESS",
    meta: dict | None = None,
    primary: str | None = "INSTRUMENT",
    recommendations: list | None = None,
    review_status: str | None = None,
    review_results: list | None = None,
) -> DiagnosisRun:
    return DiagnosisRun(
        loop_id="l1",
        status=status,
        assessment_meta=meta,
        primary_category=primary,
        secondary_categories=[],
        recommendations=recommendations or [],
        review_status=review_status,
        review_results=review_results,
    )


# ---------------------------------------------------------------------------
# 1. 保守融合核心规则（conservative-max/1，08§4.4.6 A 裁决）
# ---------------------------------------------------------------------------


class TestConservativeFusionRule:
    def test_two_high_hits_take_max_not_ds_enhancement(self) -> None:
        """两条 0.9 命中 → 0.9（D-S 旧规则会得 0.988——不因叠加方法数量增强）。"""
        f = fuse_family(
            "tuning",
            "OVERAGGRESSIVE",
            [
                _op("step_response_overshoot", ASSESSMENT_POSITIVE, 0.9, detected=True),
                _op("other", ASSESSMENT_POSITIVE, 0.9, detected=True),
            ],
        )
        assert f.detected and f.fused
        assert f.confidence == pytest.approx(0.9, abs=1e-9)

    def test_max_across_unequal_hits(self) -> None:
        f = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [
                _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.62, detected=True),
                _op("stiction_kano", ASSESSMENT_POSITIVE, 0.55, detected=True),
            ],
        )
        assert f.confidence == pytest.approx(0.62, abs=1e-9)

    def test_eight_state_gate_positives_only(self) -> None:
        """融合输入=算子八态：仅 POSITIVE 计入命中；INSUFFICIENT/NEGATIVE 不参与。"""
        f = fuse_family(
            "oscillation",
            "OSCILLATION",
            [
                _op("oscillation_fft", ASSESSMENT_POSITIVE, 0.8, detected=True),
                _op("oscillation_iae", ASSESSMENT_INSUFFICIENT_DATA, 0.0),
                _op("legacy_other", ASSESSMENT_NEGATIVE, 0.0),
            ],
        )
        assert f.detected
        assert [c["operator"] for c in f.contributors] == ["oscillation_fft"]
        assert f.confidence == pytest.approx(0.8, abs=1e-9)

    def test_legacy_fallback_without_state(self) -> None:
        """无 assessment_state（直调/旧输入）回退 executed+detected（reader-first）。"""
        f = fuse_family(
            "sensor",
            "QUALITY_ABNORMAL",
            [OperatorResult("sensor_fault", True, detected=True, confidence=0.7)],
        )
        assert f.detected and f.confidence == pytest.approx(0.7)

    def test_support_structure_queryable(self) -> None:
        """融合输出含支持结构：规则版本/贡献算子/依赖组/独立证据数可查。"""
        f = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [_op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.8, detected=True)],
        )
        d = f.to_dict()
        assert d["supportStructure"]["ruleVersion"] == SUPPORT_RULE_VERSION
        assert d["contributors"] == [{"operator": "stiction_ellipse", "confidence": 0.8}]
        assert d["confidence"] == 0.8

    def test_deterministic_output(self) -> None:
        """同输入必同输出（纯函数纪律）：依赖组列表按组名排序稳定。"""
        a = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [
                _op("stiction_kano", ASSESSMENT_POSITIVE, 0.6, detected=True),
                _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.8, detected=True),
            ],
        )
        b = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [
                _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.8, detected=True),
                _op("stiction_kano", ASSESSMENT_POSITIVE, 0.6, detected=True),
            ],
        )
        # 语义字段确定（contributors 保留输入序——证据可溯）
        assert a.confidence == b.confidence
        assert a.dependency_groups == b.dependency_groups
        assert a.independent_evidence_count == b.independent_evidence_count
        assert [g["group"] for g in a.dependency_groups] == sorted(
            g["group"] for g in a.dependency_groups
        )


# ---------------------------------------------------------------------------
# 2. C15-D05：同依赖组不虚增独立证据数
# ---------------------------------------------------------------------------


class TestDependencyGroupsC15D05:
    def test_stiction_three_methods_one_independent_evidence(self) -> None:
        """粘滞三法同依赖组（同一 PV/OP）全命中 → 独立证据 1 组，支持度取最大。"""
        f = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [
                _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.82, detected=True),
                _op("stiction_choudhury", ASSESSMENT_POSITIVE, 0.75, detected=True),
                _op("stiction_kano", ASSESSMENT_POSITIVE, 0.68, detected=True),
            ],
        )
        assert len(f.contributors) == 3  # 所有证据保留可查
        assert f.independent_evidence_count == 1  # 不虚增独立证据数
        assert f.confidence == pytest.approx(0.82, abs=1e-9)
        assert f.dependency_groups == [
            {
                "group": "stiction_pv_op",
                "operators": ["stiction_choudhury", "stiction_ellipse", "stiction_kano"],
                "maxSupport": 0.82,
            }
        ]

    def test_groups_plus_outside_operator_counts_two(self) -> None:
        """依赖组 + 组外算子 → 独立证据 2 组。"""
        f = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [
                _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.8, detected=True),
                _op("stiction_choudhury", ASSESSMENT_POSITIVE, 0.7, detected=True),
                _op("independent_probe", ASSESSMENT_POSITIVE, 0.6, detected=True),
            ],
        )
        assert f.independent_evidence_count == 2
        assert f.confidence == pytest.approx(0.8, abs=1e-9)

    def test_dependency_registry_covers_stiction_and_oscillation(self) -> None:
        """同源关系登记（08§4.4.5"至少标出"）：粘滞三法与振荡两视角。"""
        assert set(DEPENDENCY_GROUPS["stiction_pv_op"]) == {
            "stiction_ellipse",
            "stiction_choudhury",
            "stiction_kano",
        }
        assert set(DEPENDENCY_GROUPS["oscillation_pv"]) == {"oscillation_fft", "oscillation_iae"}


# ---------------------------------------------------------------------------
# 3. 矛盾证据保留（08§4.4.6/§4.4.7）
# ---------------------------------------------------------------------------


class TestConflictMarkers:
    def test_positive_negative_coexist_marked_not_dropped(self) -> None:
        """阳性与合格阴性并存 → 冲突标记保留，证据不被省略（反证≠1−支持度）。"""
        f = fuse_family(
            "oscillation",
            "OSCILLATION",
            [
                _op("oscillation_fft", ASSESSMENT_POSITIVE, 0.8, detected=True),
                _op("oscillation_iae", ASSESSMENT_NEGATIVE, 0.0),
            ],
        )
        assert f.detected
        assert f.conflicts == [CONFLICT_POSITIVE_NEGATIVE_COEXIST]
        assert [c["operator"] for c in f.contributors] == ["oscillation_fft"]
        assert f.to_dict()["supportStructure"]["conflicts"] == [CONFLICT_POSITIVE_NEGATIVE_COEXIST]

    def test_conflict_visible_in_classification_rationale(self) -> None:
        """冲突标记进分类 rationale（矛盾可查，建议人工复核）。"""
        fusion = _fusion(
            True,
            0.5,
            contributors=2,
            symptom_tag="VALVE_STICTION",
            conflicts=[CONFLICT_POSITIVE_NEGATIVE_COEXIST],
        )
        r = classify({"VALVE_STICTION": fusion}, {}, _KPI, _gate())
        assert r.primary is None
        assert any("矛盾证据" in x for x in r.rationale)


# ---------------------------------------------------------------------------
# 4. 保守融合反例：多算子部分支持 → 未决保留非强行归类（08§4.4.1）
# ---------------------------------------------------------------------------


class TestUnresolvedPreservation:
    def test_partial_support_keeps_symptom_unresolved(self) -> None:
        """两法命中但保守支持度 0.62 < 0.7 → 不判 VALVE，症状保留+原因未决。"""
        fusion = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [
                _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.62, detected=True),
                _op("stiction_kano", ASSESSMENT_POSITIVE, 0.55, detected=True),
            ],
        )
        r = classify({"VALVE_STICTION": fusion}, {}, _KPI, _gate())
        assert r.primary is None, "支持度未达原因门槛不得强行归类"
        assert r.primary != VALVE
        assert any("症状已检出" in x and "VALVE_STICTION" in x for x in r.rationale)
        assert any("原因未决" in x for x in r.rationale)
        # 不得落"未发现异常"（NO_SYMPTOM 是全部症状未命中的兜底）
        assert r.primary != NO_SYMPTOM

    def test_single_stiction_positive_symptom_detected_unresolved(self) -> None:
        """08§4.4.1 正例：单个粘滞阳性而交叉检查不足 → 症状保留+原因未决。"""
        fusion = fuse_family(
            "stiction",
            "VALVE_STICTION",
            [_op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.8, detected=True)],
        )
        r = classify({"VALVE_STICTION": fusion}, {}, _KPI, _gate())
        assert r.primary is None
        # 科学结论经 assessment_meta 表达：SYMPTOM_DETECTED + UNRESOLVED
        meta = build_assessment_meta(
            op_results={
                "stiction_ellipse": _op("stiction_ellipse", ASSESSMENT_POSITIVE, 0.8, True)
            },
            explicitly_excluded={},
            profile_expected_operators=["stiction_ellipse"],
            primary_category=None,
            gate_passed=True,
            gate_reason=None,
            selected_is_empty=False,
        )
        assert meta["judgementStatus"] == JUDGEMENT_SYMPTOM_DETECTED
        assert meta["causeStatus"] == CAUSE_UNRESOLVED

    def test_unresolved_recommendations_no_pid_tuning_guidance(self) -> None:
        """C30-D01：未决建议引导复核/扩查，不引导 PID 整定。"""
        fusion = _fusion(True, 0.5, contributors=2, symptom_tag="VALVE_STICTION")
        r = classify({"VALVE_STICTION": fusion}, {}, _KPI, _gate())
        assert r.recommendations
        text = " ".join(rec.content for rec in r.recommendations)
        assert "原因未定" in text or "原因未决" in text
        assert "整定" not in text and "Kp" not in text
        # 未决建议不冒充确定结论：无证据绑定（evidence=None）
        assert all(rec.evidence is None for rec in r.recommendations)

    def test_unresolved_severity_none(self) -> None:
        """08§4.4.4：未知工艺影响时不根据支持度自动定级。"""
        fusion = _fusion(True, 0.5, contributors=2, symptom_tag="VALVE_STICTION")
        r = classify({"VALVE_STICTION": fusion}, {}, _KPI, _gate())
        assert r.severity is None

    def test_no_symptom_fallback_unchanged(self) -> None:
        """全部症状未命中 → NO_SYMPTOM 兜底保留（行为不回归）。"""
        r = classify({}, {}, _KPI, _gate())
        assert r.primary is not None and r.primary.category == NO_SYMPTOM
        assert "未发现异常症状" in r.recommendations[0].content

    def test_cusum_positive_still_process_candidate(self) -> None:
        """级 4 正例保留：CUSUM 阳性（正面证据）仍为 PROCESS 候选。"""
        fusions = {"EXTERNAL_DISTURBANCE": _fusion(True, 0.75, symptom_tag="EXTERNAL_DISTURBANCE")}
        r = classify(fusions, {}, _KPI, _gate())
        assert r.primary is not None and r.primary.category == "PROCESS"
        assert any("候选" in b for b in r.primary.basis)


# ---------------------------------------------------------------------------
# 5. C15-D06：排除式归因删除
# ---------------------------------------------------------------------------


class TestNoExclusionAttributionC15D06:
    def test_oscillation_positive_missing_alternatives_not_process(self) -> None:
        """振荡阳性、粘滞/过激未执行（INSUFFICIENT/缺输入）→ 不自动确认 PROCESS。"""
        fusion = fuse_family(
            "oscillation",
            "OSCILLATION",
            [_op("oscillation_fft", ASSESSMENT_POSITIVE, 0.8, detected=True)],
        )
        op_results = {
            # 粘滞/过激未形成有效终态（未执行不构成反对证据）
            "stiction_ellipse": _op(
                "stiction_ellipse", ASSESSMENT_INSUFFICIENT_DATA, 0.0, executed=False
            ),
            "step_response_overshoot": _op(
                "step_response_overshoot", ASSESSMENT_INSUFFICIENT_DATA, 0.0, executed=False
            ),
        }
        r = classify({"OSCILLATION": fusion}, op_results, _KPI, _gate())
        assert r.primary is None  # 症状保留、原因未决，不自动确认 PROCESS

    def test_oscillation_with_disturbance_positive_still_process(self) -> None:
        """振荡 + CUSUM 阳性（正面证据）→ PROCESS 候选（正例不回归）。"""
        fusions = {
            "OSCILLATION": _fusion(True, 0.8, symptom_tag="OSCILLATION"),
            "EXTERNAL_DISTURBANCE": _fusion(True, 0.75, symptom_tag="EXTERNAL_DISTURBANCE"),
        }
        r = classify(fusions, {}, _KPI, _gate())
        assert r.primary is not None and r.primary.category == "PROCESS"


# ---------------------------------------------------------------------------
# 6. 建议-证据链绑定（任务书【一】2）
# ---------------------------------------------------------------------------


class TestEvidenceBinding:
    def test_candidate_recommendations_carry_evidence(self) -> None:
        """主分类建议绑定算子/支持度/症状标签（run 经 LoopActionItem.run_id 绑定）。"""
        fusions = {
            "VALVE_STICTION": FamilyFusion(
                family="stiction",
                symptom_tag="VALVE_STICTION",
                detected=True,
                confidence=0.88,
                contributors=[
                    {"operator": "stiction_ellipse", "confidence": 0.88},
                    {"operator": "stiction_kano", "confidence": 0.8},
                ],
                independent_evidence_count=1,
            )
        }
        r = classify(fusions, {}, _KPI, _gate())
        assert r.primary is not None and r.primary.category == VALVE
        rec = r.recommendations[0]
        assert rec.evidence is not None
        assert rec.evidence["category"] == VALVE
        assert set(rec.evidence["operators"]) == {"stiction_ellipse", "stiction_kano"}
        assert rec.evidence["support"] == 0.88
        assert rec.evidence["symptomTag"] == "VALVE_STICTION"
        assert rec.evidence["independentEvidenceCount"] == 1
        # 序列化透出（run.recommendations 落库结构）
        assert "evidence" in rec.to_dict()

    def test_every_candidate_level_carries_evidence(self) -> None:
        """各级候选建议均带证据引用；被污染候选（R4）转待复核不带建议。"""
        fusions = {
            "QUALITY_ABNORMAL": _fusion(True, 0.85, symptom_tag="QUALITY_ABNORMAL"),
            "OVERAGGRESSIVE": _fusion(True, 0.9, symptom_tag="OVERAGGRESSIVE"),
        }
        r = classify(fusions, {}, {"auto_rate_avg": 0.4, "score_avg": 70}, _gate())
        with_evidence = [rec for rec in r.recommendations if rec.evidence]
        # 仪表主 + 投用次（过激被仪表污染 → 待复核，R4 不生成建议）
        assert len(with_evidence) == 2
        for rec in with_evidence:
            assert "support" in rec.evidence and "category" in rec.evidence
        util_rec = next(rec for rec in with_evidence if rec.evidence["category"] == "UTILIZATION")
        assert util_rec.evidence["source"] == "kpi_context"
        assert util_rec.evidence["autoRate"] == 0.4
        assert any(j.category == "TUNING" for j in r.pending_review)

    def test_stiction_basis_marks_independent_evidence(self) -> None:
        """级 2 依据文案标明"排查候选/非独立确诊"（08§4.4.7）。"""
        fusions = {
            "VALVE_STICTION": FamilyFusion(
                family="stiction",
                symptom_tag="VALVE_STICTION",
                detected=True,
                confidence=0.88,
                contributors=[
                    {"operator": "stiction_ellipse", "confidence": 0.88},
                    {"operator": "stiction_choudhury", "confidence": 0.85},
                ],
                independent_evidence_count=1,
            )
        }
        r = classify(fusions, {}, _KPI, _gate())
        basis_text = " ".join(r.primary.basis)
        assert "支持度 0.88" in basis_text
        assert "独立证据 1 组" in basis_text
        assert "排查候选" in basis_text


# ---------------------------------------------------------------------------
# 7. SYSTEM 建议守卫：取消/失败/未评估/未决不推确定建议（08§4.2 + R11）
# ---------------------------------------------------------------------------


class TestSystemActionGuards:
    @pytest.mark.asyncio
    async def test_failed_run_produces_no_actions(self) -> None:
        """FAILED run（失败留痕/零检查占位）不发布任何 SYSTEM 建议。"""
        db, _ = _mock_db(existing_count=0)
        n = await generate_system_actions(db, _run_row(status="FAILED", primary=None))
        assert n == 0
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_audit_row_produces_no_actions(self) -> None:
        """取消审计行（FAILED + cancelled=True + NOT_ASSESSED）不产建议。"""
        db, _ = _mock_db(existing_count=0)
        meta = {
            "judgementStatus": JUDGEMENT_NOT_ASSESSED,
            "causeStatus": "NOT_ASSESSED",
            "cancelled": True,
        }
        n = await generate_system_actions(db, _run_row(status="FAILED", meta=meta, primary=None))
        assert n == 0
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_assessed_meta_blocks_actions(self) -> None:
        """judgementStatus=NOT_ASSESSED（即便 status 非 FAILED）不发布建议。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_NOT_ASSESSED}
        n = await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=meta, primary="VALVE")
        )
        assert n == 0
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_inconclusive_blocks_definite_tuning_actions(self) -> None:
        """INCONCLUSIVE（未决）不推确定整定建议（TUNING 模板滤除）。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_INCONCLUSIVE}
        n = await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=meta, primary="TUNING")
        )
        assert n == 0
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_inconclusive_keeps_utilization_facts(self) -> None:
        """INCONCLUSIVE 下投用（独立维度事实）建议不受滤除。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_INCONCLUSIVE}
        n = await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=meta, primary="UTILIZATION")
        )
        assert n > 0
        added = [c.args[0] for c in db.add.call_args_list]
        assert all(o.category == "UTILIZATION" for o in added)

    @pytest.mark.asyncio
    async def test_reviewed_overrides_inconclusive_filter(self) -> None:
        """人工复核已决的分类不受未决滤除（人工结论优先）。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_INCONCLUSIVE}
        n = await generate_system_actions(
            db,
            _run_row(
                status="SUCCESS",
                meta=meta,
                primary=None,
                review_status="REVIEWED",
                review_results=["TUNING"],
            ),
        )
        assert n > 0
        added = [c.args[0] for c in db.add.call_args_list]
        assert all(o.category == "TUNING" for o in added)
        assert all(o.basis.startswith("人工复核") for o in added)

    @pytest.mark.asyncio
    async def test_legacy_run_without_meta_unchanged(self) -> None:
        """legacy run（无 assessment_meta）行为不变（reader-first）。"""
        db, _ = _mock_db(existing_count=0)
        n = await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=None, primary="INSTRUMENT")
        )
        assert n > 0  # 历史行为：按分类生成

    @pytest.mark.asyncio
    async def test_unresolved_symptom_run_no_category_actions(self) -> None:
        """症状未决 run（primary=None）无分类来源 → 不产 SYSTEM 建议。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_SYMPTOM_DETECTED, "causeStatus": CAUSE_UNRESOLVED}
        n = await generate_system_actions(db, _run_row(status="SUCCESS", meta=meta, primary=None))
        assert n == 0
        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# 8. SYSTEM 建议证据链绑定（basis 引用算子+支持度）
# ---------------------------------------------------------------------------


class TestSystemActionEvidenceBasis:
    @pytest.mark.asyncio
    async def test_basis_carries_operators_and_support(self) -> None:
        """basis 文案含证据算子与支持度（建议-证据-run 三方可溯）。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_SYMPTOM_DETECTED, "causeStatus": "CANDIDATE"}
        recs = [
            {
                "content": "检查校验变送器/仪表",
                "basis": "…",
                "direction": "校验/维护",
                "priority": 1,
                "evidence": {
                    "category": "INSTRUMENT",
                    "operators": ["sensor_fault"],
                    "support": 0.85,
                    "symptomTag": "QUALITY_ABNORMAL",
                },
            }
        ]
        n = await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=meta, primary="INSTRUMENT", recommendations=recs)
        )
        assert n > 0
        added = [c.args[0] for c in db.add.call_args_list]
        assert "sensor_fault" in added[0].basis
        assert "支持度 0.85" in added[0].basis
        assert "规则支持度" in added[0].basis  # 不冒称概率
        assert added[0].run_id is None  # run_id 由调用方落库路径绑定（列已存在）

    @pytest.mark.asyncio
    async def test_basis_fits_column_limit(self) -> None:
        """basis 不超过 loop_action_item.basis 列宽 500。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_SYMPTOM_DETECTED}
        recs = [
            {
                "content": "x",
                "basis": "…",
                "direction": "y",
                "priority": 1,
                "evidence": {
                    "category": "INSTRUMENT",
                    "operators": [f"operator_with_a_rather_long_name_{i}" for i in range(20)],
                    "support": 0.85,
                },
            }
        ]
        await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=meta, primary="INSTRUMENT", recommendations=recs)
        )
        added = [c.args[0] for c in db.add.call_args_list]
        assert all(len(o.basis) <= 500 for o in added)

    @pytest.mark.asyncio
    async def test_utilization_basis_carries_kpi_fact(self) -> None:
        """UTILIZATION 无算子证据 → basis 引用 KPI 统计事实。"""
        db, _ = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_INCONCLUSIVE}
        recs = [
            {
                "content": "恢复自动投用",
                "basis": "…",
                "direction": "恢复自动投用",
                "priority": 2,
                "evidence": {
                    "category": "UTILIZATION",
                    "operators": [],
                    "support": 0.6,
                    "source": "kpi_context",
                    "autoRate": 0.4,
                },
            }
        ]
        await generate_system_actions(
            db, _run_row(status="SUCCESS", meta=meta, primary="UTILIZATION", recommendations=recs)
        )
        added = [c.args[0] for c in db.add.call_args_list]
        assert "自动投用率 40%" in added[0].basis


# ---------------------------------------------------------------------------
# 9. assessment_meta 增量字段（08§4.2 示例 supportRuleVersion）
# ---------------------------------------------------------------------------


class TestAssessmentMetaSupportRuleVersion:
    def test_support_rule_version_persisted(self) -> None:
        meta = build_assessment_meta(
            op_results={"a": _op("a", ASSESSMENT_NEGATIVE)},
            explicitly_excluded={},
            profile_expected_operators=["a"],
            primary_category=None,
            gate_passed=True,
            gate_reason=None,
            selected_is_empty=False,
        )
        assert meta["supportRuleVersion"] == SUPPORT_RULE_VERSION
        assert meta["supportKind"] == "RULE_SUPPORT"
        assert meta["calibrationStatus"] == "NOT_CALIBRATED"
