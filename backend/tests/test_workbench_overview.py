"""A-01 工作台总览聚合 service 单测（M2 批次 G-总览）.

覆盖：
- 纯 shaper：shape_windows / shape_plants / shape_units / shape_pareto /
  shape_roots / shape_funnel（全字段塑造 + 边界）
- build_overview 编排：patch 各 _query_* helper 返回种子数据，断言六块组装正确
  （对齐 test_workbench_summary 的 patch 范式，不依赖真实 PG）
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.workbench_overview import (
    DEFAULT_LOSE_FACTOR_THRESHOLD,
    KPI_METRICS,
    build_overview,
    shape_funnel,
    shape_pareto,
    shape_plants,
    shape_roots,
    shape_units,
    shape_windows,
)

# ---------------------------------------------------------------------------
# 合成行构造
# ---------------------------------------------------------------------------


def _win_row(window: str, score: float, rates: dict[str, float], trend=None, flags=None):
    """合成 workbench_window_summary 行（属性访问）。"""
    row = MagicMock()
    row.window_w = window
    row.score = score
    row.status = "GOOD"
    row.loop_count = 120
    row.score_trend = trend if trend is not None else [{"t": "2026-08-25T00:00:00Z", "v": score}]
    row.flags = flags if flags is not None else []
    row.snapshot_at = datetime(2026, 8, 25, 0, 0, 0)
    for key, _ in KPI_METRICS:
        setattr(row, key, rates.get(key))
    return row


def _plant_node(node_id: str, name: str, ntype: str, parent_id=None, source_id=None):
    n = MagicMock()
    n.id = node_id
    n.name = name
    n.type = ntype
    n.parent_id = parent_id
    n.source_node_id = source_id
    return n


# ===========================================================================
# shape_windows
# ===========================================================================


class TestShapeWindows:
    def test_三窗口全部塑造且含6指标(self):
        rows = [
            _win_row("24h", 82.0, {"good_value_rate": 0.95}),
            _win_row("7d", 78.5, {"steady_rate": 0.80}),
            _win_row("30d", 75.0, {}),
        ]
        out = shape_windows(rows)
        assert set(out.keys()) == {"24h", "7d", "30d"}
        assert out["24h"]["score"] == 82.0
        assert out["24h"]["status"] == "GOOD"
        assert out["24h"]["loop_count"] == 120
        assert set(out["24h"]["metrics"].keys()) == {k for k, _ in KPI_METRICS}
        assert out["24h"]["metrics"]["good_value_rate"] == 0.95
        assert out["7d"]["metrics"]["steady_rate"] == 0.80
        # 未设置的字段 → None
        assert out["30d"]["metrics"]["accuracy_rate"] is None
        assert out["24h"]["score_trend"] == rows[0].score_trend
        assert out["24h"]["flags"] == []

    def test_缺窗口补None(self):
        out = shape_windows([_win_row("24h", 70.0, {})])
        assert out["24h"] is not None
        assert out["7d"] is None
        assert out["30d"] is None

    def test_空输入三窗口None(self):
        out = shape_windows([])
        assert out == {"24h": None, "7d": None, "30d": None}


# ===========================================================================
# shape_plants
# ===========================================================================


class TestShapePlants:
    def _hierarchy(self):
        factory = _plant_node("f1", "装置A", "FACTORY", source_id=10)
        area = _plant_node("a1", "区域A", "AREA", parent_id="f1")
        unit = _plant_node("u1", "单元A1", "UNIT", parent_id="a1")
        return {
            "by_id": {"f1": factory, "a1": area, "u1": unit},
            "unit_to_factory": {"u1": "f1"},
            "name_by_source_id": {10: "装置A"},
            "factories": [factory],
        }

    def test_排名按分降序且含lose_factors与alarm_overdue(self):
        hierarchy = self._hierarchy()
        kpi = [
            _win_row("24h", 70.0, {"steady_rate": 0.80, "accuracy_rate": 0.92}),
            _win_row("24h", 90.0, {"steady_rate": 0.95}),
        ]
        # 第二行 scope_id=10（匹配 factory source_id），第一行 scope_id=99（未知装置）
        kpi[0].scope_id = 99
        kpi[1].scope_id = 10
        plants = shape_plants(
            kpi,
            hierarchy,
            alarm_per_unit={"u1": 3},
            overdue_per_unit={"u1": 1},
            threshold=DEFAULT_LOSE_FACTOR_THRESHOLD,
        )
        # 降序：90.0 在前
        assert plants[0]["score"] == 90.0
        assert plants[0]["rank"] == 1
        assert plants[0]["name"] == "装置A"
        # alarm/overdue 经 unit→factory 映射累加
        assert plants[0]["alarm_count"] == 3
        assert plants[0]["overdue_tasks"] == 1
        # plants[0]（90 分）steady_rate 0.95 ≥ 0.90 → 无损失因子
        assert plants[0]["lose_factors"] == []
        # plants[1]（70 分）steady_rate 0.80 < 0.90 → 计入；accuracy_rate 0.92 ≥ 0.90 不计
        assert "平稳率" in plants[1]["lose_factors"]
        assert "准确率" not in plants[1]["lose_factors"]

    def test_无KPI行返回空(self):
        plants = shape_plants([], self._hierarchy(), {}, {}, 0.90)
        assert plants == []

    def test_未知装置用占位名(self):
        hierarchy = self._hierarchy()
        kpi = [_win_row("24h", 60.0, {})]
        kpi[0].scope_id = 999
        plants = shape_plants(kpi, hierarchy, {}, {}, 0.90)
        assert plants[0]["name"].startswith("装置#")


# ===========================================================================
# shape_units
# ===========================================================================


class TestShapeUnits:
    def test_6指标塑造缺值None(self):
        hierarchy = {"name_by_source_id": {20: "单元B"}}
        row = _win_row("24h", 66.0, {"good_value_rate": 0.88})
        row.scope_id = 20
        units = shape_units([row], hierarchy)
        assert units[0]["name"] == "单元B"
        assert units[0]["score"] == 66.0
        assert units[0]["metrics"]["good_value_rate"] == 0.88
        assert units[0]["metrics"]["fast_rate"] is None  # 前端 N/A 斜纹

    def test_空输入(self):
        assert shape_units([], {"name_by_source_id": {}}) == []


# ===========================================================================
# shape_pareto
# ===========================================================================


class TestShapePareto:
    def test_字段完整映射(self):
        rows = [
            {
                "root_cause": "OSCILLATION",
                "tag_count": 12,
                "converted_count": 3,
                "ignored_count": 1,
                "sla_warned_count": 2,
            },
        ]
        out = shape_pareto(rows)
        assert out[0]["root_cause"] == "OSCILLATION"
        assert out[0]["tag_count"] == 12
        assert out[0]["converted_count"] == 3
        assert out[0]["ignored_count"] == 1
        assert out[0]["sla_warned_count"] == 2

    def test_空值兜底0(self):
        out = shape_pareto([{"root_cause": "X"}])
        assert out[0]["tag_count"] == 0


# ===========================================================================
# shape_roots
# ===========================================================================


class TestShapeRoots:
    """A3 迁 v2：symptom_tags 标签聚合行 → 中文 tag_name + 四档 severity。"""

    def test_中文tag_name与severity四档(self):
        row = {
            "tag_code": "OSCILLATION",
            "count": 10,
            "active_count": 5,
            "severity_rank": 4,
        }
        out = shape_roots([row], top_n=10)
        assert out[0]["tag_code"] == "OSCILLATION"
        assert out[0]["tag_name"] == "回路振荡"  # symptom_label 中文映射
        assert out[0]["severity"] == "CRITICAL"  # HIGH→rank4
        assert out[0]["count"] == 10
        assert out[0]["active_count"] == 5

    def test_severity_rank域为v2三档(self):
        """v2 经 severity_to_legacy 无 ERROR 档：rank 2→WARN、1→INFO。"""
        rows = [
            {"tag_code": "VALVE_STICTION", "count": 3, "active_count": 1, "severity_rank": 2},
            {"tag_code": "OUTPUT_SATURATION", "count": 2, "active_count": 2, "severity_rank": 1},
        ]
        out = shape_roots(rows, top_n=10)
        sev = {r["tag_code"]: r["severity"] for r in out}
        assert sev == {"VALVE_STICTION": "WARN", "OUTPUT_SATURATION": "INFO"}
        # PENDING 子计数 ≤ 检出总数
        assert all(r["active_count"] <= r["count"] for r in out)

    def test_active优先排序(self):
        r1 = {"tag_code": "A", "count": 8, "active_count": 1, "severity_rank": 2}
        r2 = {"tag_code": "B", "count": 5, "active_count": 4, "severity_rank": 3}
        out = shape_roots([r1, r2], top_n=10)
        assert out[0]["tag_code"] == "B"  # active 多的优先

    def test_topN截断(self):
        rows = [
            {"tag_code": f"T{i}", "count": i, "active_count": 0, "severity_rank": 1}
            for i in range(15)
        ]
        out = shape_roots(rows, top_n=10)
        assert len(out) == 10


# ===========================================================================
# shape_funnel
# ===========================================================================


class TestShapeFunnel:
    def test_4泳道加超期完整(self):
        out = shape_funnel(
            {
                "pending_count": 5,
                "executing_count": 3,
                "verifying_count": 2,
                "closed_count": 10,
                "reopened_count": 1,
                "breached_count": 2,
                "avg_cycle_hours": 12.5,
            }
        )
        assert out["pending"] == 5
        assert out["executing"] == 3
        assert out["verifying"] == 2
        assert out["closed"] == 10
        assert out["breached"] == 2
        assert out["avg_cycle_hours"] == 12.5

    def test_空行返回None(self):
        assert shape_funnel(None) is None
        assert shape_funnel({}) is None


# ===========================================================================
# build_overview 编排（patch helpers，不依赖真实 PG）
# ===========================================================================


class TestBuildOverview:
    @pytest.mark.asyncio
    async def test_六块组装且部分失败容错(self):
        db = AsyncMock()
        hierarchy = {
            "by_id": {},
            "unit_to_factory": {},
            "name_by_source_id": {10: "装置A"},
            "factories": [_plant_node("f1", "装置A", "FACTORY", source_id=10)],
        }
        plant_kpi = _win_row("24h", 88.0, {"steady_rate": 0.80})
        plant_kpi.scope_id = 10
        unit_kpi = _win_row("24h", 66.0, {"good_value_rate": 0.88})
        unit_kpi.scope_id = 20

        with (
            patch(
                "app.services.workbench_overview._query_windows",
                AsyncMock(return_value=[_win_row("24h", 82.0, {"good_value_rate": 0.95})]),
            ),
            patch(
                "app.services.workbench_overview._get_lose_threshold",
                AsyncMock(return_value=0.90),
            ),
            patch(
                "app.services.workbench_overview._load_plant_hierarchy",
                AsyncMock(return_value=hierarchy),
            ),
            patch(
                "app.services.workbench_overview._query_scope_rows",
                AsyncMock(side_effect=[[plant_kpi], [unit_kpi]]),
            ),
            patch(
                "app.services.workbench_overview._query_alarm_per_unit",
                AsyncMock(return_value={}),
            ),
            patch(
                "app.services.workbench_overview._query_overdue_per_unit",
                AsyncMock(return_value={}),
            ),
            patch(
                "app.services.workbench_overview._query_pareto",
                AsyncMock(return_value=[{"root_cause": "OSCILLATION", "tag_count": 7}]),
            ),
            patch(
                "app.services.workbench_overview._query_roots",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.workbench_overview._query_funnel",
                AsyncMock(side_effect=RuntimeError("MV 不可读")),  # 模拟单块失败
            ),
        ):
            data = await build_overview(db, scope_type="GLOBAL", scope_id=None, window="24h")

        # 六块顶层键齐全
        assert set(data.keys()) == {
            "scope",
            "window",
            "windows",
            "plants",
            "units",
            "pareto",
            "roots",
            "funnel",
        }
        assert data["scope"] == {"type": "GLOBAL", "id": None}
        assert data["window"] == "24h"
        # windows 塑造
        assert data["windows"]["24h"]["score"] == 82.0
        assert data["windows"]["7d"] is None
        # plants 排名
        assert data["plants"][0]["name"] == "装置A"
        assert data["plants"][0]["score"] == 88.0
        assert "平稳率" in data["plants"][0]["lose_factors"]
        # units
        assert data["units"][0]["score"] == 66.0
        assert data["units"][0]["metrics"]["good_value_rate"] == 0.88
        # pareto
        assert data["pareto"][0]["root_cause"] == "OSCILLATION"
        # funnel 单块失败 → None，不阻断其余块
        assert data["funnel"] is None

    @pytest.mark.asyncio
    async def test_全局scope_id归零(self):
        """GLOBAL scope_id=None → 内部传 0。"""
        db = AsyncMock()
        captured = {}

        async def _capture_windows(_db, scope_type, scope_id):
            captured["scope_type"] = scope_type
            captured["scope_id"] = scope_id
            return []

        with (
            patch(
                "app.services.workbench_overview._query_windows",
                AsyncMock(side_effect=_capture_windows),
            ),
            patch(
                "app.services.workbench_overview._get_lose_threshold", AsyncMock(return_value=0.90)
            ),
            patch(
                "app.services.workbench_overview._load_plant_hierarchy",
                AsyncMock(
                    return_value={
                        "by_id": {},
                        "unit_to_factory": {},
                        "name_by_source_id": {},
                        "factories": [],
                    }
                ),
            ),
            patch("app.services.workbench_overview._query_scope_rows", AsyncMock(return_value=[])),
            patch(
                "app.services.workbench_overview._query_alarm_per_unit", AsyncMock(return_value={})
            ),
            patch(
                "app.services.workbench_overview._query_overdue_per_unit",
                AsyncMock(return_value={}),
            ),
            patch("app.services.workbench_overview._query_pareto", AsyncMock(return_value=[])),
            patch("app.services.workbench_overview._query_roots", AsyncMock(return_value=[])),
            patch("app.services.workbench_overview._query_funnel", AsyncMock(return_value=None)),
        ):
            await build_overview(db, scope_type="GLOBAL", scope_id=None, window="24h")
        assert captured["scope_type"] == "GLOBAL"
        assert captured["scope_id"] == 0


# ===========================================================================
# v2 查询 helper（A3：alarm_count 流量口径 + roots symptom_tags 聚合）
# ===========================================================================


def _mock_db(rows: list) -> AsyncMock:
    """AsyncSession mock：execute 返回 rows（支持 tuple 或带 _mapping 的 row 对象）。"""
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.all.return_value = rows
    return db


class TestQueryAlarmPerUnit:
    @pytest.mark.asyncio
    async def test_近24h去重回路数按unit聚合(self):
        """D3=a 流量口径：count(DISTINCT loop_id)、primary_category 非 NULL、since ≈ now-24h。"""
        from app.services.workbench_overview import _query_alarm_per_unit

        db = _mock_db([("u1", 3), ("u2", 1)])
        out = await _query_alarm_per_unit(db)
        assert out == {"u1": 3, "u2": 1}

        sql = str(db.execute.call_args[0][0])
        assert "count(DISTINCT r.loop_id)" in sql  # 去重回路数（多次 run 计 1）
        assert "primary_category IS NOT NULL" in sql
        assert "diagnosis_run" in sql
        assert "diagnosis_tag" not in sql  # 旧引擎表停读

        since = db.execute.call_args[0][1]["since"]
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        assert (now_utc - since).total_seconds() / 3600 == pytest.approx(24, abs=1)
        assert since.tzinfo is None  # naive UTC（PG 会话时区 +8）

    @pytest.mark.asyncio
    async def test_空unit跳过(self):
        from app.services.workbench_overview import _query_alarm_per_unit

        db = _mock_db([(None, 2)])
        assert await _query_alarm_per_unit(db) == {}


class TestQueryRoots:
    @pytest.mark.asyncio
    async def test_symptom_tags展开与PENDING子计数(self):
        """近 30d detected=true 聚合；active_count = review_status='PENDING' 的 run 数。"""
        from app.services.workbench_overview import _query_roots

        row = MagicMock()
        row._mapping = {
            "tag_code": "OSCILLATION",
            "count": 5,
            "active_count": 2,
            "severity_rank": 4,
        }
        db = _mock_db([row])
        out = await _query_roots(db, top_n=10)
        assert out == [row._mapping]

        sql = str(db.execute.call_args[0][0])
        assert "jsonb_each" in sql  # symptom_tags JSONB 展开
        assert "kv.value->>'detected' = 'true'" in sql
        assert "r.review_status = 'PENDING'" in sql  # active_count 口径
        assert "diagnosis_tag" not in sql
        assert "LIMIT :top_n" in sql

        params = db.execute.call_args[0][1]
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        assert (now_utc - params["since"]).total_seconds() / 3600 == pytest.approx(720, abs=1)
        assert params["since"].tzinfo is None
        assert params["top_n"] == 10
