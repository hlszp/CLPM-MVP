"""P3-08a 公共接线（评估基础侧）专项测试.

覆盖《P3基础批-任务书》A【4】核验对应的可单测部分（真实 PG 迁移链与
受控构建全链由门禁/集成路径验证，不在本文件 mock 层重复）：

- O-1~O-5 定稿载体：诊断 assessment_meta 列（模型层）、评估三值
  assessmentState 命名空间、E 六类与算子 NOT_RUN 码表、工程事实
  provenance/origin 枚举与条目四件套；
- 评估 profile 注册表（服务端策略表=唯一来源：未注册/planned 显式拒绝）
  与发布代际（register→activate→retire 状态迁移 + 无 ACTIVE 行显式 null）；
- manifest schema 1.3（只加不改：发布三节 + KPI_ORIGINAL_EVENTS 原事件包
  + derivation 派生身份三方对账；旧 1.2 包主版本门禁不变）；
- 完整语义比较（指标集合/状态/原因/分母/覆盖/composite/身份——旧值级
  比较发现不了的 reason 差异被捕获）与离线验收隔离（只读、不写投影）；
- G2 移交观察项 O1（受控构建相对路径 resolve）与 O2（拒绝消息带 stdout）。
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.core.config import settings
from app.core.exceptions import BizError
from app.models.calc_dataset_snapshot import KIND_KPI_ORIGINAL_EVENTS
from app.services import calc_snapshot as cs
from tests.test_calc_snapshot import (
    _TEST_BUILD,
    _TS0,
    _TS1,
    _exec_result,
    _kpi_manifest_extra,
    _LoadDB,
    _make_kpi_bundles,
    _record_stub,
    _SaveDB,
)


@pytest.fixture()
def snap_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    return settings.CLPM_CALC_SNAPSHOT_DIR


@pytest.fixture()
def fixed_build(monkeypatch):
    monkeypatch.setattr(cs, "executable_build_digest", lambda: _TEST_BUILD)
    return _TEST_BUILD


def _exec_first(row=None):
    """db.execute 应答：``.scalars().first()`` 口径（get_active_release）."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    return result


# ---------------------------------------------------------------------------
# 1. O-3/O-4/O-5 契约枚举与码表载体
# ---------------------------------------------------------------------------


class TestContractEnums:
    def test_metric_assessment_state_values(self):
        """O-3：评估侧三值（COMPLETE/PARTIAL/UNASSESSABLE，11§3 一字不差）."""
        from app.contracts.data_types import MetricAssessmentState

        assert {e.value for e in MetricAssessmentState} == {
            "COMPLETE",
            "PARTIAL",
            "UNASSESSABLE",
        }

    def test_metric_state_namespaced_from_operator_states(self):
        """O-3：评估三值与诊断算子八态值域零交集（同名不同命名空间，不混用）."""
        from app.contracts.data_types import MetricAssessmentState

        operator_states = {
            "POSITIVE",
            "NEGATIVE",
            "NOT_APPLICABLE",
            "INSUFFICIENT_DATA",
            "ERROR",
            "NOT_RUN",
            "CANCELLED",
            "LEGACY_UNVERIFIABLE",
        }
        metric_states = {e.value for e in MetricAssessmentState}
        assert metric_states & operator_states == set()

    def test_roster_six_classes_with_reason_codes(self):
        """O-4：E 六类互斥归类 + 各类原因细分码（守恒等式六类齐备）."""
        from app.contracts.data_types import ROSTER_CLASS_REASON_CODES, RosterMembershipClass

        classes = {e.value for e in RosterMembershipClass}
        assert classes == {"scorable", "incomplete", "failed", "cancelled", "pending", "missing"}
        assert set(ROSTER_CLASS_REASON_CODES) == classes
        # 原因码作为标签另报，不进入等式本身：码表值跨类不重复
        all_codes = [c for codes in ROSTER_CLASS_REASON_CODES.values() for c in codes]
        assert len(all_codes) == len(set(all_codes))

    def test_operator_not_run_reason_codes(self):
        """O-4：算子 NOT_RUN 六码（08§4.1；NOT_RUN=未选/停用/上游阻断/预算终止）."""
        from app.contracts.data_types import OPERATOR_NOT_RUN_REASON_CODES

        assert OPERATOR_NOT_RUN_REASON_CODES == frozenset(
            {
                "NOT_SELECTED_GROUP",
                "DISABLED_BY_CONFIG",
                "NOT_SHORTLISTED",
                "UPSTREAM_BLOCKED",
                "SCOPE_EXCLUDED",
                "BUDGET_TERMINATED",
            }
        )

    def test_engineering_fact_provenance_and_reference_origin(self):
        """O-5：来源三分枚举 + 参考值来源枚举（值域冻结）."""
        from app.contracts.data_types import EngineeringFactProvenance, ReferenceValueOrigin

        assert {e.value for e in EngineeringFactProvenance} == {
            "ACTUAL",
            "TEMPLATE",
            "DEFAULT",
            "UNKNOWN",
        }
        assert {e.value for e in ReferenceValueOrigin} == {
            "MANUAL",
            "DEFAULT",
            "ENGINEERING_FACT",
            "VERIFIED_MODEL",
            "UNKNOWN",
        }

    def test_diagnosis_run_assessment_meta_column(self):
        """O-1：diagnosis_run.assessment_meta 可空 JSONB 载体（迁移 p308arele01 同批）."""
        from app.models.diagnosis_run import DiagnosisRun

        col = DiagnosisRun.__table__.c["assessment_meta"]
        assert col.nullable is True
        assert col.type.__class__.__name__ == "JSONB"


