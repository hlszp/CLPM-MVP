"""GET /dashboard/governance-summary 接口级单元测试（装置总览管理者版治理聚合）。

测试覆盖：
- 基本返回结构：handling/funnel/badLoops 各字段与计数映射
- 空数据场景：全部计数为 0 时结构完整
- 时间窗：custom 起止解析与回显、custom 缺起止回退 last_24_hours
- 口径自洽：funnel.closed == handling.closedInWindow、
  funnel.discovered == badLoops.warning + badLoops.poor

风格对齐 test_dashboard_board.py：直接调用端点函数 + AsyncMock db；
grade-distribution 复用 ``app.services.performance.get_grade_distribution``，
测试中以 monkeypatch 替换（其自身已有独立测试覆盖）。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints import dashboard as dashboard_module
from app.api.v1.endpoints.dashboard import get_governance_summary_endpoint

# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _scalar(value: object) -> MagicMock:
    """execute 返回值：支持 .scalar()。"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_db(side_effects: list) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=side_effects)
    return db


def _fake_distribution(warning: int = 0, poor: int = 0) -> dict:
    """构造 get_grade_distribution 返回值（仅本端点读取的键需保证存在）。"""
    return {
        "EXCELLENT": 10,
        "GOOD": 8,
        "FAIR": 4,
        "WARNING": warning,
        "POOR": poor,
        "INCONCLUSIVE": 1,
        "total": 10 + 8 + 4 + warning + poor + 1,
        "fitnessDistribution": {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0, "total": 0},
    }


def _patch_distribution(monkeypatch, warning: int = 0, poor: int = 0) -> AsyncMock:
    fake = AsyncMock(return_value=_fake_distribution(warning=warning, poor=poor))
    monkeypatch.setattr(dashboard_module, "get_grade_distribution", fake)
    return fake


# 端点内 7 次计数查询的执行顺序：
# open_items / open_orders / overdue_orders / closed_in_window /
# diagnosed / planned_orders / planned_tunings
def _counts_db(
    open_items: int = 0,
    open_orders: int = 0,
    overdue_orders: int = 0,
    closed_in_window: int = 0,
    diagnosed: int = 0,
    planned_orders: int = 0,
    planned_tunings: int = 0,
) -> AsyncMock:
    return _make_db(
        [
            _scalar(open_items),
            _scalar(open_orders),
            _scalar(overdue_orders),
            _scalar(closed_in_window),
            _scalar(diagnosed),
            _scalar(planned_orders),
            _scalar(planned_tunings),
        ]
    )


# ---------------------------------------------------------------------------
# 基本返回结构
# ---------------------------------------------------------------------------


