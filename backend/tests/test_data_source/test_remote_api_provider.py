"""RemoteApiProvider 单元测试.

覆盖：
- make_query_fn 返回的闭包正确解析 API 响应
- 质量码映射（1/192=Good→1，其余→0）
- 错误处理（HTTP 错误、业务错误、网络异常）
- 空数据情况（无 Tag 映射、空响应）
- 时间戳解析
- httpx 客户端 Celery-safe 重建
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.data_source.remote_api_provider import (
    _GOOD_QUALITY_CODES,
    RemoteApiProvider,
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_mapping(tag_role: str, tag_id: str):
    """构造 LoopTagMapping mock."""
    m = MagicMock()
    m.tag_role = tag_role
    m.tag_id = tag_id
    return m


def _make_tag(tag_id: str, tag_name: str):
    """构造 TagRegistry mock."""
    t = MagicMock()
    t.id = tag_id
    t.tag_name = tag_name
    return t


def _make_db_mock(mappings: list, tags: list):
    """构造 AsyncMock db，按调用顺序返回 mappings 和 tags."""
    db = AsyncMock()

    mapping_result = MagicMock()
    mapping_result.scalars.return_value.all.return_value = mappings

    tag_result = MagicMock()
    tag_result.scalars.return_value.all.return_value = tags

    # 第一次 execute 返回 mappings，第二次返回 tags
    db.execute = AsyncMock(side_effect=[mapping_result, tag_result])
    return db


def _make_api_response(
    timestamps: list[str] | None = None,
    series: list[dict] | None = None,
    code: int = 200,
    status_code: int = 200,
):
    """构造 httpx.Response mock."""
    resp = MagicMock()
    resp.status_code = status_code
    payload = {
        "code": code,
        "message": "Success" if code == 200 else "Error",
        "data": {
            "timestamps": timestamps or [],
            "series": series or [],
        },
    }
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


# ---------------------------------------------------------------------------
# 质量码常量测试
# ---------------------------------------------------------------------------


def test_good_quality_codes_includes_1_and_192():
    """Good 质量码集合应包含 1（API 规范）和 192（OPC DA）."""
    assert 1 in _GOOD_QUALITY_CODES
    assert 192 in _GOOD_QUALITY_CODES
    assert 0 not in _GOOD_QUALITY_CODES
    assert 2 not in _GOOD_QUALITY_CODES
    assert 3 not in _GOOD_QUALITY_CODES


# ---------------------------------------------------------------------------
# make_query_fn 闭包测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_fn_parses_successful_response():
    """闭包应正确解析成功的 API 响应."""
    # 准备 mock 数据
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    sp_tag_id = "00000000-0000-0000-0000-000000000002"
    mappings = [_make_mapping("PV", pv_tag_id), _make_mapping("SP", sp_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV"), _make_tag(sp_tag_id, "LIC-101.SP")]
    db = _make_db_mock(mappings, tags)

    # 准备 API 响应
    api_response = _make_api_response(
        timestamps=["2026-06-28T08:00:00", "2026-06-28T08:00:01"],
        series=[
            {
                "tagCode": "LIC-101.PV",
                "values": ["50.5", "51.0"],
                "qualities": [1, 1],  # 全部 Good
            },
            {
                "tagCode": "LIC-101.SP",
                "values": ["50.0", "50.0"],
                "qualities": [1, 1],
            },
        ],
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv", "sp"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert len(result.timestamps) == 2
    assert "pv" in result.signals
    assert "sp" in result.signals
    assert result.signals["pv"] == [50.5, 51.0]
    assert result.signals["sp"] == [50.0, 50.0]
    # PV 质量码应映射为 1（Good）
    assert "pv_quality" in result.quality_codes
    assert result.quality_codes["pv_quality"] == [1, 1]


@pytest.mark.asyncio
async def test_query_fn_maps_quality_codes_correctly():
    """质量码映射：1/192→1（Good），其余→0（Bad）."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "TIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    api_response = _make_api_response(
        timestamps=["t1", "t2", "t3", "t4"],
        series=[
            {
                "tagCode": "TIC-101.PV",
                "values": ["10", "20", "30", "40"],
                "qualities": [1, 192, 2, 3],  # Good, OPC-Good, Bad, 离线
            },
        ],
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    # 1→1, 192→1, 2→0, 3→0
    assert result.quality_codes["pv_quality"] == [1, 1, 0, 0]


@pytest.mark.asyncio
async def test_query_fn_returns_empty_when_no_tag_mapping():
    """无 Tag 映射时应返回空 RawTimeSeries."""
    db = _make_db_mock(mappings=[], tags=[])

    provider = RemoteApiProvider()
    query_fn = provider.make_query_fn(db)
    result = await query_fn(
        "loop-empty",
        ["pv", "sp"],
        datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
        1,
    )

    assert result.timestamps == []
    assert result.signals == {}
    assert result.quality_codes == {}


@pytest.mark.asyncio
async def test_query_fn_handles_http_error():
    """HTTP 非 200 应返回空 RawTimeSeries."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    error_response = MagicMock()
    error_response.status_code = 500
    error_response.text = "Internal Server Error"

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=error_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.timestamps == []
    assert result.signals == {}


@pytest.mark.asyncio
async def test_query_fn_handles_business_error():
    """API 业务错误（code != 200）应返回空."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    error_response = _make_api_response(code=500, status_code=200)

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=error_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.timestamps == []


@pytest.mark.asyncio
async def test_query_fn_handles_network_exception():
    """网络异常应返回空 RawTimeSeries."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    provider = RemoteApiProvider()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    with patch.object(provider, "_get_client", new=AsyncMock(return_value=mock_client)):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.timestamps == []
    assert result.signals == {}


@pytest.mark.asyncio
async def test_query_fn_parses_iso_timestamps():
    """应正确解析 ISO 8601 时间戳."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    api_response = _make_api_response(
        timestamps=["2026-06-28T08:00:00Z", "2026-06-28T08:00:01Z"],
        series=[{"tagCode": "LIC-101.PV", "values": ["10", "20"], "qualities": [1, 1]}],
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert len(result.timestamps) == 2
    assert isinstance(result.timestamps[0], datetime)


@pytest.mark.asyncio
async def test_query_fn_handles_empty_string_values():
    """空字符串值应转为 None."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    api_response = _make_api_response(
        timestamps=["t1", "t2"],
        series=[{"tagCode": "LIC-101.PV", "values": ["", "10.5"], "qualities": [0, 1]}],
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.signals["pv"] == [None, 10.5]


@pytest.mark.asyncio
async def test_query_fn_skips_missing_tag_in_series():
    """API series 中缺失的 tag 应返回空列表."""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    sp_tag_id = "00000000-0000-0000-0000-000000000002"
    mappings = [_make_mapping("PV", pv_tag_id), _make_mapping("SP", sp_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV"), _make_tag(sp_tag_id, "LIC-101.SP")]
    db = _make_db_mock(mappings, tags)

    # API 只返回 PV，不返回 SP
    api_response = _make_api_response(
        timestamps=["t1", "t2"],
        series=[{"tagCode": "LIC-101.PV", "values": ["10", "20"], "qualities": [1, 1]}],
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv", "sp"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.signals["pv"] == [10.0, 20.0]
    # SP 缺失，应按统一时间轴补 None，保持 RawTimeSeries 对齐契约
    assert result.signals["sp"] == [None, None]


@pytest.mark.asyncio
async def test_query_fn_accepts_string_success_code_and_case_insensitive_tag_code():
    """兼容字符串成功码和大小写不一致的 tagCode。"""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    api_response = _make_api_response(
        timestamps=["2026-06-28T08:00:00", "2026-06-28T08:00:01"],
        series=[{"tagCode": "lic-101.pv", "values": ["10", "11"], "qualities": ["1", "2"]}],
        code="200",
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.signals["pv"] == [10.0, 11.0]
    assert result.quality_codes["pv_quality"] == [1, 0]


@pytest.mark.asyncio
async def test_query_fn_pads_short_values_and_missing_qualities():
    """values/qualities 短于 timestamps 时按统一时间轴补齐。"""
    pv_tag_id = "00000000-0000-0000-0000-000000000001"
    mappings = [_make_mapping("PV", pv_tag_id)]
    tags = [_make_tag(pv_tag_id, "LIC-101.PV")]
    db = _make_db_mock(mappings, tags)

    api_response = _make_api_response(
        timestamps=["t1", "t2", "t3"],
        series=[{"tagCode": "LIC-101.PV", "values": ["10"], "qualities": [1]}],
    )

    provider = RemoteApiProvider()
    with patch.object(
        provider,
        "_get_client",
        new=AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=api_response))),
    ):
        query_fn = provider.make_query_fn(db)
        result = await query_fn(
            "loop-001",
            ["pv"],
            datetime(2026, 6, 28, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC),
            1,
        )

    assert result.signals["pv"] == [10.0, None, None]
    assert result.quality_codes["pv_quality"] == [1, 0, 0]


# ---------------------------------------------------------------------------
# httpx 客户端管理测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_client_creates_singleton():
    """_get_client 应创建单例客户端（同一 event loop 内复用）."""
    provider = RemoteApiProvider()
    with patch("app.services.data_source.remote_api_provider.settings") as mock_settings:
        mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
        mock_settings.HISTORY_DATA_API_TOKEN = ""

        client1 = await provider._get_client()
        # 验证内部状态：单例已缓存且 loop 已绑定
        assert provider._client is client1
        assert provider._client_loop is not None

        client2 = await provider._get_client()
        # 同一 loop 内应返回同一实例
        assert client2 is client1

        await provider.close()
        assert provider._client is None


@pytest.mark.asyncio
async def test_get_client_adds_auth_header_when_token_set():
    """配置 TOKEN 时应在 header 中添加 Bearer Token."""
    provider = RemoteApiProvider()
    with patch("app.services.data_source.remote_api_provider.settings") as mock_settings:
        mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
        mock_settings.HISTORY_DATA_API_TOKEN = "test-token-123"

        client = await provider._get_client()
        assert client.headers.get("Authorization") == "Bearer test-token-123"

        await provider.close()


@pytest.mark.asyncio
async def test_close_resets_client():
    """close 应关闭并重置客户端."""
    provider = RemoteApiProvider()
    with patch("app.services.data_source.remote_api_provider.settings") as mock_settings:
        mock_settings.HISTORY_DATA_API_TIMEOUT = 30.0
        mock_settings.HISTORY_DATA_API_TOKEN = ""

        await provider._get_client()
        assert provider._client is not None

        await provider.close()
        assert provider._client is None
        assert provider._client_loop is None


def test_provider_satisfies_protocol():
    """RemoteApiProvider 应满足 HistoryDataProvider Protocol."""
    from app.services.data_source.base import HistoryDataProvider

    provider = RemoteApiProvider()
    assert isinstance(provider, HistoryDataProvider)