# ---------------------------------------------------------------------------
# 2. O-5 工程事实条目四件套（provenance/unit/interpretationVersion）
# ---------------------------------------------------------------------------


class TestEngineeringFactsCarrier:
    def _loop(self, **kwargs):
        return SimpleNamespace(
            op_output_lower_limit=kwargs.get("lower"),
            op_output_upper_limit=kwargs.get("upper"),
            ideal_settling_time=kwargs.get("tprime"),
        )

    def test_op_limits_loop_field_provenance_actual(self):
        from app.contracts.data_types import EngineeringFactProvenance
        from app.services.engineering_facts import _resolve_op_limits

        out = _resolve_op_limits(self._loop(lower=20.0, upper=80.0), None)
        assert out["source"] == "LOOP_FIELD"
        assert out["provenance"] == EngineeringFactProvenance.ACTUAL
        assert out["interpretationVersion"] == "1"
        assert out["unit"] is None  # 无 OP tag 时单位未知（不虚构）

    def test_op_limits_default_0_100_not_actual(self):
        """三分语义：DEFAULT_0_100 只是默认假设，不当已核实限位（契约 §2.5）."""
        from app.contracts.data_types import EngineeringFactProvenance
        from app.services.engineering_facts import _resolve_op_limits

        out = _resolve_op_limits(self._loop(), None)
        assert out["source"] == "DEFAULT_0_100"
        assert out["provenance"] == EngineeringFactProvenance.DEFAULT

    def test_reference_values_atolerance_unknown_tprime_origins(self):
        """A 容限预留（UNKNOWN 不虚构，P3-03 供数）+ T′ 手动优先/默认回退显式标来源."""
        from app.contracts.data_types import ReferenceValueOrigin
        from app.services.engineering_facts import _resolve_reference_values

        with_manual = _resolve_reference_values(self._loop(tprime=120.0))
        assert with_manual["aTolerance"]["value"] is None
        assert with_manual["aTolerance"]["provenance"] == "UNKNOWN"
        assert with_manual["tPrime"] == {
            "value": 120.0,
            "origin": ReferenceValueOrigin.MANUAL,
            "sourceField": "loop_ledger.ideal_settling_time",
            "note": "手动指定的理想稳态时间（手动优先）",
            "unit": "s",
            "interpretationVersion": "1",
        }
        without = _resolve_reference_values(self._loop())
        assert without["tPrime"]["value"] is None
        assert without["tPrime"]["origin"] == ReferenceValueOrigin.DEFAULT


# ---------------------------------------------------------------------------
# 3. 评估 profile 注册表（服务端策略表=唯一来源）
# ---------------------------------------------------------------------------


class TestAssessmentProfileRegistry:
    def test_default_resolves_legacy_v2(self):
        from app.services.assessment_profile import AssessmentProfileId, resolve_assessment_profile

        got = resolve_assessment_profile(None)
        assert got["profileId"] == AssessmentProfileId.LEGACY_V2
        assert got["algorithmVersion"] == "KPI_CALC_v2.0"

    def test_unknown_profile_rejected(self):
        from app.services.assessment_profile import (
            ERR_ASSESSMENT_PROFILE_UNKNOWN,
            resolve_assessment_profile,
        )

        with pytest.raises(BizError) as ei:
            resolve_assessment_profile("KPI_CALC_v9.9-arbitrary")
        assert ei.value.code == ERR_ASSESSMENT_PROFILE_UNKNOWN

    def test_candidate_v3_registered_but_not_selectable(self):
        """CANDIDATE_V3_RC1 已登记（候选名登记位）但算法归 P3-09/10——选择即显式拒绝."""
        from app.services.assessment_profile import (
            ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE,
            PROFILES,
            AssessmentProfileId,
            resolve_assessment_profile,
        )

        assert AssessmentProfileId.CANDIDATE_V3_RC1 in PROFILES
        with pytest.raises(BizError) as ei:
            resolve_assessment_profile(AssessmentProfileId.CANDIDATE_V3_RC1)
        assert ei.value.code == ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE

    def test_manifest_source_section(self):
        from app.services.assessment_profile import release_manifest_source_section

        section = release_manifest_source_section(
            {
                "assessmentProfile": "LEGACY_V2",
                "releaseGeneration": None,
                "dataPolicyVersion": "pre_v1",
            }
        )
        assert section == {
            "assessmentProfile": "LEGACY_V2",
            "releaseGeneration": None,
            "dataPolicyVersion": "pre_v1",
        }
        assert release_manifest_source_section(None) == {
            "assessmentProfile": None,
            "releaseGeneration": None,
            "dataPolicyVersion": None,
        }


