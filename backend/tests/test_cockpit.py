"""驾驶舱聚合 service 单测（11 号方案 §10，C1 批次）.

覆盖：
- 纯函数：parse_backend_roles（默认值回退/解析）/ shape_node_tree（三层聚合）
- build_overview 编排：patch 各 _query_* helper 返回种子数据，断言组装与部分失败容错
  （对齐 test_workbench_overview 的 patch 范式，不依赖真实 PG）
- build_backend_access_roles / build_node_tree：mock AsyncSession
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.cockpit_overview import (
    DEFAULT_BACKEND_ROLES,
    GRADE_KEYS,
    build_backend_access_roles,
    build_node_tree,
    build_overview,
    parse_backend_roles,
    shape_node_tree,
)

# ---------------------------------------------------------------------------
# 合成行构造
# ---------------------------------------------------------------------------


def _plant_node(node_id: str, name: str, ntype: str, parent_id=None, source_id=None, sort=0):
    n = MagicMock()
    n.id = node_id
    n.name = name
    n.type = ntype
    n.parent_id = parent_id
    n.source_node_id = source_id
    n.sort_order = sort
    return n


def _summary_row(score: float, auto_rate: float, loop_count: int):
    row = MagicMock()
    row.score = score
    row.auto_mode_rate = auto_rate
    row.loop_count = loop_count
    return row


# ===========================================================================
# parse_backend_roles
# ===========================================================================


class TestParseBackendRoles:
    def test_缺失或为空回退默认(self):
        assert parse_backend_roles(None) == list(DEFAULT_BACKEND_ROLES)
        assert parse_backend_roles("") == list(DEFAULT_BACKEND_ROLES)
        assert parse_backend_roles("   ") == list(DEFAULT_BACKEND_ROLES)
        assert parse_backend_roles(" , ,") == list(DEFAULT_BACKEND_ROLES)

    def test_正常解析去空白去空项(self):
        assert parse_backend_roles("IC_ENGINEER, PE_ENGINEER ,,ADMIN") == [
            "IC_ENGINEER",
            "PE_ENGINEER",
            "ADMIN",
        ]

    def test_单角色(self):
        assert parse_backend_roles("ADMIN") == ["ADMIN"]


# ===========================================================================
# shape_node_tree
# ===========================================================================


class TestShapeNodeTree:
    def _nodes(self):
        return [
            _plant_node("f1", "工厂A", "FACTORY", source_id=1),
            _plant_node("a1", "装置A1", "AREA", parent_id="f1", source_id=11),
            _plant_node("u1", "单元A1a", "UNIT", parent_id="a1", source_id=111),
            _plant_node("u2", "单元A1b", "UNIT", parent_id="a1", source_id=112),
            _plant_node("f2", "工厂B", "FACTORY", source_id=2),
        ]

    def test_三层聚合与字段(self):
        tree = shape_node_tree(self._nodes(), {"u1": 5, "u2": 3})
        assert len(tree) == 2
        factory = next(r for r in tree if r["nodeId"] == "f1")
        assert factory["id"] == 1
        assert factory["type"] == "FACTORY"
        assert factory["loopCount"] == 8  # 5 + 3 向上累加
        area = factory["children"][0]
        assert area["type"] == "AREA"
        assert area["loopCount"] == 8
        units = {c["nodeId"]: c for c in area["children"]}
        assert units["u1"]["loopCount"] == 5
        assert units["u2"]["loopCount"] == 3
        # 无回路工厂 → 0
        assert next(r for r in tree if r["nodeId"] == "f2")["loopCount"] == 0

    def test_同级排序sort_order优先再按名称(self):
        nodes = [
            _plant_node("f1", "工厂B", "FACTORY", sort=2),
            _plant_node("f2", "工厂A", "FACTORY", sort=1),
            _plant_node("f3", "工厂C", "FACTORY", sort=1),
        ]
        tree = shape_node_tree(nodes, {})
        assert [r["name"] for r in tree] == ["工厂A", "工厂C", "工厂B"]

    def test_父节点缺失按根节点处理(self):
        nodes = [
            _plant_node("u1", "孤儿单元", "UNIT", parent_id="missing", source_id=9),
        ]
        tree = shape_node_tree(nodes, {"u1": 2})
        assert len(tree) == 1
        assert tree[0]["loopCount"] == 2
        assert tree[0]["id"] == 9

    def test_空输入返回空(self):
        assert shape_node_tree([], {}) == []


# ===========================================================================
# build_overview 编排（patch helpers，不依赖真实 PG）
# ===========================================================================


def _overview_patches(**overrides):
    """build_overview 全部 helper 的默认 patch 集合（可被 overrides 覆盖）。"""
    patches = {
        "_query_global_summary_row": AsyncMock(return_value=_summary_row(82.0, 0.91, 120)),
        "_query_window_weighted_avg": AsyncMock(
            side_effect=[
                {"score": 80.0, "auto_rate": 90.0},  # 当前窗口（unit_kpi_summary 为 0-100 标度）
                {"score": 78.0, "auto_rate": 88.0},  # 上一窗口
            ]
        ),
        "_query_todo_counts": AsyncMock(return_value={"pending": 7, "overdue": 2}),
        "_query_alert_counts": AsyncMock(return_value={"active": 3, "unconfirmed": 5}),
        "_query_funnel_counts": AsyncMock(
            return_value={"discovered": 10, "diagnosed": 8, "tuned": 4, "closed": 6}
        ),
        "_query_backlog": AsyncMock(return_value={"pending": 4, "inProgress": 2, "verifying": 1}),
    }
    patches.update(overrides)
    return patches


class TestBuildOverview:
    @pytest.mark.asyncio
    async def test_kpi与漏斗组装完整(self):
        db = AsyncMock()
        # loopTotal 自 2026-09-04 起改为 loop_ledger 活跃回路实时计数（db.scalar）
        db.scalar = AsyncMock(return_value=120)
        dist = {"EXCELLENT": 10, "GOOD": 50, "FAIR": 40, "WARNING": 12, "POOR": 8, "total": 120}
        prev_dist = {"WARNING": 10, "POOR": 5}
        with (
            patch.multiple("app.services.cockpit_overview", **_overview_patches()),
            patch(
                "app.services.performance.get_grade_distribution",
                AsyncMock(side_effect=[dist, prev_dist]),
            ),
        ):
            data = await build_overview(db, window="24h")

        assert data["window"] == "24h"
        kpi = data["kpi"]
        # 窗口行字段（autoRate 归一 0-100 标度：summary 表存 0-1 小数）
        assert kpi["score"] == 82.0
        assert kpi["autoRate"] == 91.0
        assert kpi["loopTotal"] == 120
        # 环比
        assert kpi["scoreDelta"] == 2.0
        assert kpi["autoRateDelta"] == 2.0
        # 等级分布（仅五档）+ 劣化
        assert set(kpi["gradeDistribution"].keys()) == set(GRADE_KEYS)
        assert kpi["gradeDistribution"]["WARNING"] == 12
        assert kpi["degradedCount"] == 20
        assert kpi["degradedDelta"] == 5  # 20 - 15
        # 待办 / 预警
        assert kpi["todoPending"] == 7
        assert kpi["todoOverdue"] == 2
        assert kpi["alertActive"] == 3
        assert kpi["alertUnconfirmed"] == 5
        # 漏斗
        funnel = data["funnel"]
        assert funnel["discovered"] == 10
        assert funnel["diagnosed"] == 8
        assert funnel["tuned"] == 4
        assert funnel["closed"] == 6
        assert funnel["backlog"] == {"pending": 4, "inProgress": 2, "verifying": 1}

    @pytest.mark.asyncio
    async def test_窗口行缺失时KPI为None且计数兜底0(self):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)  # 活跃回路计数 0
        with (
            patch.multiple(
                "app.services.cockpit_overview",
                **_overview_patches(_query_global_summary_row=AsyncMock(return_value=None)),
            ),
            patch(
                "app.services.performance.get_grade_distribution",
                AsyncMock(return_value={}),
            ),
        ):
            data = await build_overview(db, window="7d")
        kpi = data["kpi"]
        assert kpi["score"] is None
        assert kpi["autoRate"] is None
        assert kpi["loopTotal"] == 0
        assert kpi["degradedCount"] == 0
        assert kpi["degradedDelta"] == 0

    @pytest.mark.asyncio
    async def test_环比缺数据返回None(self):
        db = AsyncMock()
        with (
            patch.multiple(
                "app.services.cockpit_overview",
                **_overview_patches(
                    _query_window_weighted_avg=AsyncMock(
                        side_effect=[
                            {"score": 80.0, "auto_rate": None},
                            {"score": None, "auto_rate": 0.88},
                        ]
                    )
                ),
            ),
            patch(
                "app.services.performance.get_grade_distribution",
                AsyncMock(return_value={}),
            ),
        ):
            data = await build_overview(db, window="30d")
        assert data["kpi"]["scoreDelta"] is None
        assert data["kpi"]["autoRateDelta"] is None

    @pytest.mark.asyncio
    async def test_单块失败不阻断其余块(self):
        db = AsyncMock()
        with (
            patch.multiple(
                "app.services.cockpit_overview",
                **_overview_patches(
                    _query_todo_counts=AsyncMock(side_effect=RuntimeError("DB 抖动")),
                    _query_backlog=AsyncMock(side_effect=RuntimeError("DB 抖动")),
                ),
            ),
            patch(
                "app.services.performance.get_grade_distribution",
                AsyncMock(side_effect=RuntimeError("快照表不可读")),
            ),
        ):
            data = await build_overview(db, window="24h")
        kpi = data["kpi"]
        # 失败块回退默认
        assert kpi["todoPending"] == 0
        assert kpi["todoOverdue"] == 0
        assert kpi["gradeDistribution"] == dict.fromkeys(GRADE_KEYS, 0)
        assert kpi["degradedDelta"] is None
        assert data["funnel"]["backlog"] == {"pending": 0, "inProgress": 0, "verifying": 0}
        # 其余块不受影响
        assert kpi["score"] == 82.0
        assert kpi["alertActive"] == 3
        assert data["funnel"]["discovered"] == 10


# ===========================================================================
# build_backend_access_roles / build_node_tree（mock AsyncSession）
# ===========================================================================


def _mock_db_scalar(value):
    """AsyncSession mock：execute 返回 scalar_one_or_none=value。"""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db.execute.return_value = result
    return db


class TestBuildBackendAccessRoles:
    @pytest.mark.asyncio
    async def test_配置存在时按配置解析(self):
        row = MagicMock()
        row.value = "ADMIN, EXPERT"
        assert await build_backend_access_roles(_mock_db_scalar(row)) == {
            "roles": ["ADMIN", "EXPERT"]
        }

    @pytest.mark.asyncio
    async def test_配置缺失回退默认(self):
        assert await build_backend_access_roles(_mock_db_scalar(None)) == {
            "roles": list(DEFAULT_BACKEND_ROLES)
        }

    @pytest.mark.asyncio
    async def test_查询异常回退默认(self):
        db = AsyncMock()
        db.execute.side_effect = RuntimeError("sys_config 不可读")
        assert await build_backend_access_roles(db) == {"roles": list(DEFAULT_BACKEND_ROLES)}


class TestBuildNodeTree:
    @pytest.mark.asyncio
    async def test_聚合链路(self):
        nodes = [
            _plant_node("f1", "工厂A", "FACTORY", source_id=1),
            _plant_node("a1", "装置A1", "AREA", parent_id="f1", source_id=11),
            _plant_node("u1", "单元A1a", "UNIT", parent_id="a1", source_id=111),
        ]
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = nodes
        db.execute.return_value = result
        with patch(
            "app.services.cockpit_overview._query_loop_counts_per_unit",
            AsyncMock(return_value={"u1": 6}),
        ):
            tree = await build_node_tree(db)
        assert tree[0]["loopCount"] == 6
        assert tree[0]["children"][0]["loopCount"] == 6
        assert tree[0]["children"][0]["children"][0]["loopCount"] == 6


# ===========================================================================
# helper 级口径守护：窗口起点为 naive UTC
# ===========================================================================


class TestNaiveUtcWindows:
    @pytest.mark.asyncio
    async def test_加权均值查询窗口边界(self):
        from app.services.cockpit_overview import _query_window_weighted_avg

        db = AsyncMock()
        row = MagicMock()
        row.__getitem__ = lambda self, i: (800.0, 10, 4.5, 5)[i]
        result = MagicMock()
        result.one.return_value = row
        db.execute.return_value = result

        start = datetime.now(UTC).replace(tzinfo=None)
        out = await _query_window_weighted_avg(db, start, datetime.now(UTC).replace(tzinfo=None))
        assert out["score"] == 80.0  # 800/10
        assert out["auto_rate"] == 0.9  # 4.5/5

    @pytest.mark.asyncio
    async def test_分母为0返回None(self):
        from app.services.cockpit_overview import _query_window_weighted_avg

        db = AsyncMock()
        row = MagicMock()
        row.__getitem__ = lambda self, i: (None, 0, None, None)[i]
        result = MagicMock()
        result.one.return_value = row
        db.execute.return_value = result

        now = datetime.now(UTC).replace(tzinfo=None)
        out = await _query_window_weighted_avg(db, now, now)
        assert out == {"score": None, "auto_rate": None}
