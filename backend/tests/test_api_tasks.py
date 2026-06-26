"""任务管理接口测试 (IDS v3.2 §2.7.6).

测试覆盖：
- POST /api/v1/tasks/standard/evaluate — 触发标准评估
- POST /api/v1/tasks/custom/evaluate   — 触发自定义评估
- GET  /api/v1/tasks/{taskId}          — 查询任务状态
- GET  /api/v1/tasks                   — 查询任务列表
- POST /api/v1/tasks/{taskId}/cancel   — 取消任务

同时包含 KPI Schema 扩展验证（KpiSnapshotSchema / DataLineageSchema）。

设计依据：IDS §2.7.6, PRD §4.3.7
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# FakeTaskRedis — 支持 Hash + Sorted Set 操作的内存 Redis mock
# ---------------------------------------------------------------------------


class FakeTaskRedis:
    """In-memory Redis mock supporting hash and sorted set operations.

    FakeRedis in conftest.py only supports string/set operations.
    Task endpoints need hset/hgetall/zadd/zrange/zrevrange.
    """

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    async def hset(self, key: str, mapping: dict | None = None, **kwargs) -> int:
        if key not in self._hashes:
            self._hashes[key] = {}
        if mapping:
            self._hashes[key].update(mapping)
            return len(mapping)
        return 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def zadd(self, key: str, mapping: dict) -> int:
        if key not in self._zsets:
            self._zsets[key] = {}
        self._zsets[key].update(mapping)
        return len(mapping)

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        items = sorted(self._zsets.get(key, {}).items(), key=lambda x: x[1])
        members = [m for m, _ in items]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

    async def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        items = sorted(self._zsets.get(key, {}).items(), key=lambda x: -x[1])
        members = [m for m, _ in items]
        if end == -1:
            return members[start:]
        return members[start : end + 1]


@pytest.fixture
def task_redis() -> FakeTaskRedis:
    """Patch redis_client in tasks endpoint with FakeTaskRedis."""
    fake = FakeTaskRedis()
    with patch("app.api.v1.endpoints.tasks.redis_client", fake):
        yield fake


# ---------------------------------------------------------------------------
# Celery mock helper
# ---------------------------------------------------------------------------


def _mock_celery_result(task_id: str = "celery-task-001") -> MagicMock:
    r = MagicMock()
    r.id = task_id
    return r


def _save_task_to_redis(
    fake: FakeTaskRedis,
    task_id: str,
    task_type: str = "STANDARD",
    status: str = "PENDING",
    progress: str = "",
    created_by: str = "admin",
    created_by_id: str = "00000000-0000-0000-0000-000000000001",
    celery_task_id: str = "",
    celery_task_ids: str = "",
    loops_total: str = "",
    loops_done: str = "",
    current_stage: str = "",
    started_at: str = "",
    finished_at: str = "",
    error_message: str = "",
) -> dict[str, str]:
    """直接往 FakeTaskRedis 写入任务数据，模拟已有任务."""
    now = datetime.now(UTC).isoformat()
    data: dict[str, str] = {
        "task_id": task_id,
        "task_type": task_type,
        "status": status,
        "progress": progress,
        "current_stage": current_stage,
        "loops_total": loops_total,
        "loops_done": loops_done,
        "created_at": now,
        "started_at": started_at,
        "finished_at": finished_at,
        "error_message": error_message,
        "created_by": created_by,
        "created_by_id": created_by_id,
    }
    if celery_task_id:
        data["celery_task_id"] = celery_task_id
    if celery_task_ids:
        data["celery_task_ids"] = celery_task_ids
    fake._hashes[f"task:{task_id}"] = data
    score = datetime.fromisoformat(now).timestamp()
    fake._zsets.setdefault("task:index", {})[task_id] = score
    return data


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/standard/evaluate
# ---------------------------------------------------------------------------


class TestStandardTaskEvaluate:
    """POST /api/v1/tasks/standard/evaluate tests."""

    def test_standard_evaluate_success(
        self, client, task_redis, fake_redis
    ) -> None:
        """IC_ENGINEER 可以触发标准评估任务."""
        with (
            patch("app.tasks.kpi_calc.calculate_hourly_kpi") as mock_celery,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery.delay.return_value = _mock_celery_result()
            resp = client.post(
                "/api/v1/tasks/standard/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={"tsStart": "2026-06-22T08:00:00Z"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["taskType"] == "STANDARD"
        assert data["status"] == "PENDING"
        assert data["createdBy"] == "ic_engineer"
        assert data["taskId"]  # non-empty UUID
        # 验证 Celery 被调用
        mock_celery.delay.assert_called_once()

    def test_standard_evaluate_admin(
        self, client, task_redis, fake_redis
    ) -> None:
        """ADMIN 可以触发标准评估任务."""
        with (
            patch("app.tasks.kpi_calc.calculate_hourly_kpi") as mock_celery,
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_celery.delay.return_value = _mock_celery_result()
            resp = client.post(
                "/api/v1/tasks/standard/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={},
            )
        assert resp.status_code == 200

    def test_standard_evaluate_sponsor_forbidden(
        self, client, task_redis, fake_redis
    ) -> None:
        """SPONSOR 不能触发评估任务（403）."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/tasks/standard/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_standard_evaluate_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/tasks/standard/evaluate", json={})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/custom/evaluate
