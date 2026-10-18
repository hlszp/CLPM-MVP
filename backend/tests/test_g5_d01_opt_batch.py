"""G5-D01 优化批回归（2026-10-16）：O2 批级预载计数 + G-c B1/B2/B3 取消边界.

依据：
- D01-归因报告 §三（O2：data_version 批级一次预载，1030 次逐回路解析 → 1 次）
- G5-Gc-CancelledError边界-裁决书 §1/§2/§3（B1 静默误收 / B2 汇总崩溃
  双跑 / B3 痕迹悬挂；修复形态=取消注入断言诚实计数、无双终态、无悬挂）

全部离线：不连共享 DB/Redis，monkeypatch 边界（resolve/ledger/tracker/redis）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.task import TaskStatus
from app.services.node_performance import (
    _classify_node_results,
    batch_calculate_and_save_node_snapshots,
)
from app.tasks.kpi_calc import (
    _attempt_log_wrap,
    _do_hourly_with_tracking,
    _run_batch_loop_calculations,
    _summarize_batch_results,
)

_VERSION = "mf-count:tqv1"


# ---------------------------------------------------------------------------
# O2：_run_batch_loop_calculations 批级一次解析（1030 → 1 的缩比断言）
# ---------------------------------------------------------------------------


def _make_loop(loop_id: str, tag_name: str) -> MagicMock:
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    return loop


class TestBatchDataVersionPreload:
    async def test_batch_resolves_data_version_once_for_all_loops(self, monkeypatch) -> None:
        """N 回路批：resolve_data_version 恰调用 1 次，且注入每个 DataPlanner."""
        calls = {"n": 0}

        async def counting_resolve() -> str:
            calls["n"] += 1
            return _VERSION

        monkeypatch.setattr("app.services.data_planner.resolve_data_version", counting_resolve)

        loops = [_make_loop(f"loop-{i}", f"T-10{i}") for i in range(3)]
        loop_configs = {str(lp.id): {"op_lower": None, "op_upper": None} for lp in loops}

        planners: list[MagicMock] = []

        def fake_build_planner(db, bundle_cache=None):
            planner = MagicMock()
            planners.append(planner)
            return planner

        fake_calc = AsyncMock(return_value={"status": "SUCCESS"})
        monkeypatch.setattr("app.tasks.kpi_calc._build_data_planner", fake_build_planner)
        monkeypatch.setattr("app.tasks.kpi_calc._calculate_loop_kpi", fake_calc)

        mock_session = AsyncMock()
        monkeypatch.setattr("app.core.db.AsyncSessionLocal", lambda: mock_session)
        monkeypatch.setattr("app.core.db.close_session_guarded", AsyncMock(return_value=None))

        now = datetime.now(UTC).replace(tzinfo=None)
        results = await _run_batch_loop_calculations(
            loops=loops,
            loop_configs=loop_configs,
            metric_configs={},
            ts_start=now,
            ts_end=now,
            type_weights=None,
        )

        assert calls["n"] == 1, f"批级预载后应恰解析 1 次（实测 {calls['n']}）"
        assert len(planners) == 3
        for planner in planners:
            assert planner._preloaded_data_version == _VERSION
            # 既有注入行为不变（OP 限位批级映射仍在：{loop_id: (lower, upper)}）
            assert planner._preloaded_op_limits == {str(lp.id): (None, None) for lp in loops}
        assert [r["status"] for r in results] == ["SUCCESS"] * 3

    async def test_prewarm_resolves_data_version_once(self, monkeypatch) -> None:
        """预热路径同口径：N 回路 1 次解析（custom 批预热段）."""
        calls = {"n": 0}

        async def counting_resolve() -> str:
            calls["n"] += 1
            return _VERSION

        monkeypatch.setattr("app.services.data_planner.resolve_data_version", counting_resolve)

        from app.tasks.kpi_calc import _prewarm_cache_for_loops

        loops = [_make_loop(f"loop-{i}", f"T-20{i}") for i in range(2)]
        loop_configs = {str(lp.id): {"op_lower": None, "op_upper": None} for lp in loops}

        planners: list[MagicMock] = []

        def fake_build_planner(db, bundle_cache=None):
            planner = MagicMock()
            planner.request_bundles = AsyncMock(return_value=[])
            planners.append(planner)
            return planner

        monkeypatch.setattr("app.tasks.kpi_calc._build_data_planner", fake_build_planner)

        warm_session = MagicMock()
        warm_session.__aenter__ = AsyncMock(return_value=warm_session)
        warm_session.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr("app.core.db.AsyncSessionLocal", lambda: warm_session)

        now = datetime.now(UTC).replace(tzinfo=None)
        result = await _prewarm_cache_for_loops(loops, now, now, loop_configs)

        assert calls["n"] == 1
        assert len(planners) == 2
        assert all(p._preloaded_data_version == _VERSION for p in planners)
        assert result["failed"] == 0


# ---------------------------------------------------------------------------
# B1：节点级汇总取消静默误收（裁决书 §1.2 / §2-5）
# ---------------------------------------------------------------------------


class TestNodeAggregationCancelled:
    def test_cancelled_not_counted_as_success(self) -> None:
        """取消注入（裁决书 §2-5 形态）：取消节点不再静默计入 success."""
        snap = {"node": "snapshot-dict"}
        results = [snap, asyncio.CancelledError(), ValueError("聚合失败"), None, snap]

        counts = _classify_node_results(results)

        # 修复前形态：success=2（含 1 取消误收）、cancelled 无处可看
        assert counts == {"success": 2, "skipped": 1, "failed": 1, "cancelled": 1}

    def test_exception_branch_precedence_unchanged(self) -> None:
        """普通 Exception 仍走 failed 分支（不被 BaseException 兜底吞掉）."""
        counts = _classify_node_results([ValueError("x"), RuntimeError("y"), None])
        assert counts == {"success": 0, "skipped": 1, "failed": 2, "cancelled": 0}

    async def test_empty_nodes_return_shape_includes_cancelled(self) -> None:
        """早退路径返回形状含 cancelled 键（消费方 .get 兜底兼容旧形状）."""
        result = await batch_calculate_and_save_node_snapshots(
            nodes=[], ts_start=datetime(2026, 10, 15, 14), ts_end=datetime(2026, 10, 15, 15)
        )
        assert result == {
            "total": 0,
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": 0,
        }


# ---------------------------------------------------------------------------
# B2：回路级汇总 CancelledError 崩溃（裁决书 §1.1 / §2-3b/3c）
# ---------------------------------------------------------------------------


class TestSummarizeCancelled:
    def test_language_baseline_cancelled_not_exception(self) -> None:
        """语言基线（裁决书 §2-0）：CancelledError 继承 BaseException 非 Exception."""
        assert issubclass(asyncio.CancelledError, BaseException)
        assert not issubclass(asyncio.CancelledError, Exception)

    def test_cancelled_no_crash_honest_counting(self) -> None:
        """修复前该输入抛 AttributeError（裁决书 §2-3b 实证）；现诚实计数不崩."""
        summary = _summarize_batch_results(
            [
                {"status": "SUCCESS"},
                asyncio.CancelledError(),
                {"status": "INCONCLUSIVE"},
                None,
                ValueError("db down"),
            ]
        )
        assert summary["success"] == 1
        assert summary["inconclusive"] == 1
        # cancelled 计入 failed（熔断器口径覆盖取消：批量级取消=系统性事件）
        assert summary["failed"] == 3
        assert summary["cancelled"] == 1
        assert any("取消" in line for line in summary["failed_detail"])

    def test_all_cancelled_reaches_health_breaker_threshold(self) -> None:
        """全批取消 → failed 占比 100% 可触发 G29 熔断（可观测失败终态，
        替代修复前的 AttributeError 崩溃 + 217 回退整批双跑）."""
        summary = _summarize_batch_results([asyncio.CancelledError() for _ in range(4)])
        assert summary["failed"] == 4
        assert summary["cancelled"] == 4
        assert summary["failed"] / 4 >= 0.5

    def test_loops_param_tag_in_cancelled_detail(self) -> None:
        """带 loops 时取消明细带回路位号（排障口径与 failed 一致）."""
        loop = _make_loop("loop-9", "101-FC-1023")
        summary = _summarize_batch_results([asyncio.CancelledError()], loops=[loop])
        assert any("101-FC-1023" in line for line in summary["failed_detail"])


# ---------------------------------------------------------------------------
# B3：壳层取消痕迹悬挂（裁决书 §1.3 / §2-4）
# ---------------------------------------------------------------------------


class TestAttemptLogWrapCancelled:
    @staticmethod
    def _fake_ledger(monkeypatch) -> list[tuple]:
        calls: list[tuple] = []

        async def fake_start(logical_run_id, attempt_no, worker_pid=None):
            calls.append(("start", logical_run_id, attempt_no))

        async def fake_finish(logical_run_id, attempt_no, error=None):
            calls.append(("finish", type(error).__name__ if error is not None else None))

        monkeypatch.setattr("app.services.result_ledger.start_calculation_attempt", fake_start)
        monkeypatch.setattr("app.services.result_ledger.finish_calculation_attempt", fake_finish)
        return calls

    async def test_cancel_finishes_attempt_no_dangling(self, monkeypatch) -> None:
        """修复前 attempt 行只落 start 不落 finish（悬挂 STARTED，§2-4 实证）；
        现落 finish(error=CancelledError) 后原样 raise."""
        calls = self._fake_ledger(monkeypatch)

        async def cancelled_coro():
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _attempt_log_wrap(
                cancelled_coro, logical_run_id="run-1", attempt_no=1, worker_pid=1
            )

        assert calls == [("start", "run-1", 1), ("finish", "CancelledError")]

    async def test_success_and_exception_paths_unchanged(self, monkeypatch) -> None:
        """既有路径回归：成功 → finish(None)；Exception → finish(错误类) 后 raise."""
        calls = self._fake_ledger(monkeypatch)

        async def ok_coro():
            return {"status": "SUCCESS"}

        assert await _attempt_log_wrap(ok_coro, logical_run_id="run-2", attempt_no=1) == {
            "status": "SUCCESS"
        }

        async def failing_coro():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await _attempt_log_wrap(failing_coro, logical_run_id="run-2", attempt_no=2)

        assert calls == [
            ("start", "run-2", 1),
            ("finish", None),
            ("start", "run-2", 2),
            ("finish", "RuntimeError"),
        ]


class TestHourlyTrackingCancelled:
    async def test_cancel_writes_cancelled_terminal_once(self, monkeypatch) -> None:
        """修复前 TaskRecord 停 RUNNING 永不终态；现恰一个终态 CANCELLED 后
        原样 raise（Celery autoretry 不匹配 CancelledError，不重试不回退）."""
        statuses: list[TaskStatus] = []

        class FakeRedis:
            async def set(self, *args, **kwargs):
                return True

            async def get(self, key):
                return "task-1"

            async def delete(self, *args):
                return 1

        monkeypatch.setattr("app.core.redis.redis_client", FakeRedis())

        async def fake_create_task(**kwargs):
            return "task-1"

        async def fake_update_status(task_id, status, **kwargs):
            statuses.append(status)

        monkeypatch.setattr("app.services.task_tracker.create_task", fake_create_task)
        monkeypatch.setattr("app.services.task_tracker.update_status", fake_update_status)

        async def cancelled_calculate(**kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr("app.tasks.kpi_calc._do_calculate", cancelled_calculate)

        with pytest.raises(asyncio.CancelledError):
            await _do_hourly_with_tracking()

        # 无双终态：RUNNING → CANCELLED，恰两个状态、终态唯一
        assert statuses == [TaskStatus.RUNNING, TaskStatus.CANCELLED]