class TestGovernanceSummaryEndpoint:
    """GET /dashboard/governance-summary 基本结构与口径测试（直接调用端点函数）。"""

    async def test_basic_structure_and_counts(self, monkeypatch) -> None:
        """各区块字段齐全，计数正确映射到响应。"""
        _patch_distribution(monkeypatch, warning=3, poor=2)
        db = _counts_db(
            open_items=5,
            open_orders=4,
            overdue_orders=2,
            closed_in_window=6,
            diagnosed=7,
            planned_orders=8,
            planned_tunings=1,
        )

        resp = await get_governance_summary_endpoint(
            timeWindow="last_24_hours", startTime=None, endTime=None, db=db, user=MagicMock()
        )

        data = resp["data"]
        assert data.time_window == "last_24_hours"

        # handling：处置闭环计数
        assert data.handling.open_items == 5
        assert data.handling.open_orders == 4
        assert data.handling.overdue_orders == 2
        assert data.handling.closed_in_window == 6

        # funnel：discovered = warning + poor；closed = handling.closedInWindow
        assert data.funnel.discovered == 5
        assert data.funnel.diagnosed == 7
        assert data.funnel.planned == 9  # 处置工单 8 + 整定方案 1
        assert data.funnel.closed == 6
        assert data.funnel.closed == data.handling.closed_in_window
        assert data.funnel.discovered == data.bad_loops.warning + data.bad_loops.poor

        # badLoops：WARNING/POOR 档计数
        assert data.bad_loops.warning == 3
        assert data.bad_loops.poor == 2

    async def test_response_aliases_camel_case(self, monkeypatch) -> None:
        """序列化别名为 camelCase（与前端契约一致）。"""
        _patch_distribution(monkeypatch, warning=1, poor=1)
        db = _counts_db(open_items=1, closed_in_window=2)

        resp = await get_governance_summary_endpoint(
            timeWindow="last_24_hours", startTime=None, endTime=None, db=db, user=MagicMock()
        )

        dumped = resp["data"].model_dump(by_alias=True)
        assert set(dumped.keys()) == {"timeWindow", "handling", "funnel", "badLoops"}
        assert set(dumped["handling"].keys()) == {
            "openItems",
            "openOrders",
            "overdueOrders",
            "closedInWindow",
        }
        assert set(dumped["funnel"].keys()) == {"discovered", "diagnosed", "planned", "closed"}
        assert set(dumped["badLoops"].keys()) == {"warning", "poor"}

    async def test_empty_data_returns_zeros(self, monkeypatch) -> None:
        """空数据场景：全部计数为 0，结构完整。"""
        _patch_distribution(monkeypatch, warning=0, poor=0)
        db = _counts_db()

        resp = await get_governance_summary_endpoint(
            timeWindow="last_8_hours", startTime=None, endTime=None, db=db, user=MagicMock()
        )

        data = resp["data"]
        assert data.time_window == "last_8_hours"
        assert data.handling.open_items == 0
        assert data.handling.open_orders == 0
        assert data.handling.overdue_orders == 0
        assert data.handling.closed_in_window == 0
        assert data.funnel.discovered == 0
        assert data.funnel.diagnosed == 0
        assert data.funnel.planned == 0
        assert data.funnel.closed == 0
        assert data.bad_loops.warning == 0
        assert data.bad_loops.poor == 0

    async def test_grade_distribution_called_with_window(self, monkeypatch) -> None:
        """badLoops/discovered 复用 grade-distribution，且按时间窗边界取数。"""
        fake = _patch_distribution(monkeypatch, warning=2, poor=1)
        db = _counts_db()

        await get_governance_summary_endpoint(
            timeWindow="last_24_hours", startTime=None, endTime=None, db=db, user=MagicMock()
        )

        fake.assert_awaited_once()
        kwargs = fake.await_args.kwargs
        start, end = kwargs["start"], kwargs["end"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        # last_24_hours：窗口跨度约 24h（naive UTC）
        assert 23.9 < (end - start).total_seconds() / 3600 < 24.1

    async def test_custom_window_echo_and_parse(self, monkeypatch) -> None:
        """custom 窗口：解析 startTime/endTime 并回显 timeWindow=custom。"""
        fake = _patch_distribution(monkeypatch)
        db = _counts_db()

        resp = await get_governance_summary_endpoint(
            timeWindow="custom",
            startTime="2026-08-20T00:00:00",
            endTime="2026-08-21T00:00:00",
            db=db,
            user=MagicMock(),
        )

        assert resp["data"].time_window == "custom"
        kwargs = fake.await_args.kwargs
        assert kwargs["start"] == datetime(2026, 8, 20, 0, 0, 0)
        assert kwargs["end"] == datetime(2026, 8, 21, 0, 0, 0)

    async def test_custom_without_times_falls_back_to_last_24_hours(self, monkeypatch) -> None:
        """custom 缺起止时间：回退 last_24_hours（与 system-overview 同策略）。"""
        _patch_distribution(monkeypatch)
        db = _counts_db()

        resp = await get_governance_summary_endpoint(
            timeWindow="custom", startTime=None, endTime=None, db=db, user=MagicMock()
        )

        assert resp["data"].time_window == "last_24_hours"
