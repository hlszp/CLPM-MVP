"""Celery worker 治理测试（Phase 3 可观测性 + worker 治理）.

覆盖：
- worker 启动命令同时消费 default 与 dead_letter 队列（死信不再永久堆积）
- worker/beat 日志句柄在停止时关闭（fd 不泄漏）
- pgrep 单例匹配特征收窄：必须含 '-A app.tasks.celery_app'，
  不匹配本机其他项目的 celery 进程
- celery 配置：worker_max_tasks_per_child=50、result_expires=7 天
- 看门狗（v6.2 升级）：worker/beat 缺失时自动补拉起，在位时静默
"""

from __future__ import annotations

import re
import subprocess
import threading
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
    """看门狗探活（v6.2）：缺失自动补拉起、在位静默。"""

    @pytest.mark.asyncio
    async def test_restart_when_both_missing(self):
        """worker 与 beat 均缺失时自动补拉起（不再仅告警）。"""
        with (
            patch("app.main._any_beat_process_running", return_value=False),
            patch("app.main._any_worker_process_running", return_value=False),
            patch("app.main._start_celery_beat") as mock_start_beat,
            patch("app.main._start_celery_worker") as mock_start_worker,
        ):
            await _celery_watchdog_check()

        mock_start_beat.assert_called_once()
        mock_start_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_silent_when_running(self):
        """worker 与 beat 均在线时不拉起。"""
        with (
            patch("app.main._any_beat_process_running", return_value=True),
            patch("app.main._any_worker_process_running", return_value=True),
            patch("app.main._start_celery_beat") as mock_start_beat,
            patch("app.main._start_celery_worker") as mock_start_worker,
        ):
            await _celery_watchdog_check()

        mock_start_beat.assert_not_called()
        mock_start_worker.assert_not_called()


class TestParentWatchdog:
    """父进程看门狗（防孤儿，2026-08-18）：宿主死亡判定与安装条件。"""

    def test_parent_gone_false_when_getppid_matches(self):
        """getppid 仍等于宿主 PID → 宿主存活，不触发。"""
        from app.tasks.parent_watchdog import parent_gone

        with patch("app.tasks.parent_watchdog.os.getppid", return_value=12345):
            assert parent_gone(12345) is False

    def test_parent_gone_true_when_adopted(self):
        """getppid 不匹配（被 init/launchd 收养）→ 宿主已死，触发自退出。

        不用 kill(0) 反确认：僵尸进程（<defunct>）会让 kill(0) 成功，
        看门狗永远误判存活（2026-08-18 实测）。
        """
        from app.tasks.parent_watchdog import parent_gone

        with patch("app.tasks.parent_watchdog.os.getppid", return_value=1):
            assert parent_gone(12345) is True

    def test_install_skipped_without_env(self, monkeypatch):
        """未注入 CLPM_PARENT_PID（手工启动）→ 不建看门狗线程。"""
        from app.tasks import parent_watchdog

        monkeypatch.delenv(parent_watchdog.ENV_PARENT_PID, raising=False)
        before = threading.active_count()
        parent_watchdog.install_from_env("test")
        assert threading.active_count() == before

    def test_install_creates_thread_with_env(self):
        """合法宿主 PID → 建 daemon 看门狗线程（结束后清理）。

        CHECK_INTERVAL patch 为 1h：防被监视 PID 恰好死亡时线程在测试
        运行中真实对本进程发 SIGTERM（daemon 线程随 pytest 退出回收）。
        """
        import os

        from app.tasks import parent_watchdog

        with (
            patch.dict(
                parent_watchdog.os.environ,
                {parent_watchdog.ENV_PARENT_PID: str(os.getppid() or 99999)},
            ),
            patch.object(parent_watchdog, "CHECK_INTERVAL", 3600.0),
        ):
            parent_watchdog.install_from_env("test")
            names = [t.name for t in threading.enumerate()]
            assert "clpm-parent-watchdog-test" in names

    def test_install_idempotent_by_thread_name(self):
        """同 label 重复安装不叠加线程（按线程名判重）。"""
        import os

        from app.tasks import parent_watchdog

        with (
            patch.dict(
                parent_watchdog.os.environ,
                {parent_watchdog.ENV_PARENT_PID: str(os.getppid() or 99999)},
            ),
            patch.object(parent_watchdog, "CHECK_INTERVAL", 3600.0),
        ):
            parent_watchdog.install_from_env("dup")
            parent_watchdog.install_from_env("dup")
            count = sum(1 for t in threading.enumerate() if t.name == "clpm-parent-watchdog-dup")
        assert count == 1

    def test_install_direct_parent_watches_getppid(self):
        """prefork 子进程入口：监视对象是启动时的直接父进程（worker master）。"""
        from app.tasks import parent_watchdog

        with (
            patch("app.tasks.parent_watchdog.os.getppid", return_value=4321) as mock_ppid,
            patch.object(parent_watchdog, "CHECK_INTERVAL", 3600.0),
        ):
            parent_watchdog.install_direct_parent("worker-pool")
        mock_ppid.assert_called()
        # 看门狗线程按直接父进程 PID 启动（4321 非本进程/非 init → 生效）
        names = [t.name for t in threading.enumerate()]
        assert "clpm-parent-watchdog-worker-pool" in names

    def test_shutdown_skipped_for_tool_processes(self, monkeypatch):
        """pytest 等工具进程退出时必须跳过 Celery 清理（2026-08-18 修复：

        _should_skip_exit_hooks 此前是死代码从未被调用，测试碰过
        _stop_celery_* 后 atexit 真的 SIGKILL 了宿主机生产 Celery）。
        """
        import app.main as m

        monkeypatch.setenv("CLPM_SKIP_EXIT_HOOKS", "1")
        m._celery_shutdown_done = False
        m._celery_ever_touched = True
        try:
            with (
                patch("app.main._stop_celery_worker") as mock_stop_w,
                patch("app.main._stop_celery_beat") as mock_stop_b,
            ):
                m._shutdown_celery_once()
            mock_stop_w.assert_not_called()
            mock_stop_b.assert_not_called()
        finally:
            m._celery_shutdown_done = False
            m._celery_ever_touched = False

    def test_celery_signals_connected(self):
        """beat/worker/worker-pool 三个生命周期入口都已挂看门狗（结构性守护）。"""
        from celery.signals import beat_init, worker_process_init, worker_ready

        from app.tasks import celery_app as ca

        beat_receivers = {fn.__name__ for fn in beat_init._live_receivers(None)}
        worker_receivers = {fn.__name__ for fn in worker_ready._live_receivers(None)}
        pool_receivers = {fn.__name__ for fn in worker_process_init._live_receivers(None)}
        assert ca._on_beat_init.__name__ in beat_receivers
        assert ca._on_worker_ready.__name__ in worker_receivers
        assert ca._on_worker_process_init.__name__ in pool_receivers
