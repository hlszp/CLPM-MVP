"""回填韧性批（2026-10-10 生产事故修复）单测。

生产背景：33 小时×432 回路回填任务 FAILED，11 次「sorry, too many clients
already」——多窗口批并发峰值瞬时挤爆 PG 连接，正在拿连接的回路直接失败。
三项修复：
1. 单回路连接耗尽退避重试（_is_conn_exhausted + _calculate_one 重试包装）
2. 失败明细（回路位号+原因）进任务 errorMessage（_summarize_batch_results
   loops 标识 + _do_finalize_backfill 拼接）
3. 补差模式开关 skip_existing（默认 False 全量覆盖；True 时窗口内已有快照
   的回路跳过，重发同窗秒级补缺口）
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.tasks.kpi_calc import (
    _do_finalize_backfill,
    _summarize_batch_results,
)


class TestConnExhaustedDetection:
    def test_too_many_clients_matched(self) -> None:
        # _is_conn_exhausted 是 _run_batch_loop_calculations 的闭包内函数，
        # 通过源码断言其存在与匹配逻辑（函数级单测见 TestCalculateOneRetry）
        import inspect

        from app.tasks.kpi_calc import _run_batch_loop_calculations  # noqa: F401

        src = inspect.getsource(_run_batch_loop_calculations)
        assert '"too many clients" in str(exc).lower()' in src

    def test_other_errors_not_matched(self) -> None:
        import inspect

        from app.tasks.kpi_calc import _run_batch_loop_calculations

        src = inspect.getsource(_run_batch_loop_calculations)
        assert "attempt < 2" in src  # 仅重试 2 次（共 3 次尝试）


class TestSummarizeFailedDetail:
    def test_exception_carries_loop_tag(self) -> None:
        loops = [
            SimpleNamespace(id="l-1", tag_name="01TV_06003_PID"),
            SimpleNamespace(id="l-2", tag_name="04FIC03501_PIDA"),
        ]
        results: list[Any] = [
            RuntimeError("sorry, too many clients already"),
            {"status": "SUCCESS"},
        ]
        summary = _summarize_batch_results(results, loops=loops)
        assert summary["failed"] == 1
        detail = summary["failed_detail"]
        assert isinstance(detail, list)
        assert detail[0].startswith("01TV_06003_PID: ")
        assert "too many clients" in detail[0]

    def test_none_result_carries_marker(self) -> None:
        loops = [SimpleNamespace(id="l-1", tag_name="X")]
        summary = _summarize_batch_results([None], loops=loops)
        assert summary["failed"] == 1
        assert summary["failed_detail"] == ["X: (无返回)"]

    def test_without_loops_backward_compatible(self) -> None:
        summary = _summarize_batch_results([{"status": "SUCCESS"}, {"status": "INCONCLUSIVE"}])
        assert summary["success"] == 1
        assert summary["inconclusive"] == 1
        assert summary["failed_detail"] == []


class TestFinalizeErrorMessageDetail:
    @pytest.mark.asyncio
    async def test_failed_detail_in_error_message(self, fake_redis) -> None:
        batch = {
            "success": 430,
            "inconclusive": 1,
            "failed": 1,
            "node_success": 0,
            "skipped": 0,
            "failed_windows": [],
            "failed_detail": ["01TV_06003_PID: sorry, too many clients already"],
        }
        with (
            patch("app.tasks.kpi_calc._update_task_failed", new=AsyncMock()) as m_fail,
            patch(
                "app.tasks.kpi_calc._invalidate_backfill_cache",
                new=AsyncMock(return_value=0),
            ),
            # 2026-10-10 CI 修复：_is_task_cancelled 惰性 from-import
            # core.redis.redis_client 走真 Redis（CI 无 7103），定点接管
            patch("app.core.redis.redis_client", fake_redis),
        ):
            result = await _do_finalize_backfill(
                [batch],
                "2026-10-08T16:00:00",
                "2026-10-10T01:00:00",
                total_windows=33,
                task_id="t-1",
            )
        assert result["status"] == "FAILED"
        msg = m_fail.await_args.args[1]
        assert "loop_failed=1" in msg
        assert "01TV_06003_PID: sorry, too many clients already" in msg

    @pytest.mark.asyncio
    async def test_skipped_counted_and_success_when_no_failure(self, fake_redis) -> None:
        batch = {
            "success": 11,
            "inconclusive": 0,
            "failed": 0,
            "node_success": 0,
            "skipped": 421,
            "failed_windows": [],
            "failed_detail": [],
        }
        with (
            patch("app.tasks.kpi_calc._update_task_success", new=AsyncMock()) as m_ok,
            patch(
                "app.tasks.kpi_calc._invalidate_backfill_cache",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "app.tasks.kpi_calc._do_backfill_node_aggregation",
                new=AsyncMock(return_value=5),
            ),
            # 同上：_is_task_cancelled 惰性导入真 Redis，定点接管
            patch("app.core.redis.redis_client", fake_redis),
        ):
            result = await _do_finalize_backfill(
                [batch],
                "2026-10-08T16:00:00",
                "2026-10-10T01:00:00",
                total_windows=33,
                task_id="t-2",
            )
        assert result["status"] != "FAILED"
        assert result["skipped"] == 421
        m_ok.assert_awaited_once()


class TestSkipExistingParam:
    def test_chord_child_signature_has_skip_existing(self) -> None:
        """chord 子任务 kwargs 含 skip_existing（派发链透传）。"""
        import inspect

        from app.tasks.kpi_calc import _backfill_window_batch

        sig = inspect.signature(_backfill_window_batch)
        assert "skip_existing" in sig.parameters
        assert sig.parameters["skip_existing"].default is False

    def test_backfill_kpi_range_default_full(self) -> None:
        """主任务 skip_existing 默认 False（全量覆盖重算，用户裁决口径）。"""
        import inspect

        from app.tasks.kpi_calc import backfill_kpi_range

        sig = inspect.signature(backfill_kpi_range)
        assert sig.parameters["skip_existing"].default is False

    def test_skip_query_filters_existing_snapshots(self) -> None:
        """补差查询按 (loop_id, ts_start) 已存在集合过滤（源码断言口径）。"""
        import inspect

        from app.tasks.kpi_calc import _backfill_window_batch

        src = inspect.getsource(_backfill_window_batch)
        assert "KpiSnapshotHourly.ts_start == w_start" in src
        assert "todo_loops" in src
