"""Loop configuration & 评分算法 v2 & 节点聚合 & 实时自控率 P0 单元测试。

测试覆盖：
- TEST-01: 投用定义 CRUD（list/replace/get_auto/get_effective）
- TEST-02: 评分算法 v2（4 种回路类型 + R 缺失 + 无权重回退 + infer_score_type）
- TEST-03: 节点聚合 v2（按 level 加权 / level=NULL 回退 1.0）
- TEST-04: 实时自控率读投用定义（有配置/无配置/空回路列表）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.contracts.data_types import ConfidenceLevel, DataLineage, MetricResult
from app.core.exceptions import BizError
from app.services.confidence_evaluator import (
    ALGORITHM_VERSION as CONFIDENCE_ALGORITHM_VERSION,
)
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.services.loop_config import (
    get_auto_mode_values,
    get_effective_mode_values,
    infer_score_type,
    list_mode_mappings,
    replace_mode_mappings,
)
from app.services.node_performance import (
    aggregate_node_snapshot,
    query_realtime_auto_rate,
)

# ===========================================================================
# 辅助函数：构造 mock 对象
# ===========================================================================


def _make_mode_mapping(
    loop_id: str = "loop-001",
    mode_value: int = 1,
    mode_label: str = "AUTO",
    is_auto: bool = True,
    is_effective: bool = True,
) -> MagicMock:
    """构造 LoopModeMapping mock。"""
    m = MagicMock()
    m.id = f"mm-{loop_id}-{mode_value}"
    m.loop_id = loop_id
    m.mode_value = mode_value
    m.mode_label = mode_label
    m.is_auto = is_auto
    m.is_effective = is_effective
    m.created_at = datetime(2026, 6, 22, 8, 0, 0)
    return m


def _make_scalars_mock(items: list) -> MagicMock:
    """构造 execute 返回值，支持 scalars().all()。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_rows_mock(rows: list) -> MagicMock:
    """构造 execute 返回值，支持 .all()。"""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _make_scalar_one_or_none_mock(value: object) -> MagicMock:
    """构造 execute 返回值，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_type_weights(
    score_type: str,
    a: float,
    f: float,
    s: float,
) -> dict[str, dict]:
    """构造回路类型权重映射。"""
    return {
        score_type: {
            "weight_a": Decimal(str(a)),
            "weight_f": Decimal(str(f)),
            "weight_s": Decimal(str(s)),
        }
    }


def _make_kpi_values(
    accuracy: Decimal | None = Decimal("90"),
    fast_response: Decimal | None = Decimal("80"),
    steady: Decimal | None = Decimal("70"),
    effective_auto: Decimal | None = Decimal("60"),
) -> dict[str, Decimal | None]:
    """构造 KPI 值字典（默认 A=90, F=80, S=70, R=60）。"""
    return {
        "accuracy_rate": accuracy,
        "fast_rate": fast_response,
        "steady_rate": steady,
        "effective_auto_rate": effective_auto,
    }


def _compute_composite_score_v2_via_evaluator(
    kpi_values: dict[str, Decimal | None],
    type_weights: dict[str, dict] | None,
    score_type: str,
) -> Decimal | None:
    """通过 ConfidenceEvaluator.compute_composite_score 计算 v2 综合评分。

    适配旧 ``_compute_composite_score_v2(kpi_values, type_weights, score_type)``
    签名到 Phase 4 新接口 ``ConfidenceEvaluator.compute_composite_score(
    metric_results, weights)``。

    Phase 4 重构后 ``app.tasks.kpi_calc._compute_composite_score_v2`` 已删除，
    本函数封装新接口调用，保留旧测试用例的调用方式。

    旧 kpi_values key: accuracy_rate / fast_rate / steady_rate /
        effective_auto_rate
    新 metric_results key: accuracy_rate / fast_rate / stability_rate /
        effective_auto_rate

    type_weights[score_type] = {weight_a, weight_f, weight_s}（Decimal）
    weights = {accuracy_rate, fast_rate, stability_rate}（float）

    Returns:
        综合评分 Decimal（INCONCLUSIVE 时返回 None）
    """

    def _to_result(code: str, value: Decimal | None) -> MetricResult:
        if value is None:
            return MetricResult(
                metric_code=code,
                value=None,
                confidence_level=ConfidenceLevel.E.value,
                lineage=DataLineage(algorithm_version=CONFIDENCE_ALGORITHM_VERSION),
            )
        return MetricResult(
            metric_code=code,
            value=float(value),
            confidence_level=ConfidenceLevel.A.value,
            lineage=DataLineage(algorithm_version=CONFIDENCE_ALGORITHM_VERSION),
        )

    metric_results: dict[str, MetricResult] = {
        "accuracy_rate": _to_result("accuracy_rate", kpi_values.get("accuracy_rate")),
        "fast_rate": _to_result("fast_rate", kpi_values.get("fast_rate")),
        "stability_rate": _to_result("stability_rate", kpi_values.get("steady_rate")),
    }
    eff_auto = kpi_values.get("effective_auto_rate")
    if eff_auto is not None:
        metric_results["effective_auto_rate"] = _to_result("effective_auto_rate", eff_auto)

    weights: dict[str, float] | None = None
    if type_weights and score_type in type_weights:
        w = type_weights[score_type]
        weights = {
            "accuracy_rate": float(w.get("weight_a", 0)),
            "fast_rate": float(w.get("weight_f", 0)),
            "stability_rate": float(w.get("weight_s", 0)),
        }

    result = ConfidenceEvaluator.compute_composite_score(
        metric_results=metric_results,
        weights=weights,
    )
    if result.value is None:
        return None
    return Decimal(str(result.value))


def _make_agg_row(
    cnt: int = 3,
    auto_loop_count: int = 2,
    weight_sum: Decimal | None = Decimal("6.0"),
    score: Decimal | None = Decimal("80.00"),
) -> MagicMock:
    """构造 aggregate_node_snapshot 的聚合行 mock。"""
    row = MagicMock()
    row.cnt = cnt
    row.auto_loop_count = auto_loop_count
    row.weight_sum = weight_sum
    row.score = score
    row.good_value_rate = Decimal("95.00")
    row.auto_mode_rate = Decimal("88.00")
    row.effective_auto_rate = Decimal("85.00")
    row.steady_rate = Decimal("80.00")
    row.accuracy_rate = Decimal("78.00")
    row.fast_rate = Decimal("82.00")
    row.oscillation_rate = Decimal("15.00")
    row.saturation_rate = Decimal("8.00")
    # P1 #14: 4 个新增诊断字段
    row.stiction_index = Decimal("0.12")
    row.settling_time = Decimal("135.00")
    row.output_trip_index = Decimal("38.00")
    row.ideal_settling_time = Decimal("180.00")
    return row


# ===========================================================================
# TEST-01: 投用定义 CRUD
# ===========================================================================


class TestModeMappingCRUD:
    """投用定义 CRUD 测试。"""

    @pytest.mark.asyncio
    async def test_list_mode_mappings_empty(self) -> None:
        """无配置时返回空列表。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))

        result = await list_mode_mappings(db, "loop-001")

        assert result == []

    @pytest.mark.asyncio
    async def test_replace_mode_mappings_success(self) -> None:
        """全量替换成功（3 条映射）。"""
        db = AsyncMock()
        # 1st execute: 查询旧数据（空）；2nd execute: delete（返回值不使用）
        db.execute = AsyncMock(side_effect=[_make_scalars_mock([]), MagicMock()])
        db.add = MagicMock()
        db.commit = AsyncMock()

        mappings = [
            {"modeValue": 1, "modeLabel": "AUTO", "isAuto": True, "isEffective": True},
            {"modeValue": 2, "modeLabel": "CAS", "isAuto": True, "isEffective": False},
            {"modeValue": 0, "modeLabel": "MANUAL", "isAuto": False, "isEffective": False},
        ]

        result = await replace_mode_mappings(db, "loop-001", "admin", mappings)

        assert len(result) == 3
        assert result[0]["modeValue"] == 1
        assert result[0]["modeLabel"] == "AUTO"
        assert result[0]["isAuto"] is True
        assert result[1]["modeValue"] == 2
        assert result[1]["modeLabel"] == "CAS"
        assert result[2]["modeValue"] == 0
        assert result[2]["modeLabel"] == "MANUAL"
        assert result[2]["isAuto"] is False
        # db.add 调用 4 次：3 条映射 + 1 条审计日志
        assert db.add.call_count == 4
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_replace_mode_mappings_duplicate(self) -> None:
        """MODE 值重复时抛 ERR_MODE_MAPPING_DUPLICATE。"""
        db = AsyncMock()

        mappings = [
            {"modeValue": 1, "modeLabel": "AUTO", "isAuto": True, "isEffective": True},
            {"modeValue": 1, "modeLabel": "CAS", "isAuto": True, "isEffective": False},
        ]

        with pytest.raises(BizError) as exc_info:
            await replace_mode_mappings(db, "loop-001", "admin", mappings)
        assert exc_info.value.code == "ERR_MODE_MAPPING_DUPLICATE"

    @pytest.mark.asyncio
    async def test_replace_mode_mappings_invalid_label(self) -> None:
        """无效 modeLabel 抛 ERR_MODE_MAPPING_INVALID。"""
        db = AsyncMock()

        mappings = [
            {"modeValue": 1, "modeLabel": "INVALID", "isAuto": True, "isEffective": True},
        ]

        with pytest.raises(BizError) as exc_info:
            await replace_mode_mappings(db, "loop-001", "admin", mappings)
        assert exc_info.value.code == "ERR_MODE_MAPPING_INVALID"

    @pytest.mark.asyncio
    async def test_get_auto_mode_values_with_config(self) -> None:
        """有配置时返回配置的自动 MODE 值。"""
        db = AsyncMock()
        rows = [
            MagicMock(loop_id="loop-001", mode_value=1),
            MagicMock(loop_id="loop-001", mode_value=2),
        ]
        db.execute = AsyncMock(return_value=_make_rows_mock(rows))

        result = await get_auto_mode_values(db, "loop-001")

        assert result == {1, 2}

    @pytest.mark.asyncio
    async def test_get_auto_mode_values_no_config(self) -> None:
        """无配置时回退默认 {1,2,3}。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_rows_mock([]))

        result = await get_auto_mode_values(db, "loop-001")

        assert result == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_get_effective_mode_values_with_config(self) -> None:
        """有配置时返回有效 MODE 值。"""
        db = AsyncMock()
        rows = [
            MagicMock(loop_id="loop-001", mode_value=1),
            MagicMock(loop_id="loop-001", mode_value=3),
        ]
        db.execute = AsyncMock(return_value=_make_rows_mock(rows))

        result = await get_effective_mode_values(db, "loop-001")

        assert result == {1, 3}

    @pytest.mark.asyncio
    async def test_get_effective_mode_values_no_config(self) -> None:
        """无配置时回退默认 {1,2,3}。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_rows_mock([]))

        result = await get_effective_mode_values(db, "loop-001")

        assert result == {1, 2, 3}


