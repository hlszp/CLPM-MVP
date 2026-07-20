"""诊断任务管理 API 测试（PRD §5.6 诊断中心 — 诊断任务子模块）。

覆盖端点：
- POST   /api/v1/diagnosis/trigger                 — 触发诊断任务（手动 + 批量）
- GET    /api/v1/diagnosis/tasks                    — 诊断任务列表（未归档）
- GET    /api/v1/diagnosis/tasks/{taskId}           — 诊断任务详情
- POST   /api/v1/diagnosis/tasks/{taskId}/archive   — 归档诊断任务
- POST   /api/v1/diagnosis/tasks/{taskId}/cancel    — 取消诊断任务
- GET    /api/v1/diagnosis/records                  — 诊断记录列表（已归档）

测试要点：
- RBAC：仅 ADMIN/IC_ENGINEER/PE_ENGINEER 可触发/归档/取消；SPONSOR/EXPERT 403
- 状态机：PENDING/RUNNING 不可归档；SUCCESS/FAILED/CANCELLED 不可取消
- Celery 任务派发：trigger_diagnosis 调用 run_loop_diagnosis.delay
- 数据组装：任务列表/详情正确关联回路信息和评分
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str | None = None,
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    status: str = "SUCCESS",
    trigger_type: str = "manual",
    triggered_by: str = "admin",
    is_archived: bool = False,
) -> MagicMock:
    """构造 DiagnosisTask mock。"""
    t = MagicMock()
    t.id = task_id or str(uuid4())
    t.loop_id = loop_id
    t.trigger_type = trigger_type
    t.triggered_by = triggered_by
    t.status = status
    t.time_range_start = datetime.now(UTC).replace(tzinfo=None)
    t.time_range_end = datetime.now(UTC).replace(tzinfo=None)
    t.error_message = None
    t.triggered_at = datetime.now(UTC).replace(tzinfo=None)
    t.completed_at = datetime.now(UTC).replace(tzinfo=None)
    t.is_archived = is_archived
    t.archived_at = None
    t.archived_by = None
    return t


def _make_loop(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_name: str = "101-FC-1023",
    unit_id: str = "00000000-0000-0000-0000-000000000111",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "测试回路"
    loop.unit_id = unit_id
    loop.status = "READY"
    loop.is_active = True
    loop.score_weight = Decimal("45.20")
    return loop


def _make_scalars_all_mock(items: list) -> MagicMock:
    """构造 select(...).scalars().all() 结果 mock。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    """构造 select(...).scalar_one_or_none() 结果 mock。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalar_mock(value) -> MagicMock:
    """构造 select(...).scalar() 结果 mock（用于 count 查询）。"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_rows_mock(rows: list[tuple]) -> MagicMock:
    """构造 select 多列结果 mock（.all() 返回 rows 列表）。"""
    result = MagicMock()
    result.all.return_value = rows
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [r[0] for r in rows]
    result.scalars.return_value = scalars_mock
    return result


# ---------------------------------------------------------------------------
# POST /api/v1/diagnosis/trigger — 触发诊断任务
# ---------------------------------------------------------------------------


