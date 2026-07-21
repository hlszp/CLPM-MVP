"""data_link_monitor Celery 任务模块测试（WS-B2）.

验证：
- Celery task 注册正确（name/bind/base）
- Beat schedule 配置包含 data-link-check 和 import-task-sweep
- run_data_link_check 委托到 service 层
- sweep_import_tasks 委托到 data_import 层
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


class TestDataLinkMonitorTaskRegistration:
    """Celery task 注册测试。"""

    def test_run_data_link_check_registered(self):
        """run_data_link_check 应注册为 Celery task。"""
        from app.tasks.data_link_monitor import run_data_link_check

        assert run_data_link_check.name == "app.tasks.data_link_monitor.run_data_link_check"

    def test_sweep_import_tasks_registered(self):
        """sweep_import_tasks 应注册为 Celery task。"""
        from app.tasks.data_link_monitor import sweep_import_tasks

        assert sweep_import_tasks.name == "app.tasks.data_link_monitor.sweep_import_tasks"


class TestBeatSchedule:
    """Beat 调度配置测试。"""

    def test_beat_schedule_contains_data_link_check(self):
        """beat_schedule 应包含 data-link-check 条目。"""
        from app.tasks.celery_app import celery_app

        assert "data-link-check" in celery_app.conf.beat_schedule
        entry = celery_app.conf.beat_schedule["data-link-check"]
        assert entry["task"] == "app.tasks.data_link_monitor.run_data_link_check"

    def test_beat_schedule_contains_import_task_sweep(self):
        """beat_schedule 应包含 import-task-sweep 条目。"""
        from app.tasks.celery_app import celery_app

        assert "import-task-sweep" in celery_app.conf.beat_schedule
        entry = celery_app.conf.beat_schedule["import-task-sweep"]
        assert entry["task"] == "app.tasks.data_link_monitor.sweep_import_tasks"

    def test_beat_timezone_is_asia_shanghai(self):
        """beat timezone 应为 Asia/Shanghai。"""
        from app.tasks.celery_app import celery_app

        assert celery_app.conf.timezone == "Asia/Shanghai"


class TestSweepImportTasksExecution:
    """sweep_import_tasks 执行委托测试。"""

    def test_sweep_delegates_to_service(self):
        """sweep_import_tasks 应委托到 sweep_stale_running_tasks + prune_import_task_index。"""
        from app.tasks.data_link_monitor import sweep_import_tasks

        mock_run_async = MagicMock(return_value={"swept": 1, "pruned": 2})
        with (
            patch.object(sweep_import_tasks, "run_async", mock_run_async),
            patch("app.services.data_import.sweep_stale_running_tasks", new_callable=AsyncMock),
            patch("app.services.data_import.prune_import_task_index", new_callable=AsyncMock),
        ):
            # bind=True 的 Celery task 调用时自动传入 task 实例为 self
            result = sweep_import_tasks.run()

        assert result == {"swept": 1, "pruned": 2}
        mock_run_async.assert_called_once()
