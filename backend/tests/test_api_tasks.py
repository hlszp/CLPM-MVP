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

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# FakeTaskRedis — 支持 Hash + Sorted Set 操作的内存 Redis mock
# ---------------------------------------------------------------------------


class FakeTaskRedis:
    """In-memory Redis mock supporting hash, sorted set, and list operations.

    FakeRedis in conftest.py only supports string/set operations.
    Task endpoints need hset/hgetall/zadd/zrange/zrevrange.
    Notification endpoints need lpush/lrange/ltrim/delete.
    """

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._lists: dict[str, list[str]] = {}
        self._sets: dict[str, set[str]] = {}
        self._ttls: dict[str, int] = {}

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

    async def lpush(self, key: str, *values: str) -> int:
        """List push to head (newest first)."""
        lst = self._lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self._lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start : end + 1]

    async def ltrim(self, key: str, start: int, end: int) -> None:
        """Trim list to [start, end] range."""
        lst = self._lists.get(key, [])
        if end == -1:
            self._lists[key] = lst[start:]
        else:
            self._lists[key] = lst[start : end + 1]

    async def delete(self, *keys: str) -> int:
        """Delete keys from all stores."""
        deleted = 0
        for key in keys:
            if key in self._hashes:
                del self._hashes[key]
                deleted += 1
            if key in self._zsets:
                del self._zsets[key]
                deleted += 1
            if key in self._lists:
                del self._lists[key]
                deleted += 1
            if key in self._sets:
                del self._sets[key]
                deleted += 1
            self._ttls.pop(key, None)
        return deleted

    async def zrem(self, key: str, *members: str) -> int:
        """Remove members from sorted set."""
        zset = self._zsets.get(key, {})
        removed = 0
        for m in members:
            if m in zset:
                del zset[m]
                removed += 1
        return removed

    async def hget(self, key: str, field: str) -> str | None:
        """Get a single hash field."""
        return self._hashes.get(key, {}).get(field)

    async def eval(self, script: str, numkeys: int, *args):  # noqa: PLR0911
        """Implement the task_tracker Lua contracts atomically for service tests."""
        keys = [str(value) for value in args[:numkeys]]
        argv = [str(value) for value in args[numkeys:]]
        task = self._hashes.get(keys[0]) if keys else None

        if "CLPM_TASK_STATUS_CAS_V1" in script:
            if task is None:
                return ["MISSING", ""]
            new_status = argv[0]
            old_status = task.get("status", "")
            if old_status in {"SUCCESS", "FAILED", "CANCELLED"} and old_status != new_status:
                return ["BLOCKED", old_status]
            task["status"] = new_status
            for index in range(1, len(argv), 2):
                field, value = argv[index], argv[index + 1]
                if field in {"progress", "loops_done"} and task.get(field) not in {None, ""}:
                    if float(value) < float(task[field]):
                        continue
                task[field] = value
            return ["UPDATED", old_status]

        if "CLPM_BACKFILL_DISPATCH_RESERVE_V1" in script:
            if task is None:
                return ["MISSING", "[]", ""]
            state = task.get("backfill_dispatch_state")
            existing_ids = task.get("backfill_child_task_ids")
            callback = task.get("backfill_callback_task_id", "")
            legacy_ids = task.get("celery_task_ids")
            if state == "DISPATCHED" or (
                state is None and (existing_ids or callback or legacy_ids)
            ):
                return ["EXISTING", existing_ids or legacy_ids or "[]", callback]
            if state == "DISPATCHING":
                task["backfill_dispatch_token"] = argv[0]
                return ["RECOVER", existing_ids or "[]", callback]
            task.update(
                {
                    "backfill_dispatch_state": "DISPATCHING",
                    "backfill_dispatch_token": argv[0],
                    "backfill_child_task_ids": argv[1],
                    "backfill_callback_task_id": argv[2],
                    "celery_task_ids": argv[3],
                }
            )
            return ["CLAIMED", argv[1], argv[2]]

        if "CLPM_BACKFILL_DISPATCH_COMPLETE_V1" in script:
            if (
                task is None
                or task.get("backfill_dispatch_state") != "DISPATCHING"
                or task.get("backfill_dispatch_token") != argv[0]
            ):
                return 0
            task["backfill_dispatch_state"] = "DISPATCHED"
            task.pop("backfill_dispatch_token", None)
            return 1

        if "CLPM_BACKFILL_DISPATCH_RELEASE_V1" in script:
            if (
                task is None
                or task.get("backfill_dispatch_state") != "DISPATCHING"
                or task.get("backfill_dispatch_token") != argv[0]
            ):
                return 0
            for field in (
                "backfill_dispatch_state",
                "backfill_dispatch_token",
                "backfill_child_task_ids",
                "backfill_callback_task_id",
                "celery_task_ids",
            ):
                task.pop(field, None)
            return 1

        if "CLPM_BACKFILL_PROGRESS_V1" in script:
            if task is None:
                return ["MISSING", "0", "0"]
            current_done = int(task.get("backfill_done", "0") or 0)
            current_progress = float(task.get("progress", "0") or 0)
            if task.get("status") in {"SUCCESS", "FAILED", "CANCELLED"}:
                return ["TERMINAL", str(current_done), str(current_progress)]
            events = self._sets.setdefault(keys[1], set())
            self._ttls[keys[1]] = int(argv[3])
            if argv[0] in events:
                return ["DUPLICATE", str(current_done), str(current_progress)]
            events.add(argv[0])
            total = int(argv[1])
            done = min(current_done + 1, total)
            progress = max(done / total if total else 0.0, current_progress)
            task.update(
                {
                    "status": "RUNNING",
                    "backfill_done": str(done),
                    "progress": str(progress),
                    "loops_done": str(done),
                    "loops_total": str(total),
                    "current_stage": argv[2],
                }
            )
            return ["COUNTED", str(done), str(progress)]

        raise AssertionError("Unsupported task_tracker Lua script")


