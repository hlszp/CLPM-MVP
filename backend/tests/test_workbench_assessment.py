"""A-02 工作台评估聚合 service 单测（M2 批次 G-评估）.

覆盖：
- 纯 shaper：_window_delta / _row_score / shape_summary / shape_ranking_plant /
  shape_ranking_unit / shape_heatmap / shape_trend（全字段塑造 + 边界）
- build_assessment 编排：patch 各 _query_* helper 返回种子数据，断言四块组装正确
  （对齐 test_workbench_overview 的 patch 范式，不依赖真实 PG）
- 2026-10-10 P1-02：C06 环比=上一等长窗 / CAL-06 应评分母台账口径 /
  CAL-07 未计算显式区分 / PERF-03 子树查询去重
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.services.workbench_assessment import (
    ASSESSMENT_TARGET_SCORE,
    EVAL_METRICS,
    _count_evaluable_loops,
    _query_prev_equal_row,
    _query_scope_rows_in_ids,
    _window_delta,
    build_assessment,
    shape_heatmap,
    shape_ranking_plant,
    shape_ranking_unit,
    shape_summary,
    shape_trend,
)

# ---------------------------------------------------------------------------
# 合成行构造
# ---------------------------------------------------------------------------


def _win_row(
    window: str = "24h",
    score: float = 84.2,
    rates: dict[str, float] | None = None,
    trend: list[dict] | None = None,
    distribution: dict | None = None,
    loop_count: int = 34,
):
    """合成 workbench_window_summary 行（属性访问）。"""
    rates = rates or {}
    row = MagicMock()
    row.window_w = window
    row.scope_id = 0
    row.score = score
    row.status = "FAIR"
    row.loop_count = loop_count
    row.score_trend = (
        trend
        if trend is not None
        else (
            []
            if score is None
            else [
                {"t": "2026-08-25T00:00:00Z", "v": score - 1.2},
                {"t": "2026-08-25T12:00:00Z", "v": score},
            ]
        )
    )
    row.flags = []
    row.distribution = distribution or {}
    row.snapshot_at = datetime(2026, 8, 25, 0, 0, 0)
    # 6 评估指标 + 故障率
    for key, _label, _rev in EVAL_METRICS:
        setattr(row, key, rates.get(key))
    return row


def _plant_node(node_id, name, ntype, parent_id=None, source_id=None):
    n = MagicMock()
    n.id = node_id
    n.name = name
    n.type = ntype
    n.parent_id = parent_id
    n.source_node_id = source_id
    return n


def _hierarchy():
    factory = _plant_node("f1", "EO 工厂", "FACTORY", source_id=100)
    area = _plant_node("a1", "EO 装置", "AREA", parent_id="f1", source_id=1000)
    unit = _plant_node("u1", "精馏单元", "UNIT", parent_id="a1", source_id=10000)
    return {
        "by_id": {"f1": factory, "a1": area, "u1": unit},
        "unit_to_factory": {"u1": "f1"},
        "name_by_source_id": {100: "EO 工厂", 1000: "EO 装置", 10000: "精馏单元"},
        "factories": [factory],
    }


# ===========================================================================
# _window_delta（CAL-06：环比=上一等长窗口）
# ===========================================================================


class TestWindowDelta:
    def test_当前减上一等长窗(self):
        assert _window_delta(84.2, 82.0) == 2.2

    def test_C06反例_不再本窗首末差冒充环比(self):
        """本窗 trend 首末差 +2.2，但上一等长窗 78.0 → 环比 +6.2（而非 +2.2）。

        原 _sparkline_delta 取本窗 score_trend 首末差，衡量窗内走势而非
        跨窗环比；符号都可能错（窗内上涨但低于上一窗时应为负）。
        """
        cur_row_trend_first_last_delta = 84.2 - 82.0  # 旧口径 +2.2
        assert _window_delta(84.2, 78.0) == 6.2
        assert _window_delta(84.2, 78.0) != cur_row_trend_first_last_delta
        # 窗内上涨但低于上一窗 → 环比为负（旧口径会误报 +2.2）
        assert _window_delta(82.0, 90.0) == -8.0

    def test_任一侧未计算返回None(self):
        assert _window_delta(None, 80.0) is None
        assert _window_delta(84.2, None) is None
        assert _window_delta(None, None) is None

    def test_异常值返回None(self):
        assert _window_delta("bad", 80.0) is None
        assert _window_delta(84.2, "bad") is None


# ===========================================================================
# shape_summary
# ===========================================================================


class TestShapeSummary:
    def test_空行返回兜底摘要(self):
        out = shape_summary(None, [], 34)
        assert out["score"] is None
        assert out["score_state"] == "NOT_COMPUTED"
        assert out["participation"] == {"evaluated": 0, "total": 34}
        assert out["risks"] == []
        assert out["target"] == ASSESSMENT_TARGET_SCORE

    def test_完整摘要含结论与风险速览(self):
        row = _win_row(score=84.2, loop_count=32)
        prev = _win_row(score=83.0, loop_count=30)
        plants = [
            {"name": "催化裂化", "score": 82.1, "delta": -2.6, "lose_factors": ["振荡"]},
            {"name": "乙烯", "score": 83.5, "delta": -1.2, "lose_factors": []},
        ]
        out = shape_summary(row, plants, 34, prev)
        assert out["score"] == 84.2
        assert out["score_state"] == "COMPUTED"
        assert out["grade"] == "B 良好"
        assert out["participation"] == {"evaluated": 1, "total": 34}
        assert out["distance_to_target"] == round(84.2 - 90, 1)
        # CAL-06：环比 = 84.2 − 上一等长窗 83.0（不再本窗 trend 首末差）
        assert out["delta"] == 1.2
        assert "催化裂化" in out["conclusion"]
        # 风险速览取前 3 个装置
        assert len(out["risks"]) == 2
        assert out["risks"][0]["name"] == "催化裂化"
        # 跳转链接
        actions = {link["action"] for link in out["conclusion_links"]}
        assert "tab:diag" in actions and "alerts" in actions

    def test_C06反例_环比上一等长窗而非本窗首末差(self):
        """本窗 trend 82→84.2（首末差 +2.2），上一等长窗 78.0 → delta +6.2。"""
        row = _win_row(score=84.2, trend=[{"t": "a", "v": 82.0}, {"t": "b", "v": 84.2}])
        prev = _win_row(score=78.0)
        out = shape_summary(row, [], 34, prev)
        assert out["delta"] == 6.2

    def test_无上一等长窗行时delta为None(self):
        out = shape_summary(_win_row(score=84.2), [], 34, None)
        assert out["delta"] is None

    def test_CAL07_NULL评分显式未计算(self):
        """score=NULL（新口径）→ 未计算，score_state=NOT_COMPUTED，不渲染 0 分。"""
        row = _win_row(score=None, loop_count=0)
        out = shape_summary(row, [], 34, None)
        assert out["score"] is None
        assert out["score_state"] == "NOT_COMPUTED"
        assert "未计算" in out["conclusion"]

    def test_CAL07_旧伪0行过渡守卫(self):
        """迁移前旧代码写入 score=0.0 + loop_count=0 → 同样按未计算处理。"""
        row = _win_row(score=0.0, loop_count=0)
        out = shape_summary(row, [], 34, None)
        assert out["score"] is None
        assert out["score_state"] == "NOT_COMPUTED"

    def test_CAL07_真实0分仍是0分(self):
        """score=0.0 且有参评产出 → COMPUTED（真实 0 分，不误报未计算）。"""
        row = _win_row(score=0.0, loop_count=12)
        out = shape_summary(row, [], 34, None)
        assert out["score"] == 0.0
        assert out["score_state"] == "COMPUTED"
        assert out["grade"] == "D 较差"


# ===========================================================================
# shape_ranking_plant
# ===========================================================================


class TestShapeRankingPlant:
    def test_按分升序风险优先且含失分标签(self):
        hierarchy = _hierarchy()
        r_low = _win_row(score=82.0, rates={"steady_rate": 0.80})
        r_low.scope_id = 100
        r_high = _win_row(score=90.0, rates={"steady_rate": 0.95})
        r_high.scope_id = 200
        # 200 不在 name_by_source_id → 占位名
        plants = shape_ranking_plant(
            [r_high, r_low], hierarchy, {}, {}, threshold=0.90, total_loops=34
        )
        # 升序：82.0 在前 → rank 1
        assert plants[0]["score"] == 82.0
        assert plants[0]["rank"] == 1
        assert plants[0]["name"] == "EO 工厂"
        assert "平稳率" in plants[0]["lose_factors"]
        assert plants[0]["join"] == "34/34"
        assert plants[1]["rank"] == 2

    def test_空输入(self):
        assert shape_ranking_plant([], _hierarchy(), {}, {}, 0.90, 34) == []

    def test_C06_行级环比取上一等长窗行(self):
        """ranking 行 delta = 当前窗评分 − prev_rows_by_scope[scope_id] 评分。"""
        hierarchy = _hierarchy()
        r = _win_row(score=82.0)
        r.scope_id = 100
        prev = _win_row(score=85.0)
        plants = shape_ranking_plant(
            [r], hierarchy, {}, {}, 0.90, 34, prev_rows_by_scope={100: prev}
        )
        assert plants[0]["delta"] == -3.0

    def test_未计算行排末尾且delta为None(self):
        """CAL-07：score NULL / loop_count=0 的行排末尾，不参与环比。"""
        hierarchy = _hierarchy()
        r_ok = _win_row(score=82.0)
        r_ok.scope_id = 100
        r_na = _win_row(score=None, loop_count=0)
        r_na.scope_id = 200
        plants = shape_ranking_plant([r_ok, r_na], hierarchy, {}, {}, 0.90, 34)
        assert plants[0]["id"] == 100
        assert plants[1]["score"] is None
        assert plants[1]["delta"] is None


# ===========================================================================
# shape_ranking_unit
# ===========================================================================


class TestShapeRankingUnit:
    def test_含所属装置名且按分升序(self):
        hierarchy = _hierarchy()
        r = _win_row(score=88.5)
        r.scope_id = 10000
        units = shape_ranking_unit([r], hierarchy)
        assert units[0]["name"] == "精馏单元"
        assert units[0]["parent_name"] == "EO 装置"
        assert units[0]["rank"] == 1
        assert units[0]["join"] is None


# ===========================================================================
# shape_heatmap
# ===========================================================================


class TestShapeHeatmap:
    def test_6指标塑造且故障率反向标记(self):
        hierarchy = _hierarchy()
        r = _win_row(
            score=88.5,
            rates={
                "effective_auto_rate": 0.825,
                "steady_rate": 0.794,
                "accuracy_rate": 0.768,
                "fast_rate": 0.712,
                "good_value_rate": 0.806,
                "instrument_fault_rate": 0.068,
            },
        )
        r.scope_id = 10000
        heat = shape_heatmap([r], hierarchy)
        assert len(heat["metrics"]) == 6
        # 末项故障率 reverse=True
        assert heat["metrics"][5]["reverse"] is True
        assert heat["metrics"][5]["label"] == "故障率"
        assert heat["units"][0]["name"] == "精馏单元"
        # 值归一为 0~100 口径
        assert heat["units"][0]["values"][0] == 82.5
        assert heat["units"][0]["values"][5] == 6.8

    def test_缺值None透传(self):
        hierarchy = _hierarchy()
        r = _win_row(score=80.0, rates={})
        r.scope_id = 10000
        heat = shape_heatmap([r], hierarchy)
        # 全部 None → 前端斜纹
        assert all(v is None for v in heat["units"][0]["values"])


# ===========================================================================
# shape_trend
# ===========================================================================


class TestShapeTrend:
    def test_空行返回空trend(self):
        out = shape_trend(None, None)
        assert out["series"] == {"current": [], "previous": []}
        assert out["slopes"] == []
        assert out["target"] == ASSESSMENT_TARGET_SCORE

    def test_分布数据从distribution读取(self):
        dist = {
            "level_dist": [{"label": "优", "count": 9, "color": "#2E7D32"}],
            "mode_dist": [{"label": "自动", "count": 29, "color": "#2563EB"}],
            "data_quality": [{"label": "数据完整", "count": 33, "level": "green"}],
            "metric_slopes": [{"metric": "快速率", "delta": 2.0, "direction": "good"}],
        }
        row = _win_row(distribution=dist)
        prev = _win_row(score=82.0)
        out = shape_trend(row, prev)
        assert out["series"]["current"] == row.score_trend
        assert out["series"]["previous"] == prev.score_trend
        assert out["level_dist"][0]["count"] == 9
        assert out["mode_dist"][0]["label"] == "自动"
        assert out["data_quality"][0]["level"] == "green"
        assert out["slopes"][0]["direction"] == "good"
        assert out["snapshot_at"] is not None

    def test_distribution缺失兜底空(self):
        row = _win_row(distribution={})
        out = shape_trend(row, None)
        assert out["slopes"] == []
        assert out["level_dist"] == []


# ===========================================================================
# build_assessment 编排（patch helpers，不依赖真实 PG）
# ===========================================================================


class TestBuildAssessment:
    @pytest.mark.asyncio
    async def test_四块组装且部分失败容错(self):
        db = AsyncMock()
        win_row = _win_row(
            score=84.2,
            loop_count=32,
            distribution={
                "metric_slopes": [{"metric": "快速率", "delta": 2.0, "direction": "good"}],
                "level_dist": [{"label": "优", "count": 9}],
                "mode_dist": [{"label": "自动", "count": 29}],
                "data_quality": [{"label": "数据完整", "count": 33}],
            },
        )
        plant_kpi = _win_row(score=82.1, rates={"steady_rate": 0.80})
        plant_kpi.scope_id = 100
        unit_kpi = _win_row(
            score=88.5,
            rates={
                "effective_auto_rate": 0.825,
                "steady_rate": 0.794,
                "accuracy_rate": 0.768,
                "fast_rate": 0.712,
                "good_value_rate": 0.806,
                "instrument_fault_rate": 0.068,
            },
        )
        unit_kpi.scope_id = 10000

        with (
            patch(
                "app.services.workbench_assessment._query_scope_row",
                AsyncMock(side_effect=[win_row, win_row, None]),  # win/global/prev
            ),
            patch(
                "app.services.workbench_assessment._query_prev_equal_row",
                AsyncMock(return_value=None),  # CAL-06：上一等长窗行缺行 → delta None
            ),
            patch(
                "app.services.workbench_assessment._count_evaluable_loops",
                AsyncMock(return_value=34),  # CAL-06：台账参评分母
            ),
            patch(
                "app.services.workbench_assessment._query_prev_scope_rows",
                AsyncMock(return_value={}),  # 行级环比基准
            ),
            patch(
                "app.services.workbench_assessment._get_lose_threshold",
                AsyncMock(return_value=0.90),
            ),
            patch(
                "app.services.workbench_assessment._load_plant_hierarchy",
                AsyncMock(return_value=_hierarchy()),
            ),
            patch(
                "app.services.workbench_assessment._query_alarm_per_unit",
                AsyncMock(return_value={}),
            ),
            patch(
                "app.services.workbench_assessment._query_overdue_per_unit",
                AsyncMock(return_value={}),
            ),
            patch(
                "app.services.workbench_assessment._get_child_ids_for_plants",
                AsyncMock(return_value=("FACTORY", [])),
            ),
            patch(
                "app.services.workbench_assessment._get_descendant_unit_ids",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.workbench_assessment._query_scope_rows",
                AsyncMock(side_effect=[[plant_kpi], [unit_kpi]]),  # plant ranking / heatmap units
            ),
            patch(
                "app.services.workbench_assessment._query_prev_window_row",
                AsyncMock(side_effect=RuntimeError("prev 不可读")),  # trend 单块失败
            ),
        ):
            data = await build_assessment(db, scope_type="GLOBAL", window="24h", view="plant")

        # 四块顶层键齐全
        assert set(data.keys()) >= {"summary", "ranking", "heatmap", "trend", "view"}
        assert data["scope"] == {"type": "GLOBAL", "id": None}
        assert data["view"] == "plant"
        # summary
        assert data["summary"]["score"] == 84.2
        assert data["summary"]["grade"] == "B 良好"
        # ranking（plant 视图，1 行）
        assert data["ranking"][0]["name"] == "EO 工厂"
        assert data["ranking"][0]["score"] == 82.1
        # heatmap
        assert data["heatmap"]["metrics"][5]["reverse"] is True
        assert data["heatmap"]["units"][0]["values"][0] == 82.5
        # trend 单块失败 → None，不阻断其余块
        assert data["trend"] is None

    @pytest.mark.asyncio
    async def test_全局scope_id归零(self):
        db = AsyncMock()
        captured: dict[str, object] = {}

        async def _capture(_db, scope_type, scope_id, window):
            captured["scope_type"] = scope_type
            captured["scope_id"] = scope_id
            return None

        with (
            patch(
                "app.services.workbench_assessment._query_scope_row",
                AsyncMock(side_effect=_capture),
            ),
            patch(
                "app.services.workbench_assessment._count_evaluable_loops",
                AsyncMock(return_value=34),
            ),
            patch(
                "app.services.workbench_assessment._get_lose_threshold",
                AsyncMock(return_value=0.90),
            ),
            patch(
                "app.services.workbench_assessment._load_plant_hierarchy",
                AsyncMock(return_value=_hierarchy()),
            ),
            patch(
                "app.services.workbench_assessment._query_alarm_per_unit",
                AsyncMock(return_value={}),
            ),
            patch(
                "app.services.workbench_assessment._query_overdue_per_unit",
                AsyncMock(return_value={}),
            ),
            patch(
                "app.services.workbench_assessment._get_child_ids_for_plants",
                AsyncMock(return_value=("FACTORY", [])),
            ),
            patch(
                "app.services.workbench_assessment._get_descendant_unit_ids",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.workbench_assessment._query_scope_rows",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.workbench_assessment._query_prev_window_row",
                AsyncMock(return_value=None),
            ),
        ):
            await build_assessment(db, scope_type="GLOBAL", scope_id=None, window="24h")
        assert captured["scope_type"] == "GLOBAL"
        assert captured["scope_id"] == 0


# ===========================================================================
# async helper（CAL-06 应评分母 / 上一等长窗 / PERF-03 去重）
# ===========================================================================


class TestCountEvaluableLoops:
    """CAL-06：应评分母 = 台账参评三条件计数（与 _eval_loop_selection_stmt 一致）。"""

    @pytest.mark.asyncio
    async def test_global走台账三条件(self):
        db = AsyncMock()
        res = MagicMock()
        res.scalar.return_value = 34
        db.execute = AsyncMock(return_value=res)

        count = await _count_evaluable_loops(db, "GLOBAL", 0)

        assert count == 34
        sql = str(db.execute.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
        # 参评三条件全部入 SQL（与 kpi_calc._eval_loop_selection_stmt 全量选路一致）
        assert "is_active" in sql and "true" in sql.lower()
        assert "READY" in sql
        assert "include_in_evaluation" in sql

    @pytest.mark.asyncio
    async def test_子树走递归CTE三条件(self):
        db = AsyncMock()
        res = MagicMock()
        res.scalar.return_value = 12
        db.execute = AsyncMock(return_value=res)

        count = await _count_evaluable_loops(db, "AREA", 1000)

        assert count == 12
        stmt, params = db.execute.call_args[0]
        sql = str(stmt)
        assert "WITH RECURSIVE node_tree" in sql
        assert params == {"sid": 1000}
        for cond in ("is_active = TRUE", "status = 'READY'", "include_in_evaluation = TRUE"):
            assert cond in sql

    @pytest.mark.asyncio
    async def test_不支持scope按零显式降级(self):
        db = AsyncMock()
        assert await _count_evaluable_loops(db, "LOOP", 7) == 0
        db.execute.assert_not_awaited()


class TestQueryPrevEqualRow:
    """CAL-06：上一等长窗行查询——window_end ≤ 当前终点−窗长 的最新行。"""

    @pytest.mark.asyncio
    async def test_目标边界为终点减窗长(self):
        db = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=res)

        end = datetime(2026, 6, 25, 8, 0, tzinfo=UTC)
        await _query_prev_equal_row(db, "GLOBAL", 0, "24h", end)

        stmt = db.execute.call_args[0][0]
        params = stmt.compile().params
        bind_values = [v for v in params.values() if isinstance(v, datetime)]
        assert bind_values == [end - timedelta(hours=24)]
        sql = str(
            stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert "window_end <=" in sql
        # 同窗长（24h→24h，不是 24h→7d 跨窗长）
        assert "'24h'" in sql

    @pytest.mark.asyncio
    async def test_窗口终点缺失返回None(self):
        db = AsyncMock()
        assert await _query_prev_equal_row(db, "GLOBAL", 0, "24h", None) is None
        db.execute.assert_not_awaited()


class TestQueryScopeRowsInIds:
    """PERF-03：子树 .in_() 查询加每节点最新行去重（DISTINCT ON 模式）。"""

    @pytest.mark.asyncio
    async def test_sql带distinct_on最新行去重(self):
        db = AsyncMock()
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=res)

        await _query_scope_rows_in_ids(db, "UNIT", [10000, 10001], "24h")

        stmt = db.execute.call_args[0][0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "DISTINCT ON (workbench_window_summary.scope_id)" in sql
        # 最新行 = window_end 降序
        assert (
            "ORDER BY workbench_window_summary.scope_id, workbench_window_summary.window_end DESC"
            in sql
        )

    @pytest.mark.asyncio
    async def test_空id列表不查库(self):
        db = AsyncMock()
        assert await _query_scope_rows_in_ids(db, "UNIT", [], "24h") == []
        db.execute.assert_not_awaited()