class TestTriggerDiagnosis:
    """POST /api/v1/diagnosis/trigger tests."""

    def test_trigger_single_loop_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以触发单回路诊断任务。"""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.tasks.diagnosis_engine.run_loop_diagnosis") as mock_celery,
        ):
            mock_celery.delay = MagicMock()
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["loopId"] == "00000000-0000-0000-0000-000000000201"
        assert data["tasks"][0]["status"] == "PENDING"
        assert data["tasks"][0]["taskId"]  # 非空
        # Celery 任务应被派发
        mock_celery.delay.assert_called_once()

    def test_trigger_batch_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以批量触发诊断任务。"""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        loop_ids = [
            "00000000-0000-0000-0000-000000000201",
            "00000000-0000-0000-0000-000000000202",
            "00000000-0000-0000-0000-000000000203",
        ]
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.tasks.diagnosis_engine.run_loop_diagnosis") as mock_celery,
        ):
            mock_celery.delay = MagicMock()
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": loop_ids},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert len(data["tasks"]) == 3
        assert mock_celery.delay.call_count == 3

    def test_trigger_with_time_range(self, client, mock_db, fake_redis) -> None:
        """带时间范围的触发应正确传递。"""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with (
            mock_current_user(TEST_USERS["ic_engineer"]),
            patch("app.tasks.diagnosis_engine.run_loop_diagnosis") as mock_celery,
        ):
            mock_celery.delay = MagicMock()
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                    "startTime": "2026-01-01T00:00:00Z",
                    "endTime": "2026-01-01T01:00:00Z",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        # 验证 Celery 调用参数包含 time_range_start/end
        call_args = mock_celery.delay.call_args
        assert call_args is not None
        # delay(loop_id, task_id=..., time_range_start=..., time_range_end=...)
        assert call_args.kwargs.get("time_range_start") == "2026-01-01T00:00:00"
        assert call_args.kwargs.get("time_range_end") == "2026-01-01T01:00:00"

    def test_trigger_empty_loop_ids_rejected(self, client, mock_db, fake_redis) -> None:
        """空 loopIds 列表应被拒绝（422 校验错误）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": []},
            )
        assert resp.status_code == 422

    def test_trigger_with_labels_subset(self, client, mock_db, fake_redis) -> None:
        """B6：labels 子集应透传到 Celery 任务。"""
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.tasks.diagnosis_engine.run_loop_diagnosis") as mock_celery,
        ):
            mock_celery.delay = MagicMock()
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                    "labels": ["VALVE_STICTION"],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
        call_args = mock_celery.delay.call_args
        assert call_args is not None
        assert call_args.kwargs.get("labels") == ["VALVE_STICTION"]

    def test_trigger_invalid_label_400(self, client, mock_db, fake_redis) -> None:
        """B6：非法标签返回 400 ERR_LABEL_INVALID。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                    "labels": ["NOT_A_LABEL"],
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LABEL_INVALID"

    def test_trigger_manual_review_label_rejected(self, client, mock_db, fake_redis) -> None:
        """B6：MANUAL_REVIEW 为兜底标签，子集中不允许，返回 400。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": ["00000000-0000-0000-0000-000000000201"],
                    "labels": ["MANUAL_REVIEW"],
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LABEL_INVALID"

    def test_trigger_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能触发诊断任务（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/diagnosis/trigger",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": ["00000000-0000-0000-0000-000000000201"]},
            )
        assert resp.status_code == 403

    def test_trigger_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.post(
            "/api/v1/diagnosis/trigger",
            json={"loopIds": ["00000000-0000-0000-0000-000000000201"]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/diagnosis/tasks — 诊断任务列表
# ---------------------------------------------------------------------------


class TestListDiagnosisTasks:
    """GET /api/v1/diagnosis/tasks tests."""

    def test_list_tasks_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取诊断任务列表。"""
        task = _make_task(status="SUCCESS")
        loop = _make_loop()
        node = MagicMock()
        node.id = loop.unit_id
        node.name = "常减压装置-单元A"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            # 1: count
            if call_count[0] == 1:
                return _make_scalar_mock(1)
            # 2: 任务列表查询
            if call_count[0] == 2:
                return _make_scalars_all_mock([task])
            # 3: 回路查询
            if call_count[0] == 3:
                return _make_scalars_all_mock([loop])
            # 4: 装置查询
            if call_count[0] == 4:
                return _make_scalars_all_mock([node])
            # 5: 评分查询（6列：loop_id, score, accuracy_rate,
            #    fast_rate, steady_rate, effective_auto_rate）
            if call_count[0] == 5:
                return _make_rows_mock(
                    [
                        (
                            loop.id,
                            Decimal("75.50"),
                            Decimal("80"),
                            Decimal("70"),
                            Decimal("75"),
                            Decimal("85"),
                        )
                    ]
                )
            # 6: 诊断结果标签查询
            return _make_rows_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tasks",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "items" in data
        assert "total" in data
        assert data["total"] == 1
        if data["items"]:
            item = data["items"][0]
            assert "taskId" in item
            assert "loopId" in item
            assert "status" in item
            assert "triggerType" in item
            assert item["status"] == "SUCCESS"
            assert item["triggerType"] == "manual"

    def test_list_tasks_empty(self, client, mock_db, fake_redis) -> None:
        """无任务时返回空列表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_mock(0)
            return _make_scalars_all_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/diagnosis/tasks",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []

    def test_list_tasks_with_filter(self, client, mock_db, fake_redis) -> None:
        """支持状态和触发方式筛选。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_mock(0)
            return _make_scalars_all_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tasks?status=RUNNING&triggerType=auto",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_list_tasks_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/tasks")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/diagnosis/tasks/{taskId} — 诊断任务详情
