"""TDengineProvider 单元测试.

验证 ``TDengineProvider.make_query_fn`` 返回宽表查询闭包，
且 ``close`` 委托到 ``close_client`` + ``TDengineConnectionPool.close_all``。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source.tdengine_provider import TDengineProvider


def test_make_query_fn_returns_callable():
    """make_query_fn 应返回可调用对象."""
    mock_db = MagicMock()
    provider = TDengineProvider()
    result = provider.make_query_fn(mock_db)
    assert callable(result)


def test_make_query_fn_with_different_db_instances():
    """不同 db 实例应返回不同的闭包."""
    db1 = MagicMock(name="db1")
    db2 = MagicMock(name="db2")

    provider = TDengineProvider()
    result1 = provider.make_query_fn(db1)
    result2 = provider.make_query_fn(db2)

    assert result1 is not result2


@pytest.mark.asyncio
async def test_close_delegates_to_close_client_and_pool():
    """close 应调用 close_client 和 TDengineConnectionPool.close_all."""
    with (
        patch("app.core.tdengine.close_client", new=AsyncMock()) as mock_close,
        patch(
            "app.core.tdengine_native.TDengineConnectionPool.close_all"
        ) as mock_pool_close,
    ):
        provider = TDengineProvider()
        await provider.close()
        mock_close.assert_awaited_once()
        mock_pool_close.assert_called_once()


@pytest.mark.asyncio
async def test_close_is_idempotent():
    """多次调用 close 不应报错."""
    with (
        patch("app.core.tdengine.close_client", new=AsyncMock()),
        patch("app.core.tdengine_native.TDengineConnectionPool.close_all"),
    ):
        provider = TDengineProvider()
        await provider.close()
        await provider.close()  # 不应抛出异常


def test_provider_satisfies_protocol():
    """TDengineProvider 应满足 HistoryDataProvider Protocol."""
    from app.services.data_source.base import HistoryDataProvider

    provider = TDengineProvider()
    assert isinstance(provider, HistoryDataProvider)
