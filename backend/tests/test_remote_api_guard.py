"""远端 API 调用保护（限流 + 熔断）与 SignalR 退避测试.

背景：DataPlanner 用无界 asyncio.gather 并发查询远端边缘 API，
回填时 8 worker × ~54 并发可把服务线程池打满（2026-07-19 实测压垮
192.168.100.2:81）；服务挂死后无熔断机制继续施压。修复：
RemoteApiProvider 增加 per-loop 信号量限流 + 连续失败熔断快速失败；
SignalR 重连改指数退避。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import settings
from app.services.data_source.realtime_subscriber import next_reconnect_delay
from app.services.data_source.remote_api_provider import RemoteApiProvider


def _make_provider() -> RemoteApiProvider:
    return RemoteApiProvider()


def _mock_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "err" if status_code != 200 else ""
    resp.json.return_value = {"code": 200, "data": {"timestamps": [], "series": []}}
    return resp


class TestRateLimitSemaphore:
    """per-loop 限流信号量。"""

    @pytest.mark.asyncio
    async def test_semaphore_value_and_reuse(self):
        """同一 event loop 内复用同一信号量，上限取配置值。"""
        provider = _make_provider()
        sem1 = provider._get_semaphore()
        sem2 = provider._get_semaphore()
        assert sem1 is sem2
        assert sem1._value == settings.REMOTE_API_MAX_CONCURRENCY

    def test_semaphore_recreated_for_new_loop(self):
        """event loop 变化时重建信号量（Celery 每任务新 loop 场景）。"""
        provider = _make_provider()

        async def _get():
            return provider._get_semaphore()

        import asyncio

        sem_a = asyncio.run(_get())
        sem_b = asyncio.run(_get())
        assert sem_a is not sem_b

    @pytest.mark.asyncio
    async def test_guarded_get_acquires_semaphore(self):
        """_guarded_get 必须经信号量限流。"""
        provider = _make_provider()
        client = MagicMock()
        client.get = AsyncMock(return_value=_mock_response(200))
        sem = provider._get_semaphore()

        with patch.object(provider, "_get_client", new=AsyncMock(return_value=client)):
            await provider._guarded_get({"tagCodes": ["X"]})

        # 请求完成后信号量已释放
        assert sem._value == settings.REMOTE_API_MAX_CONCURRENCY
        client.get.assert_awaited_once()


class TestCircuitBreakerState:
    """熔断器状态机（同步逻辑）。"""

    def test_failures_open_circuit_at_threshold(self):
        provider = _make_provider()
        assert not provider._cb_is_open()
        for _ in range(settings.REMOTE_API_CIRCUIT_FAILURES):
            provider._cb_on_failure("test")
        assert provider._cb_is_open()
        assert provider._cb_fail_fast() is True

    def test_success_resets_circuit(self):
        provider = _make_provider()
        for _ in range(settings.REMOTE_API_CIRCUIT_FAILURES):
            provider._cb_on_failure("test")
        provider._cb_on_success()
        assert not provider._cb_is_open()
        assert provider._cb_failures == 0
        assert provider._cb_fail_fast() is False

    def test_below_threshold_stays_closed(self):
        provider = _make_provider()
        for _ in range(settings.REMOTE_API_CIRCUIT_FAILURES - 1):
            provider._cb_on_failure("test")
        assert not provider._cb_is_open()

    def test_half_open_probe_failure_reopens(self):
        """熔断到期后半开探测：探测失败立即重新熔断。"""
        provider = _make_provider()
        for _ in range(settings.REMOTE_API_CIRCUIT_FAILURES):
            provider._cb_on_failure("test")
        # 模拟熔断期已过
        provider._cb_open_until = 0.0
        assert provider._cb_fail_fast() is False  # 半开：允许探测
        provider._cb_on_failure("probe failed")  # 探测失败
        assert provider._cb_is_open()  # 立即重新熔断


class TestGuardedGet:
    """_guarded_get 的熔断计数与快速失败。"""

    @pytest.mark.asyncio
    async def test_200_counts_success(self):
        provider = _make_provider()
        provider._cb_failures = 3
        client = MagicMock()
        client.get = AsyncMock(return_value=_mock_response(200))
        with patch.object(provider, "_get_client", new=AsyncMock(return_value=client)):
            resp = await provider._guarded_get({})
        assert resp is not None
        assert provider._cb_failures == 0

    @pytest.mark.asyncio
    async def test_504_counts_failure(self):
        provider = _make_provider()
        client = MagicMock()
        client.get = AsyncMock(return_value=_mock_response(504))
        with patch.object(provider, "_get_client", new=AsyncMock(return_value=client)):
            resp = await provider._guarded_get({})
        assert resp is not None  # 响应仍返回给调用方处理
        assert provider._cb_failures == 1

    @pytest.mark.asyncio
    async def test_400_not_counted(self):
        """4xx 客户端错误不计入熔断（服务端并未过载）。"""
        provider = _make_provider()
        client = MagicMock()
        client.get = AsyncMock(return_value=_mock_response(400))
        with patch.object(provider, "_get_client", new=AsyncMock(return_value=client)):
            resp = await provider._guarded_get({})
        assert resp is not None
        assert provider._cb_failures == 0

    @pytest.mark.asyncio
    async def test_exception_counts_failure_and_returns_none(self):
        provider = _make_provider()
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        with patch.object(provider, "_get_client", new=AsyncMock(return_value=client)):
            resp = await provider._guarded_get({})
        assert resp is None
        assert provider._cb_failures == 1

    @pytest.mark.asyncio
    async def test_open_circuit_fails_fast_without_http(self):
        """熔断中：不发 HTTP 请求，直接返回 None。"""
        provider = _make_provider()
        provider._cb_open_until = float("inf")
        client = MagicMock()
        client.get = AsyncMock()
        with patch.object(provider, "_get_client", new=AsyncMock(return_value=client)):
            resp = await provider._guarded_get({})
        assert resp is None
        client.get.assert_not_called()


class TestReconnectBackoff:
    """SignalR 重连指数退避。"""

    def test_doubles(self):
        assert next_reconnect_delay(5, cap=30) == 10
        assert next_reconnect_delay(10, cap=30) == 20

    def test_caps_at_max(self):
        assert next_reconnect_delay(20, cap=30) == 30
        assert next_reconnect_delay(30, cap=30) == 30

    def test_sequence(self):
        """完整退避序列：5 → 10 → 20 → 30 → 30。"""
        delay, cap = 5.0, 30.0
        seq = []
        for _ in range(4):
            delay = next_reconnect_delay(delay, cap=cap)
            seq.append(delay)
        assert seq == [10.0, 20.0, 30.0, 30.0]
