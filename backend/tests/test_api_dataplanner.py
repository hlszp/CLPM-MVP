"""DataPlanner 管理接口测试 (IDS v3.2 §2.7.5).

测试覆盖：
- POST /api/v1/algorithms/dataplanner/plan       — 提交查询计划
- POST /api/v1/algorithms/dataplanner/bundle     — 获取 Bundle 摘要
- GET  /api/v1/algorithms/dataplanner/cache/stats — 缓存统计
- DELETE /api/v1/algorithms/dataplanner/cache/{loopId} — 缓存失效

设计依据：IDS §2.7.5, PRD §8.1-8.3
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------

_PLAN_BODY = {
    "loopId": "00000000-0000-0000-0000-000000000201",
    "metrics": ["accuracy_rate", "steady_rate"],
    "start": "2026-06-22T08:00:00Z",
    "end": "2026-06-22T09:00:00Z",
    "controlType": "FC",
}


def _make_query_task_mock(
    tag_group_val: str = "BASE",
    metrics: list[str] | None = None,
    tag_roles: list[str] | None = None,
    interval_s: int = 1,
    reused_from_val: str | None = None,
) -> MagicMock:
    """构造查询计划任务 mock（模拟 DataPlanner._build_query_plan 返回项）."""
    t = MagicMock()
    t.tag_group = MagicMock(value=tag_group_val)
    t.metrics = metrics or ["accuracy_rate"]
    t.tag_roles = tag_roles or ["pv", "sp"]
    t.interval_s = interval_s
    t.reused_from = MagicMock(value=reused_from_val) if reused_from_val else None
    return t


def _make_bundle_mock(
    metric_code: str = "accuracy_rate",
    tag_group: str = "BASE",
    sampling_freq: str = "1s",
    point_count: int = 3600,
    valid_rate: float = 0.95,
    data_block_id: str = "block-001",
) -> MagicMock:
    """构造 Bundle mock."""
    bundle = MagicMock()
    bundle.metric_code = metric_code
    bundle.data_block = MagicMock(
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        point_count=point_count,
        data_block_id=data_block_id,
    )
    bundle.lineage = MagicMock(valid_rate=valid_rate)
    return bundle


def _make_planner_mock(
    requirements: dict | None = None,
    query_plan: list | None = None,
    bundles: list | None = None,
) -> MagicMock:
    """构造 DataPlanner mock."""
    planner = MagicMock()
    planner._load_requirements = AsyncMock(
        return_value=requirements if requirements is not None else {"accuracy_rate": MagicMock()}
    )
    planner._build_query_plan = MagicMock(
        return_value=query_plan if query_plan is not None else [_make_query_task_mock()]
    )
    planner.request_bundles = AsyncMock(
        return_value=bundles if bundles is not None else [_make_bundle_mock()]
    )
    return planner


# ---------------------------------------------------------------------------
# POST /api/v1/algorithms/dataplanner/plan
# ---------------------------------------------------------------------------


class TestSubmitPlan:
    """POST /api/v1/algorithms/dataplanner/plan tests."""

    def test_plan_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以提交查询计划."""
        planner = _make_planner_mock(
            requirements={"accuracy_rate": MagicMock()},
            query_plan=[_make_query_task_mock(), _make_query_task_mock(tag_group_val="OP_HF")],
        )
        with (
            patch("app.api.v1.endpoints.dataplanner._build_data_planner", return_value=planner),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/plan",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == _PLAN_BODY["loopId"]
        assert data["totalTagGroups"] == 2
        assert len(data["queryTasks"]) == 2
        assert data["queryTasks"][0]["tagGroup"] == "BASE"

    def test_plan_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 角色不能提交查询计划（403）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/plan",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_plan_invalid_control_type(self, client, mock_db, fake_redis) -> None:
        """无效控制类型返回 400."""
        body = {**_PLAN_BODY, "controlType": "INVALID"}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/plan",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_CONTROL_TYPE"

    def test_plan_invalid_time_window(self, client, mock_db, fake_redis) -> None:
        """起始时间不早于结束时间返回 400."""
        body = {
            **_PLAN_BODY,
            "start": "2026-06-22T09:00:00Z",
            "end": "2026-06-22T08:00:00Z",
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/plan",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_TIME"

    def test_plan_metric_not_found(self, client, mock_db, fake_redis) -> None:
        """未找到任何指标契约返回 404."""
        planner = _make_planner_mock(requirements={})
        with (
            patch("app.api.v1.endpoints.dataplanner._build_data_planner", return_value=planner),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/plan",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_METRIC_NOT_FOUND"

    def test_plan_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/algorithms/dataplanner/plan", json=_PLAN_BODY)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/algorithms/dataplanner/bundle
# ---------------------------------------------------------------------------


class TestGetBundle:
    """POST /api/v1/algorithms/dataplanner/bundle tests."""

    def test_bundle_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以获取 Bundle 摘要."""
        bundles = [
            _make_bundle_mock(metric_code="accuracy_rate", valid_rate=0.95),
            _make_bundle_mock(
                metric_code="steady_rate",
                tag_group="OP_HF",
                valid_rate=0.80,
                data_block_id="block-002",
            ),
        ]
        planner = _make_planner_mock(bundles=bundles)
        with (
            patch("app.api.v1.endpoints.dataplanner._build_data_planner", return_value=planner),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/bundle",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == _PLAN_BODY["loopId"]
        assert len(data["bundles"]) == 2
        assert data["bundles"][0]["metricCode"] == "accuracy_rate"
        assert data["bundles"][1]["tagGroup"] == "OP_HF"
        # 平均有效数据率 = (0.95 + 0.80) / 2 = 0.875
        assert data["validRate"] == 0.875
        # 0.875 >= 0.80 → 可信度 B
        assert data["confidenceLevel"] == "B"

    def test_bundle_empty(self, client, mock_db, fake_redis) -> None:
        """DataPlanner 返回空 Bundle 列表时返回空结果."""
        planner = _make_planner_mock(bundles=[])
        with (
            patch("app.api.v1.endpoints.dataplanner._build_data_planner", return_value=planner),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/bundle",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["bundles"] == []
        assert data["validRate"] == 0.0
        assert data["confidenceLevel"] == "E"

    def test_bundle_dataplanner_exception(self, client, mock_db, fake_redis) -> None:
        """DataPlanner 异常时返回 500 ERR_DATAPLANNER_FAILED."""
        planner = MagicMock()
        planner.request_bundles = AsyncMock(side_effect=RuntimeError("TDengine down"))
        with (
            patch("app.api.v1.endpoints.dataplanner._build_data_planner", return_value=planner),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/bundle",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 500
        assert resp.json()["code"] == "ERR_DATAPLANNER_FAILED"

    def test_bundle_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 角色不能获取 Bundle（403）."""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/algorithms/dataplanner/bundle",
                json=_PLAN_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_bundle_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/algorithms/dataplanner/bundle", json=_PLAN_BODY)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/algorithms/dataplanner/cache/stats
# ---------------------------------------------------------------------------


class TestCacheStats:
    """GET /api/v1/algorithms/dataplanner/cache/stats tests."""

    def test_cache_stats_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以获取缓存统计."""
        fake_redis_mock = AsyncMock()
        # scan 返回 (cursor=0, keys) — 一次扫描即结束
        fake_redis_mock.scan = AsyncMock(
            return_value=(0, ["pdb:loop-1:BASE:100:200:1s:KEEP_ALL:pre_v1:cfg_v1"])
        )
        fake_redis_mock.info = AsyncMock(
            return_value={
                "used_memory": 1048576,  # 1 MB
                "keyspace_hits": 80,
                "keyspace_misses": 20,
            }
        )
        with (
            patch("app.api.v1.endpoints.dataplanner.redis_client", fake_redis_mock),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/dataplanner/cache/stats",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["totalKeys"] == 1
        # hit_rate = 80 / (80 + 20) = 0.8
        assert data["hitRate"] == 0.8
        # memory = 1048576 / (1024 * 1024) = 1.0 MB
        assert data["memoryUsageMb"] == 1.0
        assert "BASE" in data["byTagGroup"]
        assert data["byTagGroup"]["BASE"] == 1

    def test_cache_stats_empty(self, client, mock_db, fake_redis) -> None:
        """无缓存时返回零值."""
        fake_redis_mock = AsyncMock()
        fake_redis_mock.scan = AsyncMock(return_value=(0, []))
        fake_redis_mock.info = AsyncMock(return_value={})
        with (
            patch("app.api.v1.endpoints.dataplanner.redis_client", fake_redis_mock),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/dataplanner/cache/stats",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["totalKeys"] == 0
        assert data["hitRate"] == 0.0
        assert data["byTagGroup"] == {}

    def test_cache_stats_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 角色不能查看缓存统计（403）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/algorithms/dataplanner/cache/stats",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_cache_stats_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/algorithms/dataplanner/cache/stats")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/algorithms/dataplanner/cache/{loopId}
# ---------------------------------------------------------------------------


class TestCacheInvalidate:
    """DELETE /api/v1/algorithms/dataplanner/cache/{loopId} tests."""

    def test_invalidate_cache_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以失效指定回路缓存."""
        fake_redis_mock = AsyncMock()
        # CacheInvalidator uses scan + delete
        fake_redis_mock.scan = AsyncMock(
            return_value=(
                0,
                ["pdb:loop-1:BASE:100:200:1s:KEEP_ALL:pre_v1:cfg_v1"],
            )
        )
        fake_redis_mock.delete = AsyncMock(return_value=1)
        with (
            patch("app.api.v1.endpoints.dataplanner.redis_client", fake_redis_mock),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.delete(
                "/api/v1/algorithms/dataplanner/cache/loop-1",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == "loop-1"
        assert data["deletedKeys"] == 1

    def test_invalidate_cache_empty(self, client, mock_db, fake_redis) -> None:
        """回路无缓存时 deletedKeys=0."""
        fake_redis_mock = AsyncMock()
        fake_redis_mock.scan = AsyncMock(return_value=(0, []))
        fake_redis_mock.delete = AsyncMock(return_value=0)
        with (
            patch("app.api.v1.endpoints.dataplanner.redis_client", fake_redis_mock),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.delete(
                "/api/v1/algorithms/dataplanner/cache/loop-empty",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["deletedKeys"] == 0

    def test_invalidate_cache_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 角色不能失效缓存（403）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                "/api/v1/algorithms/dataplanner/cache/loop-1",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_invalidate_cache_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.delete("/api/v1/algorithms/dataplanner/cache/loop-1")
        assert resp.status_code == 401