# ---------------------------------------------------------------------------
# 4. 发布代际（上线清单登记：register → activate → resolve 六要素）
# ---------------------------------------------------------------------------


class TestReleaseRegistry:
    async def test_task_context_without_active_release_is_explicit_null(self):
        """无 ACTIVE 代际：releaseGeneration=None（legacy 口径显式标记，不伪代际）."""
        from app.services.assessment_profile import resolve_task_release_context

        db = MagicMock()
        db.execute = AsyncMock(return_value=_exec_first(None))
        ctx = await resolve_task_release_context(db)
        assert ctx["assessmentProfile"] == "LEGACY_V2"
        assert ctx["releaseGeneration"] is None
        assert ctx["releaseStatus"] == "UNREGISTERED"
        assert ctx["buildDigest"] == cs.executable_build_digest()
        # 六要素键齐备（上线清单可查）
        assert {
            "assessmentProfile",
            "algorithmVersion",
            "dataPolicyVersion",
            "configRevision",
            "buildDigest",
            "releaseGeneration",
        } <= set(ctx)

    async def test_register_rejects_unregistered_profile_and_version_drift(self):
        from app.services.assessment_profile import (
            ERR_ASSESSMENT_PROFILE_UNKNOWN,
            ERR_RELEASE_GENERATION_CONFLICT,
            register_release,
        )

        db = MagicMock()
        flushed: list = []
        db.add = lambda row: flushed.append(row)
        db.flush = AsyncMock()
        with pytest.raises(BizError) as ei:
            await register_release(
                db,
                release_generation="rg-x",
                assessment_profile="NOT_A_PROFILE",
                algorithm_version="X",
                data_policy_version="p",
                config_revision=None,
                snapshot_schema_version="1.3",
            )
        assert ei.value.code == ERR_ASSESSMENT_PROFILE_UNKNOWN
        # 版本漂移拒绝：登记 algorithmVersion 与策略表不一致（10§3.5 统一来源）
        with pytest.raises(BizError) as ei:
            await register_release(
                db,
                release_generation="rg-y",
                assessment_profile="LEGACY_V2",
                algorithm_version="KPI_CALC_v9.9",
                data_policy_version="pre_v1",
                config_revision=None,
                snapshot_schema_version="1.3",
            )
        assert ei.value.code == ERR_RELEASE_GENERATION_CONFLICT

    async def test_activate_retires_previous_active_and_resolves_context(self):
        """激活新代际：旧 ACTIVE 自动 RETIRE；任务上下文读新六要素（回退=再激活旧代际）."""
        from app.models.assessment_release import AssessmentReleaseRegistry
        from app.services.assessment_profile import (
            RELEASE_STATUS_ACTIVE,
            RELEASE_STATUS_STAGED,
            activate_release,
            release_row_to_context,
            resolve_task_release_context,
        )

        new_row = AssessmentReleaseRegistry(
            id="11111111-1111-1111-1111-111111111111",
            release_generation="rg-0002-v3",
            assessment_profile="LEGACY_V2",
            algorithm_version="KPI_CALC_v2.0",
            data_policy_version="pre_v1",
            snapshot_schema_version="1.3",
            config_revision="rev:7:abcd1234",
            status=RELEASE_STATUS_STAGED,
        )
        old_row = AssessmentReleaseRegistry(
            id="22222222-2222-2222-2222-222222222222",
            release_generation="rg-0001-legacy-v2",
            assessment_profile="LEGACY_V2",
            algorithm_version="KPI_CALC_v2.0",
            data_policy_version="pre_v1",
            snapshot_schema_version="1.3",
            status=RELEASE_STATUS_ACTIVE,
        )
        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        # activate_release：目标行 → 当前 ACTIVE 行；后续 resolve 的 get_active_release
        db.execute = AsyncMock(
            side_effect=[
                _exec_result(scalar=new_row),
                _exec_result(scalar=old_row),
                _exec_first(new_row),
            ]
        )
        from app.services.assessment_profile import activate_release

        activated = await activate_release(db, "rg-0002-v3")
        assert activated.status == RELEASE_STATUS_ACTIVE
        assert old_row.status == "RETIRED"
        assert old_row.retired_at is not None
        # 任务边界读 ACTIVE：六要素落位（登记值优先，不读缓存）
        ctx = await resolve_task_release_context(db)
        assert ctx["releaseGeneration"] == "rg-0002-v3"
        assert ctx["configRevision"] == "rev:7:abcd1234"
        assert ctx["releaseStatus"] == RELEASE_STATUS_ACTIVE
        # 行→上下文直转（上线清单六要素可查）
        assert release_row_to_context(new_row)["algorithmVersion"] == "KPI_CALC_v2.0"


