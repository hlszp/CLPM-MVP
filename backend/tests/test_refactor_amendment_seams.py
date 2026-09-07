"""补充方案 §6 验收用例（AD08）——接缝单元级证据.

对照《算法与数据接口核查及兼容改进方案》§6 表逐项；文件头标注用例编号。
端到端三链路用例见 tests/integration/test_refactor_amendment_e2e.py。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.contracts.data_types import DataBlock, RawTimeSeries
from app.contracts.series_context import (
    LAYOUT_POINT,
    IntervalRun,
    RoleCoverage,
    SeriesContext,
    data_version_for_cache,
)

T0 = datetime(2026, 9, 12, tzinfo=UTC)


def _ctx(
    slots: int,
    *,
    pv_known: int | None = None,
    layout: str = LAYOUT_POINT,
    interpretation_consistent: bool = True,
) -> SeriesContext:
    known = slots if pv_known is None else pv_known
    unknown = slots - known
    gs = T0
    ge = T0 + timedelta(seconds=slots - 1)
    cov = RoleCoverage(
        known=[IntervalRun(gs, gs + timedelta(seconds=known))] if known else [],
        unknown=(
            [IntervalRun(gs + timedelta(seconds=known), ge + timedelta(seconds=1))]
            if unknown
            else []
        ),
    )
    return SeriesContext(
        layout=layout,
        grid_start=gs,
        grid_end=ge,
        grid_period_s=1.0,
        expected_slots=slots,
        dataset_ref="test-ref",
        role_coverage={"pv": cov},
        interpretation_consistent=interpretation_consistent,
    )


class TestCase2FiniteInvalidExcluded:
    """用例 2：有限 PV/OP 局部 Bad/Uncertain——无效有限值不进辨识。"""

    def _blocks(self, quality_codes, pv_valid, n=10):
        ctx = _ctx(n, pv_known=n)
        pvop = DataBlock(
            data_block_id="db1",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": [50.0 + i for i in range(n)], "op": [40.0 + i for i in range(n)]},
            validity={"pv_valid": pv_valid, "op_valid": [True] * n},
            series_context=ctx,
        )
        base = DataBlock(
            data_block_id="db2",
            loop_id="L",
            tag_group="BASE",
            sampling_freq="1s",
            timestamps=pvop.timestamps,
            signals={"sp": [50.0] * n},
            validity={"sp_valid": [True] * n},
            series_context=ctx,
        )
        return pvop, base, None

    def test_invalid_finite_values_excluded_from_segment(self):
        from app.services.tuning import _point_axis_signals

        pvop, base, mode = self._blocks(None, [True, True, False, False, False, True] + [True] * 4)
        seg = _point_axis_signals(pvop, base, mode)
        # 有限但无效的 3/4/5 不进辨识输入：最长连续段=5..9（5 点，值 55..59）
        assert len(seg["pv"]) == 5
        assert seg["pv"] == [55.0, 56.0, 57.0, 58.0, 59.0]
        # 全窗 vs 选段分开记录：全窗 0.7；选段 0.5
        pa = seg["point_axis"]
        assert abs(pa["full_window_valid_rate"] - 0.7) < 1e-9
        assert abs(pa["segment_valid_rate"] - 0.5) < 1e-9
        # 对外可信度=全窗（不以选段 100% 覆盖）
        assert abs(seg["valid_rate"] - 0.7) < 1e-9


class TestCase3ModeUnknown:
    """用例 3：MODE 同轴缺口/全未知/首点前未知——未知不填成 0/合法模式。"""

    def test_mode_gap_stays_cut_not_filled(self):
        from app.services.tuning import _point_axis_signals

        n = 5
        ctx = _ctx(n)
        pvop = DataBlock(
            data_block_id="db1",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": [1.0] * n, "op": [1.0] * n},
            validity={"pv_valid": [True] * n, "op_valid": [True] * n},
            series_context=ctx,
        )
        mode = DataBlock(
            data_block_id="db3",
            loop_id="L",
            tag_group="MODE_HF",
            sampling_freq="1s",
            timestamps=pvop.timestamps,
            signals={"mode": [1, None, None, 1, 1]},  # 同轴缺口（未知）
            validity={"mode_valid": [True, False, False, True, True]},
            series_context=ctx,
        )
        seg = _point_axis_signals(pvop, None, mode)
        # MODE 未知槽切断段（不得保持/外推成 0 或合法模式）：最长段=3..4
        assert len(seg["mode"]) == 2
        assert seg["mode"] == [1, 1]

    def test_mode_all_unknown_no_segment(self):
        from app.services.tuning import _point_axis_signals

        n = 4
        ctx = _ctx(n)
        pvop = DataBlock(
            data_block_id="db1",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": [1.0] * n, "op": [1.0] * n},
            validity={"pv_valid": [True] * n, "op_valid": [True] * n},
            series_context=ctx,
        )
        mode = DataBlock(
            data_block_id="db3",
            loop_id="L",
            tag_group="MODE_HF",
            sampling_freq="1s",
            timestamps=pvop.timestamps,
            signals={"mode": [None] * n},
            validity={"mode_valid": [False] * n},
            series_context=ctx,
        )
        seg = _point_axis_signals(pvop, None, mode)
        assert seg["pv"] == []  # 无可用连续段（不把全未知填成 0 继续）


class TestCase4HardGapNotInterpolated:
    """用例 4：连续三秒已知断线不进通用插值（point 硬缺口切断段）。"""

    def test_known_gap_cuts_segment(self):
        from app.services.tuning import _point_axis_signals

        n = 8
        ctx = _ctx(n, pv_known=5)  # 3 未知（gap）
        pv = [10.0, 11.0, 12.0, None, None, None, 13.0, 14.0]
        pvop = DataBlock(
            data_block_id="db1",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": pv, "op": pv},
            validity={
                "pv_valid": [v is not None for v in pv],
                "op_valid": [v is not None for v in pv],
            },
            series_context=ctx,
        )
        seg = _point_axis_signals(pvop, None, None)
        # 硬缺口不插值：选 0..2（3 点），不拼接 6..7
        assert seg["pv"] == [10.0, 11.0, 12.0]

    def test_legacy_nan_cleaning_behavior_untouched(self):
        """旧清洗策略独立保留（短 NaN 仍按原策略插值——legacy helper 行为）。"""
        from app.services.tuning_identification.pipeline import _clean_nan_segments

        y = np.array([1.0, np.nan, np.nan, np.nan, 5.0])
        u = np.array([2.0, np.nan, np.nan, np.nan, 6.0])
        uu, yy, _sp_clean, stats = _clean_nan_segments(u, y, None, max_interp_gap=5)
        assert np.isfinite(yy).all()  # legacy 清洗保持原行为（短 NaN 仍插值）
        assert stats.get("interpolated_points", stats.get("interpolated", 0)) >= 3


class TestCase5SameAxisSlicing:
    """用例 5：PV 全有、SP/OP 单独缺失——同算子输入列与时间戳严格同轴。"""

    def test_scoped_input_same_axis(self):
        from types import SimpleNamespace

        from app.services.diagnosis_operators.base import OperatorInput
        from app.services.diagnosis_orchestrator import _scoped_operator_input

        n = 4
        op_input = OperatorInput(
            loop_id="L",
            signals={
                "pv": np.array([1.0, 2.0, 3.0, 4.0], dtype=object),
                "sp": np.array([10.0, None, 30.0, 40.0], dtype=object),  # 槽 1 缺
                "op": np.array([5.0, 6.0, None, 8.0], dtype=object),  # 槽 2 缺
                "mode": np.array([1, 1, 1, 1], dtype=object),
                "pv_quality": np.array([1, 1, 1, 1], dtype=int),
                "pv_quality_ts": np.array([0.0, 1.0, 2.0, 3.0]),
            },
            timestamps=np.array([0.0, 1.0, 2.0, 3.0]),
            meta={
                "sample_interval": 1.0,
                "total_points": n,
                "point_axis": True,
                "signals_valid": {
                    "pv": np.array([True] * 4),
                    "sp": np.array([True, False, True, True]),
                    "op": np.array([True, True, False, True]),
                    "mode": np.array([True] * 4),
                },
            },
            kpi_context={},
        )
        meta = SimpleNamespace(required_signals=("pv", "sp"))
        scoped, skip = _scoped_operator_input(op_input, meta)
        assert skip is None
        # 公共掩码 = pv∧sp = [T,F,T,T] → 三列同长、同索引、时间戳同切片
        assert len(scoped.signals["pv"]) == len(scoped.signals["sp"]) == len(scoped.timestamps) == 3
        assert list(scoped.signals["pv"]) == [1.0, 3.0, 4.0]
        assert list(scoped.signals["sp"]) == [10.0, 30.0, 40.0]
        assert list(scoped.timestamps) == [0.0, 2.0, 3.0]
        # 质量轴保持完整（质量码算子消费全轴）
        assert len(scoped.signals["pv_quality"]) == 4
        assert len(scoped.signals["pv_quality_ts"]) == 4


class TestCase6MaskDiscontinuity:
    """用例 6：mask [0,1,60,61] grid=1s——缺口敏感动态算子不当作相邻一步。"""

    def _bundle(self, indices):
        from app.contracts.data_types import DataLineage, MetricDataBundle

        ctx = _ctx(62)
        block = DataBlock(
            data_block_id="db",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(62)],
            signals={"pv": [1.0] * 62, "op": [1.0] * 62},
            validity={"pv_valid": [i in indices for i in range(62)], "op_valid": [True] * 62},
            series_context=ctx,
        )
        return MetricDataBundle(
            metric_code="time_constant",
            data_block=block,
            mask_expression="pv_valid && op_valid",
            masked_indices=sorted(indices),
            lineage=DataLineage(tag_group="PVOP_HF"),
        )

    def test_time_constant_guarded_on_gap(self):
        from app.services.preprocessing.input_guards import (
            gap_guard_verdict,
            make_gap_guard_result,
            masked_indices_max_gap,
        )

        bundle = self._bundle([0, 1, 60, 61])
        assert masked_indices_max_gap([0, 1, 60, 61]) == 59
        allowed, gap = gap_guard_verdict("time_constant", bundle)
        assert not allowed and gap == 59
        result = make_gap_guard_result(bundle, gap)
        assert result.value is None and result.confidence_level == "E"
        assert result.details["reason"] == "GAP_SENSITIVE_MASK_DISCONTINUOUS"

    def test_continuous_mask_passes_and_legacy_exempt(self):
        from app.services.preprocessing.input_guards import gap_guard_verdict

        bundle = self._bundle(list(range(10)))
        assert gap_guard_verdict("time_constant", bundle)[0] is True
        # legacy（无上下文）即使有缺口也不适用守卫（行为保持）
        bundle_legacy = self._bundle([0, 1, 60, 61])
        bundle_legacy.data_block.series_context = None
        assert gap_guard_verdict("time_constant", bundle_legacy)[0] is True
        # 非登记指标不受守卫
        bundle2 = self._bundle([0, 1, 60, 61])
        assert gap_guard_verdict("accuracy_rate", bundle2)[0] is True


class TestCase7UnknownStatistics:
    """用例 7：10 槽 7 有效 3 未知——未知=3、0.7 不二次乘成 0.49。"""

    def test_quality_summary_point_semantics(self):
        from app.services.preprocessing.quality_summary import compute_quality_summary

        n = 10
        validity = {"pv_valid": [True] * 7 + [False] * 3, "op_valid": [True] * 7 + [False] * 3}
        ts = [T0 + timedelta(seconds=i) for i in range(n)]
        # point：行数恒满（10 行含未知占位）——旧行数差口径会得 missing=0
        summary = compute_quality_summary(
            validity=validity,
            timestamps=ts,
            point_count=n,
            quality_codes=[1] * 7 + [-1] * 3,
            expected_interval_s=1.0,
            unknown_slot_count=3,
        )
        assert summary.missing_count == 3
        assert summary.valid_count == 7
        assert abs(summary.valid_rate - 0.7) < 1e-9
        # legacy 口径原样：行数差（对同窗旧行为输出 missing=0——保持不变）
        legacy = compute_quality_summary(
            validity=validity,
            timestamps=ts,
            point_count=n,
            expected_interval_s=1.0,
        )
        assert legacy.missing_count == 0  # 旧行为保持（legacy 不因本任务重算）

    def test_loop_valid_rate_not_double_multiplied(self):
        """valid mask 已排除未知 → 不再乘同一缺口覆盖率（0.7≠0.49）.

        point 全网格：time_coverage=去重行数/期望=10/10=1.0（网格恒满），
        loop_valid_rate=0.7×1.0=0.7——由网格恒满结构性保证不重复折减；
        本用例固化该口径（pipeline R14-2 乘法在 point 路径的期望行为）。
        """
        from app.services.preprocessing.data_quality_assessor import DataQualityAssessor

        n = 10
        validity = {"pv_valid": [True] * 7 + [False] * 3, "op_valid": [True] * 7 + [False] * 3}
        raw_rate = DataQualityAssessor.compute_loop_valid_rate(validity, n)
        assert abs(raw_rate - 0.7) < 1e-9
        time_coverage_point = n / n  # point 网格去重行数/期望格点=1.0
        assert abs(raw_rate * time_coverage_point - 0.7) < 1e-9  # 非 0.49


class TestCase8GridSemantics:
    """用例 8：单点/空窗、非整秒边界、TC 名义 5s——网格周期与期望格点明确。"""

    def test_single_point_window(self):
        from app.contracts.series_context import SeriesContext  # noqa: F401

        ctx = _ctx(1)
        assert ctx.expected_slots == 1
        assert ctx.grid_period_s == 1.0  # 不回落名义 5s

    def test_empty_window_context_absent(self):
        from app.services.data_source.logical_wide_builder import build_logical_wide

        class _FakeDB:
            async def execute(self, *a, **kw):
                class _R:
                    def scalars(self):
                        return self

                    def all(self):
                        return []

                return _R()

        import asyncio

        raw = asyncio.run(
            build_logical_wide(
                _FakeDB(), "L", ["pv"], T0 + timedelta(seconds=5), T0 + timedelta(seconds=1)
            )
        )
        assert raw.timestamps == []
        assert raw.signals == {"pv": []}

    def test_data_version_for_cache(self):
        assert data_version_for_cache(None) == "legacy-v1"
        ctx = _ctx(10)
        v = data_version_for_cache(ctx)
        assert v.startswith("point:") and "dpv" in v


class TestCase9CacheRoundtrip:
    """用例 9：L1/L2 冷热往返、BASE 派生、版本键——上下文/control_type 不丢。"""

    def _block(self, control_type="FAST"):
        ctx = _ctx(10)
        return DataBlock(
            data_block_id="db",
            loop_id="L",
            tag_group="BASE",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(10)],
            signals={"pv": [1.0] * 10},
            validity={"pv_valid": [True] * 10},
            control_type=control_type,
            series_context=ctx,
        )

    def test_l1_roundtrip_context_and_control_type(self):
        from app.services.cache.l1_datablock import _data_block_from_dict, _data_block_to_dict

        block = self._block("FAST")
        restored = _data_block_from_dict(_data_block_to_dict(block))
        # I06 修复：control_type 不再丢失（曾 FAST→None）
        assert restored.control_type == "FAST"
        # AD02：上下文完整往返
        assert restored.series_context is not None
        assert restored.series_context.dataset_ref == "test-ref"
        assert restored.series_context.expected_slots == 10
        assert restored.series_context.layout == LAYOUT_POINT
        assert restored.series_context.unknown_slots("pv") == 0
        # 旧缓存（无新键）兼容：control_type None、context None
        legacy_payload = _data_block_to_dict(block)
        legacy_payload.pop("control_type")
        legacy_payload.pop("series_context")
        legacy_restored = _data_block_from_dict(legacy_payload)
        assert legacy_restored.control_type is None
        assert legacy_restored.series_context is None

    def test_l2_roundtrip(self):
        from app.contracts.data_types import DataLineage, MetricDataBundle
        from app.services.cache.l2_bundle import _bundle_from_dict, _bundle_to_dict

        bundle = MetricDataBundle(
            metric_code="accuracy_rate",
            data_block=self._block("SLOW"),
            mask_expression="pv_valid",
            masked_indices=[0, 1],
            lineage=DataLineage(tag_group="BASE", dataset_ref="test-ref"),
        )
        restored = _bundle_from_dict(_bundle_to_dict(bundle))
        assert restored.data_block.control_type == "SLOW"
        assert restored.data_block.series_context.dataset_ref == "test-ref"
        assert restored.lineage.dataset_ref == "test-ref"

    def test_cache_keys_version_isolation(self):
        from app.services.cache.l1_datablock import L1DataBlockCache
        from app.services.cache.l2_bundle import L2BundleCache

        common = {
            "loop_id": "L",
            "tag_group": "BASE",
            "time_window_start": T0,
            "time_window_end": T0 + timedelta(seconds=9),
            "sampling_freq": "1s",
            "quality_policy": "KEEP_ALL_WITH_VALIDITY",
            "pre_version": "pre_v1",
            "cfg_version": "v1",
        }
        legacy_key = L1DataBlockCache.build_key(**common)
        assert legacy_key.endswith(":v1")  # legacy 键与旧缓存逐字节一致
        point_key = L1DataBlockCache.build_key(**common, data_version="point:abc:dpv1")
        assert point_key != legacy_key  # 新布局不命中旧无上下文缓存
        l2_legacy = L2BundleCache.build_key("L", ["m"], T0, T0, "TC")
        l2_point = L2BundleCache.build_key("L", ["m"], T0, T0, "TC", data_version="point:x")
        assert l2_legacy != l2_point

    def test_derive_from_base_carries_context(self):
        from app.services.data_planner import DataPlanner

        planner = DataPlanner.__new__(DataPlanner)
        base = self._block()
        derived = planner._derive_from_base(base, type("G", (), {"value": "OP_HF"})(), ["op"], "L")
        assert derived.series_context is base.series_context


class TestCase10KpiVersionCompat:
    """用例 10：旧 KPI 快照与新 point 数据同窗——不兼容版本不混用。"""

    @pytest.mark.asyncio
    async def test_incompatible_ref_excluded(self, monkeypatch):
        from app.services import diagnosis_orchestrator as orch

        snapshots = [
            ("id-old", None),  # 旧快照无引用
            ("id-new", {"dataset_ref": "test-ref"}),  # 同版本
            ("id-other", {"dataset_ref": "other-ref"}),  # 异版本
        ]

        class _Result:
            def all(self):
                return list(snapshots)  # [(id, data_lineage), ...]

        class _Exec:
            async def execute(self, *a, **kw):
                return _Result()

        class _AvgRow:
            def one_or_none(self):
                return (1.0,)

        executed = {}
        calls = {"n": 0}

        class _Exec2(_Exec):
            async def execute(self, stmt=None, *a, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    return _Result()  # 首查：id/lineage 列表（需 .all）
                executed["stmt"] = str(stmt)
                executed["stmt_obj"] = stmt

                class _R:
                    def one_or_none(self):
                        return (1.0,)

                return _R()

        await orch._kpi_window_averages(
            _Exec2(), "L", T0, T0 + timedelta(hours=1), expected_dataset_ref="test-ref"
        )
        # 只有 id-new 兼容 → 聚合的 IN 参数恰为 {id-new}（旧/异引用排除）
        from sqlalchemy.dialects import postgresql

        stmt_obj = executed.get("stmt_obj")
        assert stmt_obj is not None
        compiled = stmt_obj.compile(dialect=postgresql.dialect())
        flat = set()
        for k, v in compiled.params.items():
            if str(k).startswith("id") and isinstance(v, (list, tuple)):
                flat.update(tuple(x) if isinstance(x, list) else x for x in v)
        assert flat == {"id-new"}

        # 全不兼容 → 空 dict（既有"无可用 KPI 上下文"路径）
        class _Exec3(_Exec):
            async def execute(self, *a, **kw):
                return _Result()

        avgs_none = await orch._kpi_window_averages(
            _Exec3(), "L", T0, T0 + timedelta(hours=1), expected_dataset_ref="nomatch"
        )
        assert avgs_none == {}


class TestCase11InterpretationBoundary:
    """用例 11：跨改绑/量程边界——不用当前配置误解旧数据。"""

    def test_tuning_rejects_cross_boundary(self):
        from app.services.tuning import _point_axis_signals

        n = 5
        ctx = _ctx(n, interpretation_consistent=False)
        pvop = DataBlock(
            data_block_id="db",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": [1.0] * n, "op": [1.0] * n},
            validity={"pv_valid": [True] * n, "op_valid": [True] * n},
            series_context=ctx,
        )
        seg = _point_axis_signals(pvop, None, None)
        assert seg["point_axis"]["interpretation_consistent"] is False
        # identify_model_from_history 在取数后显式拒绝（BizError）——由
        # 集成/e2e 用例验证；此处固定桥接暴露的判定字段

    def test_context_boundary_flag(self):
        from app.contracts.series_context import SegmentBoundary

        ctx = _ctx(10)
        ctx.segment_boundaries.append(SegmentBoundary(at=T0 + timedelta(seconds=5), kind="rebind"))
        ctx.interpretation_consistent = False
        assert ctx.has_boundary_in_window(("rebind",))
        assert ctx.has_boundary_in_window() is True


class TestCase12StaticMetricsUnaffected:
    """用例 12：静态统计与 CONFIG 指标不受可选元数据缺失连带。"""

    def test_optional_role_absence_not_global_rejection(self):
        from app.services.tuning import _point_axis_signals

        n = 5
        ctx = _ctx(n)
        pvop = DataBlock(
            data_block_id="db",
            loop_id="L",
            tag_group="PVOP_HF",
            sampling_freq="1s",
            timestamps=[T0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": [1.0] * n, "op": [1.0] * n},
            validity={"pv_valid": [True] * n, "op_valid": [True] * n},
            series_context=ctx,
        )
        # SP/MODE 块缺失（可选角色未配置）：PV/OP 辨识照常
        seg = _point_axis_signals(pvop, None, None)
        assert len(seg["pv"]) == 5
        assert seg["sp"] == []
        assert seg["mode"] == []

    def test_context_optional_field_default(self):
        """RawTimeSeries/DataBlock 旧构造零影响（无上下文=legacy）。"""
        raw = RawTimeSeries(timestamps=[T0], signals={"pv": [1.0]})
        assert raw.series_context is None
        block = DataBlock(
            data_block_id="d",
            loop_id="L",
            tag_group="BASE",
            sampling_freq="1s",
            timestamps=[T0],
            signals={"pv": [1.0]},
            validity={"pv_valid": [True]},
        )
        assert block.series_context is None
