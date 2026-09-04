"""工作台 v2.0 Celery 任务 + EventBus 单测（M1 skeleton）。

覆盖：
1. 5 条 beat 调度条目已注册（beat_schedule 断言，含 MV 错峰 2min）
2. 5 个 task 已注册到 celery_app（name 断言）
3. EventBus.publish 双阶段：DB add+flush + WS 存根（mock session）
4. EventBus.count_unread / mark_read 调用路径（mock session）
5. task skeleton 可执行：asyncio.run 调用内部协程（连真实 DB，integration 标记，
   PG 不可达时 skip）

M1 验收口径："4 beat 注册 + 单测触发 OK"。
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.event_bus import count_unread, mark_read, publish
from app.tasks.celery_app import celery_app

# ---------------------------------------------------------------------------
# beat_schedule 注册断言
# ---------------------------------------------------------------------------

_EXPECTED_BEATS: dict[str, str] = {
    "workbench-precalc": "app.tasks.workbench.workbench_precalc",
    "sla-sweep": "app.tasks.workbench.sla_sweep",
    "event-archive": "app.tasks.workbench.event_archive",
    "wb-cache-cleanup": "app.tasks.workbench.wb_cache_cleanup",
    "refresh-workbench-mv": "app.tasks.workbench.refresh_workbench_mv",
}


class TestBeatScheduleRegistered:
    """5 条工作台 beat 调度条目必须注册到 celery_app.conf.beat_schedule。"""

    def test_five_workbench_beats_registered(self) -> None:
        schedule = celery_app.conf.beat_schedule
        for name, task_path in _EXPECTED_BEATS.items():
            assert name in schedule, f"beat_schedule 缺少条目 {name}"
            assert schedule[name]["task"] == task_path, f"{name} 的 task 路径不符"

    def test_precalc_runs_every_5min(self) -> None:
        crontab = celery_app.conf.beat_schedule["workbench-precalc"]["schedule"]
        assert crontab.minute == {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}

    def test_mv_refresh_offset_2min_from_precalc(self) -> None:
        """MV 刷新与 precalc 错峰 2min（2,7,12...而非 0,5,10...）。"""
        crontab = celery_app.conf.beat_schedule["refresh-workbench-mv"]["schedule"]
        assert crontab.minute == {2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}

    def test_sla_sweep_runs_every_minute(self) -> None:
        crontab = celery_app.conf.beat_schedule["sla-sweep"]["schedule"]
        assert crontab.minute == set(range(60))

    def test_event_archive_runs_daily_0330(self) -> None:
        crontab = celery_app.conf.beat_schedule["event-archive"]["schedule"]
        assert crontab.hour == {3}
        assert crontab.minute == {30}


# ---------------------------------------------------------------------------
# task 注册断言
# ---------------------------------------------------------------------------


class TestTasksRegistered:
    """5 个 task 必须注册到 celery_app.tasks（可被 worker 发现）。"""

    def test_five_tasks_importable(self) -> None:
        for task_path in _EXPECTED_BEATS.values():
            assert task_path in celery_app.tasks, f"task {task_path} 未注册到 celery_app"


# ---------------------------------------------------------------------------
# EventBus.publish / count_unread / mark_read 单测（mock session）
# ---------------------------------------------------------------------------


class TestEventBusPublish:
    """publish 双阶段：DB add+flush + WS 存根（不阻塞主流程）。"""

    def test_publish_adds_event_and_flushes(self) -> None:
        mock_db = AsyncMock()
        mock_db.add = MagicMock()

        event = asyncio.run(
            publish(
                mock_db,
                source_module="alert",
                event_type="ALERT_NEW",
                severity="WARN",
                title="测试告警事件",
                scope_type="LOOP",
                scope_id=42,
                metadata={"level": "WARN"},
            )
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()
        assert event.source_module == "alert"
        assert event.event_type == "ALERT_NEW"
        assert event.severity == "WARN"
        assert event.title == "测试告警事件"
        assert event.scope_type == "LOOP"
        assert event.scope_id == 42
        assert event.read_by_users == []
        assert event.ext_metadata == {"level": "WARN"}
        assert event.occurred_at is not None

    def test_publish_metadata_defaults_to_empty_dict(self) -> None:
        mock_db = AsyncMock()
        mock_db.add = MagicMock()

        event = asyncio.run(
            publish(
                mock_db,
                source_module="handling",
                event_type="ORDER_CREATED",
                severity="INFO",
                title="工单创建",
            )
        )
        assert event.ext_metadata == {}
        assert event.read_by_users == []
        assert event.body is None


class TestEventBusReadOps:
    """count_unread / mark_read 调用路径（mock session）。"""

    def test_count_unread_executes_query(self) -> None:
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 7
        mock_db.execute.return_value = mock_result

        count = asyncio.run(count_unread(mock_db, user_id=1))

        mock_db.execute.assert_awaited_once()
        assert count == 7

    def test_mark_read_returns_zero_for_empty_ids(self) -> None:
        mock_db = AsyncMock()

        count = asyncio.run(mark_read(mock_db, event_ids=[], user_id=1))

        assert count == 0
        mock_db.execute.assert_not_awaited()

    def test_mark_read_executes_update(self) -> None:
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_db.execute.return_value = mock_result

        count = asyncio.run(mark_read(mock_db, event_ids=[1, 2, 3], user_id=5))

        mock_db.execute.assert_awaited_once()
        assert count == 3


# ---------------------------------------------------------------------------
# task skeleton 可执行性（连真实 DB；DB 不可达时 skip，与 tests/integration/ 同模式）
# ---------------------------------------------------------------------------


def _pg_reachable() -> bool:
    """检查 PG 端口是否可达（socket 探测，不依赖额外驱动）。"""
    try:
        from app.core.config import settings

        with socket.create_connection((settings.POSTGRES_HOST, settings.POSTGRES_PORT), timeout=3):
            return True
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _pg_reachable(), reason="本地开发 PG 不可达，跳过 task skeleton 执行测试")
class TestTaskSkeletonExecution:
    """task 内部协程可被 asyncio.run 调用且返回 dict（验证 DB 连通 + 表存在）。

    需真实 PG：CI 经 pyproject addopts ``-m 'not integration'`` 排除；
    本地 PG 不可达时按 skipif 跳过而非报错。
    """

    def test_workbench_precalc_executable(self) -> None:
        from app.tasks.workbench import _workbench_precalc_async

        result = asyncio.run(_workbench_precalc_async())
        # M2：真实聚合 upsert（scope 数 × 3 窗口），不再返回 skeleton
        assert result["status"] == "ok"
        assert result["written"] > 0
        assert result["errors"] == 0

    def test_sla_sweep_executable(self) -> None:
        from app.tasks.workbench import _sla_sweep_async

        result = asyncio.run(_sla_sweep_async())
        assert result["status"] == "skeleton"
        assert "warn_count" in result
        assert "breach_count" in result

    def test_event_archive_executable(self) -> None:
        from app.tasks.workbench import _event_archive_async

        result = asyncio.run(_event_archive_async())
        assert result["status"] == "skeleton"
        assert "stale_count" in result

    def test_wb_cache_cleanup_executable(self) -> None:
        from app.tasks.workbench import _wb_cache_cleanup_async

        result = asyncio.run(_wb_cache_cleanup_async())
        assert result["status"] == "skeleton"
        assert "stale_count" in result

    def test_refresh_workbench_mv_executable(self) -> None:
        from app.tasks.workbench import _refresh_workbench_mv_async

        result = asyncio.run(_refresh_workbench_mv_async())
        assert result["status"] == "ok"
        assert set(result["refreshed"]) == {
            "mv_staff_workload",
            "mv_diagnosis_pareto",
            "mv_handling_funnel",
        }