# ---------------------------------------------------------------------------
# 5. manifest schema 1.3：发布三节 + 原事件包 + 派生身份（只加不改）
# ---------------------------------------------------------------------------


class TestSchema13OriginalEvents:
    async def test_kpi_grid_schema_1_3_with_release_section(self, snap_dir, fixed_build):
        """schema 1.3 + source 发布三节（六要素之三；其余三既有）落包可读回."""
        from app.services.assessment_profile import release_manifest_source_section

        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays={"sig": np.arange(8, dtype=np.float64)},
            manifest_extra=_kpi_manifest_extra(
                source_extra=release_manifest_source_section(
                    {
                        "assessmentProfile": "LEGACY_V2",
                        "releaseGeneration": "rg-0001-legacy-v2",
                        "dataPolicyVersion": "pre_v1",
                    }
                ),
            ),
        )
        assert row.schema_version == "1.3"
        paths = cs.snapshot_file_paths(str(row.id))
        manifest = json.loads(
            next(p for p in paths if p.name.endswith(".manifest.json")).read_text(encoding="utf-8")
        )
        src = manifest["source"]
        assert src["assessmentProfile"] == "LEGACY_V2"
        assert src["releaseGeneration"] == "rg-0001-legacy-v2"
        assert src["dataPolicyVersion"] == "pre_v1"
        assert src["executableBuildDigest"] == _TEST_BUILD  # 既有键不受影响

    async def test_original_events_snapshot_saved_and_readable(self, snap_dir, fixed_build):
        """原事件包：事件/覆盖/初值/绑定节完整落盘，kind 专属可读回（08b 填真实数据）."""
        arrays = {
            "ev__ts": cs._ns_int([_TS0, _TS0 + timedelta(seconds=5)]),
            "ev__sig_sp": np.array([50.0, 51.0], dtype=np.float64),
        }
        row = await cs.save_original_events_snapshot(
            _SaveDB()._db,
            loop_id="loop-oe",
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:loop-oe",
            arrays=arrays,
            binding={"datasetRef": "test:loop:loop-oe", "bindingVersion": "bv-1"},
            events={"sp": [{"ts": _TS0.isoformat(), "kind": "SP_STEP"}], "mode": None},
            coverage={"pv": {"known": [[0, 1800]], "unknown": []}},
            initial_state={"cov": {"pv": 49.9, "ts": _TS0.isoformat()}},
            derivation_plan={"grids": ["legacy-base-5s", "candidate-hf-1s"]},
        )
        assert row.kind == KIND_KPI_ORIGINAL_EVENTS
        paths = cs.snapshot_file_paths(str(row.id))
        manifest = json.loads(
            next(p for p in paths if p.name.endswith(".manifest.json")).read_text(encoding="utf-8")
        )
        assert manifest["kind"] == KIND_KPI_ORIGINAL_EVENTS
        assert manifest["schemaVersion"] == "1.3"
        assert manifest["events"]["sp"][0]["kind"] == "SP_STEP"
        assert manifest["coverage"]["pv"]["known"] == [[0, 1800]]
        assert manifest["initialState"]["cov"]["pv"] == 49.9
        assert manifest["derivationPlan"]["grids"] == ["legacy-base-5s", "candidate-hf-1s"]

    async def test_derived_grid_identity_three_way_accountable(self, snap_dir, fixed_build):
        """派生网格 derivation 节：原事件 hash + 派生包 hash 三方可对账（10§3.5）."""
        oe = await cs.save_original_events_snapshot(
            _SaveDB()._db,
            loop_id="loop-oe",
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:loop-oe",
            arrays={"ev__sig_sp": np.array([50.0, 51.0], dtype=np.float64)},
            binding={"datasetRef": "test:loop:loop-oe"},
        )
        deriv = cs.derivation_section(
            oe, derivation_method="grid/base-5s-cov-keep", grid_policy={"periodS": 5}
        )
        grid_old = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:loop-oe",
            arrays={"g1__sig_pv": np.arange(10, dtype=np.float64)},
            manifest_extra=_kpi_manifest_extra(derivation={**deriv, "gridPolicy": {"periodS": 5}}),
        )
        grid_new = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:loop-oe",
            arrays={"g1__sig_pv": np.arange(20, dtype=np.float64) / 2.0},
            manifest_extra=_kpi_manifest_extra(
                derivation=cs.derivation_section(
                    oe, derivation_method="grid/hf-1s-cov-keep", grid_policy={"periodS": 1}
                )
            ),
        )
        # 三方身份互异且派生节回指原事件（原事件→旧网格/新网格双 hash 可复核）
        assert len({oe.input_hash, grid_old.input_hash, grid_new.input_hash}) == 3
        assert deriv["sourceSnapshotId"] == str(oe.id)
        assert deriv["sourceInputHash"] == oe.input_hash

    async def test_legacy_1_2_manifest_still_loads(self, snap_dir, fixed_build):
        """1.1/1.2 兼容（R17 只加不改）：旧 schema 次版本包按主版本门禁照常可读."""
        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L12",
            arrays={"sig": np.arange(6, dtype=np.float64)},
            manifest_extra=_kpi_manifest_extra(),
            schema_version="1.2",  # 显式按旧次版本落包
        )
        row.schema_version = "1.2"
        loaded = await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert loaded.manifest["schemaVersion"] == "1.2"
        assert "assessmentProfile" not in (loaded.manifest.get("source") or {})  # 当时未记录


