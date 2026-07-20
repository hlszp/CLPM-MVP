"""数据源工厂单元测试.

架构决策（2026-07-20）：导入走远端、计算全本地。
``get_provider`` 不再按 ``DATA_SOURCE_TYPE`` 分支，计算类历史数据查询
一律返回本地 TDengineProvider；远端 API 仅历史数据导入任务直接调用
（data_import.py 自带独立客户端，不经本工厂）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.data_source import HistoryDataProvider, get_provider
from app.services.data_source import factory as factory_module
from app.services.data_source.base import HistoryDataProvider as BaseProvider
from app.services.data_source.tdengine_provider import TDengineProvider


@pytest.fixture(autouse=True)
def reset_provider_singleton():
    """每个测试前后重置工厂单例，避免测试间相互影响."""
    original = factory_module._provider_instance
    factory_module._provider_instance = None
    yield
    factory_module._provider_instance = original


def test_get_provider_always_returns_tdengine():
    """无论 DATA_SOURCE_TYPE 为何，计算类查询一律返回本地 TDengineProvider."""
    provider = get_provider()
    assert isinstance(provider, TDengineProvider)
    assert isinstance(provider, BaseProvider)


def test_get_provider_is_singleton():
    """同一进程内多次调用应返回同一实例."""
    p1 = get_provider()
    p2 = get_provider()
    assert p1 is p2


def test_provider_implements_protocol():
    """Provider 实现应满足 HistoryDataProvider Protocol."""
    provider = get_provider()
    assert isinstance(provider, HistoryDataProvider)
    # Protocol runtime check
    assert hasattr(provider, "make_query_fn")
    assert hasattr(provider, "close")


@pytest.mark.asyncio
async def test_close_provider_resets_singleton():
    """close_provider 应关闭并重置单例."""
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
