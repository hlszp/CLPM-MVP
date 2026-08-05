"""P3-01 整定知识库 API 测试.

覆盖 3 个端点：
- GET /api/v1/tuning/knowledge-base          列表+筛选+分页
- GET /api/v1/tuning/knowledge-base/similar   相似案例推荐
- GET /api/v1/tuning/knowledge-base/{entryId} 详情（含 404）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from tests.conftest import TEST_USERS, mock_current_user


def _make_orm_entry(
    *,
    entry_id: str | None = None,
    loop_type: str = "FLOW",
    diagnosis_label: str = "OSCILLATION",
    effect_verified: bool = True,
    improved_count: int = 3,
    match_source: str = "exact",
) -> MagicMock:
    """构造模拟的 TuningKnowledgeEntry ORM 对象。"""
    entry = MagicMock()
    entry.id = entry_id or str(uuid4())
    entry.tracker_id = str(uuid4())
    entry.tuning_record_id = str(uuid4())
    entry.loop_id = str(uuid4())
    entry.loop_type = loop_type
    entry.control_type = "PID"
    entry.tag_name = "FIC-101"
    entry.diagnosis_label = diagnosis_label
    entry.severity = "WARN"
    entry.model_type = "FOPDT"
    entry.algorithm = "arx"
    entry.identify_method = "least_squares"
    entry.confidence_level = "B"
    entry.pid_before = {"p": 1.5, "i": 0.8, "d": 0.1}
    entry.pid_after = {"p": 2.0, "i": 1.0, "d": 0.2}
    entry.kpi_summary = {
        "improvedCount": improved_count,
        "deterioratedCount": 1,
        "kpiComparison": [],
    }
    entry.effect_verified = effect_verified
    entry.improved_count = improved_count
    entry.deteriorated_count = 1
    entry.match_source = match_source
    entry.implemented_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    entry.verified_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    entry.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    return entry


# ---------------------------------------------------------------------------
# GET /tuning/knowledge-base — 列表
# ---------------------------------------------------------------------------


class TestListKnowledgeBase:
    """知识库列表 API 测试。"""

    def test_list_returns_entries(self, client, mock_db, fake_redis) -> None:
        """正常返回知识库列表。"""
        entries = [_make_orm_entry() for _ in range(3)]
        with (
            patch(
                "app.api.v1.endpoints.tuning.list_knowledge_entries",
                new_callable=AsyncMock,
                return_value={
                    "items": entries,
                    "total": 3,
                    "page": 1,
                    "pageSize": 20,
                },
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 3
        assert len(body["data"]["items"]) == 3
        item = body["data"]["items"][0]
        assert "id" in item
        assert "tagName" in item
        assert "diagnosisLabel" in item
        assert "matchSource" in item

    def test_list_with_filters(self, client, mock_db, fake_redis) -> None:
        """支持筛选参数。"""
        captured: dict = {}

        async def _capture(db, **kwargs):
            captured.update(kwargs)
            return {"items": [], "total": 0, "page": 1, "pageSize": 20}

        with (
            patch(
                "app.api.v1.endpoints.tuning.list_knowledge_entries",
                side_effect=_capture,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base?loopType=FLOW&diagnosisLabel=OSCILLATION&effectVerified=true&page=2&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert captured["loop_type"] == "FLOW"
        assert captured["diagnosis_label"] == "OSCILLATION"
        assert captured["effect_verified"] is True
        assert captured["page"] == 2
        assert captured["page_size"] == 10

    def test_list_empty_result(self, client, mock_db, fake_redis) -> None:
        """空结果。"""
        with (
            patch(
                "app.api.v1.endpoints.tuning.list_knowledge_entries",
                new_callable=AsyncMock,
                return_value={"items": [], "total": 0, "page": 1, "pageSize": 20},
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0
        assert resp.json()["data"]["items"] == []

    def test_list_camel_case_response(self, client, mock_db, fake_redis) -> None:
        """响应字段为 camelCase。"""
        entry = _make_orm_entry()
        with (
            patch(
                "app.api.v1.endpoints.tuning.list_knowledge_entries",
                new_callable=AsyncMock,
                return_value={"items": [entry], "total": 1, "page": 1, "pageSize": 20},
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base",
                headers={"Authorization": "Bearer fake-token"},
            )
        item = resp.json()["data"]["items"][0]
        # camelCase 字段
        assert "trackerId" in item
        assert "tuningRecordId" in item
        assert "loopId" in item
        assert "loopType" in item
        assert "controlType" in item
        assert "tagName" in item
        assert "diagnosisLabel" in item
        assert "modelType" in item
        assert "identifyMethod" in item
        assert "confidenceLevel" in item
        assert "pidBefore" in item
        assert "pidAfter" in item
        assert "kpiSummary" in item
        assert "effectVerified" in item
        assert "improvedCount" in item
        assert "deterioratedCount" in item
        assert "matchSource" in item
        assert "implementedAt" in item
        assert "verifiedAt" in item
        assert "createdAt" in item

    def test_list_invalid_page_returns_422(self, client, mock_db, fake_redis) -> None:
        """page < 1 返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/knowledge-base?page=0",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /tuning/knowledge-base/similar — 相似案例