# ---------------------------------------------------------------------------
# 6. 完整语义比较（10§3.5.3：状态/原因/分母/覆盖/composite/身份入比较域）
# ---------------------------------------------------------------------------


def _compute_semantic(bundles=None):
    from app.contracts.data_types import ControlType
    from app.tasks.kpi_calc import (
        _build_config_bundle,
        _compute_kpis_three_layer,
        _extract_metrics_semantic,
    )

    bundles = bundles or _make_kpi_bundles()
    config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
    metric_results, composite = _compute_kpis_three_layer(bundles, config_bundle, None)
    return _extract_metrics_semantic(metric_results, composite)


class TestSemanticComparison:
    def test_extract_metrics_semantic_shape(self):
        semantic = _compute_semantic()
        entry = next(iter(semantic.values()))
        assert {"value", "confidenceLevel", "assessmentState", "details"} <= set(entry)
        composite = semantic["__composite__"]
        assert {"value", "confidenceLevel", "details"} <= set(composite)
        # legacy 计算器未填评估状态 → 显式 null（不伪补 COMPLETE）
        assert all(v["assessmentState"] is None for v in semantic.values())

    def test_diff_semantic_views_reason_change_detected(self):
        """核心证据：reason 变化（value 不变）旧值级比较发现不了，语义比较捕获."""
        semantic = _compute_semantic()
        v1 = cs.extract_semantic_view(metrics_semantic=semantic)
        replay = cs.extract_semantic_view(metrics_semantic=semantic)
        # 值级不动，仅 details 原因变化（例如 09a 后 NO_DYNAMIC_EVIDENCE 语义）
        first = next(k for k in replay["metrics"] if k != "__composite__")
        replay["metrics"][first]["details"]["reason"] = "NO_DYNAMIC_EVIDENCE"
        diffs = cs.diff_semantic_views(v1, replay)
        assert any(d.startswith(f"metrics.{first}.details.reason") for d in diffs)
        # 值级一致场景下 differences（旧比较域）为空——语义比较是唯一捕获者
        assert "value" not in "".join(diffs)

    def test_diff_distinguishes_absent_from_null(self):
        v1 = cs.extract_semantic_view(metrics_semantic={"a": {"value": None, "details": {}}})
        replay = cs.extract_semantic_view(metrics_semantic={"a": {"value": None}})
        diffs = cs.diff_semantic_views(v1, replay)
        assert any("details" in d for d in diffs)  # 缺键 ≠ 显式 null（分开）
        assert diffs  # 非空

    def test_semantic_equivalent_views_no_diff(self):
        semantic = _compute_semantic()
        v1 = cs.extract_semantic_view(metrics_semantic=semantic)
        v2 = cs.extract_semantic_view(metrics_semantic=semantic)
        assert cs.diff_semantic_views(v1, v2) == {}

    async def test_replay_semantic_compare_with_new_record(self, snap_dir, fixed_build):
        """新 record（带 metricsSemantic）重放：identical 含语义域；v1 语义可比."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        metric_results, composite = _compute_kpis_three_layer(bundles, config_bundle, None)
        from app.tasks.kpi_calc import _extract_metrics_semantic

        v1_detail = _extract_metrics_detail(metric_results)
        v1_semantic = _extract_metrics_semantic(metric_results, composite)
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays,
            manifest_extra=_kpi_manifest_extra(blocks=blocks_meta),
        )
        record = SimpleNamespace(
            id="rec-sem",
            dataset_snapshot_id=str(row.id),
            payload={
                "metricsDetail": v1_detail,
                "metricsSemantic": v1_semantic,
                "composite": v1_semantic["__composite__"],
                # assessmentContext 与包 manifest source 键域对齐（六键）：
                # legacy-unpinned 口径（fixture 包无发布节/无 effectiveParams）
                "assessmentContext": {
                    "assessmentProfile": None,
                    "releaseGeneration": None,
                    "dataPolicyVersion": None,
                    "algorithmVersion": "KPI_CALC_v4.0",
                    "configRevision": "digest:abc",
                    "paramsSource": "legacy-unpinned",
                },
            },
        )
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-sem")
        assert report["v1SemanticAvailable"] is True
        # identity 域：包内无发布节（legacy-unpinned）与 assessmentContext 的
        # paramsSource 口径对齐（无 params 节=legacy-unpinned）
        assert report["semanticDifferences"] == {} or all(
            "identity.configRevision" not in d for d in report["semanticDifferences"]
        ), report["semanticDifferences"]
        assert report["identical"], report["semanticDifferences"]
        assert report["comparisonKind"] == "reproduction"

    async def test_replay_legacy_record_value_level_only(self, snap_dir, fixed_build):
        """旧 record（P3-08a 前无 metricsSemantic）：显式退回值级比较，非静默."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        v1_detail = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None)[0]
        )
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays,
            manifest_extra=_kpi_manifest_extra(blocks=blocks_meta),
        )
        record = _record_stub("rec-legacy", str(row.id), v1_detail)
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-legacy")
        assert report["v1SemanticAvailable"] is False
        assert report["identical"] is True


