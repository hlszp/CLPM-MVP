"""P5 批 α 容量工具修正单测（kpi_capacity_benchmark.py 重写）.

覆盖（P0-03 §3.4 修复项 + 任务书批 α 核验）：
- 分页参数名 pageSize / 逐页拉全量 / 去重（原 ``size`` 被静默忽略的缺陷）
- 冻结窗口解析（整点校验）
- standard-hourly 前置三条件计数断言（不足即拒绝，禁止重放凑数）
- dry-run 不投递任何任务
- custom 模式少 ID 重放需显式 --allow-replay（独立单列，不计标准 SLO）
"""

from __future__ import annotations

import argparse
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.kpi_capacity_benchmark import (
    _frozen_window,
    _load_loops,
    _replay_windows,
    run_standard_hourly,
)


def _httpx_page(items: list[dict], total: int) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": {"items": items, "total": total}}
    return resp


class TestLoadLoopsPagination:
    """P0-03 §3.4-1/3：pageSize 参数名 + 逐页拉全量 + 去重。"""

    def test_使用pageSize参数并翻页拉全量(self):
        client = MagicMock()
        page1 = _httpx_page([{"loopId": f"L{i}"} for i in range(100)], total=150)
        page2 = _httpx_page([{"loopId": f"L{i}"} for i in range(100, 150)], total=150)
        client.get.side_effect = [page1, page2]

        ids = _load_loops(client, "http://x/api/v1", {}, page_size=100)

        assert len(ids) == 150
        params_used = [c.kwargs["params"] for c in client.get.call_args_list]
        # 修复点：参数名必须是 pageSize（原 size 会被 API 静默忽略回退默认 20）
        assert all(p["pageSize"] == 100 for p in params_used)
        assert [p["page"] for p in params_used] == [1, 2]

    def test_total满足即停不再空页探测(self):
        client = MagicMock()
        page1 = _httpx_page([{"loopId": "L1"}, {"loopId": "L2"}], total=2)
        client.get.side_effect = [page1]

        ids = _load_loops(client, "http://x/api/v1", {}, page_size=100)
        assert ids == ["L1", "L2"]
        assert client.get.call_count == 1

    def test_跨页重复loopId去重(self):
        client = MagicMock()
        page1 = _httpx_page([{"loopId": "L1"}, {"loopId": "L2"}], total=3)
        page2 = _httpx_page([{"loopId": "L2"}, {"loopId": "L3"}], total=3)
        client.get.side_effect = [page1, page2]

        ids = _load_loops(client, "http://x/api/v1", {}, page_size=2)
        assert ids == ["L1", "L2", "L3"]


class TestFrozenWindow:
    def test_缺省取上一个完整小时(self):
        win = _frozen_window(None)
        assert win.minute == 0 and win.second == 0 and win.microsecond == 0

    def test_显式ISO解析为naiveUTC(self):
        win = _frozen_window("2026-10-13T08:00:00Z")
        assert win == datetime(2026, 10, 13, 8, 0, 0)
        assert win.tzinfo is None

    def test_非整点拒绝(self):
        with pytest.raises(SystemExit, match="整点"):
            _frozen_window("2026-10-13T08:30:00")


def _sh_args(**kw) -> argparse.Namespace:
    base: dict = {
        "mode": "standard-hourly",
        "base_url": "http://localhost:17101/api/v1",
        "username": "admin",
        "password": "admin123",
        "loops": 1000,
        "ts_start": "2026-10-13T08:00:00",
        "expect_eligible": 1000,
        "barrier_loop_prefix": "PERF-",
        "timeout": 60.0,
        "slo_seconds": 600.0,
        "poll_interval": 1.0,
        "page_size": 100,
        "retry_seconds": 60.0,
        "batch_spacing": 2.0,
        "allow_replay": False,
        "dry_run": True,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestStandardHourlyPreflight:
    @pytest.mark.asyncio
    async def test_三条件计数不足即拒绝_禁止重放凑数(self):
        with patch(
            "scripts.kpi_capacity_benchmark._preflight_eligible",
            AsyncMock(return_value=(["L1", "L2"], 2)),
        ):
            with pytest.raises(SystemExit, match="禁止重放凑数"):
                await run_standard_hourly(_sh_args())

    @pytest.mark.asyncio
    async def test_dry_run通过且不投递任务(self, capsys):
        eligible = [f"PERF-L{i}" for i in range(1000)]
        barrier0 = {
            "ledger_rows": 0,
            "latest_rows": 0,
            "node_rows": 0,
            "predicted_node_count": 0,
            "source_breakdown": {},
            "foreign_source_rows": 0,
        }
        submit_spy = MagicMock()
        with (
            patch(
                "scripts.kpi_capacity_benchmark._preflight_eligible",
                AsyncMock(return_value=(eligible, 1000)),
            ),
            patch(
                "scripts.kpi_capacity_benchmark._barrier_snapshot",
                AsyncMock(return_value=barrier0),
            ),
            patch("app.tasks.kpi_calc.calculate_hourly_kpi", submit_spy),
        ):
            rc = await run_standard_hourly(_sh_args(dry_run=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "独立 loopId 断言: 1000 unique" in out
        assert "[dry-run] 计划" in out
        submit_spy.delay.assert_not_called()  # dry-run 零投递零写入

    @pytest.mark.asyncio
    async def test_屏障SQL条件常量(self):
        from scripts.kpi_capacity_benchmark import _ALLOWED_SOURCES, _PREDICTED_NODES_SQL

        assert set(_ALLOWED_SOURCES) == {"SCHEDULED", "MANUAL_STANDARD"}
        # 预定节点预测：is_kpi_enabled 祖先 × 子树回路 × 本窗快照
        assert "is_kpi_enabled" in _PREDICTED_NODES_SQL
        assert "kpi_snapshot_hourly" in _PREDICTED_NODES_SQL
        assert "RECURSIVE" in _PREDICTED_NODES_SQL


class TestCustomReplayGuard:
    def test_replay窗口切分(self):
        batches = _replay_windows(10, ["A", "B", "C"])
        assert [len(b[1]) for b in batches] == [3, 3, 3, 1]

    @pytest.mark.asyncio
    async def test_少ID重放未显式allow即拒绝(self):
        import scripts.kpi_capacity_benchmark as bench

        login_resp = MagicMock()
        login_resp.raise_for_status = MagicMock()
        login_resp.json.return_value = {"data": {"accessToken": "t"}}
        mock_client = MagicMock()
        mock_client.post.return_value = login_resp
        ctx = MagicMock()
        ctx.__enter__.return_value = mock_client
        ctx.__exit__.return_value = False
        with (
            patch.object(bench.httpx, "Client", MagicMock(return_value=ctx)),
            patch.object(bench, "_load_loops", MagicMock(return_value=["A", "B"])),
        ):
            with pytest.raises(SystemExit, match="--allow-replay"):
                bench.run_custom(
                    _sh_args(mode="custom", loops=1000, allow_replay=False, dry_run=False)
                )
