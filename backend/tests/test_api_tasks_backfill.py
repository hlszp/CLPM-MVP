"""POST /api/v1/tasks/backfill 端点测试（历史重算）.

测试覆盖：
- dryRun=True 返回预览结果（不触发 Celery）
- dryRun=False 创建 BACKFILL 任务并返回 taskId
- 时间窗超过 30 天返回 400
- PE_ENGINEER 无权限（403）
- loopIds 优先级高于 plantNodeIds

设计依据：IDS §2.7.6.5, PRD §4.3.7
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USERS, mock_current_user
from tests.test_api_tasks import task_redis  # noqa: F401 — reuse FakeTaskRedis fixture


def _mock_loop(loop_id: str, tag_name: str | None = None) -> MagicMock:
    """构造 LoopLedger mock."""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name or f"TAG-{loop_id}"
    return loop


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/backfill
# ---------------------------------------------------------------------------


class TestBackfillTaskEvaluate:
    """POST /api/v1/tasks/backfill tests."""

    def test_backfill_dry_run_returns_preview(
        self, client, task_redis, fake_redis
    ) -> None:
        """dryRun=True 应返回预览结果，不触发 Celery."""
        fake_loops = [_mock_loop(f"loop-{i}", f"L-{i:03d}") for i in range(3)]
        with (
            patch(
                "app.api.v1.endpoints.tasks._resolve_loop_ids",
                AsyncMock(return_value=fake_loops),
            ),
            patch("app.tasks.kpi_calc.backfill_kpi_range") as mock_task,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/tasks/backfill",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tsStart": "2026-07-04T00:00:00Z",
                    "tsEnd": "2026-07-05T00:00:00Z",
                    "dryRun": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "loopCount" in data
        assert "windowCount" in data
        assert "estimatedDurationSec" in data
        assert "sampleLoopNames" in data
        assert data["windowCount"] == 24  # 24 小时
        assert data["loopCount"] == 3
        # dryRun 不应触发 Celery
        mock_task.delay.assert_not_called()

    def test_backfill_submit_creates_task(
        self, client, task_redis, fake_redis
    ) -> None:
        """dryRun=False 应创建 BACKFILL 任务并返回 taskId."""
        fake_loops = [_mock_loop("loop-1", "L-001"), _mock_loop("loop-2", "L-002")]
        with patch("app.tasks.kpi_calc.backfill_kpi_range") as mock_task:
            mock_task.delay.return_value.id = "celery-123"
            with (
                patch(
                    "app.api.v1.endpoints.tasks._resolve_loop_ids",
                    AsyncMock(return_value=fake_loops),
                ),
                mock_current_user(TEST_USERS["admin"]),
            ):
                resp = client.post(
                    "/api/v1/tasks/backfill",
                    headers={"Authorization": "Bearer fake-token"},
                    json={
                        "tsStart": "2026-07-04T00:00:00Z",
                        "tsEnd": "2026-07-04T02:00:00Z",
                    },
                )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "taskId" in data
        assert mock_task.delay.called
        # 验证 Celery 调用参数：ts_start, ts_end, loop_ids
        call_args = mock_task.delay.call_args
        assert call_args.args[0] == "2026-07-04T00:00:00Z"
        assert call_args.args[1] == "2026-07-04T02:00:00Z"
        assert call_args.kwargs.get("loop_ids") == ["loop-1", "loop-2"]

    def test_backfill_time_window_exceeds_30_days_rejected(
        self, client, task_redis, fake_redis
    ) -> None:
        """时间窗超过 30 天应返回 400."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tasks/backfill",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tsStart": "2026-06-01T00:00:00Z",
                    "tsEnd": "2026-07-05T00:00:00Z",  # 34 天
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_BACKFILL_WINDOW_TOO_LARGE"

    def test_backfill_pe_engineer_forbidden(
        self, client, task_redis, fake_redis
    ) -> None:
        """PE_ENGINEER 应无权限（403）."""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/tasks/backfill",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tsStart": "2026-07-04T00:00:00Z",
                    "tsEnd": "2026-07-05T00:00:00Z",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_backfill_loop_ids_priority_over_plant_node_ids(
        self, client, task_redis, fake_redis
    ) -> None:
        """同时传 loopIds 和 plantNodeIds 时，loopIds 优先."""
        fake_loops = [_mock_loop("loop-1", "L-001"), _mock_loop("loop-2", "L-002")]
        with (
            patch(
                "app.api.v1.endpoints.tasks._resolve_loop_ids",
                AsyncMock(return_value=fake_loops),
            ) as mock_resolve,
            patch("app.tasks.kpi_calc.backfill_kpi_range"),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/tasks/backfill",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tsStart": "2026-07-04T00:00:00Z",
                    "tsEnd": "2026-07-04T01:00:00Z",
                    "plantNodeIds": ["node-1"],
                    "loopIds": ["loop-1", "loop-2"],
                    "dryRun": True,
                },
            )
        # _resolve_loop_ids 应被调用，且 loopIds 与 plantNodeIds 都透传
        mock_resolve.assert_called_once()
        call_args = mock_resolve.call_args
        # 位置参数：(db, loop_ids, plant_node_ids)
        assert call_args.args[1] == ["loop-1", "loop-2"]
        assert call_args.args[2] == ["node-1"]
        assert resp.status_code == 200
