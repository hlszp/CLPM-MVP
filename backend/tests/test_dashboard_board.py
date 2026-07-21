"""Dashboard board/aggregate timeWindow（P1 #5）接口级单元测试。

测试覆盖：
- _resolve_aggregate_window：各时间窗取值解析（含未知值回退）
- GET /dashboard/board/aggregate 缺省（无 timeWindow）：每节点最新快照，无窗口回显
- GET /dashboard/board/aggregate 窗口模式：rate 字段按 evaluated_loops 加权，
  计数字段取窗口内最新快照，响应回显 timeWindow/windowStart/windowEnd
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints.dashboard import (
    _resolve_aggregate_window,
    get_board_aggregate_endpoint,
)

# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_summary(
    node_id: str = "node-1",
    snapshot_time: datetime | None = None,
    avg_score: Decimal | None = Decimal("80.00"),
    evaluated_loops: int = 10,
) -> MagicMock:
    """构造 UnitKpiSummary mock（属性值为真实数值，供 float() 转换）。"""
    s = MagicMock()
    s.node_id = node_id
    s.snapshot_time = snapshot_time or datetime(2026, 7, 21, 8, 0, 0)
    s.avg_score = avg_score
    s.auto_mode_rate = Decimal("88.00")
    s.stability_rate = Decimal("85.00")
    s.effective_auto_rate = Decimal("82.00")
    s.accuracy_rate = Decimal("78.00")
    s.fast_rate = Decimal("75.00")
    s.good_value_rate = Decimal("96.00")
    s.oscillation_rate = Decimal("15.00")
    s.saturation_rate = Decimal("8.00")
    s.total_loops = 12
    s.evaluated_loops = evaluated_loops
    s.inconclusive_loops = 1
    s.excluded_loops = 1
    s.status = "SUCCESS"
    s.algorithm_version = "v6.1"
    return s


def _make_weighted_row(
    node_id: str = "node-1",
    eval_sum: int = 10,
    avg_score_sum: float = 800.0,
) -> MagicMock:
    """构造窗口加权和行 mock（字段为加权和，非均值）。"""
    row = MagicMock()
    row.nid = node_id
    row.avg_score = Decimal(str(avg_score_sum))
    row.auto_mode_rate = Decimal("880.0")
    row.stability_rate = Decimal("850.0")
    row.effective_auto_rate = Decimal("820.0")
    row.accuracy_rate = Decimal("780.0")
    row.fast_rate = Decimal("750.0")
    row.good_value_rate = Decimal("960.0")
    row.oscillation_rate = Decimal("150.0")
    row.saturation_rate = Decimal("80.0")
    row.eval_sum = eval_sum
    return row


def _all_rows(rows: list) -> MagicMock:
    """execute 返回值：支持 .all()。"""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _scalar(value: object) -> MagicMock:
    """execute 返回值：支持 .scalar()。"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_db(side_effects: list) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=side_effects)
    return db


# ---------------------------------------------------------------------------
# _resolve_aggregate_window
# ---------------------------------------------------------------------------


class TestResolveAggregateWindow:
    """时间窗解析测试。"""

    def test_none_returns_none(self) -> None:
        """缺省（None/空串）→ 不启用窗口。"""
        assert _resolve_aggregate_window(None) is None
        assert _resolve_aggregate_window("") is None

    def test_last_8_hours(self) -> None:
        start, end = _resolve_aggregate_window("last_8_hours")  # type: ignore[misc]
        assert timedelta(hours=7.9) < end - start < timedelta(hours=8.1)

    def test_today_is_24h(self) -> None:
        start, end = _resolve_aggregate_window("today")  # type: ignore[misc]
        assert timedelta(hours=23.9) < end - start < timedelta(hours=24.1)

    def test_yesterday(self) -> None:
        start, end = _resolve_aggregate_window("yesterday")  # type: ignore[misc]
        assert abs((end - start).total_seconds() - 86400) < 2
        # end 距现在约 1 天
        now = datetime.now(UTC).replace(tzinfo=None)
        assert abs((now - end).total_seconds() - 86400) < 2

    def test_last_7_days(self) -> None:
        start, end = _resolve_aggregate_window("last_7_days")  # type: ignore[misc]
        assert timedelta(days=6.9) < end - start < timedelta(days=7.1)

    def test_last_30_days(self) -> None:
        start, end = _resolve_aggregate_window("last_30_days")  # type: ignore[misc]
        assert timedelta(days=29.9) < end - start < timedelta(days=30.1)

    def test_unknown_falls_back_to_24h(self) -> None:
        start, end = _resolve_aggregate_window("bogus")  # type: ignore[misc]
        assert timedelta(hours=23.9) < end - start < timedelta(hours=24.1)