@pytest.fixture
def task_redis() -> FakeTaskRedis:
    """Patch redis_client in tasks endpoint AND task_tracker service.

    通知端点调用 task_tracker 服务，task_tracker 内部使用自己的
    redis_client 导入。两个位置都需要 patch 才能生效。
    """
    fake = FakeTaskRedis()
    with (
        patch("app.api.v1.endpoints.tasks.redis_client", fake),
        patch("app.services.task_tracker.redis_client", fake),
    ):
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

    def test_standard_evaluate_success(self, client, task_redis, fake_redis) -> None:
        """IC_ENGINEER 可以触发标准评估任务，tsStart 透传给 Celery（P1 #11）."""
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
        # P1 #11: 验证 tsStart 透传给 Celery 任务
        mock_celery.delay.assert_called_once_with(ts_start="2026-06-22T08:00:00Z")

    def test_standard_evaluate_admin(self, client, task_redis, fake_redis) -> None:
        """ADMIN 可以触发标准评估任务（不传 tsStart 时 ts_start=None）."""
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
        # P1 #11: 未传 tsStart 时 ts_start=None（取上一个完整计算周期）
        mock_celery.delay.assert_called_once_with(ts_start=None)

    def test_standard_evaluate_sponsor_forbidden(self, client, task_redis, fake_redis) -> None:
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

    @pytest.mark.skip(reason="calculate_custom_loop_kpi 未实现（PR #61 已关闭），端点暂不可用")
    def test_custom_evaluate_success(self, client, task_redis, fake_redis) -> None:
        """IC_ENGINEER 可以触发自定义评估任务."""
        with (
            patch("app.tasks.kpi_calc.calculate_custom_loop_kpi") as mock_celery,
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
        # P1 #12: 验证 ts_end 透传给 Celery 任务
        mock_celery.delay.assert_called_once()
        call_args = mock_celery.delay.call_args
        assert call_args.args[1] == "00000000-0000-0000-0000-000000000201"
        assert call_args.args[2] == "2026-06-22T08:00:00Z"
        assert call_args.args[3] == "2026-06-22T09:00:00Z"

    def test_custom_evaluate_empty_loops(self, client, task_redis, fake_redis) -> None:
        """空回路列表返回 400 ERR_INVALID_REQUEST."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/custom/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={**_CUSTOM_BODY, "loopIds": []},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_REQUEST"

    def test_custom_evaluate_empty_metrics(self, client, task_redis, fake_redis) -> None:
        """空指标列表返回 400 ERR_INVALID_REQUEST."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/custom/evaluate",
                headers={"Authorization": "Bearer fake-token"},
                json={**_CUSTOM_BODY, "metrics": []},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_REQUEST"

    def test_custom_evaluate_concurrency_limit_user(self, client, task_redis, fake_redis) -> None:
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

    def test_get_task_success_status(self, client, task_redis, fake_redis) -> None:
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

    def test_list_tasks_filter_by_type(self, client, task_redis, fake_redis) -> None:
        """按任务类型筛选."""
        _save_task_to_redis(task_redis, task_id="task-std", task_type="STANDARD", status="SUCCESS")
        _save_task_to_redis(task_redis, task_id="task-cust", task_type="CUSTOM", status="PENDING")
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks?taskType=CUSTOM",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["taskType"] == "CUSTOM"

    def test_list_tasks_filter_by_status(self, client, task_redis, fake_redis) -> None:
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

    def test_cancel_pending_task(self, client, task_redis, fake_redis) -> None:
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
        # 验证 Celery revoke 被调用（terminate=True 强制终止 worker 进程）
        mock_celery_app.control.revoke.assert_called_once_with("celery-001", terminate=True)

    def test_cancel_terminal_task(self, client, task_redis, fake_redis) -> None:
        """取消已完成的任务返回 400."""
        _save_task_to_redis(task_redis, task_id="task-done", status="SUCCESS")
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/task-done/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_TASK_NOT_CANCELLABLE"

    def test_cancel_cas_preserves_terminal_update_after_stale_read(
        self, client, task_redis, fake_redis
    ) -> None:
        """SUCCESS won after the endpoint read; cancellation CAS must not overwrite it."""
        stale = _save_task_to_redis(task_redis, task_id="task-race", status="PENDING")

        async def stale_read(_task_id: str) -> dict[str, str]:
            task_redis._hashes["task:task-race"]["status"] = "SUCCESS"
            return dict(stale)

        with (
            patch("app.api.v1.endpoints.tasks._get_task", side_effect=stale_read),
            patch("app.tasks.celery_app.celery_app") as mock_celery_app,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/tasks/task-race/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        assert task_redis._hashes["task:task-race"]["status"] == "SUCCESS"
        mock_celery_app.control.revoke.assert_not_called()

    def test_cancel_not_found(self, client, task_redis, fake_redis) -> None:
        """取消不存在的任务返回 404."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tasks/nonexistent/cancel",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_TASK_NOT_FOUND"

    def test_cancel_sponsor_forbidden(self, client, task_redis, fake_redis) -> None:
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
# DELETE /api/v1/tasks/{taskId}
# ---------------------------------------------------------------------------


class TestDeleteTask:
    """DELETE /api/v1/tasks/{taskId} tests — IDS §2.7.6.6"""

    def test_delete_success_task(self, client, task_redis, fake_redis) -> None:
        """删除 SUCCESS 状态任务：从 Redis 哈希与索引中移除."""
        _save_task_to_redis(
            task_redis,
            task_id="task-del-success",
            status="SUCCESS",
            task_type="BACKFILL",
        )
        event_key = "task:task-del-success:backfill_progress_events"
        task_redis._sets[event_key] = {"w1:loop-1"}
        task_redis._ttls[event_key] = 604_800
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                "/api/v1/tasks/task-del-success",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["task_id"] == "task-del-success"
        assert body["data"]["deleted"] is True
        # 验证 Redis Hash 已删除
        assert "task:task-del-success" not in task_redis._hashes
        assert event_key not in task_redis._sets
        assert event_key not in task_redis._ttls
        # 验证索引 zset 中已移除
        assert "task-del-success" not in task_redis._zsets.get("task:index", {})

    def test_delete_failed_task(self, client, task_redis, fake_redis) -> None:
        """删除 FAILED 状态任务."""
        _save_task_to_redis(
            task_redis,
            task_id="task-del-failed",
            status="FAILED",
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                "/api/v1/tasks/task-del-failed",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True
        assert "task:task-del-failed" not in task_redis._hashes

    def test_delete_cancelled_task(self, client, task_redis, fake_redis) -> None:
        """删除 CANCELLED 状态任务."""
        _save_task_to_redis(
            task_redis,
            task_id="task-del-cancelled",
            status="CANCELLED",
        )
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.delete(
                "/api/v1/tasks/task-del-cancelled",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    def test_delete_running_task_rejected(self, client, task_redis, fake_redis) -> None:
        """删除 RUNNING 状态任务返回 400 — 必须先取消."""
        _save_task_to_redis(
            task_redis,
            task_id="task-running",
            status="RUNNING",
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                "/api/v1/tasks/task-running",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_TASK_NOT_DELETABLE"
        # 验证任务仍在 Redis 中
        assert "task:task-running" in task_redis._hashes

    def test_delete_pending_task_rejected(self, client, task_redis, fake_redis) -> None:
        """删除 PENDING 状态任务返回 400."""
        _save_task_to_redis(
            task_redis,
            task_id="task-pending",
            status="PENDING",
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                "/api/v1/tasks/task-pending",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_TASK_NOT_DELETABLE"

    def test_delete_not_found(self, client, task_redis, fake_redis) -> None:
        """删除不存在的任务返回 404."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                "/api/v1/tasks/nonexistent-task",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_TASK_NOT_FOUND"

    def test_delete_sponsor_forbidden(self, client, task_redis, fake_redis) -> None:
        """SPONSOR 不能删除任务（403）."""
        _save_task_to_redis(
            task_redis,
            task_id="task-sponsor",
            status="SUCCESS",
        )
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.delete(
                "/api/v1/tasks/task-sponsor",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"
        # 任务仍存在
        assert "task:task-sponsor" in task_redis._hashes

    def test_delete_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.delete("/api/v1/tasks/some-task")
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


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/notifications — 任务通知查询（Phase 5 补齐）
# ---------------------------------------------------------------------------


class TestListNotifications:
    """GET /api/v1/tasks/notifications tests."""

    def test_list_notifications_empty(self, client, task_redis, fake_redis) -> None:
        """无通知时返回空列表."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/notifications",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_notifications_with_data(self, client, task_redis, fake_redis) -> None:
        """查询到任务完成通知."""
        import json as _json

        user_id = "00000000-0000-0000-0000-000000000001"
        notification = {
            "task_id": "task-notify-001",
            "task_type": "STANDARD",
            "status": "SUCCESS",
            "created_by": "admin",
            "finished_at": "2026-06-26T10:00:00+00:00",
            "error_message": "",
            "notification_time": "2026-06-26T10:00:01+00:00",
        }
        task_redis._lists[f"task:notifications:{user_id}"] = [_json.dumps(notification)]
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/notifications",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["task_id"] == "task-notify-001"
        assert data["items"][0]["status"] == "SUCCESS"

    def test_list_notifications_with_limit(self, client, task_redis, fake_redis) -> None:
        """limit 参数控制返回条数."""
        import json as _json

        user_id = "00000000-0000-0000-0000-000000000002"
        notifications = []
        for i in range(5):
            notifications.append(_json.dumps({"task_id": f"task-{i}", "status": "SUCCESS"}))
        task_redis._lists[f"task:notifications:{user_id}"] = notifications
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/tasks/notifications?limit=3",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["items"]) == 3

    def test_list_notifications_invalid_limit(self, client, task_redis, fake_redis) -> None:
        """limit 超出范围返回 422."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/notifications?limit=0",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 422

    def test_list_notifications_no_token(self, client) -> None:
        """未认证返回 401."""
        resp = client.get("/api/v1/tasks/notifications")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/tasks/notifications/{taskId}/read — 标记通知已读
# ---------------------------------------------------------------------------


class TestMarkNotificationRead:
    """POST /api/v1/tasks/notifications/{taskId}/read tests."""

    def test_mark_read_success(self, client, task_redis, fake_redis) -> None:
        """标记通知已读成功."""
        import json as _json

        user_id = "00000000-0000-0000-0000-000000000001"
        notification = {"task_id": "task-read-001", "status": "SUCCESS"}
        task_redis._lists[f"task:notifications:{user_id}"] = [_json.dumps(notification)]
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tasks/notifications/task-read-001/read",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["taskId"] == "task-read-001"
        assert data["read"] is True
        # 验证通知已从列表移除
        assert f"task:notifications:{user_id}" not in task_redis._lists or (
            len(task_redis._lists[f"task:notifications:{user_id}"]) == 0
        )

    def test_mark_read_not_found(self, client, task_redis, fake_redis) -> None:
        """通知不存在返回 404."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tasks/notifications/nonexistent-task/read",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NOTIFICATION_NOT_FOUND"

    def test_mark_read_no_token(self, client) -> None:
        """未认证返回 401."""
        resp = client.post("/api/v1/tasks/notifications/some-task/read")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 路由顺序验证：GET /tasks/notifications 不被 GET /tasks/{taskId} 拦截
# ---------------------------------------------------------------------------


class TestNotificationRouteOrder:
    """验证 /notifications 路由不被 /{task_id} 拦截."""

    def test_notifications_route_not_intercepted(self, client, task_redis, fake_redis) -> None:
        """GET /tasks/notifications 应命中通知端点，而非 /{task_id}."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/notifications",
                headers={"Authorization": "Bearer fake-token"},
            )
        # 若被 /{task_id} 拦截，会返回 404 (ERR_TASK_NOT_FOUND)
        # 正确行为：返回 200 + 空通知列表
        assert resp.status_code == 200
        assert resp.json()["code"] == "0"


# ---------------------------------------------------------------------------
# Celery 任务 ID 格式兼容
# ---------------------------------------------------------------------------


class TestCeleryTaskIdParsing:
    """单任务 ID 与旧数组 ID 都可被状态和取消路径读取。"""

    def test_single_task_id_is_supported(self) -> None:
        from app.api.v1.endpoints.tasks import _parse_celery_task_ids

        assert _parse_celery_task_ids({"celery_task_id": "celery-001"}) == ["celery-001"]

    def test_legacy_task_id_array_is_supported(self) -> None:
        from app.api.v1.endpoints.tasks import _parse_celery_task_ids

        assert _parse_celery_task_ids({"celery_task_ids": '["celery-001", "celery-002"]'}) == [
            "celery-001",
            "celery-002",
        ]

    def test_root_and_canvas_task_ids_are_combined(self) -> None:
        from app.api.v1.endpoints.tasks import _parse_celery_task_ids

        assert _parse_celery_task_ids(
            {
                "celery_task_id": "dispatcher-001",
                "celery_task_ids": '["child-001", "callback-001"]',
            }
        ) == ["dispatcher-001", "child-001", "callback-001"]

    def test_invalid_task_id_value_is_ignored(self) -> None:
        from app.api.v1.endpoints.tasks import _parse_celery_task_ids

        assert _parse_celery_task_ids({"celery_task_ids": "not-json"}) == []


class TestTaskTrackerService:
    """task_tracker 服务层核心功能测试."""

    async def test_create_task_returns_uuid(self, task_redis) -> None:
        """create_task 返回 UUID 字符串."""
        from app.schemas.task import TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.STANDARD,
            created_by="admin",
            created_by_id="user-001",
            celery_task_id="celery-001",
            triggered_by="user",
        )
        assert task_id
        assert len(task_id) == 36  # UUID format

    async def test_update_status_to_success_sends_notification(self, task_redis) -> None:
        """任务进入 SUCCESS 时自动发送通知."""
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.CUSTOM,
            created_by="ic_engineer",
            created_by_id="user-002",
            celery_task_ids=["celery-002"],
        )
        # 初始状态 PENDING → 更新为 SUCCESS（终态转换）
        await task_tracker.update_status(
            task_id,
            TaskStatus.SUCCESS,
            finished_at=task_tracker._now_iso(),
            progress=1.0,
        )
        # 验证通知已写入
        notifications = await task_tracker.get_notifications("user-002")
        assert len(notifications) == 1
        assert notifications[0]["task_id"] == task_id
        assert notifications[0]["status"] == "SUCCESS"

    async def test_update_status_running_no_notification(self, task_redis) -> None:
        """任务进入 RUNNING（非终态）不发送通知."""
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.STANDARD,
            created_by="admin",
            created_by_id="user-003",
            celery_task_id="celery-003",
        )
        await task_tracker.update_status(
            task_id,
            TaskStatus.RUNNING,
            started_at=task_tracker._now_iso(),
            progress=0.5,
        )
        notifications = await task_tracker.get_notifications("user-003")
        assert len(notifications) == 0

    async def test_update_status_failed_sends_notification(self, task_redis) -> None:
        """任务进入 FAILED 时自动发送通知."""
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.CUSTOM,
            created_by="pe_engineer",
            created_by_id="user-004",
            celery_task_ids=["celery-004"],
        )
        await task_tracker.update_status(
            task_id,
            TaskStatus.FAILED,
            finished_at=task_tracker._now_iso(),
            error_message="计算失败",
        )
        notifications = await task_tracker.get_notifications("user-004")
        assert len(notifications) == 1
        assert notifications[0]["status"] == "FAILED"
        assert notifications[0]["error_message"] == "计算失败"

    async def test_system_task_no_notification(self, task_redis) -> None:
        """系统任务（created_by_id 为空）不发送通知."""
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.STANDARD,
            created_by="system",
            created_by_id="",  # 系统任务无用户
            celery_task_id="celery-system",
            triggered_by="system",
        )
        await task_tracker.update_status(
            task_id,
            TaskStatus.SUCCESS,
            finished_at=task_tracker._now_iso(),
            progress=1.0,
        )
        # 系统任务不通知个人用户
        notifications = await task_tracker.get_notifications("")
        assert len(notifications) == 0

    async def test_mark_notification_read(self, task_redis) -> None:
        """标记通知已读后从列表移除."""
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.CUSTOM,
            created_by="admin",
            created_by_id="user-005",
            celery_task_ids=["celery-005"],
        )
        await task_tracker.update_status(
            task_id, TaskStatus.SUCCESS, finished_at=task_tracker._now_iso()
        )
        # 标记已读
        removed = await task_tracker.mark_notification_read("user-005", task_id)
        assert removed is True
        # 验证已移除
        notifications = await task_tracker.get_notifications("user-005")
        assert len(notifications) == 0

    async def test_mark_notification_read_not_found(self, task_redis) -> None:
        """标记不存在的通知返回 False."""
        from app.services import task_tracker

        result = await task_tracker.mark_notification_read("user-006", "nonexistent")
        assert result is False

    async def test_notification_max_limit(self, task_redis) -> None:
        """通知列表不超过 _NOTIFICATION_MAX 条."""
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        # 创建 105 个任务并全部完成，触发 105 次通知
        for i in range(105):
            task_id = await task_tracker.create_task(
                task_type=TaskType.CUSTOM,
                created_by="admin",
                created_by_id="user-007",
                celery_task_ids=[f"celery-{i}"],
            )
            await task_tracker.update_status(
                task_id,
                TaskStatus.SUCCESS,
                finished_at=task_tracker._now_iso(),
            )
        # 验证通知列表被 ltrim 到 _NOTIFICATION_MAX 条
        notifications = await task_tracker.get_notifications("user-007", limit=200)
        assert len(notifications) == task_tracker._NOTIFICATION_MAX

    async def test_concurrent_terminal_updates_keep_first_terminal(self, task_redis) -> None:
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.CUSTOM,
            created_by="admin",
            created_by_id="",
        )
        await asyncio.gather(
            task_tracker.update_status(task_id, TaskStatus.CANCELLED),
            task_tracker.update_status(task_id, TaskStatus.FAILED),
        )
        first_terminal = (await task_tracker.get_task(task_id))["status"]
        await task_tracker.update_status(task_id, TaskStatus.SUCCESS)
        assert (await task_tracker.get_task(task_id))["status"] == first_terminal

    async def test_backfill_progress_is_monotonic_and_duplicate_safe(self, task_redis) -> None:
        from app.schemas.task import TaskType
        from app.services import task_tracker

        task_id = await task_tracker.create_task(
            task_type=TaskType.BACKFILL,
            created_by="admin",
            created_by_id="",
        )
        assert (await task_tracker.get_task(task_id))["progress"] == ""
        first = await task_tracker.record_backfill_progress_once(
            task_id,
            event_id="w1:loop-1",
            total_loops=2,
            current_stage="w1",
        )
        event_key = f"task:{task_id}:backfill_progress_events"
        assert task_redis._ttls[event_key] == 604_800
        task_redis._ttls[event_key] = 1
        duplicate = await task_tracker.record_backfill_progress_once(
            task_id,
            event_id="w1:loop-1",
            total_loops=2,
            current_stage="w1 duplicate",
        )
        assert task_redis._ttls[event_key] == 604_800
        second = await task_tracker.record_backfill_progress_once(
            task_id,
            event_id="w1:loop-2",
            total_loops=2,
            current_stage="w1",
        )

        assert first == (True, 1, 0.5)
        assert duplicate == (False, 1, 0.5)
        assert second == (True, 2, 1.0)
        task = await task_tracker.get_task(task_id)
        assert task["loops_done"] == "2"
        assert task["progress"] == "1.0"
