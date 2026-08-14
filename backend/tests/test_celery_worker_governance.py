"""Celery worker 治理测试（Phase 3 可观测性 + worker 治理）.

覆盖：
- worker 启动命令同时消费 default 与 dead_letter 队列（死信不再永久堆积）
- worker/beat 日志句柄在停止时关闭（fd 不泄漏）
- pgrep 单例匹配特征收窄：必须含 '-A app.tasks.celery_app'，
  不匹配本机其他项目的 celery 进程
- celery 配置：worker_max_tasks_per_child=50、result_expires=7 天
- 看门狗：worker/beat 缺失时记录 error 级告警（仅告警不拉起）
"""

from __future__ import annotations

import logging
import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import app.main as main_module
from app.main import (
    _BEAT_PGREP_PATTERN,
    _WORKER_PGREP_PATTERN,
    _celery_watchdog_check,
    _start_celery_worker,
    _stop_celery_worker,
)

_OWN_BEAT_CMDLINE = (
    f"/usr/bin/python -m celery -A app.tasks.celery_app beat -l info "
    f"--pidfile /path/to/{main_module._PROJECT_TAG}/backend/celerybeat.pid"
)
_OWN_WORKER_CMDLINE = (
    f"/usr/bin/python -m celery -A app.tasks.celery_app worker -l info "
    f"-Q default,dead_letter --hostname {main_module._PROJECT_TAG}@%h"
)


class TestWorkerCommand:
    """worker 启动命令与日志句柄生命周期。"""

    def test_worker_command_includes_dead_letter_queue(self, tmp_path, monkeypatch):
        """worker 命令 -Q 必须同时包含 default 与 dead_letter 队列。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_module, "_celery_worker_process", None)
        monkeypatch.setattr(main_module, "_celery_worker_log_handle", None)

        with (
            patch("app.main._any_worker_process_running", return_value=False),
            patch("app.main.subprocess.Popen") as mock_popen,
        ):
            _start_celery_worker()

            mock_popen.assert_called_once()
            cmd = mock_popen.call_args.args[0]
            q_index = cmd.index("-Q")
            assert cmd[q_index + 1] == "default,dead_letter"
            # stderr 合并到 stdout（单句柄，少占 fd）
            assert mock_popen.call_args.kwargs["stderr"] == subprocess.STDOUT

        # 清理：关闭测试产生的日志句柄并复位模块全局
        handle = main_module._celery_worker_log_handle
        if handle is not None:
            handle.close()
        main_module._celery_worker_log_handle = None
        main_module._celery_worker_process = None

    def test_log_handle_closed_on_stop(self, tmp_path, monkeypatch):
        """_stop_celery_worker 关闭日志句柄并复位全局引用（fd 不泄漏）。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(main_module, "_celery_worker_process", None)
        monkeypatch.setattr(main_module, "_celery_worker_log_handle", None)

        with (
            patch("app.main._any_worker_process_running", return_value=False),
            patch("app.main.subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(pid=43210)
            _start_celery_worker()

        handle = main_module._celery_worker_log_handle
        assert handle is not None and not handle.closed

        _stop_celery_worker()
        assert handle.closed
        assert main_module._celery_worker_log_handle is None
        assert main_module._celery_worker_process is None


class TestPgrepPatterns:
    """pgrep 匹配特征收窄：只匹配本项目的 celery 进程。"""

    def test_worker_pattern_matches_own_cmdline(self):
        assert re.search(_WORKER_PGREP_PATTERN, _OWN_WORKER_CMDLINE)

    def test_worker_pattern_rejects_other_project(self):
        """其他项目的 celery worker 不得被误判为本项目 worker。"""
        assert re.search(_WORKER_PGREP_PATTERN, "celery -A other_project worker") is None
        assert re.search(_WORKER_PGREP_PATTERN, "python -m celery worker -l info") is None

    def test_beat_pattern_matches_own_cmdline(self):
        assert re.search(_BEAT_PGREP_PATTERN, _OWN_BEAT_CMDLINE)

    def test_beat_pattern_rejects_other_project(self):
        assert re.search(_BEAT_PGREP_PATTERN, "celery -A other_project beat") is None

    def test_beat_pattern_does_not_match_worker_cmdline(self):
        """beat 模式不得误匹配 worker 命令（单例检查相互独立）。"""
        assert re.search(_BEAT_PGREP_PATTERN, _OWN_WORKER_CMDLINE) is None


class TestCeleryConfGovernance:
    """celery_app 配置项：子进程回收与结果过期。"""

    def test_worker_max_tasks_per_child(self):
        from app.tasks.celery_app import celery_app

        assert celery_app.conf.worker_max_tasks_per_child == 50

    def test_result_expires_seven_days(self):
        from app.tasks.celery_app import celery_app

        assert celery_app.conf.result_expires == 7 * 24 * 3600


class TestCeleryWatchdog:
    """看门狗探活：缺失告警、在位静默。"""

    @pytest.mark.asyncio
    async def test_alert_when_both_missing(self, caplog):
        """worker 与 beat 均缺失时各记录一条 error 级告警。"""
        with (
            patch("app.main._any_beat_process_running", return_value=False),
            patch("app.main._any_worker_process_running", return_value=False),
            caplog.at_level(logging.ERROR, logger="app.main"),
        ):
            await _celery_watchdog_check()

        error_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("Celery Beat" in m for m in error_messages)
        assert any("Celery Worker" in m for m in error_messages)

    @pytest.mark.asyncio
    async def test_silent_when_running(self, caplog):
        """worker 与 beat 均在线时不产生告警。"""
        with (
            patch("app.main._any_beat_process_running", return_value=True),
            patch("app.main._any_worker_process_running", return_value=True),
            caplog.at_level(logging.ERROR, logger="app.main"),
        ):
            await _celery_watchdog_check()

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
