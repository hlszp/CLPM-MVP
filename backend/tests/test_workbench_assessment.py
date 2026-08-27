"""A-02 工作台评估聚合 service 单测（M2 批次 G-评估）.

覆盖：
- 纯 shaper：_sparkline_delta / shape_summary / shape_ranking_plant /
  shape_ranking_unit / shape_heatmap / shape_trend（全字段塑造 + 边界）
- build_assessment 编排：patch 各 _query_* helper 返回种子数据，断言四块组装正确
  （对齐 test_workbench_overview 的 patch 范式，不依赖真实 PG）
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.workbench_assessment import (
    ASSESSMENT_TARGET_SCORE,
    EVAL_METRICS,
    _sparkline_delta,
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
        else [
            {"t": "2026-08-25T00:00:00Z", "v": score - 1.2},
            {"t": "2026-08-25T12:00:00Z", "v": score},
        ]
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
# _sparkline_delta
# ===========================================================================


class TestSparklineDelta:
    def test_末减首(self):
        spark = [{"t": "x", "v": 82.0}, {"t": "y", "v": 84.2}]
        assert _sparkline_delta(spark) == 2.2

    def test_原始数值列表(self):
        assert _sparkline_delta([82.0, 84.2]) == 2.2

    def test_不足两点返回None(self):
        assert _sparkline_delta([{"v": 1}]) is None
        assert _sparkline_delta([]) is None

    def test_异常值返回None(self):
        assert _sparkline_delta([{"v": "bad"}, {"v": 1}]) is None


# ===========================================================================
# shape_summary
# ===========================================================================


class TestShapeSummary:
    def test_空行返回兜底摘要(self):
        out = shape_summary(None, [], 34)
        assert out["score"] is None
        assert out["participation"] == {"evaluated": 0, "total": 34}
        assert out["risks"] == []
        assert out["target"] == ASSESSMENT_TARGET_SCORE

    def test_完整摘要含结论与风险速览(self):
        row = _win_row(score=84.2, loop_count=32)
        plants = [
            {"name": "催化裂化", "score": 82.1, "delta": -2.6, "lose_factors": ["振荡"]},
            {"name": "乙烯", "score": 83.5, "delta": -1.2, "lose_factors": []},
        ]
        out = shape_summary(row, plants, 34)
        assert out["score"] == 84.2
        assert out["grade"] == "B 良好"
        assert out["participation"] == {"evaluated": 32, "total": 34}
        assert out["distance_to_target"] == round(84.2 - 90, 1)
        assert out["delta"] == 1.2  # trend 末减首（score - (score-1.2))
        assert "催化裂化" in out["conclusion"]
        # 风险速览取前 3 个装置
        assert len(out["risks"]) == 2
        assert out["risks"][0]["name"] == "催化裂化"
        # 跳转链接
        actions = {link["action"] for link in out["conclusion_links"]}
        assert "tab:diag" in actions and "alerts" in actions


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
