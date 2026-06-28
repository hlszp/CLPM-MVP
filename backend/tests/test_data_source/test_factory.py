"""数据源工厂单元测试.

验证 ``get_provider`` 根据 ``DATA_SOURCE_TYPE`` 配置返回对应 Provider 实现，
且单例缓存生效。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.data_source import HistoryDataProvider, get_provider
from app.services.data_source import factory as factory_module
from app.services.data_source.base import HistoryDataProvider as BaseProvider
from app.services.data_source.remote_api_provider import RemoteApiProvider
from app.services.data_source.tdengine_provider import TDengineProvider


@pytest.fixture(autouse=True)
def reset_provider_singleton():
    """每个测试前后重置工厂单例，避免测试间相互影响."""
    original = factory_module._provider_instance
    factory_module._provider_instance = None
    yield
    factory_module._provider_instance = original


def test_get_provider_returns_tdengine_by_default():
    """默认配置应返回 TDengineProvider."""
    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "tdengine"
        provider = get_provider()
        assert isinstance(provider, TDengineProvider)
        assert isinstance(provider, BaseProvider)


def test_get_provider_returns_remote_api_when_configured():
    """DATA_SOURCE_TYPE=remote_api 应返回 RemoteApiProvider."""
    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "remote_api"
        provider = get_provider()
        assert isinstance(provider, RemoteApiProvider)
        assert isinstance(provider, BaseProvider)


def test_get_provider_is_singleton():
    """同一进程内多次调用应返回同一实例."""
    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "tdengine"
        p1 = get_provider()
        p2 = get_provider()
        assert p1 is p2


def test_get_provider_case_insensitive():
    """DATA_SOURCE_TYPE 大小写不敏感."""
    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "TDENGINE"
        provider = get_provider()
        assert isinstance(provider, TDengineProvider)


def test_get_provider_raises_on_unsupported_type():
    """不支持的类型应抛出 ValueError."""
    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "mongodb"
        with pytest.raises(ValueError, match="不支持的 DATA_SOURCE_TYPE"):
            get_provider()


def test_provider_implements_protocol():
    """Provider 实现应满足 HistoryDataProvider Protocol."""
    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "tdengine"
        provider = get_provider()
        assert isinstance(provider, HistoryDataProvider)
        # Protocol runtime check
        assert hasattr(provider, "make_query_fn")
        assert hasattr(provider, "close")


@pytest.mark.asyncio
async def test_close_provider_resets_singleton():
    """close_provider 应关闭并重置单例."""
    from unittest.mock import AsyncMock

    with patch("app.services.data_source.factory.settings") as mock_settings:
        mock_settings.DATA_SOURCE_TYPE = "tdengine"
        provider = get_provider()
        assert factory_module._provider_instance is provider

        # 替换 close 为 AsyncMock 避免真实关闭
        provider.close = AsyncMock()

        await factory_module.close_provider()
        assert factory_module._provider_instance is None
        provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_provider_when_none_is_noop():
    """Provider 未初始化时 close_provider 是空操作."""
    factory_module._provider_instance = None
    await factory_module.close_provider()  # 不应抛出异常
    assert factory_module._provider_instance is None
