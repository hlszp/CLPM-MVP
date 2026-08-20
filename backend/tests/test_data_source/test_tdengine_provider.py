"""TDengineProvider 单元测试.

验证 ``TDengineProvider.make_query_fn`` 返回宽表查询闭包，
且 ``close`` 委托到 ``close_client`` + ``TDengineConnectionPool.close_all``。
另覆盖回填性能优化：历史窗口跳过 Redis 实时缓存探测（近 1 小时窗口仍探测）。
以及 P0-3 时区修复：查询边界输出带 Z 的 UTC 串、Redis 缓存行 +8 墙钟
与 UTC 窗口的 epoch 对齐比较。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source import tdengine_provider as provider_module
from app.services.data_source.tdengine_provider import (
    TDengineProvider,
    _format_ts,
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


def _make_db_with_mapping(tag_name: str = "TC101") -> AsyncMock:
    """构造可解析宽表名的 mock db（LoopLedger.tag_name 单次查询）.

    2026-08-20 子表名 bug 修复后：_resolve_subtable 只查回路台账 tag_name
    （scalar_one_or_none），不再查 mapping + tag 两步。
    """
    loop_result = MagicMock()
    loop_result.scalar_one_or_none.return_value = tag_name

    db = AsyncMock()
    db.execute = AsyncMock(return_value=loop_result)
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


# ---------------------------------------------------------------------------
# P0-3 时区修复：_format_ts / _parse_ts / _stored_ts_to_utc_naive
# ---------------------------------------------------------------------------


def test_format_ts_naive_datetime_treated_as_utc() -> None:
    """naive datetime 视为 UTC，输出带 Z 的 ISO 串（毫秒精度）."""
    assert _format_ts(datetime(2026, 7, 28, 2, 0, 0)) == "2026-07-28T02:00:00.000Z"
    assert _format_ts(datetime(2026, 7, 28, 2, 0, 0, 123456)) == "2026-07-28T02:00:00.123Z"


def test_format_ts_aware_datetime_converted_to_utc() -> None:
    """aware datetime 先转 UTC 再格式化（+8 墙钟 10:00 → 02:00Z）."""
    aware = datetime(2026, 7, 28, 10, 0, 0, tzinfo=_STORED_TZ)
    assert _format_ts(aware) == "2026-07-28T02:00:00.000Z"
    aware_utc = datetime(2026, 7, 28, 2, 0, 0, tzinfo=UTC)
    assert _format_ts(aware_utc) == "2026-07-28T02:00:00.000Z"


def test_format_ts_string_passthrough() -> None:
    """字符串边界原样透传（趋势路径的 Z 后缀 ISO 串）."""
    assert _format_ts("2026-07-28T02:00:00.000Z") == "2026-07-28T02:00:00.000Z"


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


# ---------------------------------------------------------------------------
# P0-3 时区修复：窗口边界 + Redis 缓存命中路径
# ---------------------------------------------------------------------------


def _redis_history_rows(start_utc: datetime, end_utc: datetime, step_s: int = 300) -> list[dict]:
    """构造 Redis 1 小时缓存行（ts 为 +8 墙钟字符串，与落库口径一致）."""
    rows = []
    t = start_utc
    while t <= end_utc:
        stored = t.replace(tzinfo=UTC).astimezone(_STORED_TZ)
        rows.append(
            {
                "ts": stored.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "pv": 50.0,
                "sp": 50.0,
                "op": 25.0,
                "mode": 1,
                "pid_p": 1.0,
                "pid_i": 0.5,
                "pid_d": 0.1,
                "pv_quality": 100,
            }
        )
        t += timedelta(seconds=step_s)
    return rows


@pytest.mark.asyncio
async def test_window_boundary_hits_stored_plus8_rows() -> None:
    """窗口边界用例：模拟 +8 墙钟存储的行，naive UTC 窗口应精确命中.

    存储侧：+8 墙钟 '2026-07-28 10:00:00' → 实际时刻 2026-07-28T02:00:00Z。
    修复前 _format_ts 输出 naive '2026-07-28 02:00:00.000'，服务器按 +8
    解释（实际过滤 2026-07-27T18:00:00Z 起），必然 miss；修复后输出
    '2026-07-28T02:00:00.000Z'，服务器按 UTC 解释，精确命中。
    """
    provider_module._subtable_cache.clear()

    stored_rows = _wide_rows()  # ts base = 2024-01-01 10:00（mock 直接返回）

    async def fake_wide_query(subtable: str, start_str: str, end_str: str) -> list[dict]:
        # 模拟 TDengine 服务器行为：Z 串按 UTC 解释，存储行实际时刻 02:00Z
        start_utc = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end_utc = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        stored_instant = datetime(2026, 7, 28, 2, 0, 0, tzinfo=UTC)  # +8 墙钟 10:00
        if start_utc <= stored_instant <= end_utc:
            return stored_rows
        return []

    with (
        patch(
            "app.core.tdengine_native.query_wide_table_native",
            new=AsyncMock(side_effect=fake_wide_query),
        ) as mock_wide,
        patch(
            "app.core.tdengine_native.query_last_values_before",
            new=AsyncMock(return_value={}),
        ) as mock_last,
    ):
        query_fn = TDengineProvider().make_query_fn(_make_db_with_mapping())
        # naive UTC 窗口 02:00-03:00（恰覆盖存储时刻 02:00Z），历史窗口跳过 Redis
        start = datetime(2026, 7, 28, 2, 0, 0)
        end = datetime(2026, 7, 28, 3, 0, 0)
        result = await query_fn(
            loop_id="loop-tz",
            tag_roles=["pv"],
            start=start,
            end=end,
            interval_s=1,
        )

    # SQL 边界必须是带 Z 的 UTC 串
    mock_wide.assert_awaited_once_with(
        "d_loop_tc101", "2026-07-28T02:00:00.000Z", "2026-07-28T03:00:00.000Z"
    )
    mock_last.assert_awaited_once_with("d_loop_tc101", "2026-07-28T02:00:00.000Z")
    # 命中存储行（修复前窗口偏移 8 小时会返回空）
    assert len(result.timestamps) == 3
    assert result.signals["pv"] == [50.0, 51.0, 52.0]


@pytest.mark.asyncio
async def test_redis_cache_hit_with_plus8_wallclock_rows() -> None:
    """Redis 缓存命中路径：+8 墙钟 ts 与 UTC 窗口按 epoch 对齐后应命中.

    修复前用窗口字符串（UTC 口径）直接与缓存行 ts（+8 墙钟）比较，恒假，
    缓存永不命中；修复后两侧统一解析为 UTC 时刻比较，缓存真正命中，
    且返回的 timestamps 与宽表路径一致（naive UTC）。
    """
    provider_module._subtable_cache.clear()

    # 微秒清零：缓存行 ts 仅毫秒精度，避免比较时微秒级误差
    end = datetime.now(UTC).replace(microsecond=0, tzinfo=None) - timedelta(minutes=5)
    start = end - timedelta(minutes=20)
    redis_rows = _redis_history_rows(start, end)

    subscriber = MagicMock()
    subscriber.get_history_values = AsyncMock(return_value=redis_rows)

    with (
        patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=subscriber,
        ),
        patch(
            "app.core.tdengine_native.query_wide_table_native",
            new=AsyncMock(return_value=[]),
        ) as mock_wide,
        patch(
            "app.core.tdengine_native.query_last_values_before",
            new=AsyncMock(return_value={}),
        ),
    ):
        query_fn = TDengineProvider().make_query_fn(_make_db_with_mapping())
        result = await query_fn(
            loop_id="loop-cache",
            tag_roles=["pv"],
            start=start,
            end=end,
            interval_s=1,
        )

    # 缓存命中 → 不回退宽表查询
    mock_wide.assert_not_called()
    assert len(result.timestamps) == len(redis_rows)
    # timestamps 为 naive UTC（与宽表路径口径一致），而非 +8 墙钟
    assert all(ts.tzinfo is None for ts in result.timestamps)
    assert result.timestamps[0] == start.replace(microsecond=0)
    assert result.timestamps[-1] == end.replace(microsecond=0)
    assert result.signals["pv"] == [50.0] * len(redis_rows)


@pytest.mark.asyncio
async def test_redis_cache_partial_coverage_falls_back_to_wide_query() -> None:
    """缓存行在窗口内但未完整覆盖窗口（缺口 > 60s 容差）→ 回退宽表查询."""
    provider_module._subtable_cache.clear()

    # 微秒清零：缓存行 ts 仅毫秒精度，避免比较时微秒级误差
    end = datetime.now(UTC).replace(microsecond=0, tzinfo=None) - timedelta(minutes=5)
    start = end - timedelta(minutes=20)
    # 缓存只有窗口中间一段，首尾缺口超 60s 容差
    redis_rows = _redis_history_rows(start + timedelta(minutes=8), end - timedelta(minutes=8))

    subscriber = MagicMock()
    subscriber.get_history_values = AsyncMock(return_value=redis_rows)

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
        result = await query_fn(
            loop_id="loop-cache-partial",
            tag_roles=["pv"],
            start=start,
            end=end,
            interval_s=1,
        )

    mock_wide.assert_awaited_once()
    assert len(result.timestamps) == 3


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


def test_resolve_subtable_survives_across_event_loops() -> None:
    """冒烟：跨事件循环顺序解析两条回路，均正常返回（修复前此路径即
    抛 RuntimeError 的根源路径，配合结构性断言共同防护）。"""
    import asyncio as _asyncio

    async def _concurrent_resolve() -> None:
        """在同一循环内制造解析竞争（旧实现在此绑定锁到本循环）."""
        provider_module._subtable_cache.clear()
        with (
            patch(
                "app.core.tdengine_native.query_wide_table_native",
                new=AsyncMock(return_value=_wide_rows()),
            ),
            patch(
                "app.core.tdengine_native.query_last_values_before",
                new=AsyncMock(return_value={}),
            ),
        ):
            end = datetime.now(UTC) - timedelta(hours=2)
            start = end - timedelta(hours=1)
            fn_a = TDengineProvider().make_query_fn(_make_db_with_mapping())
            fn_b = TDengineProvider().make_query_fn(_make_db_with_mapping())
            await _asyncio.gather(
                fn_a(loop_id="loop-a", tag_roles=["pv"], start=start, end=end, interval_s=1),
                fn_b(loop_id="loop-b", tag_roles=["pv"], start=start, end=end, interval_s=1),
            )

    async def _resolve_once() -> int:
        """在新循环内再次解析（旧实现在此抛 RuntimeError）."""
        provider_module._subtable_cache.clear()
        with (
            patch(
                "app.core.tdengine_native.query_wide_table_native",
                new=AsyncMock(return_value=_wide_rows()),
            ),
            patch(
                "app.core.tdengine_native.query_last_values_before",
                new=AsyncMock(return_value={}),
            ),
        ):
            query_fn = TDengineProvider().make_query_fn(_make_db_with_mapping())
            end = datetime.now(UTC) - timedelta(hours=2)
            start = end - timedelta(hours=1)
            result = await query_fn(
                loop_id="loop-c", tag_roles=["pv"], start=start, end=end, interval_s=1
            )
            return len(result.timestamps)

    _asyncio.run(_concurrent_resolve())  # 事件循环 1：制造竞争
    assert _asyncio.run(_resolve_once()) == 3  # 事件循环 2：不得抛异常
