"""TDengineProvider 单元测试.

验证 ``TDengineProvider.make_query_fn`` 正确包装现有
``make_dataplanner_query_fn`` 适配器闭包，且 ``close`` 委托到
``close_client``。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source.tdengine_provider import TDengineProvider


def test_make_query_fn_delegates_to_make_dataplanner_query_fn():
    """make_query_fn 应返回 make_dataplanner_query_fn 的闭包."""
    mock_db = MagicMock()
    expected_fn = MagicMock(name="query_fn_closure")

    with patch(
        "app.core.tdengine.make_dataplanner_query_fn", return_value=expected_fn
    ) as mock_make:
        provider = TDengineProvider()
        result = provider.make_query_fn(mock_db)

        mock_make.assert_called_once_with(mock_db)
        assert result is expected_fn


def test_make_query_fn_with_different_db_instances():
    """不同 db 实例应传递给 make_dataplanner_query_fn."""
    db1 = MagicMock(name="db1")
    db2 = MagicMock(name="db2")
    fn1 = MagicMock(name="fn1")
    fn2 = MagicMock(name="fn2")

    with patch(
        "app.core.tdengine.make_dataplanner_query_fn", side_effect=[fn1, fn2]
    ) as mock_make:
        provider = TDengineProvider()
        result1 = provider.make_query_fn(db1)
        result2 = provider.make_query_fn(db2)

        assert mock_make.call_count == 2
        mock_make.assert_any_call(db1)
        mock_make.assert_any_call(db2)
        assert result1 is fn1
        assert result2 is fn2


@pytest.mark.asyncio
async def test_close_delegates_to_close_client():
    """close 应调用 app.core.tdengine.close_client."""
    with patch("app.core.tdengine.close_client", new=AsyncMock()) as mock_close:
        provider = TDengineProvider()
        await provider.close()
        mock_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_is_idempotent():
    """多次调用 close 不应报错."""
    with patch("app.core.tdengine.close_client", new=AsyncMock()):
        provider = TDengineProvider()
        await provider.close()
        await provider.close()  # 不应抛出异常


def test_provider_satisfies_protocol():
    """TDengineProvider 应满足 HistoryDataProvider Protocol."""
    from app.services.data_source.base import HistoryDataProvider

    provider = TDengineProvider()
    assert isinstance(provider, HistoryDataProvider)
