"""Celery worker sys_config 预载测试.

回归背景：PR #75 将业务 URL/Token 从 .env 移除，改由 sys_config 管理，
但预载（preload_datasource_config）只在 FastAPI lifespan 执行。Celery worker
是独立进程，不经过 lifespan，导致 worker 内 settings.HISTORY_DATA_API_URL 为空，
历史数据导入等任务报 "HISTORY_DATA_API_URL 未配置"。修复方式：
worker_process_init 信号处理器为每个 prefork 子进程执行预载。
"""

from unittest.mock import AsyncMock, MagicMock, patch

from celery.signals import worker_process_init

from app.tasks.celery_app import _on_worker_process_init, _preload_datasource_config_sync


class TestWorkerPreloadSignal:
    """worker_process_init 预载处理器的行为与注册。"""

    def test_signal_triggers_handler(self):
        """worker_process_init 信号必须触发预载（验证处理器已注册）。"""
        with patch("app.tasks.celery_app._preload_datasource_config_sync") as mock_preload:
            worker_process_init.send(sender="test")
            mock_preload.assert_called()

    def test_preload_failure_does_not_raise(self):
        """预载失败（如 DB 未就绪）必须被吞掉，不阻塞 worker 启动。"""
        with patch(
            "app.tasks.celery_app._preload_datasource_config_sync",
            side_effect=RuntimeError("db down"),
        ):
            # 不应抛出异常
            _on_worker_process_init()

    def test_sync_wrapper_runs_async_preload(self):
        """同步封装内部应创建事件循环并以 AsyncSession 调用 preload。"""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_preload = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal", return_value=mock_session),
            patch(
                "app.services.datasource_config.preload_datasource_config",
                mock_preload,
            ),
        ):
            _preload_datasource_config_sync()
            mock_preload.assert_awaited_once_with(mock_session)