# ---------------------------------------------------------------------------


class TestGetDiagnosisTaskDetail:
    """GET /api/v1/diagnosis/tasks/{taskId} tests."""

    def test_get_task_detail_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取诊断任务详情。"""
        task = _make_task(status="SUCCESS")
        loop = _make_loop()
        node = MagicMock()
        node.id = loop.unit_id
        node.name = "常减压装置-单元A"
        snapshot = MagicMock()
        snapshot.loop_id = loop.id
        snapshot.score = Decimal("82.50")
        snapshot.accuracy_rate = Decimal("80.00")
        snapshot.fast_rate = Decimal("70.00")
        snapshot.steady_rate = Decimal("75.00")
        snapshot.effective_auto_rate = Decimal("85.00")
        snapshot.ts_start = datetime.now(UTC)

        diag_record = MagicMock()
        diag_record.id = str(uuid4())
        diag_record.diag_label = "OSCILLATION"
        diag_record.confidence = Decimal("85.00")
        diag_record.feature_values = {"oscillation_index": 0.42}
        diag_record.evidence_chain = {"fused_confidence": 0.82}
        diag_record.algorithm_version = "DIAG_ENGINE_v1.0"
        diag_record.diagnosed_at = datetime.now(UTC)

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            # 1: task 查询
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(task)
            # 2: loop 查询
            if call_count[0] == 2:
                return _make_scalar_one_or_none_mock(loop)
            # 3: unit 查询
            if call_count[0] == 3:
                return _make_scalar_one_or_none_mock(node)
            # 4: 诊断结果查询
            if call_count[0] == 4:
                return _make_scalars_all_mock([diag_record])
            # 5: snapshot 查询
            return _make_scalar_one_or_none_mock(snapshot)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/tasks/{task.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["taskId"] == task.id
        assert data["status"] == "SUCCESS"
        assert data["loopId"] == loop.id
        assert "results" in data
        if data["results"]:
            r = data["results"][0]
            assert "label" in r
            assert "confidence" in r

    def test_get_task_detail_not_found(self, client, mock_db, fake_redis) -> None:
        """任务不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tasks/nonexistent-task-id",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_TASK_NOT_FOUND"

    def test_get_task_detail_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/tasks/some-id")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/diagnosis/tasks/{taskId}/archive — 归档诊断任务
# ---------------------------------------------------------------------------


