"""Celery Beat 单例防护测试.

回归背景（2026-07-20）：手工启动的 beat 与 backend lifespan 自动启动的 beat
共用同一 celerybeat.pid 路径，pidfile 被覆盖后手工 beat 对 pidfile 检查不可见，
导致两个 beat 并存、每个定时任务双触发（43 组同标题 STANDARD 任务）。
修复：pidfile 检查之外增加 pgrep 兜底扫描。
"""

import os
from unittest.mock import MagicMock, patch

from app.main import _any_beat_process_running, _start_celery_beat


class TestBeatSingletonGuard:
    """_start_celery_beat 的重复启动防护。"""

    def test_skip_when_pidfile_alive(self, tmp_path, monkeypatch):
        """pidfile 指向活进程时跳过启动。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "celerybeat.pid").write_text(str(os.getpid()))

        with patch("app.main.subprocess.Popen") as mock_popen:
            _start_celery_beat()
            mock_popen.assert_not_called()

    def test_start_when_pidfile_dead(self, tmp_path, monkeypatch):
        """pidfile 指向死进程时清理并启动新 beat。"""
        monkeypatch.chdir(tmp_path)
        pid_file = tmp_path / "celerybeat.pid"
        pid_file.write_text("999999999")

        with (
            patch("app.main.subprocess.Popen") as mock_popen,
            patch("app.main._any_beat_process_running", return_value=False),
        ):
            _start_celery_beat()
            mock_popen.assert_called_once()
        # 遗留 pidfile 已被清理
        assert not pid_file.exists()

    def test_skip_when_pgrep_finds_beat(self, tmp_path, monkeypatch):
        """pidfile 缺失/失效但 pgrep 发现 beat 进程时跳过启动（兜底场景）。"""
        monkeypatch.chdir(tmp_path)

        with (
            patch("app.main.subprocess.Popen") as mock_popen,
            patch("app.main._any_beat_process_running", return_value=True),
        ):
            _start_celery_beat()
            mock_popen.assert_not_called()

    def test_start_when_no_beat_anywhere(self, tmp_path, monkeypatch):
        """pidfile 与 pgrep 均无 beat 时正常启动。"""
        monkeypatch.chdir(tmp_path)

        with (
            patch("app.main.subprocess.Popen") as mock_popen,
            patch("app.main._any_beat_process_running", return_value=False),
        ):
            _start_celery_beat()
            mock_popen.assert_called_once()


class TestAnyBeatProcessRunning:
    """pgrep 兜底扫描的行为。"""

    def test_true_when_pgrep_matches(self):
        with patch("app.main.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="123\n456\n")
            assert _any_beat_process_running() is True

    def test_false_when_no_match(self):
        with patch("app.main.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert _any_beat_process_running() is False

    def test_false_when_pgrep_unavailable(self):
        with patch("app.main.subprocess.run", side_effect=OSError("no pgrep")):
            assert _any_beat_process_running() is False