# ===========================================================================
# TEST-02: 评分算法 v2（4 种回路类型）
# ===========================================================================


class TestComputeCompositeScoreV2:
    """评分算法 v2 测试 — 国标公式 P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R。

    使用纯函数测试（不需要 mock DB）。
    默认 KPI 值：A=90, F=80, S=70, R=60。
    """

    def test_score_v2_stable(self) -> None:
        """稳定型：a=0.2, f=0.3, s=0.5。

        P = (0.2*0.9 + 0.3*0.8 + 0.5*0.7) / 1.0 * 0.6 * 100 = 46.20
        """
        type_weights = _make_type_weights("STABLE", 0.2, 0.3, 0.5)
        kpi_values = _make_kpi_values()

        score = _compute_composite_score_v2_via_evaluator(kpi_values, type_weights, "STABLE")

        assert score == Decimal("46.20")

    def test_score_v2_slow(self) -> None:
        """慢速型：a=0.3, f=0.1, s=0.6。

        P = (0.3*0.9 + 0.1*0.8 + 0.6*0.7) / 1.0 * 0.6 * 100 = 46.20
        """
        type_weights = _make_type_weights("SLOW", 0.3, 0.1, 0.6)
        kpi_values = _make_kpi_values()

        score = _compute_composite_score_v2_via_evaluator(kpi_values, type_weights, "SLOW")

        assert score == Decimal("46.20")

    def test_score_v2_fast(self) -> None:
        """快速型：a=0.2, f=0.5, s=0.3。

        P = (0.2*0.9 + 0.5*0.8 + 0.3*0.7) / 1.0 * 0.6 * 100 = 47.40
        """
        type_weights = _make_type_weights("FAST", 0.2, 0.5, 0.3)
        kpi_values = _make_kpi_values()

        score = _compute_composite_score_v2_via_evaluator(kpi_values, type_weights, "FAST")

        assert score == Decimal("47.40")

    def test_score_v2_logic(self) -> None:
        """逻辑型：a=0.0, f=0.5, s=0.6。

        P = (0.0*0.9 + 0.5*0.8 + 0.6*0.7) / 1.1 * 0.6 * 100 = 44.73
        """
        type_weights = _make_type_weights("LOGIC", 0.0, 0.5, 0.6)
        kpi_values = _make_kpi_values()

        score = _compute_composite_score_v2_via_evaluator(kpi_values, type_weights, "LOGIC")

        assert score == Decimal("44.73")

    def test_score_v2_r_missing_is_inconclusive(self) -> None:
        """R 缺失时视为 INCONCLUSIVE（P1 #18 修正，原为降级 60%）。

        设计文档 §4.10 未定义 R 缺失的降级逻辑，
        原 base_score * 0.6 系数缺乏依据，统一并入 INCONCLUSIVE 路径。
        """
        type_weights = _make_type_weights("STABLE", 0.2, 0.3, 0.5)
        kpi_values = _make_kpi_values(effective_auto=None)

        score = _compute_composite_score_v2_via_evaluator(kpi_values, type_weights, "STABLE")

        assert score is None

    def test_score_v2_no_weights(self) -> None:
        """无权重配置时使用 ConfidenceEvaluator 默认权重（a=0.25, f=0.20, s=0.55）。

        Phase 4 重构后，weights=None 时不再回退平等加权，而是使用
        ConfidenceEvaluator.DEFAULT_WEIGHTS（对齐国标附录 C 稳定型）。

        基础评分 = (0.25*0.9 + 0.20*0.8 + 0.55*0.7) / 1.0 * 100 = 77.00
        P = 77.00 * 0.6 = 46.20
        """
        kpi_values = _make_kpi_values()

        score = _compute_composite_score_v2_via_evaluator(kpi_values, None, "STABLE")

        assert score == Decimal("46.20")


