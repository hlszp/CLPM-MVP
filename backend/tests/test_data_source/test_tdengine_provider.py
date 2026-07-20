"""TDengineProvider 单元测试.

验证 ``TDengineProvider.make_query_fn`` 返回宽表查询闭包，
且 ``close`` 委托到 ``close_client`` + ``TDengineConnectionPool.close_all``。
另覆盖回填性能优化：历史窗口跳过 Redis 实时缓存探测（近 1 小时窗口仍探测）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source import tdengine_provider as provider_module
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


def _make_db_with_mapping(tag_name: str = "TC101.pv") -> AsyncMock:
    """构造可解析宽表名的 mock db（LoopTagMapping + TagRegistry 各一次查询）."""
    mapping = MagicMock()
    mapping.tag_id = "tag-1"
    tag = MagicMock()
    tag.tag_name = tag_name

    mapping_result = MagicMock()
    mapping_result.scalars.return_value.all.return_value = [mapping]
    tag_result = MagicMock()
    tag_result.scalars.return_value.all.return_value = [tag]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[mapping_result, tag_result])
    return db


def _wide_rows(n: int = 3) -> list[dict]:
    """构造宽表查询返回行（含 COV 列与 PV 质量码）."""
    base = datetime(2024, 1, 1, 10, 0, 0)
    return [
        {
            "ts": base + timedelta(seconds=i),
            "pv": 50.0 + i,
            "sp": 50.0,
            "mode": 1,
            "pid_p": None,
            "pid_i": None,
            "pid_d": None,
            "pv_quality": 192,
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_historical_window_skips_redis_probe() -> None:
    """历史窗口（end 早于 now-65min）应跳过 Redis 实时缓存探测，直接查宽表."""
    provider_module._subtable_cache.clear()  # 模块级缓存，避免跨测试污染

    subscriber = MagicMock()
    subscriber.get_history_values = AsyncMock()

    with (
        patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=subscriber,
        ),
        patch(
            "app.core.tdengine_native.query_wide_table_native",
            new=AsyncMock(return_value=_wide_rows()),
        ) as mock_wide,
        patch(
            "app.core.tdengine_native.query_last_values_before",
            new=AsyncMock(return_value={}),
        ),
    ):
        # make_query_fn 内部 from-import 宽表查询函数，需在 patch 生效后创建闭包
        query_fn = TDengineProvider().make_query_fn(_make_db_with_mapping())
        end = datetime.now(UTC) - timedelta(hours=2)
        start = end - timedelta(hours=1)
        result = await query_fn(
            loop_id="loop-hist",
            tag_roles=["pv"],
            start=start,
            end=end,
            interval_s=1,
        )

    # 历史窗口必然 miss → 不探测 Redis，直接回退宽表查询
    subscriber.get_history_values.assert_not_called()
    mock_wide.assert_awaited_once()
    # 行处理结果正确（宽表 3 行 → 3 个采样点）
    assert len(result.timestamps) == 3
    assert result.signals["pv"] == [50.0, 51.0, 52.0]
    assert result.quality_codes["pv_quality"] == [192, 192, 192]


@pytest.mark.asyncio
async def test_recent_window_still_probes_redis() -> None:
    """近 1 小时窗口保持原有探测行为：调用 subscriber.get_history_values."""
    provider_module._subtable_cache.clear()

    subscriber = MagicMock()
    subscriber.get_history_values = AsyncMock(return_value=[])

    with (
        patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=subscriber,
        ),
        patch(
            "app.core.tdengine_native.query_wide_table_native",
            new=AsyncMock(return_value=_wide_rows()),
        ) as mock_wide,
        patch(
            "app.core.tdengine_native.query_last_values_before",
            new=AsyncMock(return_value={}),
        ),
    ):
        query_fn = TDengineProvider().make_query_fn(_make_db_with_mapping())
        end = datetime.now(UTC)
        start = end - timedelta(minutes=30)
        result = await query_fn(
            loop_id="loop-recent",
            tag_roles=["pv"],
            start=start,
            end=end,
            interval_s=1,
        )

    # 近 1 小时窗口仍探测 Redis（空缓存 → 回退宽表查询）
    subscriber.get_history_values.assert_awaited_once()
    mock_wide.assert_awaited_once()
    assert len(result.timestamps) == 3
