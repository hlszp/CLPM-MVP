"""评估性能总览改版（2026-10-03）新增能力单元测试。

覆盖：
- get_valve_alerts：阀门越限判定/severity 排序/total 与 limit 截断
- GET /dashboard/board/tree：树形聚合组树/无快照节点空值/totalLoops 填充
- _get_board_trend_data：granularity=auto 粒度选择 + 完整桶边界对齐
  （首桶向上取整、末桶=上一完整小时；day 粒度止于昨日北京日）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints.dashboard import (
    _get_board_trend_data,
    get_board_tree_endpoint,
)
from app.services.performance import get_valve_alerts


def _rows_result(rows: list, *, scalars: bool = False) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    if scalars:
        scalars_result = MagicMock()
        scalars_result.all.return_value = rows
        result.scalars.return_value = scalars_result
    return result


# ---------------------------------------------------------------------------
# get_valve_alerts
# ---------------------------------------------------------------------------


def _valve_row(
    loop_id: str,
    lo: Decimal | None,
    hi: Decimal | None,
    tag: str = "FIC-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        loop_id=loop_id,
        valve_op_min=lo,
        valve_op_max=hi,
        tag_name=tag,
        loop_name=None,
    )


class TestGetValveAlerts:
    """阀门越限聚合：判定、排序、截断。"""

    async def test_filters_and_severity_order(self) -> None:
        rows = [
            # min=2 越下限，severity = 5-2 = 3
            _valve_row("l1", Decimal("2.00"), Decimal("50.00"), "FIC-A"),
            # max=96 越上限，severity = 96-95 = 1
            _valve_row("l2", Decimal("10.00"), Decimal("96.00"), "FIC-B"),
            # 区间内 → 剔除
            _valve_row("l3", Decimal("10.00"), Decimal("50.00"), "FIC-C"),
            # valve 值缺失 → 剔除
            _valve_row("l4", None, Decimal("50.00"), "FIC-D"),
            # 双边越限取更深侧：min=0/max=99 → severity = max(5, 4) = 5
            _valve_row("l5", Decimal("0.00"), Decimal("99.00"), "FIC-E"),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_rows_result(rows))

        data = await get_valve_alerts(db, limit=10)

        assert data["total"] == 3
        assert [it["loopId"] for it in data["items"]] == ["l5", "l1", "l2"]
        assert data["items"][0]["severity"] == 5.0
        assert data["items"][1]["severity"] == 3.0
        assert data["items"][2]["severity"] == 1.0

    async def test_limit_truncates_items_but_not_total(self) -> None:
        rows = [
            _valve_row("l1", Decimal("1.00"), Decimal("50.00"), "FIC-A"),
            _valve_row("l2", Decimal("2.00"), Decimal("50.00"), "FIC-B"),
            _valve_row("l3", Decimal("3.00"), Decimal("50.00"), "FIC-C"),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_rows_result(rows))

        data = await get_valve_alerts(db, limit=2)

        assert data["total"] == 3
        assert len(data["items"]) == 2
        # severity 降序：l1(4) → l2(3)
        assert [it["loopId"] for it in data["items"]] == ["l1", "l2"]


# ---------------------------------------------------------------------------
# GET /dashboard/board/tree
# ---------------------------------------------------------------------------


def _node(node_id: str, parent_id: str | None, name: str, type_: str) -> MagicMock:
    n = MagicMock()
    n.id = node_id
    n.parent_id = parent_id
    n.name = name
    n.type = type_
    return n


class TestBoardTree:
    """树形聚合端点：组树、无快照空值、回路数填充。"""

    async def test_tree_structure_and_empty_snapshot(self) -> None:
        factory = _node("root", None, "全厂", "FACTORY")
        unit1 = _node("u1", "root", "一装置", "UNIT")
        unit2 = _node("u2", "root", "二装置", "UNIT")
        nodes = [factory, unit1, unit2]

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _rows_result(nodes, scalars=True),  # select(PlantNode)
                _rows_result([]),  # _load_window_items: latest subq（无快照）
                _rows_result([]),  # _load_window_items: 加权和
                _rows_result([]),  # batch_collect_descendant_loop_ids CTE
            ]
        )

        resp = await get_board_tree_endpoint(
            plantId=None, timeWindow="today", db=db, user=MagicMock()
        )
        data = resp["data"]

        assert data["total"] == 3
        assert len(data["items"]) == 1  # 单根
        root = data["items"][0]
        assert root["nodeId"] == "root"
        assert root["nodeType"] == "FACTORY"
        assert root["hasSnapshot"] is False
        assert root["avgScore"] is None  # 无快照 → null（前端显示 —）
        assert {c["nodeId"] for c in root["children"]} == {"u1", "u2"}

    async def test_total_loops_filled_from_batch_counts(self) -> None:
        factory = _node("root", None, "全厂", "FACTORY")
        unit1 = _node("u1", "root", "一装置", "UNIT")
        nodes = [factory, unit1]
        # batch CTE 行：(root_id, loop_id)
        cte_rows = [
            SimpleNamespace(root_id="root", loop_id="l1"),
            SimpleNamespace(root_id="root", loop_id="l2"),
            SimpleNamespace(root_id="u1", loop_id="l1"),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _rows_result(nodes, scalars=True),
                _rows_result([]),
                _rows_result([]),
                _rows_result(cte_rows),
            ]
        )

        resp = await get_board_tree_endpoint(
            plantId=None, timeWindow="today", db=db, user=MagicMock()
        )
        data = resp["data"]

        root = data["items"][0]
        unit = root["children"][0]
        assert root["totalLoops"] == 2  # 子树回路数（l1+l2）
        assert unit["totalLoops"] == 1  # 仅 l1 归属 u1


# ---------------------------------------------------------------------------
# _get_board_trend_data：granularity + 完整桶
# ---------------------------------------------------------------------------


def _trend_db(unit_rows: list, loop_rows: list, agg_rows: list) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _rows_result(unit_rows),  # UNIT 节点
            _rows_result(loop_rows),  # 全厂活跃回路
            _rows_result(agg_rows),  # 桶聚合
        ]
    )
    return db


class TestBoardTrendGranularity:
    """粒度选择与完整桶边界。"""

    async def test_today_uses_hour_and_ends_at_last_complete_hour(self) -> None:
        db = _trend_db(
            unit_rows=[SimpleNamespace(id="u1")],
            loop_rows=[("l1",), ("l2")],
            agg_rows=[],
        )
        resp = await _get_board_trend_data(db, None, "today", None, None, "auto")
        data = resp["data"]

        now = datetime.now(UTC).replace(tzinfo=None)
        assert data["granularity"] == "hour"
        n = len(data["timestamps"])
        # 滚动 24h：完整小时桶 23~24 个（末桶恒为 floor(now)-1h）
        assert 23 <= n <= 24
        # 每个桶都是整点格式（分钟/秒恒为 00）
        assert all(ts[14:] == "00:00" for ts in data["timestamps"])
        # 末桶 = floor(now)-1h（进行中的小时必无快照，出桶必空——2026-10-03 修复）
        last_bucket = datetime.strptime(data["timestamps"][-1], "%Y-%m-%dT%H:%M:%S")
        assert last_bucket <= now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        # 空数据桶：参评 0、率值 null
        assert data["evaluatedLoops"] == [0] * n
        assert data["avgScore"] == [None] * n

    async def test_last_7_days_auto_switches_to_day(self) -> None:
        db = _trend_db(
            unit_rows=[SimpleNamespace(id="u1")],
            loop_rows=[("l1",)],
            agg_rows=[],
        )
        resp = await _get_board_trend_data(db, None, "last_7_days", None, None, "auto")
        data = resp["data"]

        assert data["granularity"] == "day"
        # 日粒度：北京日字符串、序列止于昨日
        now_cst = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=8)
        yesterday = (now_cst.date() - timedelta(days=1)).isoformat()
        assert data["timestamps"][-1] == yesterday
        # 7 天窗口 → 完整日 6~7 个（首尾残日丢弃）
        assert 6 <= len(data["timestamps"]) <= 7

    async def test_explicit_hour_granularity_respected(self) -> None:
        db = _trend_db(
            unit_rows=[SimpleNamespace(id="u1")],
            loop_rows=[("l1",)],
            agg_rows=[],
        )
        resp = await _get_board_trend_data(db, None, "last_30_days", None, None, "hour")
        data = resp["data"]

        assert data["granularity"] == "hour"
        # 30 天小时粒度 ≈ 720 个完整桶
        assert 718 <= len(data["timestamps"]) <= 720