# ---------------------------------------------------------------------------
# 7. 离线验收隔离（C12-EA3：不改生产投影；candidate 未接入显式拒绝）
# ---------------------------------------------------------------------------


class TestOfflineAcceptance:
    async def test_offline_replay_readonly_no_projection_writes(self, snap_dir, fixed_build):
        """离线验收全程只读：报告显式 wroteProjection=false，db 无投影写语句."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        v1_detail = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None)[0]
        )
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays,
            manifest_extra=_kpi_manifest_extra(blocks=blocks_meta),
        )
        record = _record_stub("rec-off", str(row.id), v1_detail)

        db = MagicMock()
        stmts: list = []
        db.execute = AsyncMock(
            side_effect=lambda stmt: (
                stmts.append(stmt),
                _exec_result(scalar=row),
            )[1]
        )
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.offline_acceptance_replay(db, "rec-off")
        assert report["offlineAcceptance"] is True
        assert report["wroteProjection"] is False
        assert report["identical"] is True
        # 只读断言：仅发生 load SELECT 一条语句，且不含任何投影/账本写
        assert len(stmts) == 1
        joined = str(stmts[0]).lower()
        for forbidden in ("insert", "update", "delete"):
            assert forbidden not in joined
        assert "kpi_snapshot" not in joined and "loop_confidence" not in joined

    async def test_offline_candidate_profile_explicitly_rejected(self, snap_dir, fixed_build):
        """CANDIDATE_V3_RC1 未接入（P3-09/10）→ 离线验收显式拒绝，不冒称可用."""
        from app.services.assessment_profile import (
            ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE,
            AssessmentProfileId,
        )

        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays={"sig": np.arange(4, dtype=np.float64)},
            manifest_extra=_kpi_manifest_extra(),
        )
        record = _record_stub("rec-off2", str(row.id), {"accuracy_rate": {"value": 1.0}})
        with (
            patch(
                "app.services.result_ledger.get_record_by_id",
                new=AsyncMock(return_value=record),
            ),
            pytest.raises(BizError) as ei,
        ):
            await cs.offline_acceptance_replay(
                _LoadDB(row)._db, "rec-off2", profile_id=AssessmentProfileId.CANDIDATE_V3_RC1
            )
        assert ei.value.code == ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE

    async def test_offline_same_profile_runs_in_process_contrast_mode(self, snap_dir, fixed_build):
        """指定 LEGACY_V2（=包隐式 profile）→ profile 对照模式路由 in-process."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        v1_detail = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None)[0]
        )
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays,
            manifest_extra=_kpi_manifest_extra(blocks=blocks_meta),
        )
        record = _record_stub("rec-off3", str(row.id), v1_detail)
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.offline_acceptance_replay(
                _LoadDB(row)._db, "rec-off3", profile_id="LEGACY_V2"
            )
        assert report["comparisonKind"] == "reproduction"
        assert report["targetAssessmentProfile"] == "LEGACY_V2"
        assert report["identical"] is True


# ---------------------------------------------------------------------------
# 8. G2 验收移交观察项 O1/O2（controlled_build）
# ---------------------------------------------------------------------------


