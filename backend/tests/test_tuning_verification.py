"""GET /tuning/verification/data 效果验证数据端点测试（09 设计方案 §4.5）。

覆盖：
- 前后窗边界与时间串正确（pointTime ± window，Z 后缀 UTC）
- windowHours 非法值 → 400 ERR_PARAM
- 回路不存在 → 404（get_waveform 既有行为透传）
- KPI 快照摘要：有快照侧字段齐全，无快照侧为 null
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.exceptions import BizError
from tests.conftest import TEST_USERS, mock_current_user

_URL = "/api/v1/tuning/verification/data"
_AUTH = {"Authorization": "Bearer fake-token"}
_LOOP_ID = "00000000-0000-0000-0000-0000000000a1"

_FAKE_WAVEFORM = {
    "loopId": _LOOP_ID,
    "timestamps": ["2026-08-10T11:00:00Z", "2026-08-10T11:01:00Z"],
    "pv": [1.0, 1.1],
    "sp": [1.2, 1.2],
    "op": [50.0, 51.0],
    "mode": [1, 1],
    "pvQuality": ["Good", "Good"],
    "downsampled": False,
    "pointCount": 2,
    "sampleInterval": 60,
}


def _params(point: str = "2026-08-10T12:00:00", hours: int = 1) -> dict:
    return {"loopId": _LOOP_ID, "pointTime": point, "windowHours": hours}


class TestVerificationDataAPI:
    """效果验证前后窗数据端点。"""

    def test_window_split_and_structure(self, client, mock_db) -> None:
        """前后窗各拉一次波形，时间串 Z 后缀边界正确；无快照侧 KPI 为 null。"""
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning.get_waveform",
                new=AsyncMock(return_value=_FAKE_WAVEFORM),
            ) as mock_wf,
        ):
            resp = client.get(_URL, headers=_AUTH, params=_params())
        assert resp.status_code == 200
        data = resp.json()["data"]

        # 两次调用：前窗 [11:00, 12:00]，后窗 [12:00, 13:00]
        assert mock_wf.await_count == 2
        before_call, after_call = mock_wf.await_args_list
        assert before_call.kwargs["start_time"] == "2026-08-10T11:00:00Z"
        assert before_call.kwargs["end_time"] == "2026-08-10T12:00:00Z"
        assert after_call.kwargs["start_time"] == "2026-08-10T12:00:00Z"
        assert after_call.kwargs["end_time"] == "2026-08-10T13:00:00Z"

        assert data["pointTime"] == "2026-08-10T12:00:00Z"
        assert data["windowHours"] == 1
        assert data["before"]["pv"] == [1.0, 1.1]
        assert data["after"]["op"] == [50.0, 51.0]
        # mock_db 默认无快照
        assert data["kpiBefore"] is None
        assert data["kpiAfter"] is None
        # 历史时点：后窗不截断
        assert data["afterTruncated"] is False

    def test_invalid_window_hours(self, client) -> None:
        """windowHours 仅支持 1/2/24，其余 → 400 ERR_PARAM。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(_URL, headers=_AUTH, params=_params(hours=3))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_invalid_point_time(self, client) -> None:
        """pointTime 非法 → 400 ERR_PARAM（不透出 500）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(_URL, headers=_AUTH, params=_params(point="not-a-time"))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_loop_not_found(self, client) -> None:
        """回路不存在 → 404（get_waveform 行为透传）。"""
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning.get_waveform",
                new=AsyncMock(
                    side_effect=BizError(
                        code="ERR_LOOP_NOT_FOUND", message="回路不存在", status_code=404
                    )
                ),
            ),
        ):
            resp = client.get(_URL, headers=_AUTH, params=_params())
        assert resp.status_code == 404

    def test_kpi_summary_present(self, client, mock_db) -> None:
        """窗口内有快照时返回评分+六率+可信度摘要。"""
        snap = MagicMock()
        snap.score = 85.5
        snap.good_value_rate = 0.97
        snap.effective_auto_rate = 0.88
        snap.steady_rate = 0.9
        snap.accuracy_rate = 0.86
        snap.fast_rate = 0.8
        snap.oscillation_rate = 0.05
        snap.saturation_rate = 0.02
        snap.confidence_level = "A"
        snap.ts_start = datetime(2026, 8, 10, 11, 0)
        snap.ts_end = datetime(2026, 8, 10, 12, 0)
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=snap)
        mock_db.execute = AsyncMock(return_value=result)

        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning.get_waveform",
                new=AsyncMock(return_value=_FAKE_WAVEFORM),
            ),
        ):
            resp = client.get(_URL, headers=_AUTH, params=_params())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["kpiBefore"]["score"] == 85.5
        assert data["kpiBefore"]["effectiveAutoRate"] == 0.88
        assert data["kpiBefore"]["confidenceLevel"] == "A"
        assert data["kpiBefore"]["tsStart"] == "2026-08-10T11:00:00Z"
        assert data["kpiAfter"]["score"] == 85.5
