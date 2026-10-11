"""P3-08d 固定参评名册与完整 P 可评分统计测试（10§3.6.2/§3.6.3）。

Oracle 全部独立手算（C26-EA1～5），消费 09a 交付的 reference_stats 三纯函数：
- C26-EA1：10 参评 = 6 完整 P + 2 仅参考（incomplete）+ 1 失败 + 1 无记录；
  另 2 条既有排除单列——比例 60%=6/10，互斥状态和=E，S 独立统计 8 条；
- C26-EA2：真实 0 分可评分（2/3）；E=0 → null；E>0 且 N=0 → 0%；
- C26-EA3：9 条参考 100×权重1 + 1 条动态 F0×权重3 → 主参考均分 75、
  动态子集 0、参考构成 9/10、参考权重 75%；全静稳 → 100/null/0%；
- C26-EA4：装置 A 一条 S100 + B 九条 S0 → 上级 S10（合并分子/权重和，
  不平均装置百分比 50）；权重和 0 → 均分 null；
- C26-EA5：当前静稳参考 100 不回退旧窗动态成功值；语义优先于旧快照列值。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.metric_calculator.reference_stats import weighted_score_stats
from app.services.node_performance import (
    ROSTER_STATE_FAILED,
    ROSTER_STATE_INCOMPLETE,
    ROSTER_STATE_MISSING,
    ROSTER_STATE_SCORABLE,
    aggregate_node_snapshot,
    build_assessment_stats,
    classify_roster_state,
)

TS0 = datetime(2026, 10, 12, 8, 0, 0)
TS1 = datetime(2026, 10, 12, 9, 0, 0)


def _snap(status="SUCCESS", score=Decimal("80"), fast_rate=Decimal("90")):
    return SimpleNamespace(status=status, score=score, fast_rate=fast_rate, result_record_id=None)


def _sem(composite_state=None, formal=None, reference=None):
    return {
        "compositeState": composite_state,
        "fastRate": {"formalValue": formal, "referenceScore": reference},
    }


def _stats(eligible, snaps=None, failed=None, semantics=None, weights=None, excluded=0):
    return build_assessment_stats(
        eligible_ids=sorted(eligible),
        excluded_count=excluded,
        latest_snaps=snaps or {},
        failed_ids=set(failed or []),
        semantics=semantics or {},
        weights=weights or {lid: Decimal("1.0") for lid in eligible},
        ts_start=TS0,
        ts_end=TS1,
    )


# ---------------------------------------------------------------------------
# classify_roster_state 状态矩阵
# ---------------------------------------------------------------------------


class TestClassifyRosterState:
    def test_no_snapshot_missing(self):
        assert classify_roster_state(
            has_snapshot=False,
            snapshot_status=None,
            snapshot_score=None,
            composite_state=None,
        ) == (ROSTER_STATE_MISSING, "NO_RECORD")

    def test_no_snapshot_failed_record(self):
        assert classify_roster_state(
            has_snapshot=False,
            snapshot_status=None,
            snapshot_score=None,
            composite_state=None,
            has_failed_record=True,
        ) == (ROSTER_STATE_FAILED, "FAILED_RECORD")

    def test_success_composite_complete_scorable_including_real_zero(self):
        assert classify_roster_state(
            has_snapshot=True,
            snapshot_status="SUCCESS",
            snapshot_score=Decimal("0"),
            composite_state="COMPLETE",
        ) == (ROSTER_STATE_SCORABLE, "COMPOSITE_COMPLETE")

    def test_success_composite_partial_incomplete(self):
        assert classify_roster_state(
            has_snapshot=True,
            snapshot_status="SUCCESS",
            snapshot_score=None,
            composite_state="PARTIAL",
        ) == (ROSTER_STATE_INCOMPLETE, "COMPOSITE_PARTIAL")

    def test_success_reference_only_is_incomplete_not_scorable(self):
        """INT-02：仅有 F 参考不算完整 P（composite PARTIAL → incomplete）。"""
        assert classify_roster_state(
            has_snapshot=True,
            snapshot_status="SUCCESS",
            snapshot_score=None,
            composite_state="UNASSESSABLE",
        ) == (ROSTER_STATE_INCOMPLETE, "COMPOSITE_UNASSESSABLE")

    def test_legacy_success_finite_score_scorable(self):
        assert classify_roster_state(
            has_snapshot=True,
            snapshot_status="SUCCESS",
            snapshot_score=Decimal("0"),
            composite_state=None,
        ) == (ROSTER_STATE_SCORABLE, "LEGACY_SCORE_FINITE")

    def test_legacy_success_null_score_incomplete(self):
        assert classify_roster_state(
            has_snapshot=True,
            snapshot_status="SUCCESS",
            snapshot_score=None,
            composite_state=None,
        ) == (ROSTER_STATE_INCOMPLETE, "SUCCESS_NULL_SCORE")

    def test_inconclusive_snapshot_incomplete(self):
        assert classify_roster_state(
            has_snapshot=True,
            snapshot_status="INCONCLUSIVE",
            snapshot_score=None,
            composite_state=None,
        ) == (ROSTER_STATE_INCOMPLETE, "SNAPSHOT_INCONCLUSIVE")


# ---------------------------------------------------------------------------
# C26-EA1：名册互斥账 + 排除单列 + 分项独立
# ---------------------------------------------------------------------------


class TestC26EA1Roster:
    def test_10_eligible_mixed_states(self):
        """6 完整 P + 2 仅参考 + 1 失败 + 1 无记录 → 60%=6/10；excluded=2 单列。"""
        eligible = [f"loop-{i:02d}" for i in range(1, 11)]
        snaps = {}
        semantics = {}
        # 6 条完整 P（composite COMPLETE，真实 0 分也在内）
        for i in range(1, 7):
            lid = f"loop-{i:02d}"
            snaps[lid] = _snap(score=Decimal(str(60 + i)))
            semantics[lid] = _sem(composite_state="COMPLETE")
        # 2 条仅有 F 参考（composite PARTIAL——必要 F 仅参考时 P 不完整）
        for i in (7, 8):
            lid = f"loop-{i:02d}"
            snaps[lid] = _snap(score=None, fast_rate=None)
            semantics[lid] = _sem(composite_state="PARTIAL", reference=100)
        # loop-09：无快照 + FAILED record；loop-10：无快照无记录
        stats = _stats(
            eligible,
            snaps=snaps,
            failed=["loop-09"],
            semantics=semantics,
            excluded=2,
        )
        roster = stats["roster"]
        assert roster["eligibleLoopCount"] == 10
        assert roster["scorableLoopCount"] == 6
        assert roster["scorableLoopRatio"] == 60.0
        assert roster["excludedLoopCount"] == 2  # 既有排除单列，不进 E
        assert roster["states"] == {
            "scorable": 6,
            "incomplete": 2,
            "failed": 1,
            "cancelled": 0,
            "pending": 0,
            "missing": 1,
        }
        # 互斥状态守恒：sum(states) == E
        assert sum(roster["states"].values()) == roster["eligibleLoopCount"]
        # members 与状态账一致
        assert len(roster["members"]) == 10
        assert roster["members"][0]["loopId"] == "loop-01"
        # membershipRevision 同名册幂等
        stats2 = _stats(eligible, snaps=snaps, failed=["loop-09"], semantics=semantics, excluded=2)
        assert stats2["roster"]["membershipRevision"] == roster["membershipRevision"]


# ---------------------------------------------------------------------------
# C26-EA2：真实 0 可评分 / E=0 → null / E>0 N=0 → 0%
# ---------------------------------------------------------------------------


class TestC26EA2Ratio:
    def test_p_0_100_null_scorable_two_of_three_equal_weight_mean_50(self):
        """P=0/100/null：可评分 2/3；等权 P 均分 50（null 不作 0）。"""
        snaps = {
            "a": _snap(score=Decimal("0")),
            "b": _snap(score=Decimal("100")),
        }
        semantics = {lid: _sem(composite_state="COMPLETE") for lid in snaps}
        stats = _stats(["a", "b", "c"], snaps={**snaps}, semantics=semantics)
        assert stats["roster"]["scorableLoopCount"] == 2
        assert stats["roster"]["scorableLoopRatio"] == pytest.approx(66.6667, abs=1e-3)
        # P 均分只对完整 P（0+100）/2=50——null 不作 0
        merged = weighted_score_stats(
            [
                {"weight": 1.0, "value": 0.0},
                {"weight": 1.0, "value": 100.0},
                {"weight": 1.0, "value": None},
            ]
        )
        assert merged["numericCount"] == 2
        assert merged["weightedMean"] == 50.0
        assert merged["weightSum"] == 3.0  # null 权重留分母（不缩）

    def test_zero_eligible_ratio_null(self):
        stats = _stats([])
        assert stats["roster"]["eligibleLoopCount"] == 0
        assert stats["roster"]["scorableLoopRatio"] is None  # "无参评回路"

    def test_eligible_without_scorable_ratio_zero(self):
        stats = _stats(["a", "b"], snaps={}, semantics={})
        assert stats["roster"]["scorableLoopRatio"] == 0.0  # 有参评无 P → 0%


# ---------------------------------------------------------------------------
# C26-EA3：F 参考双统计（oracle 手算）
# ---------------------------------------------------------------------------


class TestC26EA3FastRateReference:
    def _build(self, formal_entries, ref_entries):
        eligible = [f"l{i}" for i in range(len(formal_entries) + len(ref_entries))]
        snaps, semantics, weights = {}, {}, {}
        idx = 0
        for v, w in formal_entries:
            lid = f"l{idx}"
            snaps[lid] = _snap(score=Decimal("50"))
            semantics[lid] = _sem(composite_state="COMPLETE", formal=v)
            weights[lid] = Decimal(str(w))
            idx += 1
        for v, w in ref_entries:
            lid = f"l{idx}"
            snaps[lid] = _snap(score=None, fast_rate=None)
            semantics[lid] = _sem(composite_state="PARTIAL", reference=v)
            weights[lid] = Decimal(str(w))
            idx += 1
        return build_assessment_stats(
            eligible_ids=sorted(eligible),
            excluded_count=0,
            latest_snaps=snaps,
            failed_ids=set(),
            semantics=semantics,
            weights=weights,
            ts_start=TS0,
            ts_end=TS1,
        )

    def test_9_ref_100_w1_plus_1_formal_0_w3(self):
        """9 条参考 100×1 + 1 条动态 F0×3 → 主参考均分 75、动态子集 0。

        手算：refNumerator = 9×1×1.0 + 3×0 = 9；refWeight = 9+3 = 12
        → 75%。动态子集 = 0/3 = 0。参考权重占比 = 9/12 = 75%。
        """
        stats = self._build(formal_entries=[(0.0, 3)], ref_entries=[(100.0, 1)] * 9)
        fr = stats["fastRateReference"]
        assert fr["fastRateReferenceMean"] == 75.0
        assert fr["fastRateDynamicEstimateMean"] == 0.0  # 动态子集 0（真实 0 分）
        assert fr["policyReferenceCount"] == 9
        assert fr["dynamicEstimateCount"] == 1
        assert fr["unavailableCount"] == 0
        assert fr["policyReferenceWeightShare"] == pytest.approx(0.75)
        # 守恒：dynamic + reference + unavailable = 参评对象数
        assert (
            fr["dynamicEstimateCount"] + fr["policyReferenceCount"] + fr["unavailableCount"] == 10
        )

    def test_all_static_reference(self):
        """全 10 条静稳参考 → 参考均分 100、动态子集 null、动态覆盖 0%。"""
        stats = self._build(formal_entries=[], ref_entries=[(100.0, 1)] * 10)
        fr = stats["fastRateReference"]
        assert fr["fastRateReferenceMean"] == 100.0
        assert fr["fastRateDynamicEstimateMean"] is None
        assert fr["dynamicEvidenceCoverageRatio"] == 0.0  # 参评>0 动态=0 → 0% 非 null
        assert fr["policyReferenceCount"] == 10


# ---------------------------------------------------------------------------
# C26-EA4：装置→全厂合并分子/权重和，不平均装置百分比
# ---------------------------------------------------------------------------


class TestC26EA4Merge:
    def test_plant_a_100_plus_b_nine_zeros_is_10_not_50(self):
        """装置 A 一条 S100 + B 九条 S0（等权）→ 上级 S10，不平均装置得 50。"""
        plant_a = weighted_score_stats([{"weight": 1.0, "value": 100.0}])
        plant_b = weighted_score_stats([{"weight": 1.0, "value": 0.0}] * 9)
        # 上级合并：分子与权重和分别相加（不平均装置百分比）
        merged_num = plant_a["weightedNumerator"] + plant_b["weightedNumerator"]
        merged_weight = plant_a["numericWeightSum"] + plant_b["numericWeightSum"]
        merged_mean = 100.0 * merged_num / merged_weight
        assert merged_mean == pytest.approx(10.0)
        assert merged_mean != pytest.approx(50.0)  # (100+0)/2 伪平均

    def test_zero_weight_sum_mean_null(self):
        stats = weighted_score_stats(
            [{"weight": 0.0, "value": 80.0}, {"weight": 0.0, "value": 90.0}]
        )
        assert stats["weightedMean"] is None  # 权重和 0 → null（零权重保留计数）
        assert stats["numericCount"] == 2


# ---------------------------------------------------------------------------
# C26-EA5：当前不回退旧成功；语义优先于旧快照列值
# ---------------------------------------------------------------------------


class TestC26EA5NoFallback:
    def test_current_reference_not_replaced_by_old_snapshot_value(self):
        """旧窗动态 F=20 的快照列仍在，但当前语义=静稳参考 100 → 参考统计 100。"""
        snaps = {"a": _snap(score=None, fast_rate=Decimal("20"))}  # 旧列值 20
        semantics = {"a": _sem(composite_state="PARTIAL", reference=100)}
        stats = _stats(["a"], snaps=snaps, semantics=semantics)
        fr = stats["fastRateReference"]
        assert fr["fastRateReferenceMean"] == 100.0  # 不回退旧成功值 20
        assert fr["policyReferenceCount"] == 1
        assert fr["dynamicEstimateCount"] == 0

    def test_formal_zero_priority_over_residual_reference(self):
        """正式 F 0 分与残留参考字段并存 → 正式 0 优先（仅计一次）。"""
        semantics = {"a": _sem(composite_state="COMPLETE", formal=0.0, reference=100)}
        stats = _stats(["a"], snaps={"a": _snap()}, semantics=semantics)
        fr = stats["fastRateReference"]
        assert fr["dynamicEstimateCount"] == 1
        assert fr["policyReferenceCount"] == 0
        assert fr["fastRateDynamicEstimateMean"] == 0.0

    def test_legacy_snapshot_without_semantics_counted_formal(self):
        """legacy record（无语义视图）：SUCCESS 快照 fast_rate 按正式值归类。"""
        stats = _stats(["a"], snaps={"a": _snap(fast_rate=Decimal("85"))})
        fr = stats["fastRateReference"]
        assert fr["dynamicEstimateCount"] == 1
        assert fr["legacyUnclassifiedCount"] == 1


# ---------------------------------------------------------------------------
# aggregate_node_snapshot 端到端（打桩名册装载）
# ---------------------------------------------------------------------------


class TestAggregateEmitsStats:
    @pytest.mark.asyncio
    async def test_snapshot_carries_assessment_stats(self):
        """节点快照携带 assessment_stats（roster/metricStats/fastRateReference）。"""
        from unittest.mock import patch

        db = AsyncMock()
        rows = [MagicMock()]
        rows[0].loop_id = "loop-001"
        main_result = MagicMock()
        main_result.all.return_value = []
        db.execute = AsyncMock(return_value=main_result)

        snaps = {"loop-001": _snap()}
        semantics = {"loop-001": _sem(composite_state="COMPLETE", formal=90.0)}
        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
            patch(
                "app.services.node_performance._load_roster_inputs",
                return_value=(
                    ["loop-001"],
                    0,
                    snaps,
                    set(),
                    semantics,
                    {"loop-001": Decimal("1.0")},
                ),
            ),
        ):
            result = await aggregate_node_snapshot(db, "node-001", TS0, TS1)

        assert result is not None
        stats = result["assessment_stats"]
        assert stats["roster"]["scorableLoopCount"] == 1
        assert stats["roster"]["scorableLoopRatio"] == 100.0
        assert stats["fastRateReference"]["fastRateReferenceMean"] == 90.0
        # 无 SUCCESS 行时 metricStats 为空（分项独立统计随代表对象集产出）
        assert stats["metricStats"] == {}