class TestControlledBuildObservations:
    def _write_recipe(self, digest: str, path: str) -> None:
        import json

        root = cs.snapshot_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "retained-builds.json").write_text(
            json.dumps(
                {
                    "digests": [digest],
                    "builds": {digest: {"kind": "source", "path": path, "env": {}}},
                }
            ),
            encoding="utf-8",
        )

    def test_o1_relative_recipe_path_resolved(self, snap_dir, fixed_build, tmp_path, monkeypatch):
        """O1：配方相对路径先 resolve()——相对当前 cwd 的构建目录可用."""
        from app.services import controlled_build as cb

        build_dir = tmp_path / "build" / "backend"
        build_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        self._write_recipe(_TEST_BUILD, "build/backend")  # 相对路径
        ok_payload = {"ok": True, "buildDigest": _TEST_BUILD, "metrics": {"m": {"value": 1.0}}}
        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(ok_payload), stderr=""),
        ) as mock_run:
            out = cb.run_build_cli(_TEST_BUILD, ["digest"])
        assert out["ok"] is True
        # 子进程 cwd = resolve 后的绝对路径
        assert mock_run.call_args.kwargs["cwd"] == str(build_dir.resolve())

    def test_o2_rejection_message_carries_stdout_too(self, snap_dir, fixed_build):
        """O2：子进程失败拒绝消息同时带 stderr 与 stdout（CLI 失败详情可能走 stdout）."""
        from app.services import controlled_build as cb

        self._write_recipe(_TEST_BUILD, str(snap_dir))  # 任意存在目录
        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(
                returncode=2, stdout='{"ok": false, "error": "boom-detail"}', stderr="traceback"
            ),
        ):
            with pytest.raises(BizError) as ei:
                cb.run_build_cli(_TEST_BUILD, ["kpi", "--npz", "x", "--manifest", "y"])
        assert "stderr=traceback" in ei.value.message
        assert "boom-detail" in ei.value.message


# ---------------------------------------------------------------------------
# 9. 迁移链位（结构核对；真实 upgrade/alembic check 走门禁）
# ---------------------------------------------------------------------------


class TestMigrationChain:
    def test_migration_revision_chain(self):
        """p308arele01 单头挂在 p204engtpl01（G2 收口基线头）之后."""
        from pathlib import Path

        text = (
            Path(__file__).resolve().parent.parent
            / "alembic"
            / "versions"
            / "p308arele01_assessment_release_registry.py"
        ).read_text(encoding="utf-8")
        assert 'revision: str = "p308arele01"' in text
        assert 'down_revision: str | None = "p204engtpl01"' in text
        # 只加不改自检：downgrade 路径完整（表/列/CHECK 三步可逆）
        assert "drop_table" in text and "drop_column" in text


# ---------------------------------------------------------------------------
# 10. REWORK-D1：相对 snapshot 目录 + 受控构建真实子进程重放（G2 O1 完整闭合）
# ---------------------------------------------------------------------------


class TestReworkD1RelativeSnapshotDir:
    @pytest.mark.timeout(300)
    async def test_relative_snap_dir_controlled_build_replay_succeeds(
        self, tmp_path, monkeypatch, fixed_build
    ):
        """REWORK-D1 回归：CLPM_CALC_SNAPSHOT_DIR 为相对值 + cwd 漂移场景，
        受控构建子进程按**绝对化后的包路径**读原包真实重放成功（非 mock）。

        缺陷复现口径（验收 D1）：修复前 _locate_files 产物相对路径原样传
        子进程，构建 cwd 下解析必然"输入包文件缺失"恒拒绝——与 G2 基线
        同状；run_kpi_replay 入口 .resolve() 后闭合。
        """
        from pathlib import Path

        from tests.test_g2_increment import (
            _g2_arrays_and_meta,
            _g2_kpi_manifest_extra,
            _make_controlled_build,
            _package_paths,
            _save_package_in_build_era,
            _write_retained,
        )

        # cwd=临时基准，snapshot 目录用**相对值**（相对 cwd 解析）
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", "rel-snaps")
        build = _make_controlled_build(tmp_path, "D1", 0.5)  # 真实源码树副本构建
        assert build["digest"] != cs.executable_build_digest()  # 路由到子进程
        _write_retained(cs.snapshot_root(), [build["digest"]], {build["digest"]: build["recipe"]})
        arrays, blocks_meta = _g2_arrays_and_meta()
        row = await _save_package_in_build_era(build, blocks_meta, _g2_kpi_manifest_extra())
        npz, man = _package_paths(str(row.id))
        # 场景自检：包路径确为相对（缺陷前提成立）
        assert not Path(npz).is_absolute()

        # 受控构建真实子进程直接重放（REWORK-D1 修复目标入口）
        from app.services import controlled_build as cb

        out = cb.run_kpi_replay(build["digest"], npz, man)
        assert out["ok"] is True and out["buildDigest"] == build["digest"]
        v1 = out["metrics"]
        assert v1["steady_rate"]["value"] is not None

        # 全链复现：record 重放走 controlled-build 路由，相对目录场景不再
        # "文件缺失"拒绝，数值级 identical
        record = _record_stub("rec-d1", str(row.id), v1)
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-d1")
        assert report["executionMode"] == "controlled-build"
        assert report["identical"] is True, f"差异: {report['differences']}"
        assert report["executedBuildDigest"] == build["digest"]