class TestArchiveDiagnosisTask:
    """POST /api/v1/diagnosis/tasks/{taskId}/archive tests."""

    def test_archive_success_status(self, client, mock_db, fake_redis) -> None:
        """SUCCESS 状态的任务可以归档。"""
        task = _make_task(status="SUCCESS", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["isArchived"] is True

    def test_archive_failed_status(self, client, mock_db, fake_redis) -> None:
        """FAILED 状态的任务可以归档。"""
        task = _make_task(status="FAILED", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_archive_pending_status_rejected(self, client, mock_db, fake_redis) -> None:
        """PENDING 状态的任务不可归档（400）。"""
        task = _make_task(status="PENDING", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_DIAG_TASK_NOT_ARCHIVABLE"

    def test_archive_running_status_rejected(self, client, mock_db, fake_redis) -> None:
        """RUNNING 状态的任务不可归档（400）。"""
        task = _make_task(status="RUNNING", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400

    def test_archive_already_archived(self, client, mock_db, fake_redis) -> None:
        """已归档的任务再次归档返回 400。"""
        task = _make_task(status="SUCCESS", is_archived=True)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_DIAG_TASK_NOT_ARCHIVABLE"

    def test_archive_not_found(self, client, mock_db, fake_redis) -> None:
        """任务不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/diagnosis/tasks/nonexistent/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_archive_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能归档任务（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/diagnosis/tasks/some-id/archive",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/diagnosis/tasks/{taskId}/cancel — 取消诊断任务
# ---------------------------------------------------------------------------


class TestCancelDiagnosisTask:
    """POST /api/v1/diagnosis/tasks/{taskId}/cancel tests."""

    def test_cancel_pending_success(self, client, mock_db, fake_redis) -> None:
        """PENDING 状态的任务可以取消。"""
        task = _make_task(status="PENDING", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["status"] == "CANCELLED"

    def test_cancel_running_success(self, client, mock_db, fake_redis) -> None:
        """RUNNING 状态的任务可以取消。"""
        task = _make_task(status="RUNNING", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_cancel_success_status_rejected(self, client, mock_db, fake_redis) -> None:
        """SUCCESS 状态的任务不可取消（400）。"""
        task = _make_task(status="SUCCESS", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_DIAG_TASK_NOT_CANCELLABLE"

    def test_cancel_failed_status_rejected(self, client, mock_db, fake_redis) -> None:
        """FAILED 状态的任务不可取消（400）。"""
        task = _make_task(status="FAILED", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/tasks/{task.id}/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400

    def test_cancel_not_found(self, client, mock_db, fake_redis) -> None:
        """任务不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/diagnosis/tasks/nonexistent/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_cancel_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能取消任务（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/diagnosis/tasks/some-id/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/v1/diagnosis/tasks/{taskId} — 删除诊断任务
# ---------------------------------------------------------------------------


class TestDeleteDiagnosisTask:
    """DELETE /api/v1/diagnosis/tasks/{taskId} tests."""

    def test_delete_terminal_status_success(self, client, mock_db, fake_redis) -> None:
        """终态（SUCCESS）任务可删除。"""
        task = _make_task(status="SUCCESS", is_archived=True)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.add = MagicMock()
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/diagnosis/tasks/{task.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["deleted"] is True
        mock_db.delete.assert_awaited_once()

    def test_delete_pending_success(self, client, mock_db, fake_redis) -> None:
        """PENDING 任务可删除。"""
        task = _make_task(status="PENDING", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.add = MagicMock()
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                f"/api/v1/diagnosis/tasks/{task.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_delete_running_rejected(self, client, mock_db, fake_redis) -> None:
        """RUNNING 任务不可删除（400，须先取消）。"""
        task = _make_task(status="RUNNING", is_archived=False)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(task))
        mock_db.delete = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/diagnosis/tasks/{task.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_DIAG_TASK_NOT_DELETABLE"
        assert "请先取消" in resp.json()["message"]
        mock_db.delete.assert_not_awaited()

    def test_delete_not_found(self, client, mock_db, fake_redis) -> None:
        """任务不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                "/api/v1/diagnosis/tasks/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_TASK_NOT_FOUND"

    def test_delete_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能删除任务（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.delete(
                "/api/v1/diagnosis/tasks/some-id",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/diagnosis/records — 诊断记录列表（已归档）
# ---------------------------------------------------------------------------


class TestListDiagnosisRecords:
    """GET /api/v1/diagnosis/records tests."""

    def test_list_records_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取已归档诊断记录列表（含全量聚合统计）。"""
        task = _make_task(status="SUCCESS", is_archived=True)
        loop = _make_loop()
        node = MagicMock()
        node.id = loop.unit_id
        node.name = "常减压装置-单元A"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            # 1: count
            if call_count[0] == 1:
                return _make_scalar_mock(1)
            # 2: 状态聚合（含近 7 天归档）
            if call_count[0] == 2:
                return _make_rows_mock([("SUCCESS", 1, 1)])
            # 3: 标签聚合
            if call_count[0] == 3:
                return _make_rows_mock([("OSCILLATION", 1)])
            # 4: 任务列表查询
            if call_count[0] == 4:
                return _make_scalars_all_mock([task])
            # 5: 回路查询
            if call_count[0] == 5:
                return _make_scalars_all_mock([loop])
            # 6: 装置查询
            if call_count[0] == 6:
                return _make_scalars_all_mock([node])
            # 7: 评分查询（6列）
            if call_count[0] == 7:
                return _make_rows_mock(
                    [
                        (
                            loop.id,
                            Decimal("75.50"),
                            Decimal("80"),
                            Decimal("70"),
                            Decimal("75"),
                            Decimal("85"),
                        )
                    ]
                )
            # 8: 诊断结果标签查询
            return _make_rows_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/records",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "items" in data
        assert "total" in data
        assert data["total"] == 1
        if data["items"]:
            item = data["items"][0]
            assert "taskId" in item
            assert "isArchived" in item
            assert item["isArchived"] is True
        # 聚合统计：对全部筛选结果聚合（A4）
        assert data["aggregates"]["total"] == 1
        assert data["aggregates"]["statusCounts"] == {"SUCCESS": 1}
        assert data["aggregates"]["labelCounts"] == {"OSCILLATION": 1}
        assert data["aggregates"]["recent7Days"] == 1

    def test_list_records_aggregates_stable_across_pages(self, client, mock_db, fake_redis) -> None:
        """翻页不影响记录聚合计数（A4）。"""
        # 每个请求固定 4 步查询：count → 状态聚合 → 标签聚合 → 主查询（返回空页）
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            pos = (call_count[0] - 1) % 4
            if pos == 0:
                return _make_scalar_mock(12)
            if pos == 1:
                return _make_rows_mock([("SUCCESS", 10, 6), ("FAILED", 2, 1)])
            if pos == 2:
                return _make_rows_mock([("OSCILLATION", 7), ("VALVE_STICTION", 5)])
            return _make_scalars_all_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp_p1 = client.get(
                "/api/v1/diagnosis/records?page=1&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
            resp_p2 = client.get(
                "/api/v1/diagnosis/records?page=2&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp_p1.status_code == 200
        assert resp_p2.status_code == 200
        agg_p1 = resp_p1.json()["data"]["aggregates"]
        agg_p2 = resp_p2.json()["data"]["aggregates"]
        assert agg_p1 == agg_p2
        assert agg_p1["total"] == 12
        assert agg_p1["statusCounts"] == {"SUCCESS": 10, "FAILED": 2}
        assert agg_p1["labelCounts"] == {"OSCILLATION": 7, "VALVE_STICTION": 5}
        assert agg_p1["recent7Days"] == 7

    def test_list_records_empty(self, client, mock_db, fake_redis) -> None:
        """无归档记录时返回空列表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_mock(0)
            # 2: 状态聚合；3: 标签聚合（均空）
            if call_count[0] in (2, 3):
                return _make_rows_mock([])
            return _make_scalars_all_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/diagnosis/records",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 0
        assert body["data"]["aggregates"]["total"] == 0
        assert body["data"]["aggregates"]["recent7Days"] == 0

    def test_list_records_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/records")
        assert resp.status_code == 401
