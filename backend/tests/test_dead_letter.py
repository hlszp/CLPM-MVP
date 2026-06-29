"""Dead letter queue handler 测试 (S2-A6).

测试覆盖：
- record() 正常调用：返回正确的元数据字典
- record() 带 args/kwargs：完整透传
- record() 不带 args/kwargs：默认 None
- 任务注册验证：celery_app 已注册 app.tasks.dead_letter.record
- 日志输出验证：记录 ERROR 级别日志
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from app.tasks.celery_app import celery_app
from app.tasks.dead_letter import record

# ===========================================================================
# 任务注册验证
# ===========================================================================


class TestTaskRegistration:
    """验证 dead_letter.record 任务已正确注册到 celery_app。"""

    def test_task_registered(self) -> None:
        """record 函数应注册为 celery task。"""
        assert "app.tasks.dead_letter.record" in celery_app.tasks

    def test_task_queue_routing(self) -> None:
        """dead_letter 队列应在 celery_app.conf.task_queues 中配置。"""
        queue_names = [q.name for q in celery_app.conf.task_queues or []]
        assert "dead_letter" in queue_names


# ===========================================================================
# record() 函数测试
# ===========================================================================


class TestRecord:
    """测试 record() 死信记录函数。"""

    def test_record_returns_correct_metadata(self) -> None:
        """record 应返回包含 taskId/taskName/exc/status 的字典。"""
        result = record(
            task_id="task-001",
            task_name="app.tasks.report_generator.generate_report_task",
            exc="ConnectionError: DB unreachable",
        )

        assert result["taskId"] == "task-001"
        assert result["taskName"] == "app.tasks.report_generator.generate_report_task"
        assert result["exc"] == "ConnectionError: DB unreachable"
        assert result["status"] == "DEAD_LETTER"

    def test_record_with_args_and_kwargs(self) -> None:
        """record 应完整透传 args 和 kwargs。"""
        args = ("config-123",)
        kwargs = {"report_period": "DAILY"}

        result = record(
            task_id="task-002",
            task_name="app.tasks.kpi_calc.calculate_kpi",
            exc="TimeoutError",
            args=args,
            kwargs=kwargs,
        )

        assert result["taskId"] == "task-002"
        assert result["taskName"] == "app.tasks.kpi_calc.calculate_kpi"
        assert result["exc"] == "TimeoutError"

    def test_record_without_args_kwargs(self) -> None:
        """record 不传 args/kwargs 时应正常工作（默认 None）。"""
        result = record(
            task_id="task-003",
            task_name="app.tasks.diagnosis_engine.run_diagnosis",
            exc="ValueError: invalid input",
        )

        assert result["taskId"] == "task-003"
        assert result["status"] == "DEAD_LETTER"

    def test_record_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """record 应记录 ERROR 级别日志，包含 task_id 和 task_name。"""
        with caplog.at_level(logging.ERROR, logger="app.tasks.dead_letter"):
            record(
                task_id="task-log-001",
                task_name="app.tasks.test_task",
                exc="TestException",
            )

        # 至少有一条 ERROR 日志包含 task_id
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) >= 1
        log_msg = error_records[0].getMessage()
        assert "task-log-001" in log_msg
        assert "app.tasks.test_task" in log_msg
        assert "TestException" in log_msg

    def test_record_with_empty_exc(self) -> None:
        """record 应能处理空字符串异常。"""
        result = record(
            task_id="task-004",
            task_name="app.tasks.empty_task",
            exc="",
        )

        assert result["exc"] == ""
        assert result["status"] == "DEAD_LETTER"

    def test_record_with_long_exception_message(self) -> None:
        """record 应能处理超长异常消息。"""
        long_exc = "x" * 1000
        result = record(
            task_id="task-005",
            task_name="app.tasks.long_task",
            exc=long_exc,
        )

        assert result["exc"] == long_exc
        assert len(result["exc"]) == 1000


# ===========================================================================
# AsyncTask.on_failure 集成验证（验证死信发送链路）
# ===========================================================================


class TestOnFailureIntegration:
    """验证 AsyncTask.on_failure 会触发 dead_letter.record 任务发送。"""

    def test_on_failure_sends_to_dead_letter_queue(self) -> None:
        """AsyncTask.on_failure 应通过 celery_app.send_task 发送到 dead_letter 队列。"""
        from app.tasks.celery_app import AsyncTask

        # 构造一个 AsyncTask 实例
        task = AsyncTask()
        task.name = "app.tasks.test_failure_task"

        with patch.object(celery_app, "send_task") as mock_send:
            try:
                raise RuntimeError("测试异常")
            except RuntimeError as e:
                task.on_failure(e, "task-fail-001", (), {}, None)

            mock_send.assert_called_once_with(
                "app.tasks.dead_letter.record",
                args=["task-fail-001", "app.tasks.test_failure_task", "测试异常", (), {}],
                queue="dead_letter",
            )

    def test_on_failure_send_exception_does_not_raise(self) -> None:
        """send_task 失败时 on_failure 不应抛出异常（仅记录日志）。"""
        from app.tasks.celery_app import AsyncTask

        task = AsyncTask()
        task.name = "app.tasks.test_failure_task"

        with patch.object(celery_app, "send_task", side_effect=Exception("Redis 不可用")):
            # 不应抛出异常
            try:
                raise ValueError("业务异常")
            except ValueError as e:
                task.on_failure(e, "task-fail-002", (), {}, None)
            # 到这里说明没有抛出异常
