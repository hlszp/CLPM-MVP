"""GET /tuning/verification/data 效果验证数据端点测试（09 设计方案 §4.5）。

覆盖：
- 前后窗边界与时间串正确（pointTime ± window，Z 后缀 UTC）
- windowHours 非法值 → 400 ERR_PARAM
- 回路不存在 → 404（get_waveform 既有行为透传）
- KPI 窗口均值摘要（2026-10-04 B2：单条→均值；有快照侧字段齐全，无快照侧为 null；
  等级取窗口内最新一条；后窗快照数不足 → dataInsufficient=true）
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


def _empty_scalars_db(db: AsyncMock) -> None:
    """window_avg_summary 取数路径返回空（窗口无快照 → None）。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)


def _params(point: str = "2026-08-10T12:00:00", hours: int = 1) -> dict:
    return {"loopId": _LOOP_ID, "pointTime": point, "windowHours": hours}


class TestVerificationDataAPI:
    """效果验证前后窗数据端点。"""

    def test_window_split_and_structure(self, client, mock_db) -> None:
        """前后窗各拉一次波形，时间串 Z 后缀边界正确；无快照侧 KPI 为 null。"""
        _empty_scalars_db(mock_db)
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
        assert data["beforeSnapshotCount"] == 0
        assert data["afterSnapshotCount"] == 0
        # 1h 窗口无后窗快照 → 数据不足
        assert data["dataInsufficient"] is True
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
        """窗口内多条快照 → 均值摘要 + 条数 + 等级取最新一条（B2 口径）。"""
        snap1 = MagicMock()
        snap1.score = 85.0
        snap1.good_value_rate = 0.96
        snap1.effective_auto_rate = 0.86
        snap1.steady_rate = 0.89
        snap1.accuracy_rate = 0.85
        snap1.fast_rate = 0.79
        snap1.oscillation_rate = 0.06
        snap1.saturation_rate = 0.03
        snap1.confidence_level = "B"
        snap1.fitness_level = "L2"
        snap1.tune_level = None
        snap1.ts_start = datetime(2026, 8, 10, 11, 0)
        snap2 = MagicMock()
        snap2.score = 86.0
        snap2.good_value_rate = 0.98
        snap2.effective_auto_rate = 0.90
        snap2.steady_rate = 0.91
        snap2.accuracy_rate = 0.87
        snap2.fast_rate = 0.81
        snap2.oscillation_rate = 0.04
        snap2.saturation_rate = 0.01
        snap2.confidence_level = "A"
        snap2.fitness_level = "L3"
        snap2.tune_level = "L3"
        snap2.ts_start = datetime(2026, 8, 10, 11, 30)
        result = MagicMock()
        result.scalars.return_value.all.return_value = [snap1, snap2]
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
        kb = data["kpiBefore"]
        assert kb["score"] == 85.5  # (85 + 86) / 2
        assert kb["effectiveAutoRate"] == 0.88  # (0.86 + 0.90) / 2
        assert kb["snapshotCount"] == 2
        # 等级分类型不可均值：取窗口内最新一条
        assert kb["fitnessLevel"] == "L3"
        assert kb["tuneLevel"] == "L3"
        assert kb["confidenceLevel"] == "A"
        assert kb["tsStart"] == "2026-08-10T11:00:00Z"
        assert kb["tsEnd"] == "2026-08-10T11:30:00Z"
        # 后窗同样有 2 条快照 → 1h 窗口数据充分
        assert data["afterSnapshotCount"] == 2
        assert data["dataInsufficient"] is False
