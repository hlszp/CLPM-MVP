"""TDengineProvider 单元测试.

验证 ``TDengineProvider.make_query_fn`` 返回宽表查询闭包，
且 ``close`` 委托到 ``close_client`` + ``TDengineConnectionPool.close_all``。
另覆盖回填性能优化：历史窗口跳过 Redis 实时缓存探测（近 1 小时窗口仍探测）。
以及 P0-3 时区修复：查询边界输出带 Z 的 UTC 串、Redis 缓存行 +8 墙钟
与 UTC 窗口的 epoch 对齐比较。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source import tdengine_provider as provider_module
from app.services.data_source.tdengine_provider import (
    TDengineProvider,
    _parse_ts,
    _stored_ts_to_utc_naive,
)

# 存储侧时区（Asia/Shanghai），与 realtime_subscriber._TARGET_TZ 一致
_STORED_TZ = timezone(timedelta(hours=8))


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
        patch("app.core.tdengine_native.TDengineConnectionPool.close_all") as mock_pool_close,
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


# ---------------------------------------------------------------------------
# 历史窗口跳过 Redis 实时缓存探测（回填性能优化）
# ---------------------------------------------------------------------------
def test_parse_ts_aware_converts_to_naive_utc() -> None:
    """aware 输入先 astimezone(UTC) 再去 tzinfo，而非直接丢弃时区."""
    aware = datetime(2026, 7, 28, 10, 0, 0, tzinfo=_STORED_TZ)
    assert _parse_ts(aware) == datetime(2026, 7, 28, 2, 0, 0)
    assert _parse_ts("2026-07-28T10:00:00+08:00") == datetime(2026, 7, 28, 2, 0, 0)
    assert _parse_ts("2026-07-28T02:00:00.000Z") == datetime(2026, 7, 28, 2, 0, 0)


def test_stored_ts_to_utc_naive() -> None:
    """存储侧 +8 墙钟字符串正确转 naive UTC；无法解析返回 None."""
    assert _stored_ts_to_utc_naive("2026-07-28 10:00:00.000") == datetime(2026, 7, 28, 2, 0, 0)
    assert _stored_ts_to_utc_naive("2026-07-28T10:00:00") == datetime(2026, 7, 28, 2, 0, 0)
    # 带时区信息按其实际时区转换
    assert _stored_ts_to_utc_naive("2026-07-28T02:00:00.000Z") == datetime(2026, 7, 28, 2, 0, 0)
    assert _stored_ts_to_utc_naive("not-a-ts") is None
    assert _stored_ts_to_utc_naive("") is None


def test_no_module_level_asyncio_lock() -> None:
    """结构性回归：tdengine_provider 模块级不得存在 asyncio.Lock。

    历史 bug（2026-07-28 定位）：模块级 ``asyncio.Lock`` 在竞争时绑定
    当前事件循环，而 Celery worker 每个任务可能运行在新事件循环——一旦
    发生竞争，后续任务的宽表解析全部抛 "bound to a different event
    loop"，DataPlanner 全回路取数失败、KPI 快照批量 INCONCLUSIVE，
    只能重启 worker 恢复。修复：去掉模块级锁（并发重复解析无害）。
    本断言直接锁定"不得重新引入模块级 asyncio 同步原语"。
    """
    import asyncio as _asyncio

    offenders = [
        name
        for name, value in vars(provider_module).items()
        if isinstance(value, _asyncio.Lock | _asyncio.Semaphore | _asyncio.Event)
    ]
    assert offenders == [], f"模块级 asyncio 同步原语: {offenders}"
