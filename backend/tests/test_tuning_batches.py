"""GAP-2a 整定批次端点测试（docs/MVP设计/13 §6.2）.

覆盖 GET /api/v1/tuning/batches（列表分页/筛选/B-06 动态阻塞摘要）与
GET /api/v1/tuning/batches/{id}（详情 N:M 组装/前置工单摘要/404）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user


def _pg_sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _make_batch_mock(
    batch_id: int = 1,
    status: str = "READY",
    prereq_order_ids: list | None = None,
) -> MagicMock:
    """构造 TuningBatch mock。"""
    b = MagicMock()
    b.id = batch_id
    b.batch_no = f"TB-2026-{batch_id:03d}"
    b.title = "一单元温度回路整定批次"
    b.scope_type = "UNIT"
    b.scope_id = 101
    b.status = status
    b.prereq_order_ids = prereq_order_ids or []
    b.block_reason = None
    b.scatters_before = [{"loop_id": "loop-1", "score": 62.5}]
    b.scatters_after = [{"loop_id": "loop-1", "score": 78.0}]
    b.owner_id = 1
    b.expected_start_at = None
    b.actual_start_at = None
    b.completed_at = None
    b.created_at = datetime(2026, 8, 20, 8, 0, 0, tzinfo=UTC)
    return b


def _make_record_mock() -> MagicMock:
    """构造 TuningRecord mock。"""
    r = MagicMock()
    r.id = "00000000-0000-0000-0000-000000000301"
    r.loop_id = "00000000-0000-0000-0000-000000000201"
    r.model_type = "FOPDT"
    r.algorithm = "IMC"
    r.status = "VERIFIED"
    r.fitting_score = Decimal("95.5")
    r.created_by = "admin"
    r.created_at = datetime(2026, 8, 19, 9, 30, 0, tzinfo=UTC)
    return r


def _scalars_result(items: list) -> MagicMock:
    """select(TuningBatch) → .scalars().all() 结果 mock。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _mapping_result(rows: list[dict]) -> MagicMock:
    """text() 原生 SQL → .all() 行（row._mapping + 同名属性）结果 mock。"""
    result = MagicMock()
    mocked_rows = []
    for row in rows:
        m = MagicMock()
        m._mapping = row
        for k, v in row.items():
            setattr(m, k, v)
        mocked_rows.append(m)
    result.all.return_value = mocked_rows
    return result


class TestListTuningBatches:
    """GET /api/v1/tuning/batches"""

    def test_unauthorized(self, client) -> None:
        """未认证访问应返回 401。"""
        resp = client.get("/api/v1/tuning/batches")
        assert resp.status_code == 401

    def test_list_empty(self, client, mock_db) -> None:
        """空批次列表（count → page 两次查询；空页不再查统计/前置）。"""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)
        mock_db.execute = AsyncMock(
            side_effect=[count_result, _scalars_result([])],
        )

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/batches",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1
        assert data["pageSize"] == 20

    def test_list_filter_sql(self, client, mock_db) -> None:
        """status + startTime/endTime → count/page 两条 SQL 均带过滤条件。"""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)
        results = iter([count_result, _scalars_result([])])
        captured: list = []

        async def _execute(stmt, *args, **kwargs):  # noqa: ARG001
            captured.append(stmt)
            return next(results)

        mock_db.execute = _execute
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/batches",
                params={
                    "status": "BLOCKED",
                    "startTime": "2026-08-01T00:00:00Z",
                    "endTime": "2026-08-25T00:00:00Z",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        for stmt in captured:
            sql = _pg_sql(stmt)
            assert "tuning_batch.status = 'BLOCKED'" in sql
            assert "tuning_batch.created_at >= '2026-08-01 00:00:00'" in sql
            assert "tuning_batch.created_at <= '2026-08-25 00:00:00'" in sql

    def test_list_blocking_summary(self, client, mock_db) -> None:
        """前置工单未闭合 → B-06 动态判定 status=BLOCKED + 阻塞原因 + 记录数。"""
        batch = _make_batch_mock(batch_id=7, status="READY", prereq_order_ids=["ord-uuid-1"])
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=1)
        stats_result = _mapping_result(
            [{"batch_id": 7, "loop_count": 3, "algorithms": ["IMC"], "owner": "admin"}]
        )
        prereq_result = _mapping_result(
            [
                {
                    "order_id": "ord-uuid-1",
                    "order_no": "HD-20260820-001",
                    "title": "阀门处理",
                    "status": "EXECUTING",
                }
            ]
        )
        mock_db.execute = AsyncMock(
            side_effect=[count_result, _scalars_result([batch]), stats_result, prereq_result],
        )

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/batches?page=1&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["pageSize"] == 10
        item = data["items"][0]
        assert item["id"] == 7
        assert item["batchNo"] == "TB-2026-007"
        assert item["status"] == "BLOCKED"
        assert item["storedStatus"] == "READY"
        assert item["blocked"] is True
        assert "HD-20260820-001" in item["blockReason"]
        assert item["recordCount"] == 3


class TestGetTuningBatchDetail:
    """GET /api/v1/tuning/batches/{batch_id}"""

    def test_detail_assembles_records_and_prereqs(self, client, mock_db) -> None:
        """详情：N:M 关联记录 + 前置工单摘要 + scatters 原样返回。"""
        batch = _make_batch_mock(batch_id=9, status="READY", prereq_order_ids=["ord-uuid-2"])
        batch_result = MagicMock()
        batch_result.scalar_one_or_none = MagicMock(return_value=batch)

        record = _make_record_mock()
        records_result = MagicMock()
        records_result.all = MagicMock(return_value=[(record, 0, "TIC-101")])

        prereq_result = _mapping_result(
            [
                {
                    "order_id": "ord-uuid-2",
                    "order_no": "HD-20260819-002",
                    "title": "仪表校验",
                    "status": "CLOSED",
                }
            ]
        )
        mock_db.execute = AsyncMock(
            side_effect=[batch_result, records_result, prereq_result],
        )

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/batches/9",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == 9
        assert data["batchNo"] == "TB-2026-009"
        # 前置已闭合 → 有效状态保持 READY
        assert data["status"] == "READY"
        assert data["blocked"] is False
        # scatters JSONB 原样返回
        assert data["scattersBefore"] == [{"loop_id": "loop-1", "score": 62.5}]
        assert data["scattersAfter"] == [{"loop_id": "loop-1", "score": 78.0}]
        # 前置工单摘要
        assert data["prereqOrderIds"] == ["ord-uuid-2"]
        prereq = data["prereqOrders"][0]
        assert prereq["orderNo"] == "HD-20260819-002"
        assert prereq["status"] == "CLOSED"
        assert prereq["closed"] is True
        # N:M 关联整定记录（含回路位号与 sort_order）
        assert data["recordCount"] == 1
        rec = data["records"][0]
        assert rec["recordId"] == "00000000-0000-0000-0000-000000000301"
        assert rec["sortOrder"] == 0
        assert rec["tagName"] == "TIC-101"
        assert rec["algorithm"] == "IMC"
        assert rec["fittingScore"] == 95.5

    def test_detail_not_found(self, client, mock_db) -> None:
        """批次不存在 → 404 ERR_TUNING_BATCH_NOT_FOUND。"""
        batch_result = MagicMock()
        batch_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=batch_result)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/batches/99999",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_TUNING_BATCH_NOT_FOUND"
