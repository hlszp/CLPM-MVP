"""单片历史网格快速路径保持缺列和质量码语义。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock


async def test_held_config_cache_does_not_leak_sampling_step(monkeypatch):
    from app.services.data_source import logical_wide_builder as builder

    monkeypatch.setattr(builder, "_held_cfg_cache", {})
    monkeypatch.setattr(builder, "_held_cfg_read_at", 0.0)
    result = MagicMock()
    result.all.return_value = [
        (builder.SYSCONF_HELD_FAST_S, "60"),
        (builder.SYSCONF_HELD_SLOW_S, "7200"),
    ]
    execute = AsyncMock(return_value=result)
    monkeypatch.setattr(builder, "_metadata_execute", execute)
    assert await builder._held_limits_s(300) == {"fast": 900, "slow": 7200}
    assert await builder._held_limits_s(1) == {"fast": 60, "slow": 7200}
    assert await builder._held_limits_s(3600) == {"fast": 10800, "slow": 10800}
    execute.assert_awaited_once()


async def test_single_fragment_reuses_ordered_grid_without_losing_missing_columns(monkeypatch):
    from types import SimpleNamespace

    from app.contracts.data_types import RawTimeSeries
    from app.services.data_source import tdengine_provider

    origin = datetime(2026, 10, 18, tzinfo=UTC)
    stamps = [origin + timedelta(seconds=i) for i in range(1000)]
    # builder 出口形态（2026-10-23 全角色质量透传后）：逐请求角色输出
    # {role}_quality；OP 列缺失由 provider 补 None，质量键整体透传不合成
    # （缺省=契约语义"视为全部 Good"，合成 -1 会与 RawTimeSeries 契约矛盾）
    raw = RawTimeSeries(
        timestamps=stamps,
        signals={"pv": [1.0] * 1000},
        quality_codes={"pv_quality": [1] * 1000},
    )
    monkeypatch.setattr(
        "app.services.data_source.logical_wide_builder.build_logical_wide",
        AsyncMock(return_value=raw),
    )
    result = await tdengine_provider._point_or_mixed_query(
        None,
        "loop",
        ["PV", "OP"],
        stamps[0],
        stamps[-1],
        1,
        [SimpleNamespace(start=stamps[0], end=stamps[-1])],
    )
    assert result.timestamps is stamps
    assert result.signals["pv"] is raw.signals["pv"]
    assert result.signals["op"] == [None] * 1000
    assert result.quality_codes == {"pv_quality": [1] * 1000}
