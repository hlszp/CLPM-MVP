"""R14 稀疏数据 KPI 准入测试（2026-09-06 数据链路整改 S2/B）.

验收场景（主计划 §4 R14 / S0 契约 §7）——走**真实** Pipeline / gate /
采样链路（非只测 helper）：

- 120 个 Good 点、30s 间隔、跨 1 小时（coverage≈3.3%）：
  * missing_rate ≈ 0.967（时间覆盖率缺口显式暴露）
  * 可信度非 A（时间覆盖率折入可信度判定 → E 级 / INCONCLUSIVE 兼容口径）
  * gate_passed=False（期望点数按契约 1s 间隔 = 3600，gap_ratio≈0.967）
  * 快照状态非 SUCCESS、评分不可用
  * settling_time 不产出伪 1s 值（等间隔准入：真实 30s 或跳过）
- 真实 1Hz 均匀序列：正常计算不受影响（可信度 A、gate 通过、settling
  按真实 1s 计算）。
- gate 不通过但综合评分可计算（span 盲区场景）→ 快照强制 INCONCLUSIVE
  + score=None（gate 结论贯穿快照）。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.contracts.data_types import (
    ControlType,
    DataBlock,
    DataLineage,
    LoopPreprocessConfig,
    MetricDataBundle,
    MetricResult,
    QualitySummary,
    RawTimeSeries,
    TagGroup,
)
from app.services.confidence_evaluator import ALGORITHM_VERSION
from app.services.diagnosis_operators.gate import evaluate_gate
from app.services.metric_calculator.settling_time import SettlingTimeCalculator
from app.services.preprocessing.pipeline import PreprocessingPipeline
from app.tasks.kpi_calc import (
    _calculate_loop_kpi,
    _derive_expected_points,
)

_WINDOW_START = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
_WINDOW_END = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)


def _make_config() -> LoopPreprocessConfig:
    """FLOW 回路（契约采样 1s）预处理配置."""
    return LoopPreprocessConfig(
        loop_id="loop-r14",
        control_type=ControlType.FLOW,
        range_min=0.0,
        range_max=100.0,
        op_range_min=0.0,
        op_range_max=100.0,
    )


def _make_raw(n: int, step_s: float, varying: bool = True) -> RawTimeSeries:
    """构造原始时序：n 点、step_s 间隔、全部 Good、值有变化（避免 FROZEN）."""
    base = _WINDOW_START.replace(tzinfo=None)
    timestamps = [base + timedelta(seconds=i * step_s) for i in range(n)]
    pv = [50.0 + (2.0 * math.sin(i / 7.0) + i * 0.001 if varying else 0.0) for i in range(n)]
    return RawTimeSeries(
        timestamps=timestamps,
        signals={
            "pv": pv,
            "sp": [50.0] * n,
            "op": [55.0 + (math.sin(i / 11.0) if varying else 0.0) for i in range(n)],
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},  # 全部 Good
    )


def _make_loop() -> MagicMock:
    loop = MagicMock()
    loop.id = "00000000-0000-0000-0000-000000000201"
    loop.tag_name = "101-FC-1023"
    loop.loop_type = "FLOW"
    loop.is_active = True
    loop.status = "READY"
    loop.unit_id = "00000000-0000-0000-0000-000000000111"
    loop.ideal_settling_time = None
    return loop


def _make_db() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.first.return_value = ("snap-id-1",)
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    return db


def _bundle_for(metric_code: str, block: DataBlock) -> MetricDataBundle:
    """从真实 DataBlock 组装 MetricDataBundle（mask=None → 全索引）."""
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression="",
        masked_indices=list(range(block.point_count)),
        lineage=DataLineage(
            sampling_freq=block.sampling_freq,
            aggregation_policy="LAST",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            tag_group=block.tag_group,
            data_block_ids=[block.data_block_id],
            valid_rate=block.loop_valid_rate,
            data_policy_version=block.preprocess_version,
            algorithm_version=ALGORITHM_VERSION,
        ),
    )


def _core_bundles(block: DataBlock) -> list[MetricDataBundle]:
    """覆盖三层计算所需的核心 DB 指标代码（共享同一真实 DataBlock）."""
    codes = [
        "accuracy_rate",
        "effective_auto_rate",
        "good_value_rate",
        "oscillation_rate",
        "saturation_rate",
        "stiction_index",
        "output_trip_index",
        "auto_mode_rate",
        "settling_time",
        "instrument_fault_rate",
        "pv_mean",
        "pv_std",
        "sp_mean",
        "sp_std",
        "op_mean",
        "op_std",
        "error_mean",
        "error_std",
        "valve_linearity",
        "valve_operating_range",
        "setpoint_crossing_count",
        "oscillation_amplitude",
        "steady_rate",
        "fast_rate",
    ]
    return [_bundle_for(code, block) for code in codes]


# ---------------------------------------------------------------------------
# 验收场景 1：120 点 / 30s 间隔 / 跨 1 小时（稀疏 COV 型数据）
# ---------------------------------------------------------------------------


class TestSparseAdmission:
    """稀疏数据不得获得 A 可信度/有效评分，ARMA 不得按伪 1s 计算."""

    @pytest.fixture
    def sparse_block(self) -> DataBlock:
        pipeline = PreprocessingPipeline(_make_config())
        raw = _make_raw(n=120, step_s=30.0)
        return pipeline.process(
            raw,
            TagGroup.BASE,
            (_WINDOW_START.replace(tzinfo=None), _WINDOW_END.replace(tzinfo=None)),
        )

    def test_sampling_freq_reflects_actual_interval(self, sparse_block: DataBlock) -> None:
        """DataBlock.sampling_freq = 实际中位间隔（30s），非名义 1s 标签."""
        assert sparse_block.sampling_freq == "30s"

    def test_missing_rate_exposes_time_gap(self, sparse_block: DataBlock) -> None:
        """missing_rate ≈ 0.967（3600s 窗口只有 120 点，按契约 1s 期望）."""
        assert sparse_block.quality_summary.missing_rate == pytest.approx(0.9664, abs=1e-3)

    def test_confidence_not_a_sparse_data(self, sparse_block: DataBlock) -> None:
        """120 个 Good 点跨 1 小时不得 A 可信度：覆盖率 3.3% 折入后为 E."""
        assert sparse_block.loop_confidence_level != "A"
        assert sparse_block.loop_confidence_level == "E"
        # 有效可信度 = valid_rate(1.0) × coverage(120/3601)
        assert sparse_block.loop_valid_rate == pytest.approx(120 / 3601, rel=1e-3)

    def test_gate_fails_with_contract_expected_points(self, sparse_block: DataBlock) -> None:
        """gate：期望点数按契约 1s（3600），gap_ratio≈0.967 → 不通过."""
        bundles = _core_bundles(sparse_block)
        expected = _derive_expected_points(
            bundles,
            _WINDOW_START.replace(tzinfo=None),
            _WINDOW_END.replace(tzinfo=None),
            expected_interval_s=1,
        )
        assert expected == 3600
        gate = evaluate_gate(
            point_count=sparse_block.point_count,
            expected_points=expected,
            valid_rate=sparse_block.loop_valid_rate,
            confidence_level=sparse_block.loop_confidence_level,
        )
        assert gate.passed is False
        assert gate.gap_ratio == pytest.approx(1 - 120 / 3600, abs=1e-3)
        assert gate.reason  # 原因可查

    def test_settling_time_no_pseudo_one_second_interval(self, sparse_block: DataBlock) -> None:
        """settling_time：30s 均匀序列按真实 30s 计算（不得按伪 1s）."""
        result = SettlingTimeCalculator().calculate(_bundle_for("settling_time", sparse_block))
        # 等间隔 30s 且声明一致 → 要么计算（sample_interval=30.0，真实尺度），
        # 要么因辨识语义（never_settles/identification_failed）INCONCLUSIVE；
        # 绝不允许出现按 1s 计算的伪值。
        if result.value is not None:
            assert result.details.get("sample_interval") == 30.0
        else:
            assert result.details.get("sample_interval") != 1.0 or (
                result.details.get("reason") in ("never_settles", "identification_failed")
            )

    @pytest.mark.asyncio
    async def test_snapshot_not_success_with_sparse_data(self, sparse_block: DataBlock) -> None:
        """全链路（真实 Pipeline → 真实三层计算 → 快照）：稀疏数据快照非 SUCCESS."""
        loop = _make_loop()
        db = _make_db()
        planner = AsyncMock()
        planner.request_bundles = AsyncMock(return_value=_core_bundles(sparse_block))

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=_WINDOW_START,
            ts_end=_WINDOW_END,
            data_planner=planner,
        )
        assert result["status"] != "SUCCESS"
        assert result["status"] == "INCONCLUSIVE"
        assert result["score"] is None


# ---------------------------------------------------------------------------
# 验收场景 2：真实 1Hz 均匀序列不受影响
# ---------------------------------------------------------------------------


class TestDenseUniformUnaffected:
    def test_dense_1hz_keeps_confidence_and_gate(self) -> None:
        """3600 点 / 1s / 满 1 小时：coverage=1.0 → 可信度 A，gate 通过."""
        pipeline = PreprocessingPipeline(_make_config())
        raw = _make_raw(n=3600, step_s=1.0)
        block = pipeline.process(
            raw,
            TagGroup.BASE,
            (_WINDOW_START.replace(tzinfo=None), _WINDOW_END.replace(tzinfo=None)),
        )

        assert block.sampling_freq == "1s"
        assert block.quality_summary.missing_rate == 0.0
        assert block.loop_confidence_level == "A"
        # 个别点可能被异常值检测标记（合成信号的局部跳变），有效可信度仍 ≈1
        assert block.loop_valid_rate == pytest.approx(1.0, abs=5 / 3600)

        bundles = _core_bundles(block)
        expected = _derive_expected_points(
            bundles,
            _WINDOW_START.replace(tzinfo=None),
            _WINDOW_END.replace(tzinfo=None),
            expected_interval_s=1,
        )
        gate = evaluate_gate(
            point_count=block.point_count,
            expected_points=expected,
            valid_rate=block.loop_valid_rate,
            confidence_level=block.loop_confidence_level,
        )
        assert gate.passed is True
        assert gate.gap_ratio == pytest.approx(0.0, abs=1e-6)

    def test_settling_time_computes_with_real_interval(self) -> None:
        """1Hz 均匀序列：settling_time 正常计算，sample_interval=1.0（真实尺度）."""
        pipeline = PreprocessingPipeline(_make_config())
        raw = _make_raw(n=3600, step_s=1.0)
        block = pipeline.process(raw, TagGroup.BASE)
        result = SettlingTimeCalculator().calculate(_bundle_for("settling_time", block))
        assert result.details.get("sample_interval") == 1.0
        # 稳定收敛序列应产出稳态时间（非跳过）
        assert result.value is not None
        assert result.value > 0


# ---------------------------------------------------------------------------
# gate 结论贯穿快照：gate 不通过 + 综合评分可计算 → 强制 INCONCLUSIVE
# ---------------------------------------------------------------------------


def _make_gate_bundle(point_count: int) -> MetricDataBundle:
    """mock 三层计算用的最小 bundle（point_count 驱动 gate 输入）."""
    ts = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
    data_block = DataBlock(
        data_block_id=f"db_loop-r14_BASE_{point_count}",
        loop_id="loop-r14",
        tag_group=TagGroup.BASE.value,
        sampling_freq="1s",
        timestamps=[ts],
        signals={"pv": [50.0], "sp": [50.0]},
        validity={"pv_valid": [True]},
        quality_summary=QualitySummary(total_count=1, valid_count=1, valid_rate=1.0),
        point_count=point_count,
        loop_confidence_level="A",
        loop_valid_rate=1.0,
    )
    return _bundle_for("accuracy_rate", data_block)


def _full_metric_results() -> dict[str, MetricResult]:
    """构造全量可计算指标（composite 可算出 76.0 / A 级）."""
    lineage = DataLineage(
        sampling_freq="1s",
        aggregation_policy="LAST",
        quality_policy="KEEP_ALL_WITH_VALIDITY",
        tag_group=TagGroup.BASE.value,
        data_block_ids=["db_test_1s"],
        valid_rate=1.0,
        algorithm_version=ALGORITHM_VERSION,
    )

    def _r(code: str, value: float | None) -> MetricResult:
        return MetricResult(
            metric_code=code, value=value, confidence_level="A", lineage=lineage, details={}
        )

    return {
        "accuracy_rate": _r("accuracy_rate", 80.0),
        "fast_rate": _r("fast_rate", 70.0),
        "stability_rate": _r("stability_rate", 75.0),
        "effective_auto_rate": _r("effective_auto_rate", 90.0),
        "good_value_rate": _r("good_value_rate", 100.0),
        "auto_mode_rate": _r("auto_mode_rate", 95.0),
        "settling_time": _r("settling_time", 120.0),
        "ideal_settling_time": _r("ideal_settling_time", 300.0),
        "oscillation_rate": _r("oscillation_rate", 10.0),
    }


def _extract_upsert_set_values(upsert_stmt: object) -> dict:
    return dict(upsert_stmt._post_values_clause.update_values_to_set)


class TestGateConclusionPropagatesToSnapshot:
    """缺口 gate 不通过时最终快照不得 SUCCESS + 高分（R14-3）."""

    @pytest.mark.asyncio
    async def test_gate_fail_forces_inconclusive_even_with_score(self) -> None:
        """综合评分可计算（76/A）但 gate 不通过（点数缺口）→ INCONCLUSIVE + score=None."""
        loop = _make_loop()
        db = _make_db()
        planner = AsyncMock()
        # point_count=1000 << 期望 3600 → gap_ratio≈0.72 > 30% 门槛
        planner.request_bundles = AsyncMock(return_value=[_make_gate_bundle(1000)])

        composite = MetricResult(
            metric_code="composite_score",
            value=76.0,
            confidence_level="A",
            lineage=DataLineage(),
        )
        with patch(
            "app.tasks.kpi_calc._compute_kpis_three_layer",
            return_value=(_full_metric_results(), composite),
        ):
            result = await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs={},
                ts_start=_WINDOW_START,
                ts_end=_WINDOW_END,
                data_planner=planner,
            )

        assert result["status"] == "INCONCLUSIVE"
        assert result["score"] is None

        # UPSERT 写入值：可信度降为 E，fitness_detail 携带 gate 失败原因
        stmt = db.execute.call_args_list[-2].args[0]  # 倒数第 2 次 = 主快照 UPSERT
        set_values = _extract_upsert_set_values(stmt)
        assert set_values.get("confidence_level") == "E"
        fitness_detail = set_values.get("fitness_detail") or {}
        assert "断点比例" in (fitness_detail.get("gate_failed_reason") or "")
        assert fitness_detail.get("gate", {}).get("passed") is False

    @pytest.mark.asyncio
    async def test_gate_pass_keeps_success(self) -> None:
        """对照组：点数充足（3600/3600）gate 通过 → 保持 SUCCESS + 评分."""
        loop = _make_loop()
        db = _make_db()
        planner = AsyncMock()
        planner.request_bundles = AsyncMock(return_value=[_make_gate_bundle(3600)])

        composite = MetricResult(
            metric_code="composite_score",
            value=76.0,
            confidence_level="A",
            lineage=DataLineage(),
        )
        with patch(
            "app.tasks.kpi_calc._compute_kpis_three_layer",
            return_value=(_full_metric_results(), composite),
        ):
            result = await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs={},
                ts_start=_WINDOW_START,
                ts_end=_WINDOW_END,
                data_planner=planner,
            )

        assert result["status"] == "SUCCESS"
        assert result["score"] == 76.0


# ---------------------------------------------------------------------------
# 期望点数口径：契约间隔 vs 实际标签
# ---------------------------------------------------------------------------


class TestExpectedPointsContractInterval:
    def test_contract_interval_overrides_actual_label(self) -> None:
        """BASE 标签 "30s"（实际）但契约 1s → 期望 3600（缺口不被洗白）."""
        block = DataBlock(
            data_block_id="db_x",
            loop_id="x",
            tag_group=TagGroup.BASE.value,
            sampling_freq="30s",
            timestamps=[],
            signals={},
            validity={},
            point_count=120,
        )
        bundles = [_bundle_for("accuracy_rate", block)]
        s, e = _WINDOW_START.replace(tzinfo=None), _WINDOW_END.replace(tzinfo=None)
        # 契约口径（新行为）：按 threshold 间隔
        assert _derive_expected_points(bundles, s, e, expected_interval_s=1) == 3600
        # 兼容口径（未传参）：回退标签解析 → 120（ documenting 为什么必须传契约间隔）
        assert _derive_expected_points(bundles, s, e) == 120

    def test_pc_contract_2s(self) -> None:
        """PC 回路契约 2s → 1 小时期望 1800 点."""
        block = DataBlock(
            data_block_id="db_y",
            loop_id="y",
            tag_group=TagGroup.BASE.value,
            sampling_freq="2s",
            timestamps=[],
            signals={},
            validity={},
            point_count=100,
        )
        bundles = [_bundle_for("accuracy_rate", block)]
        s, e = _WINDOW_START.replace(tzinfo=None), _WINDOW_END.replace(tzinfo=None)
        assert _derive_expected_points(bundles, s, e, expected_interval_s=2) == 1800


# ---------------------------------------------------------------------------
# settling_time 等间隔准入（R14-4）
# ---------------------------------------------------------------------------


class TestSettlingTimeUniformSamplingGate:
    """非等间隔序列跳过计算并记录原因，不得按声明间隔计算."""

    @staticmethod
    def _bundle(timestamps, sampling_freq: str) -> MetricDataBundle:
        n = len(timestamps)
        block = DataBlock(
            data_block_id="db_st",
            loop_id="loop-st",
            tag_group=TagGroup.BASE.value,
            sampling_freq=sampling_freq,
            timestamps=list(timestamps),
            signals={
                "pv": [50.0 + math.sin(i / 5.0) * 3.0 for i in range(n)],
                "sp": [50.0] * n,
            },
            validity={"pv_valid": [True] * n, "sp_valid": [True] * n},
            point_count=n,
            loop_confidence_level="A",
            loop_valid_rate=1.0,
        )
        return _bundle_for("settling_time", block)

    def test_declared_interval_mismatch_skips(self):
        """声明 1s 但实际 30s（旧缓存块典型形态）→ 跳过 + sampling_interval_mismatch."""
        base = datetime(2026, 6, 22, 8, 0, 0)
        ts = [base + timedelta(seconds=30 * i) for i in range(150)]
        result = SettlingTimeCalculator().calculate(self._bundle(ts, "1s"))
        assert result.value is None
        assert result.details.get("reason") == "sampling_interval_mismatch"
        assert result.details.get("median_interval_s") == 30.0

    def test_non_uniform_intervals_skip(self):
        """间隔抖动（COV 事件流型不规则间隔）→ 跳过 + non_uniform_sampling."""
        base = datetime(2026, 6, 22, 8, 0, 0)
        offsets = [0, 3, 4, 10, 11, 12, 25, 26, 40, 41, 55, 56]
        offsets = [o * 8 for o in offsets][:12]
        # 扩展到 >MIN_POINTS：以不规则块重复但整体不均匀
        ts = [base + timedelta(seconds=o) for o in offsets]
        while len(ts) < 150:
            last = ts[-1]
            gap = 5 if len(ts) % 3 else 40  # 5s/40s 交替 → 1/3 间隔偏离中位 >20%
            ts.append(last + timedelta(seconds=gap))
        result = SettlingTimeCalculator().calculate(self._bundle(ts, "5s"))
        assert result.value is None
        assert result.details.get("reason") == "non_uniform_sampling"

    def test_uniform_30s_passes_gate_and_uses_real_interval(self):
        """均匀 30s + 标签 30s → 通过准入，sample_interval=30.0（真实尺度）."""
        base = datetime(2026, 6, 22, 8, 0, 0)
        ts = [base + timedelta(seconds=30 * i) for i in range(150)]
        result = SettlingTimeCalculator().calculate(self._bundle(ts, "30s"))
        assert result.details.get("sample_interval") == 30.0
        assert result.value is not None or result.details.get("reason") in (
            "never_settles",
            "identification_failed",
        )

    def test_minor_jitter_within_tolerance_allowed(self):
        """轻微抖动（±10% 内）不触发跳过——真实 1Hz 数据允许秒级抖动."""
        base = datetime(2026, 6, 22, 8, 0, 0)
        ts = []
        cur = base
        for i in range(200):
            jitter = 0.1 if i % 2 else -0.1  # ±0.1s << ±20% 容差
            cur = cur + timedelta(seconds=1 + jitter)
            ts.append(cur)
        result = SettlingTimeCalculator().calculate(self._bundle(ts, "1s"))
        assert result.details.get("sample_interval") == 1.0
        assert result.value is not None