# ---------------------------------------------------------------------------


class TestRecommendSimilar:
    """相似案例推荐 API 测试。"""

    def test_similar_returns_entries(self, client, mock_db, fake_redis) -> None:
        """正常返回相似案例。"""
        entries = [_make_orm_entry() for _ in range(5)]
        with (
            patch(
                "app.api.v1.endpoints.tuning.recommend_similar",
                new_callable=AsyncMock,
                return_value=entries,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base/similar?loopId=abc-123&limit=5",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 5
        assert len(body["data"]["items"]) == 5

    def test_similar_passes_loop_id_for_exclusion(self, client, mock_db, fake_redis) -> None:
        """loopId 传递到服务层（排除自身）。"""
        captured: dict = {}

        async def _capture(db, **kwargs):
            captured.update(kwargs)
            return []

        with (
            patch(
                "app.api.v1.endpoints.tuning.recommend_similar",
                side_effect=_capture,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base/similar?loopId=loop-xyz",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert captured["loop_id"] == "loop-xyz"

    def test_similar_with_label_and_type(self, client, mock_db, fake_redis) -> None:
        """支持 loopType + diagnosisLabel 参数。"""
        captured: dict = {}

        async def _capture(db, **kwargs):
            captured.update(kwargs)
            return []

        with (
            patch(
                "app.api.v1.endpoints.tuning.recommend_similar",
                side_effect=_capture,
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base/similar?loopType=PRESSURE&diagnosisLabel=OVERAGGRESSIVE",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert captured["loop_type"] == "PRESSURE"
        assert captured["diagnosis_label"] == "OVERAGGRESSIVE"

    def test_similar_empty_result(self, client, mock_db, fake_redis) -> None:
        """无匹配时返回空列表。"""
        with (
            patch(
                "app.api.v1.endpoints.tuning.recommend_similar",
                new_callable=AsyncMock,
                return_value=[],
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base/similar?loopId=abc",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /tuning/knowledge-base/{entryId} — 详情
# ---------------------------------------------------------------------------


class TestGetKnowledgeEntry:
    """知识库详情 API 测试。"""

    def test_get_existing_entry(self, client, mock_db, fake_redis) -> None:
        """查询存在的条目。"""
        entry = _make_orm_entry(entry_id="test-entry-id")
        with (
            patch(
                "app.api.v1.endpoints.tuning.get_knowledge_entry",
                new_callable=AsyncMock,
                return_value=entry,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base/test-entry-id",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["id"] == "test-entry-id"
        assert body["data"]["tagName"] == "FIC-101"
        assert body["data"]["diagnosisLabel"] == "OSCILLATION"

    def test_get_nonexistent_returns_404(self, client, mock_db, fake_redis) -> None:
        """查询不存在的条目返回 404。"""
        with (
            patch(
                "app.api.v1.endpoints.tuning.get_knowledge_entry",
                new_callable=AsyncMock,
                return_value=None,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/tuning/knowledge-base/nonexistent-id",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_get_entry_camel_case_dates(self, client, mock_db, fake_redis) -> None:
        """详情中日期字段为 ISO 字符串。"""
        entry = _make_orm_entry()
        with (
            patch(
                "app.api.v1.endpoints.tuning.get_knowledge_entry",
                new_callable=AsyncMock,
                return_value=entry,
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.get(
                f"/api/v1/tuning/knowledge-base/{entry.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        data = resp.json()["data"]
        # 日期为 ISO 字符串
        assert isinstance(data["implementedAt"], str)
        assert isinstance(data["verifiedAt"], str)
        assert isinstance(data["createdAt"], str)
        assert "T" in data["implementedAt"]  # ISO 格式