# ---------------------------------------------------------------------------
# 11. REWORK-D2：真实 writer 产物 assessmentContext 语义比较零伪差异
# ---------------------------------------------------------------------------


class TestReworkD2RealWriterContext:
    async def test_real_release_context_replay_semantic_identical(self, snap_dir, fixed_build):
        """REWORK-D2 回归：record assessmentContext 用**真实链产物**
        （resolve_task_release_context 全量 12 键 + writer 回填函数），而非
        手工 6 键 fixture——新 record 同构建重放 identity 域零伪差异。

        缺陷复现口径（验收 D2）：修复前 12 键 vs 6 键恒报 8 键 <absent> 伪
        差异 + configRevision [None, 实值] → identical 恒 False。
        """
        from app.services.assessment_profile import (
            release_manifest_source_section,
            resolve_task_release_context,
        )
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
            _extract_metrics_semantic,
            _release_context_with_actuals,
        )

        # 真实 resolve_task_release_context 产物（mock db 无 ACTIVE 行 → 12 键
        # UNREGISTERED 全量——正是验收复现的 writer 输入形态）
        db = MagicMock()
        db.execute = AsyncMock(return_value=_exec_first(None))
        ctx12 = await resolve_task_release_context(db)
        assert len(ctx12) >= 12  # 全量键（含 paramsSchemaVersion/buildDigest/...）

        # 真实 writer 回填（任务实际 configRevision + paramsSource）
        task_config_revision = "rev:3:digest:deadbeefcafe"
        ctx_actual = _release_context_with_actuals(ctx12, task_config_revision, params_pinned=True)
        assert ctx_actual["configRevision"] == task_config_revision
        assert ctx_actual["paramsSource"] == "package"

        # 按真实 writer 口径落包（manifest source=release 优先 algorithmVersion
        # + configRevision + 发布三节 + effectiveParams.params 非空）
        from app.contracts.data_types import ControlType

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        metric_results, composite = _compute_kpis_three_layer(bundles, config_bundle, None)
        v1_detail = _extract_metrics_detail(metric_results)
        v1_semantic = _extract_metrics_semantic(metric_results, composite)
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        meta = _kpi_manifest_extra(
            blocks=blocks_meta,
            effective_params={
                "status": "PINNED",
                "configRevision": 3,
                "params": {"stability_rate|STABLE": {"decay_ratio": 0.1}},
                "sources": {},
                "scope": None,
            },
            source_extra=release_manifest_source_section(ctx_actual),
        )
        meta["source"]["algorithmVersion"] = ctx_actual["algorithmVersion"]
        meta["source"]["configRevision"] = task_config_revision
        row = await _SaveDB().save(
            kind=cs.KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L-d2",
            arrays=arrays,
            manifest_extra=meta,
        )
        # record payload = 真实 writer 产物（assessmentContext 全量 12+2 键）
        record = SimpleNamespace(
            id="rec-d2",
            dataset_snapshot_id=str(row.id),
            payload={
                "metricsDetail": v1_detail,
                "metricsSemantic": v1_semantic,
                "composite": v1_semantic["__composite__"],
                "assessmentContext": ctx_actual,
            },
        )
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-d2")
        assert report["v1SemanticAvailable"] is True
        # 修复断言：identity 域零伪差异（12 键投影后与 manifest 6 键同域恒等）
        assert report["semanticDifferences"] == {}, report["semanticDifferences"]
        assert report["identical"] is True
        # 反证保护：若 v1 侧不做投影（旧缺陷形态），全量 ctx 必产伪差异——
        # 用缺陷口径直接 diff 证明本用例确实覆盖该形态
        broken = cs.diff_semantic_views(
            cs.extract_semantic_view(metrics_semantic=v1_semantic, identity=ctx_actual),
            cs.extract_semantic_view(
                metrics_semantic=v1_semantic,
                identity=cs._semantic_view_from_manifest_source(meta),
            ),
        )
        assert any(k.startswith("identity.") for k in broken), (
            "反证失败：全量 ctx 与 manifest 域若无差异则本用例未覆盖 D2 形态"
        )
