"""批量波形查询接口测试 (IDS v3.2 §2.4.5).

测试覆盖：
- POST /api/v1/timeseries/batch/waveform — 批量波形查询

设计依据：IDS §2.4.5, 算法说明 §3.4-3.7, 数据流程图 §7
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.exceptions import BizError
from app.schemas.tag import WaveformResponse, WaveformTimeRange
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_waveform_response(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_name: str = "101-FC-1023",
    point_count: int = 3,
) -> WaveformResponse:
    """构造 WaveformResponse 对象."""
    points = []
    for i in range(point_count):
        from app.schemas.tag import WaveformPoint

        points.append(
            WaveformPoint(
                timestamp=f"2026-06-22T08:00:0{i}Z",
                pv=50.0 + i * 0.1,
                sp=50.0,
                op=55.0,
                mode=1,
                pvQuality=1,
                valid=True,
                outlierReason=None,
            )
        )
    return WaveformResponse(
        loopId=loop_id,
        tagName=tag_name,
        timeRange=WaveformTimeRange(
            startTime="2026-06-22T08:00:00Z",
            endTime="2026-06-22T08:00:10Z",
        ),
        points=points,
        samplingFreq="1s",
        qualityPolicy="KEEP_ALL_WITH_VALIDITY",
        validRate=1.0,
        downsampled=False,
        pointCount=point_count,
    )


_BATCH_BODY = {
    "loopIds": [
        "00000000-0000-0000-0000-000000000201",
        "00000000-0000-0000-0000-000000000202",
    ],
    "startTime": "2026-06-22T08:00:00Z",
    "endTime": "2026-06-22T08:00:10Z",
    "tagGroup": "BASE",
    "includeValidMask": True,
    # R21：maxPoints 契约收紧为 100~2000（默认 2000，对齐 LTTB 2000 点契约）
    "maxPoints": 2000,
}


# ---------------------------------------------------------------------------
# POST /api/v1/timeseries/batch/waveform
# ---------------------------------------------------------------------------


class TestBatchWaveform:
    """POST /api/v1/timeseries/batch/waveform tests."""

    def test_batch_waveform_success(self, client, mock_db, fake_redis) -> None:
        """批量查询多个回路波形数据."""
        resp1 = _make_waveform_response(loop_id="00000000-0000-0000-0000-000000000201")
        resp2 = _make_waveform_response(loop_id="00000000-0000-0000-0000-000000000202")

        async def fetch_side_effect(*, loop_id, **kwargs):
            if "201" in loop_id:
                return resp1
            return resp2

        with (
            patch(
                "app.api.v1.endpoints.tags._fetch_waveform_for_loop",
                new_callable=AsyncMock,
                side_effect=fetch_side_effect,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=_BATCH_BODY,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert len(data["failed"]) == 0
        assert data["items"][0]["loopId"] == "00000000-0000-0000-0000-000000000201"
        assert data["items"][1]["loopId"] == "00000000-0000-0000-0000-000000000202"

    def test_batch_waveform_partial_failure(self, client, mock_db, fake_redis) -> None:
        """部分回路查询失败时，失败信息放入 failed 列表."""
        resp_ok = _make_waveform_response(loop_id="00000000-0000-0000-0000-000000000201")

        async def fetch_side_effect(*, loop_id, **kwargs):
            if "201" in loop_id:
                return resp_ok
            raise BizError(
                code="ERR_LOOP_NOT_FOUND",
                message="回路不存在",
                status_code=404,
            )

        with (
            patch(
                "app.api.v1.endpoints.tags._fetch_waveform_for_loop",
                new_callable=AsyncMock,
                side_effect=fetch_side_effect,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=_BATCH_BODY,
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert len(data["failed"]) == 1
        assert data["failed"][0]["loopId"] == "00000000-0000-0000-0000-000000000202"
        assert "ERR_LOOP_NOT_FOUND" in data["failed"][0]["error"]

    def test_batch_waveform_all_fail(self, client, mock_db, fake_redis) -> None:
        """所有回路都失败时，items 为空，failed 包含全部."""

        async def fetch_side_effect(*, loop_id, **kwargs):
            raise BizError(
                code="ERR_WAVEFORM_FETCH",
                message="取数失败",
                status_code=500,
            )

        with (
            patch(
                "app.api.v1.endpoints.tags._fetch_waveform_for_loop",
                new_callable=AsyncMock,
                side_effect=fetch_side_effect,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=_BATCH_BODY,
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert len(data["items"]) == 0
        assert len(data["failed"]) == 2

    def test_batch_waveform_empty_loops(self, client, mock_db, fake_redis) -> None:
        """空回路列表返回 422 校验错误."""
        body = {**_BATCH_BODY, "loopIds": []}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )
        assert resp.status_code == 422

    def test_batch_waveform_exceeds_limit(self, client, mock_db, fake_redis) -> None:
        """超过 50 个回路返回 422 校验错误."""
        loop_ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(51)]
        body = {**_BATCH_BODY, "loopIds": loop_ids}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )
        assert resp.status_code == 422

    def test_batch_waveform_max_points_over_2000_rejected(
        self, client, mock_db, fake_redis
    ) -> None:
        """R21：maxPoints 超过 2000 上限返回 422（不静默截断）."""
        body = {**_BATCH_BODY, "maxPoints": 2001}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )
        assert resp.status_code == 422

    def test_batch_waveform_max_points_legacy_5000_rejected(
        self, client, mock_db, fake_redis
    ) -> None:
        """R21：旧默认值 5000 同样被拒绝（契约收紧回归）."""
        body = {**_BATCH_BODY, "maxPoints": 5000}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )
        assert resp.status_code == 422

    def test_single_waveform_max_points_over_2000_rejected(
        self, client, mock_db, fake_redis
    ) -> None:
        """R21：单回路波形 maxPoints 超上限返回 422."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/timeseries/00000000-0000-0000-0000-000000000201/waveform",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startTime": "2026-06-22T08:00:00Z",
                    "endTime": "2026-06-22T08:00:10Z",
                    "maxPoints": 50000,
                },
            )
        assert resp.status_code == 422

    def test_batch_waveform_with_tag_group(self, client, mock_db, fake_redis) -> None:
        """使用不同 tagGroup 查询波形."""
        resp_mock = _make_waveform_response()

        with (
            patch(
                "app.api.v1.endpoints.tags._fetch_waveform_for_loop",
                new_callable=AsyncMock,
                return_value=resp_mock,
            ) as mock_fetch,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    **_BATCH_BODY,
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                    "tagGroup": "OP_HF",
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        # 验证 tagGroup 参数被传递
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs["tag_group"] == "OP_HF"

    def test_batch_waveform_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/timeseries/batch/waveform", json=_BATCH_BODY)
        assert resp.status_code == 401

    def test_batch_waveform_unexpected_exception(self, client, mock_db, fake_redis) -> None:
        """非 BizError 异常也放入 failed 列表."""

        async def fetch_side_effect(*, loop_id, **kwargs):
            raise RuntimeError("Unexpected internal error")

        with (
            patch(
                "app.api.v1.endpoints.tags._fetch_waveform_for_loop",
                new_callable=AsyncMock,
                side_effect=fetch_side_effect,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/timeseries/batch/waveform",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    **_BATCH_BODY,
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert len(data["failed"]) == 1
        assert "Unexpected internal error" in data["failed"][0]["error"]
