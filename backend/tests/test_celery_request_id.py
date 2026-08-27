"""异步链路请求关联测试（S3-B4 延伸）。

测试覆盖：
- before_task_publish: 投递时把 contextvar 中的 request_id 写入消息 headers
- task_prerun / task_postrun: worker 侧从 headers 恢复 request_id，结束后清理
- JsonFormatter: 任务日志单行 JSON 同时携带 request_id 与 task_id，脱敏不受影响
"""

from __future__ import annotations

import json
import logging
from unittest.mock import Mock

from app.core.logging import JsonFormatter, _request_id_ctx
from app.tasks.celery_app import (
    _clear_request_id_on_postrun,
    _restore_request_id_on_prerun,
)


class TestRequestIdPropagation:
    """request_id 跨 Celery 投递/执行链路传递。"""

    def test_publish_injects_request_id_into_headers(self) -> None:
        """有请求上下文时，发布信号把 request_id 写入 headers。"""
        headers: dict = {}
        token = _request_id_ctx.set("req-abc-123")
        try:
            from celery.signals import before_task_publish

            before_task_publish.send(
                sender=None,
                body=None,
                exchange=None,
                routing_key=None,
                headers=headers,
                properties=None,
                declare=[],
                retry_policy=None,
            )
            assert headers.get("request_id") == "req-abc-123"
        finally:
            _request_id_ctx.reset(token)

    def test_publish_without_context_leaves_headers_empty(self) -> None:
        """无请求上下文（如 Beat 定时派发）时不注入。"""
        headers: dict = {}
        from celery.signals import before_task_publish

        before_task_publish.send(
            sender=None,
            body=None,
            exchange=None,
            routing_key=None,
            headers=headers,
            properties=None,
            declare=[],
            retry_policy=None,
        )
        assert "request_id" not in headers

    def test_prerun_restores_and_postrun_clears(self) -> None:
        """task_prerun 从消息 headers 恢复 request_id，task_postrun 清理。

        直接驱动信号函数，模拟 worker 从队列消息执行任务的真实路径
        （apply() 同步路径不经消息序列化，headers 不生效）。
        """
        task = Mock()
        task.request.headers = {"request_id": "req-task-001"}

        _restore_request_id_on_prerun(task=task)
        assert _request_id_ctx.get() == "req-task-001"

        _clear_request_id_on_postrun()
        # postrun 后 contextvar 已清理（防止泄漏到下一任务）
        assert _request_id_ctx.get() is None

    def test_prerun_without_headers_keeps_context_clean(self) -> None:
        """旧队列消息 / Beat 任务无 headers 时不设置 contextvar。"""
        task = Mock()
        task.request.headers = None

        _restore_request_id_on_prerun(task=task)
        assert _request_id_ctx.get() is None


class TestTaskLogJsonOutput:
    """任务侧日志输出格式：单行 JSON + request_id + task_id + 脱敏。"""

    def test_json_log_contains_request_id_and_task_id(self) -> None:
        """带请求上下文的任务日志为单行 JSON，同时含 request_id 与 task_id。

        模拟 worker 侧链路：task_prerun 信号从消息 headers 恢复 contextvar
        后，任务日志经 JsonFormatter 输出（不使用 Task.apply()，其在隔离
        副本 context 中运行，与真实 worker 进程内行为不同）。
        """
        task = Mock()
        task.request.headers = {"request_id": "req-log-42"}

        try:
            _restore_request_id_on_prerun(task=task)

            # 任务体内典型日志：带 task_id，含敏感字段
            record = logging.LogRecord(
                name="app.tasks.kpi_calc",
                level=logging.INFO,
                pathname="app/tasks/kpi_calc.py",
                lineno=1,
                msg="KPI 计算任务开始, celery_id=%s, password=%s",
                args=("6023b7ea-64a7", "hunter2"),
                exc_info=None,
            )
            line = JsonFormatter().format(record)

            # 单行 JSON
            assert "\n" not in line
            payload = json.loads(line)
            assert payload["request_id"] == "req-log-42"
            assert "celery_id=6023b7ea-64a7" in payload["message"]
            # 脱敏未被破坏
            assert "hunter2" not in line
            assert "password=***" in payload["message"]
        finally:
            _clear_request_id_on_postrun()