class TestInferScoreType:
    """工艺类型→评分类型映射测试。"""

    @pytest.mark.parametrize(
        "loop_type,expected",
        [
            ("TEMPERATURE", "STABLE"),
            ("PRESSURE", "STABLE"),
            ("LEVEL", "SLOW"),
            ("ANALYSIS", "SLOW"),
            ("FLOW", "FAST"),
            ("SPEED", "FAST"),
            ("OTHER", "LOGIC"),
            (None, "LOGIC"),
            ("UNKNOWN", "LOGIC"),
        ],
    )
    def test_infer_score_type(self, loop_type: str | None, expected: str) -> None:
        """工艺类型→评分类型映射（TEMPERATURE→STABLE 等）。"""
        assert infer_score_type(loop_type) == expected


# ===========================================================================
# TEST-03: 节点聚合 v2（3 种级别加权）
# ===========================================================================


class TestAggregateNodeSnapshotLevelWeighting:
    """节点聚合 v2 级别加权测试。

    验证 aggregate_node_snapshot 在不同 level 权重场景下的处理。
    level 权重由 SQL 中 func.coalesce(LoopLevelWeight.weight, 1.0) 计算，
    测试通过 mock 聚合行验证函数对结果的正确处理。
    """

    @pytest.mark.asyncio
    async def test_aggregate_node_snapshot_level_weighting(self) -> None:
        """验证按 level 加权（mock loop_level_weight 表数据）。

        3 条回路分别 level=1/2/3，权重 3.0/2.0/1.0，weight_sum=6.0。
        """
        db = AsyncMock()
        # mock 聚合查询返回（加权计算由 SQL 完成，mock 返回最终聚合值）
        agg_row = _make_agg_row(
            cnt=3,
            auto_loop_count=2,
            weight_sum=Decimal("6.0"),
            score=Decimal("80.00"),
        )
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row
        db.execute = AsyncMock(return_value=agg_result)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001", "loop-002", "loop-003"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value={
                    "rate": Decimal("66.67"),
                    "auto_count": 2,
                    "manual_count": 1,
                    "total_count": 3,
                    "read_at": "2026-06-22T08:00:00Z",
                },
            ),
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        assert result["plant_node_id"] == "node-001"
        assert result["loop_count"] == 3
        assert result["score"] == Decimal("80.00")
        assert result["auto_loop_ratio"] == Decimal("66.67")  # 2/3*100
        assert result["realtime_auto_rate"] == Decimal("66.67")
        assert result["status"] == "GOOD"  # score=80 → GOOD

    @pytest.mark.asyncio
    async def test_aggregate_node_snapshot_no_level(self) -> None:
        """level=NULL 时回退 1.0。

        2 条回路 level=NULL，COALESCE 到 1.0，weight_sum=2.0。
        """
        db = AsyncMock()
        agg_row = _make_agg_row(
            cnt=2,
            auto_loop_count=1,
            weight_sum=Decimal("2.0"),
            score=Decimal("75.00"),
        )
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row
        db.execute = AsyncMock(return_value=agg_result)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001", "loop-002"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-002",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        assert result["plant_node_id"] == "node-002"
        assert result["loop_count"] == 2
        assert result["score"] == Decimal("75.00")
        assert result["auto_loop_ratio"] == Decimal("50.00")  # 1/2*100
        assert result["realtime_auto_rate"] is None
        assert result["status"] == "FAIR"  # score=75 → FAIR