# ---------------------------------------------------------------------------


_CUSTOM_BODY = {
    "loopIds": ["00000000-0000-0000-0000-000000000201"],
    "metrics": ["accuracy_rate"],
    "tsStart": "2026-06-22T08:00:00Z",
    "tsEnd": "2026-06-22T09:00:00Z",
}


class TestCustomTaskEvaluate:
    """POST /api/v1/tasks/custom/evaluate tests."""

    def test_custom_evaluate_success(
        self, client, task_redis, fake_redis
    ) -> None:
        """IC_ENGINEER 可以触发自定义评估任务."""
        with (
            patch("app.tasks.kpi_calc.calculate_loop_kpi") as mock_celery,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery.delay.return_value = _mock_celery_result()
            resp = client.post(
                "/api/v1/tasks/custom/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json=_CUSTOM_BODY,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["taskType"] == "CUSTOM"
        assert data["status"] == "PENDING"
        assert data["loopsTotal"] == 1
        assert data["currentStage"] == "取数"
        assert data["createdBy"] == "ic_engineer"

    def test_custom_evaluate_empty_loops(
        self, client, task_redis, fake_redis
    ) -> None:
        """空回路列表返回 400 ERR_INVALID_REQUEST."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/custom/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={**_CUSTOM_BODY, "loopIds": []},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_REQUEST"

    def test_custom_evaluate_empty_metrics(
        self, client, task_redis, fake_redis
    ) -> None:
        """空指标列表返回 400 ERR_INVALID_REQUEST."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/custom/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={**_CUSTOM_BODY, "metrics": []},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_REQUEST"

    def test_custom_evaluate_concurrency_limit_user(
        self, client, task_redis, fake_redis
    ) -> None:
        """单用户活跃任务超过 3 个时返回 429."""
        # 预先创建 3 个活跃任务
        for i in range(3):
            _save_task_to_redis(
                task_redis,
                task_id=f"existing-task-{i}",
                task_type="CUSTOM",
                status="PENDING",
                created_by_id="00000000-0000-0000-0000-000000000002",  # ic_engineer
            )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/custom/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json=_CUSTOM_BODY,
            )
        assert resp.status_code == 429
        assert resp.json()["code"] == "ERR_TASK_CONCURRENCY_LIMIT"

    def test_custom_evaluate_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/tasks/custom/evaluate", json=_CUSTOM_BODY)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{taskId}
# ---------------------------------------------------------------------------


class TestGetTaskStatus:
    """GET /api/v1/tasks/{taskId} tests."""

    def test_get_task_pending(self, client, task_redis, fake_redis) -> None:
        """查询 PENDING 状态任务."""
        _save_task_to_redis(task_redis, task_id="task-pending", status="PENDING")
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/task-pending",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["taskId"] == "task-pending"
        assert data["status"] == "PENDING"

    def test_get_task_success_status(
        self, client, task_redis, fake_redis
    ) -> None:
        """查询 SUCCESS 状态任务."""
        _save_task_to_redis(
            task_redis,
            task_id="task-success",
            status="SUCCESS",
            progress="1.0",
            finished_at="2026-06-22T09:30:00+00:00",
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/task-success",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "SUCCESS"
        assert data["progress"] == 1.0

    def test_get_task_not_found(self, client, task_redis, fake_redis) -> None:
        """任务不存在返回 404."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/nonexistent-task",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_TASK_NOT_FOUND"

    def test_get_task_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/tasks/some-task")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/tasks — 任务列表
# ---------------------------------------------------------------------------


class TestListTasks:
    """GET /api/v1/tasks tests."""

    def test_list_tasks_success(self, client, task_redis, fake_redis) -> None:
        """查询任务列表."""
        _save_task_to_redis(task_redis, task_id="task-1", status="SUCCESS")
        _save_task_to_redis(task_redis, task_id="task-2", status="PENDING")
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_tasks_filter_by_type(
        self, client, task_redis, fake_redis
    ) -> None:
        """按任务类型筛选."""
        _save_task_to_redis(
            task_redis, task_id="task-std", task_type="STANDARD", status="SUCCESS"
        )
        _save_task_to_redis(
            task_redis, task_id="task-cust", task_type="CUSTOM", status="PENDING"
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks?taskType=CUSTOM",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["taskType"] == "CUSTOM"

    def test_list_tasks_filter_by_status(
        self, client, task_redis, fake_redis
    ) -> None:
        """按状态筛选."""
        _save_task_to_redis(task_redis, task_id="task-s1", status="SUCCESS")
        _save_task_to_redis(task_redis, task_id="task-s2", status="SUCCESS")
        _save_task_to_redis(task_redis, task_id="task-p1", status="PENDING")
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks?status=SUCCESS",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        for item in data["items"]:
            assert item["status"] == "SUCCESS"

    def test_list_tasks_empty(self, client, task_redis, fake_redis) -> None:
        """无任务时返回空列表."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_tasks_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/{taskId}/cancel
# ---------------------------------------------------------------------------


class TestCancelTask:
    """POST /api/v1/tasks/{taskId}/cancel tests."""

    def test_cancel_pending_task(
        self, client, task_redis, fake_redis
    ) -> None:
        """取消 PENDING 状态任务."""
        _save_task_to_redis(
            task_redis,
            task_id="task-cancel",
            status="PENDING",
            task_type="STANDARD",
            celery_task_id="celery-001",
        )
        with (
            patch("app.tasks.celery_app.celery_app") as mock_celery_app,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery_app.control.revoke = MagicMock()
            resp = client.post(
                "/api/v1/tasks/task-cancel/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["status"] == "CANCELLED"
        # 验证 Celery revoke 被调用
        mock_celery_app.control.revoke.assert_called_once_with(
            "celery-001", terminate=False
        )

    def test_cancel_terminal_task(
        self, client, task_redis, fake_redis
    ) -> None:
        """取消已完成的任务返回 400."""
        _save_task_to_redis(
            task_redis, task_id="task-done", status="SUCCESS"
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/task-done/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_TASK_NOT_CANCELLABLE"

    def test_cancel_not_found(self, client, task_redis, fake_redis) -> None:
        """取消不存在的任务返回 404."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/nonexistent/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_TASK_NOT_FOUND"

    def test_cancel_sponsor_forbidden(
        self, client, task_redis, fake_redis
    ) -> None:
        """SPONSOR 不能取消任务（403）."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/tasks/some-task/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_cancel_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/tasks/some-task/cancel")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# KPI Schema 扩展验证（Phase 5 Track A — IDS §2.7.1）
# ---------------------------------------------------------------------------


class TestKpiSnapshotSchemaLineage:
    """KpiSnapshotSchema 数据血缘字段验证."""

    _LINEAGE_FIELDS = (
        "idealSettlingTime",
        "algorithmVersion",
        "samplingFreq",
        "qualityPolicy",
        "validRate",
        "confidenceLevel",
        "dataLineage",
    )

    def test_kpi_snapshot_has_7_lineage_fields(self) -> None:
        """KpiSnapshotSchema 应包含 7 个血缘字段."""
        from app.schemas.performance import KpiSnapshotSchema

        fields = set(KpiSnapshotSchema.model_fields.keys())
        # performance.py 中血缘字段直接以 camelCase 名定义
        expected = {
            "idealSettlingTime",
            "algorithmVersion",
            "samplingFreq",
            "qualityPolicy",
            "validRate",
            "confidenceLevel",
            "dataLineage",
        }
        missing = expected - fields
        assert not missing, f"KpiSnapshotSchema 缺少血缘字段: {missing}"
        assert len(expected) == 7

    def test_kpi_snapshot_lineage_fields_default_none(self) -> None:
        """新血缘字段默认 None，保持向后兼容."""
        from app.schemas.performance import KpiSnapshotSchema

        snap = KpiSnapshotSchema(loopId="loop-1")
        assert snap.idealSettlingTime is None
        assert snap.algorithmVersion is None
        assert snap.samplingFreq is None
        assert snap.qualityPolicy is None
        assert snap.validRate is None
        assert snap.confidenceLevel is None
        assert snap.dataLineage is None

    def test_kpi_snapshot_with_lineage_data(self) -> None:
        """KpiSnapshotSchema 可以填充血缘字段."""
        from app.schemas.performance import DataLineageSchema, KpiSnapshotSchema

        lineage = DataLineageSchema(
            samplingFreq="1s",
            aggregationPolicy="MEAN",
            qualityPolicy="KEEP_ALL_WITH_VALIDITY",
            tagGroup="BASE",
            dataBlockIds=["block-001"],
            validRate=0.95,
            dataPolicyVersion="pre_v1",
            algorithmVersion="KPI_CALC_v2.0",
        )
        snap = KpiSnapshotSchema(
            loopId="loop-1",
            score=85.5,
            validRate=0.95,
            confidenceLevel="A",
            samplingFreq="1s",
            qualityPolicy="KEEP_ALL_WITH_VALIDITY",
            algorithmVersion="KPI_CALC_v2.0",
            idealSettlingTime=120.0,
            dataLineage=lineage,
        )
        assert snap.validRate == 0.95
        assert snap.confidenceLevel == "A"
        assert snap.idealSettlingTime == 120.0
        assert snap.dataLineage is not None
        assert snap.dataLineage.tagGroup == "BASE"


class TestDataLineageSchema:
    """DataLineageSchema 子字段验证."""

    _EXPECTED_FIELDS = (
        "samplingFreq",
        "aggregationPolicy",
        "qualityPolicy",
        "tagGroup",
        "dataBlockIds",
        "validRate",
        "dataPolicyVersion",
        "algorithmVersion",
    )

    def test_data_lineage_has_8_fields(self) -> None:
        """DataLineageSchema 应包含 8 个子字段."""
        from app.schemas.performance import DataLineageSchema

        fields = set(DataLineageSchema.model_fields.keys())
        expected = {
            "samplingFreq",
            "aggregationPolicy",
            "qualityPolicy",
            "tagGroup",
            "dataBlockIds",
            "validRate",
            "dataPolicyVersion",
            "algorithmVersion",
        }
        missing = expected - fields
        assert not missing, f"DataLineageSchema 缺少字段: {missing}"
        assert len(expected) == 8

    def test_data_lineage_defaults(self) -> None:
        """DataLineageSchema 默认值合理."""
        from app.schemas.performance import DataLineageSchema

        lineage = DataLineageSchema()
        assert lineage.samplingFreq == ""
        assert lineage.aggregationPolicy == ""
        assert lineage.qualityPolicy == ""
        assert lineage.tagGroup == ""
        assert lineage.dataBlockIds == []
        assert lineage.validRate == 0.0
        assert lineage.dataPolicyVersion == "pre_v1"
        assert lineage.algorithmVersion == "KPI_CALC_v2.0"
