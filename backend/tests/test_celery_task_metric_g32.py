"""Celery 任务计数埋点（整改 G32）。

背景：celery_task_total 此前**只有定义、没有任何埋点**，于是所有「任务静默失败／
静默跳过」在监控侧零信号——deploy/prometheus/alerts.yml 的 celery 失败率告警因
无数据永不触发（该文件自述「无数据时不触发，属预期」）。G29/G30 修好的失败可见性
也因此只能靠日志。

限制（未解决）：worker 为 prefork 多进程，各子进程持独立 registry，本处 .inc()
计入子进程内存，父进程 /metrics 读不到；需 PROMETHEUS_MULTIPROC_DIR +
MultiProcessCollector，或 Pushgateway/celery-exporter。本文件只守护「打点已接线」。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.metrics import celery_task_total
from app.tasks.celery_app import _clear_request_id_on_postrun


def _value(task_name: str, status: str) -> float:
    return celery_task_total.labels(task_name=task_name, status=status)._value.get()


class TestCeleryTaskMetric:
    """task_postrun 必须为 counter 打点。"""

    def test_success_increments(self) -> None:
        before = _value("t.dummy.success", "SUCCESS")
        _clear_request_id_on_postrun(task=SimpleNamespace(name="t.dummy.success"), state="SUCCESS")
        assert _value("t.dummy.success", "SUCCESS") == before + 1, (
            "task_postrun 未为 celery_task_total 打点（G32 回归）"
        )

    def test_failure_state_recorded_separately(self) -> None:
        """失败必须落在独立 status 标签上，否则失败率无从计算。"""
        before = _value("t.dummy.fail", "FAILURE")
        _clear_request_id_on_postrun(task=SimpleNamespace(name="t.dummy.fail"), state="FAILURE")
        assert _value("t.dummy.fail", "FAILURE") == before + 1

    def test_missing_state_defaults_without_raising(self) -> None:
        """无 state（旧队列消息）时不得抛错，落 UNKNOWN。"""
        before = _value("t.dummy.nostate", "UNKNOWN")
        _clear_request_id_on_postrun(task=SimpleNamespace(name="t.dummy.nostate"))
        assert _value("t.dummy.nostate", "UNKNOWN") == before + 1

    def test_missing_task_name_defaults_without_raising(self) -> None:
        """无 task 对象时不得抛错，落 unknown。"""
        before = _value("unknown", "SUCCESS")
        _clear_request_id_on_postrun(state="SUCCESS")
        assert _value("unknown", "SUCCESS") == before + 1