# ===========================================================================
# TEST-04: 实时自控率读投用定义
# ===========================================================================


class TestRealtimeAutoRate:
    """实时自控率读投用定义测试。

    验证 query_realtime_auto_rate 在有/无投用定义时的行为。
    使用 mock_db + mock TDengine（patch query_trend_data）。

    P1 #15 修正后，自动 MODE 来源优先级：
    1. LoopModeMapping 投用定义（回路级，最高优先级）
    2. sys_config.loop.default_auto_modes（全局覆盖，可选）
    3. 行业默认 {1, 2, 3}（无任何配置时回退，对齐算法说明 §4.0.3）

    DB execute 调用顺序（3 次）：
    1. sys_config 查询（scalar_one_or_none）
    2. LoopModeMapping 投用定义查询（all）
    3. LoopTagMapping MODE tag 映射查询（all）
    """

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_with_loop_config(self) -> None:
        """有投用定义时按回路配置判断（sys_config 空 → 行业默认 {1,2,3,4}）。

        loop-001 配置自动 MODE={1,2}（LoopModeMapping），
        loop-002 无配置 → 回退行业默认 {1,2,3,4}。
        TAG_001 返回 mode=1（在 {1,2} → 自动），TAG_002 返回 mode=0（不在 {1,2,3,4} → 手动）。
        期望：1/2 = 50.0%
        """
        db = AsyncMock()
        # 1st execute: sys_config 查询（无配置 → 行业默认 {1,2,3,4}）
        # 2nd execute: LoopModeMapping 投用定义（loop-001 有配置）
        mm_rows = [
            MagicMock(loop_id="loop-001", mode_value=1),
            MagicMock(loop_id="loop-001", mode_value=2),
        ]
        # 3rd execute: MODE tag 映射查询
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(None),
                _make_rows_mock(mm_rows),
                _make_rows_mock(tag_rows),
            ]
        )

        async def _mock_query_trend(tag_name: str, start: str, end: str):
            if tag_name == "TAG_001":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 1}]
            if tag_name == "TAG_002":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 0}]
            return []

        with patch(
            "app.core.tdengine.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("50.00")
        assert result["auto_count"] == 1
        assert result["manual_count"] == 1
        assert result["total_count"] == 2
        # mode_counts 验证：mode=1 一个回路，mode=0 一个回路
        assert result["mode_counts"][0] == 1
        assert result["mode_counts"][1] == 1
        assert result["mode_counts"][2] == 0
        assert result["mode_counts"][3] == 0
        assert result["mode_counts"][4] == 0

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_with_sysconfig_default(self) -> None:
        """无回路配置时回退 sys_config 全局默认 [1,2,3]。

        两个回路均无 LoopModeMapping，回退到 sys_config.loop.default_auto_modes=[1,2,3]。
        TAG_001 返回 mode=1（在 {1,2,3} → 自动），TAG_002 返回 mode=2（在 {1,2,3} → 自动）。
        期望：2/2 = 100.0%
        """
        db = AsyncMock()
        # 1st execute: sys_config 查询（返回 "[1, 2, 3]"）
        # 2nd execute: LoopModeMapping 投用定义（空，无配置）
        # 3rd execute: MODE tag 映射查询
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock("[1, 2, 3]"),
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
            ]
        )

        async def _mock_query_trend(tag_name: str, start: str, end: str):
            if tag_name == "TAG_001":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 1}]
            if tag_name == "TAG_002":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 2}]
            return []

        with patch(
            "app.core.tdengine.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("100.00")
        assert result["auto_count"] == 2
        assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_empty_default_uses_industry_default(self) -> None:
        """无任何配置（sys_config 空 + 无 LoopModeMapping）→ 回退行业默认 {1,2,3}。

        修正：原 P1 #15 严格空集设计已废弃，
        现默认与算法说明 §4.0.3 + KPI 计算器（auto_mode.py）保持一致：
        MODE=1/2/3 计入自动，MODE=0 计入手动。
        sys_config.loop.default_auto_modes 仅作可选覆盖，不强制配置。
        """
        db = AsyncMock()
        # 1st execute: sys_config 查询（无配置 → 行业默认 {1,2,3}）
        # 2nd execute: LoopModeMapping（空）
        # 3rd execute: MODE tag 映射
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(None),
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
            ]
        )

        async def _mock_query_trend(tag_name: str, start: str, end: str):
            if tag_name == "TAG_001":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 1}]
            if tag_name == "TAG_002":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 2}]
            return []

        with patch(
            "app.core.tdengine.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("100.00")
        assert result["auto_count"] == 2
        assert result["manual_count"] == 0
        assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_invalid_sysconfig_value(self) -> None:
        """sys_config 值非法（非 JSON 数组）时回退行业默认 {1,2,3} 并记录告警。

        验证 get_default_auto_modes 的异常分支容错：value="invalid" → 行业默认。
        """
        db = AsyncMock()
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock("invalid-json"),
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
            ]
        )

        async def _mock_query_trend(tag_name: str, start: str, end: str):
            if tag_name == "TAG_001":
                return [{"ts": "2026-06-22T08:00:00Z", "value": 1}]
            return []

        with patch(
            "app.core.tdengine.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001"])

        # 非法 sys_config → 行业默认 {1,2,3} → mode=1 在 {1,2,3} → 自动
        assert result is not None
        assert result["rate"] == Decimal("100.00")
        assert result["auto_count"] == 1
        assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_no_loops(self) -> None:
        """空回路列表返回 None。"""
        db = AsyncMock()

        result = await query_realtime_auto_rate(db, [])

        assert result is None
        # 空列表时应立即返回，不查询 DB
        db.execute.assert_not_called()
