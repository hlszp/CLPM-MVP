"""回路最新可信度评估记录 API 测试.

覆盖 GET /api/v1/loops/{loop_id}/confidence-latest 端点：
- 有记录：响应结构（camelCase 字段 + metrics 12 子指标 JSONB）
- 无记录：HTTP 200 且 data=null（不抛 404）
- RBAC：未认证 401
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = "00000000-0000-0000-0000-000000000201"


def _make_confidence_latest() -> MagicMock:
    """构造 LoopConfidenceLatest ORM mock（字段名对齐模型）。"""
    r = MagicMock()
    r.id = "00000000-0000-0000-0000-000000000901"
    r.loop_id = LOOP_ID
    r.eval_time = datetime(2026, 7, 4, 9, 0, 30, tzinfo=UTC)
    r.data_ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
    r.data_ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)
    r.status = "SUCCESS"
    r.score = Decimal("90.50")
    r.confidence_level = "A"
    r.valid_rate = 0.982
    r.metrics = {
        "accuracy_rate": {"value": 93.35, "confidence": "A"},
        "fast_rate": {"value": 88.0, "confidence": "B"},
        "steady_rate": {"value": 91.2, "confidence": "A"},
        "effective_auto_rate": {"value": 85.0, "confidence": "B"},
        "good_value_rate": {"value": 96.8, "confidence": "A"},
        "auto_mode_rate": {"value": 90.0, "confidence": "A"},
        "settling_time": {"value": 120.5, "confidence": "C"},
        "ideal_settling_time": {"value": 100.0, "confidence": "A"},
        "oscillation_rate": {"value": 15.0, "confidence": "B"},
        "saturation_rate": {"value": 8.0, "confidence": "A"},
        "stiction_index": {"value": 0.12, "confidence": "C"},
        "output_trip_index": {"value": 45.3, "confidence": "B"},
    }
    r.algorithm_version = "KPI_CALC_v2.0"
    r.updated_at = datetime(2026, 7, 4, 9, 0, 30, tzinfo=UTC)
    return r


def _make_result(value: object) -> MagicMock:
    """构造 execute 返回值，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestGetLoopConfidenceLatest:
    """GET /api/v1/loops/{loop_id}/confidence-latest"""

    def test_returns_record_with_metrics(self, client, mock_db, fake_redis) -> None:
        """有记录：返回 camelCase 结构 + metrics 子指标明细。"""
        mock_db.execute = AsyncMock(return_value=_make_result(_make_confidence_latest()))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/loops/{LOOP_ID}/confidence-latest",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == LOOP_ID
        assert data["status"] == "SUCCESS"
        assert data["score"] == 90.5
        assert data["confidenceLevel"] == "A"
        assert data["validRate"] == 0.982
        assert data["algorithmVersion"] == "KPI_CALC_v2.0"
        # 时间字段序列化为 ISO 字符串
        assert data["evalTime"].startswith("2026-07-04T09:00:30")
        assert data["dataTsStart"].startswith("2026-07-04T08:00:00")
        assert data["dataTsEnd"].startswith("2026-07-04T09:00:00")
        # 12 子指标：键为 DB 列名（snake_case），含 value + confidence
        metrics = data["metrics"]
        assert len(metrics) == 12
        assert metrics["accuracy_rate"] == {"value": 93.35, "confidence": "A"}
        assert metrics["settling_time"] == {"value": 120.5, "confidence": "C"}
        assert metrics["stiction_index"] == {"value": 0.12, "confidence": "C"}

    def test_returns_null_data_when_no_record(self, client, mock_db, fake_redis) -> None:
        """无记录：HTTP 200 且 data=null（前端展示"暂无评估记录"）。"""
        mock_db.execute = AsyncMock(return_value=_make_result(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/loops/{LOOP_ID}/confidence-latest",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"] is None

    def test_unauthenticated_returns_401(self, client, mock_db, fake_redis) -> None:
        """未认证请求返回 401。"""
        resp = client.get(f"/api/v1/loops/{LOOP_ID}/confidence-latest")
        assert resp.status_code == 401