# ---------------------------------------------------------------------------
# board/aggregate 端点
# ---------------------------------------------------------------------------


class TestBoardAggregateEndpoint:
    """GET /dashboard/board/aggregate timeWindow 测试（直接调用端点函数）。"""

    async def test_default_latest_snapshot_no_window_echo(self) -> None:
        """缺省：每节点最新快照，响应无 timeWindow 回显。"""
        summary = _make_summary(avg_score=Decimal("80.00"))
        db = _make_db(
            [
                _all_rows([("loop-1",), ("loop-2",)]),  # 活跃回路
                _all_rows([MagicMock(id="node-1")]),  # 根节点
                _all_rows([(summary, "Node 1")]),  # 最新快照
                _scalar(0),  # excluded
                _all_rows([("loop-1",)]),  # SUCCESS 回路
                _scalar(0),  # inconclusive
            ]
        )

        resp = await get_board_aggregate_endpoint(
            plantId=None, timeWindow=None, db=db, user=MagicMock()
        )

        data = resp["data"]
        assert "timeWindow" not in data
        assert "windowStart" not in data
        assert len(data["items"]) == 1
        # 缺省取最新快照原值（非窗口加权）
        assert data["items"][0]["avgScore"] == 80.0
        assert data["items"][0]["snapshotTime"] == "2026-07-21T08:00:00"
        assert data["aggregate"]["avgScore"] == 80.0
        assert data["aggregate"]["totalLoops"] == 2
        assert data["aggregate"]["evaluatedLoops"] == 1

    async def test_window_mode_weighted_rates_and_echo(self) -> None:
        """窗口模式：rate 字段按 evaluated_loops 加权，响应回显窗口。"""
        # 最新快照 avg_score=90，但窗口加权和 800/10=80 → 验证走加权口径
        summary = _make_summary(avg_score=Decimal("90.00"), evaluated_loops=10)
        w_row = _make_weighted_row(eval_sum=10, avg_score_sum=800.0)
        db = _make_db(
            [
                _all_rows([("loop-1",), ("loop-2",)]),  # 活跃回路
                _all_rows([MagicMock(id="node-1")]),  # 根节点
                _all_rows([(summary, "Node 1")]),  # 窗口内最新快照
                _all_rows([w_row]),  # 窗口加权和
                _scalar(0),  # excluded
                _all_rows([("loop-1",), ("loop-2",)]),  # SUCCESS 回路
                _scalar(0),  # inconclusive
            ]
        )

        resp = await get_board_aggregate_endpoint(
            plantId=None, timeWindow="last_7_days", db=db, user=MagicMock()
        )

        data = resp["data"]
        # 窗口回显
        assert data["timeWindow"] == "last_7_days"
        assert "windowStart" in data
        assert "windowEnd" in data
        # rate 字段为窗口加权值（800/10=80），非最新快照值 90
        assert data["items"][0]["avgScore"] == 80.0
        assert data["items"][0]["autoModeRate"] == 88.0
        # 计数字段取窗口内最新快照
        assert data["items"][0]["totalLoops"] == 12
        assert data["items"][0]["evaluatedLoops"] == 10
        # 聚合值同样为窗口加权
        assert data["aggregate"]["avgScore"] == 80.0

    async def test_window_mode_empty_eval_sum_yields_null_rates(self) -> None:
        """窗口内 evaluated_loops 合计为 0 时 rate 字段返回 None。"""
        summary = _make_summary(evaluated_loops=0)
        w_row = _make_weighted_row(eval_sum=0, avg_score_sum=0.0)
        db = _make_db(
            [
                _all_rows([("loop-1",)]),
                _all_rows([MagicMock(id="node-1")]),
                _all_rows([(summary, "Node 1")]),
                _all_rows([w_row]),
                _scalar(0),
                _all_rows([]),
                _scalar(0),
            ]
        )

        resp = await get_board_aggregate_endpoint(
            plantId=None, timeWindow="today", db=db, user=MagicMock()
        )

        data = resp["data"]
        assert data["items"][0]["avgScore"] is None
        assert data["items"][0]["autoModeRate"] is None
