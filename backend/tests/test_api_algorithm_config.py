"""指标算法参数配置接口测试 (P0-B A7).

测试覆盖：
- GET  /api/v1/configs/algorithm-params            — 全部算法参数合并视图
- GET  /api/v1/configs/algorithm-params/{metric}   — 单指标算法参数
- PUT  /api/v1/configs/algorithm-params/{metric}   — 更新算法参数（仅 ADMIN）

权限矩阵：
- GET  允许 ADMIN / IC_ENGINEER / PE_ENGINEER（只读）
- PUT  仅 ADMIN

设计依据：app/api/v1/endpoints/algorithm_config.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import algorithm_config as ac
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_cache():
    """每个测试前后清空进程内缓存，避免相互污染。"""
    saved = dict(ac._merged_cache)
    ac._merged_cache = {}
    yield
    ac._merged_cache = saved


def _make_param_row(metric_code: str, control_type: str, params: dict) -> MagicMock:
    """构造 AlgorithmParameter ORM mock（load_stored_config 返回项）。"""
    row = MagicMock()
    row.metric_code = metric_code
    row.control_type = control_type
    row.params = params
    return row


def _make_existing_param(
    metric_code: str = "oscillation_rate",
    control_type: str = "STABLE",
    params: dict | None = None,
    updated_by: str = "admin",
    version: int = 1,
) -> MagicMock:
    """构造已存在的 AlgorithmParameter ORM mock（PUT upsert 查询返回）。"""
    row = MagicMock()
    row.metric_code = metric_code
    row.control_type = control_type
    row.params = params or {}
    row.updated_by = updated_by
    row.version = version
    row.updated_at = None
    return row


def _make_scalar_none_result() -> MagicMock:
    """构造 db.execute 返回值，scalar_one_or_none() 返回 None（新建记录）。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


def _make_scalar_result(value) -> MagicMock:
    """构造 db.execute 返回值，scalar_one_or_none() 返回 value（存量记录）。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_count_result(count: int = 5) -> MagicMock:
    """构造 db.execute 返回值，scalar_one() 返回 int（COUNT 聚合查询）。"""
    result = MagicMock()
    result.scalar_one.return_value = count
    return result


def _make_put_execute_side_effect(
    *,
    row_results: list[MagicMock],
    stored_rows: list | None = None,
    reset_results: list[MagicMock] | None = None,
) -> list:
    """构造 P2-02 后 PUT /algorithm-params 的 db.execute side_effect 序列.

    调用序（endpoints/algorithm_config.py::save_metric_algorithm_params）：
    1. get_current_revision（scalar_one_or_none → None 即 revision 0）
    2. before 快照循环（每 touched 控制类型一次，sorted 序）
    3. reset 循环（每 reset ct 一次）→ items 循环（每非空 item 一次）
    4. after 快照循环（每 touched ct 一次）
    5. note_legacy_sync：get_current_revision（expectedRevision 未传时再读一次）
       → _count_affected_loops（scalar_one → int）
    6. commit 后 sync_runtime_cache：load_stored_config（scalars().all()）→
       load_metric_thresholds（all()）→ get_current_revision

    Args:
        row_results: 存量记录查询结果（before/items/after 循环同结果复用；
            多控制类型时按 sorted(ct) 顺序给）
        stored_rows: commit 后 load_stored_config 返回行（None = 提交失败场景，
            不追加 commit 后查询）
        reset_results: reset 分支的存量查询结果（无 reset 省略）
    """
    side: list = [_make_scalar_none_result()]  # 1. 当前 revision → 0
    side.extend(row_results)  # 2. before 快照
    side.extend(reset_results or [])  # 3a. reset 存量查询
    side.extend(row_results)  # 3b. items 存量查询
    side.extend(row_results)  # 4. after 快照
    side.append(_make_scalar_none_result())  # 5a. note_legacy_sync revision
    side.append(_make_count_result())  # 5b. 影响回路数 COUNT
    if stored_rows is not None:
        side.append(_make_scalars_all_result(stored_rows))
        side.append(_make_all_result([]))
        side.append(_make_scalar_none_result())
    return side


def _make_scalars_all_result(items: list) -> MagicMock:
    """构造 db.execute 返回值，scalars().all() 返回 items（load_stored_config）。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_all_result(items: list) -> MagicMock:
    """构造 db.execute 返回值，all() 返回 items（load_metric_thresholds）。"""
    result = MagicMock()
    result.all.return_value = items
    return result


def _make_first_result(row=None) -> MagicMock:
    """构造 db.execute 返回值，first() 返回 row（GET 最近更新时间查询）。"""
    result = MagicMock()
    result.first.return_value = row
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/configs/algorithm-params — 全部算法参数合并视图
# ---------------------------------------------------------------------------


class TestGetAllAlgorithmParams:
    """GET /api/v1/configs/algorithm-params tests."""

    def test_success_returns_6_metrics_4_control_types(
        self, client, mock_db, fake_redis, reset_cache
    ) -> None:
        """返回 3 指标 × 4 控制类型，未覆盖时 overridden=False。"""
        # 缓存为空 → build_merged_view 回落默认值
        mock_db.execute = AsyncMock(return_value=_make_first_result(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        metrics = body["data"]["metrics"]
        # 8 指标（2026-10-10 新增 saturation_rate.saturation_epsilon 可配置）
        assert len(metrics) == 8
        codes = {m["metricCode"] for m in metrics}
        assert codes == {
            "oscillation_rate",
            "fast_rate",
            "accuracy_rate",
            "settling_time",
            "effective_auto_rate",
            "output_trip_index",
            "stability_rate",
            "saturation_rate",
        }
        # F6：paramMeta 注册表随视图下发（min/max/description/category 单源）
        osc = next(m for m in metrics if m["metricCode"] == "oscillation_rate")
        assert "similarity_threshold" in osc["paramMeta"]
        assert osc["paramMeta"]["similarity_threshold"]["min"] == 0.1
        assert osc["paramMeta"]["similarity_threshold"]["category"] == "判定阈值"
        # 每个指标 4 控制类型，未覆盖
        for m in metrics:
            assert len(m["items"]) == 4
            cts = {i["controlType"] for i in m["items"]}
            assert cts == {"STABLE", "SLOW", "FAST", "LOGIC"}
            for item in m["items"]:
                assert item["overridden"] is False
                assert item["params"] == item["defaults"]
        # 无 DB 记录时 updatedAt/updatedBy 为 None
        assert body["data"]["updatedAt"] is None
        assert body["data"]["updatedBy"] is None

    def test_success_with_overrides_marks_overridden(
        self, client, mock_db, fake_redis, reset_cache
    ) -> None:
        """缓存含覆盖时对应控制类型 overridden=True，params 与 defaults 不同。"""
        ac.apply_runtime({"oscillation_rate": {"STABLE": {"similarity_threshold": 0.55}}})
        mock_db.execute = AsyncMock(return_value=_make_first_result(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        metrics = resp.json()["data"]["metrics"]
        osc = next(m for m in metrics if m["metricCode"] == "oscillation_rate")
        stable = next(i for i in osc["items"] if i["controlType"] == "STABLE")
        assert stable["overridden"] is True
        assert stable["params"]["similarity_threshold"] == 0.55
        assert stable["defaults"]["similarity_threshold"] == 0.4
        # 其他控制类型未覆盖
        slow = next(i for i in osc["items"] if i["controlType"] == "SLOW")
        assert slow["overridden"] is False

    def test_includes_updated_at_from_db(self, client, mock_db, fake_redis, reset_cache) -> None:
        """DB 含最近更新记录时返回 updatedAt/updatedBy。"""
        from datetime import datetime

        latest_row = MagicMock()
        latest_row.updated_at = datetime(2026, 7, 24, 10, 0, 0)
        latest_row.updated_by = "admin"
        mock_db.execute = AsyncMock(return_value=_make_first_result(latest_row))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["updatedBy"] == "admin"
        assert data["updatedAt"] is not None

    def test_ic_engineer_allowed(self, client, mock_db, fake_redis, reset_cache) -> None:
        """IC_ENGINEER 可以查看算法参数（只读权限）。"""
        mock_db.execute = AsyncMock(return_value=_make_first_result(None))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_pe_engineer_allowed(self, client, mock_db, fake_redis, reset_cache) -> None:
        """PE_ENGINEER 可以查看算法参数（只读权限）。"""
        mock_db.execute = AsyncMock(return_value=_make_first_result(None))
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_no_token_401(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/configs/algorithm-params")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/configs/algorithm-params/{metricCode} — 单指标算法参数
# ---------------------------------------------------------------------------


class TestGetMetricAlgorithmParams:
    """GET /api/v1/configs/algorithm-params/{metricCode} tests."""

    def test_success_returns_4_control_types(
        self, client, mock_db, fake_redis, reset_cache
    ) -> None:
        """返回指定指标的 4 控制类型参数。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["metricCode"] == "oscillation_rate"
        assert data["metricName"] == "振荡率"
        assert len(data["items"]) == 4
        stable = next(i for i in data["items"] if i["controlType"] == "STABLE")
        assert "similarity_threshold" in stable["params"]
        assert "min_ratio" in stable["params"]
        assert "max_ratio" in stable["params"]

    def test_unknown_metric_404(self, client, mock_db, fake_redis, reset_cache) -> None:
        """未知指标代码返回 404。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params/nonexistent_metric",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NOT_FOUND"

    def test_ic_engineer_allowed(self, client, mock_db, fake_redis, reset_cache) -> None:
        """IC_ENGINEER 可以查看单指标算法参数。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params/fast_rate",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_no_token_401(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/configs/algorithm-params/oscillation_rate")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/v1/configs/algorithm-params/{metricCode} — 更新算法参数
# ---------------------------------------------------------------------------


class TestSaveMetricAlgorithmParams:
    """PUT /api/v1/configs/algorithm-params/{metricCode} tests."""

    def test_update_success_new_record(self, client, mock_db, fake_redis, reset_cache) -> None:
        """ADMIN 新建算法参数覆盖记录成功，缓存刷新后 overridden=True。"""
        # P2-02 后 PUT 调用序列（经 _make_put_execute_side_effect 组装）：
        # revision → before 快照 → items 存量查询 → after 快照 → 影响回路数
        # → commit → load_stored_config / load_metric_thresholds / revision
        saved_row = _make_param_row("oscillation_rate", "STABLE", {"similarity_threshold": 0.55})
        mock_db.execute = AsyncMock(
            side_effect=_make_put_execute_side_effect(
                row_results=[_make_scalar_none_result()],
                stored_rows=[saved_row],
            )
        )
        mock_db.add = MagicMock()

        body = {"items": [{"controlType": "STABLE", "params": {"similarity_threshold": 0.55}}]}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["metricCode"] == "oscillation_rate"
        stable = next(i for i in data["items"] if i["controlType"] == "STABLE")
        # 缓存已刷新，覆盖生效
        assert stable["overridden"] is True
        assert stable["params"]["similarity_threshold"] == 0.55
        # 新建记录 + 审计 + LEGACY_SYNC 发布账本 + 发布审计 = 4 次 db.add（P2-02）
        assert mock_db.add.call_count == 4
        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_not_awaited()

    def test_update_success_existing_record_merge(
        self, client, mock_db, fake_redis, reset_cache
    ) -> None:
        """已存在记录时部分覆盖合并（保留未传参数）。"""
        existing = _make_existing_param(
            "oscillation_rate",
            "STABLE",
            {"similarity_threshold": 0.55, "min_ratio": 0.05, "max_ratio": 15.0},
            version=1,
        )
        # 保存后 load_stored_config 返回合并后的参数
        merged_row = _make_param_row(
            "oscillation_rate",
            "STABLE",
            {"similarity_threshold": 0.55, "min_ratio": 0.08, "max_ratio": 15.0},
        )
        # 存量记录查询（before 快照 / items upsert / after 快照）返回已存在记录
        existing_result = _make_scalar_result(existing)
        mock_db.execute = AsyncMock(
            side_effect=_make_put_execute_side_effect(
                row_results=[existing_result],
                stored_rows=[merged_row],
            )
        )
        mock_db.add = MagicMock()

        # 仅覆盖 min_ratio，保留其他
        body = {"items": [{"controlType": "STABLE", "params": {"min_ratio": 0.08}}]}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        # 已存在记录：审计 + LEGACY_SYNC 发布账本 + 发布审计 = 3 次 add（P2-02）
        assert mock_db.add.call_count == 3
        # 验证已存在记录被合并更新
        assert existing.params["min_ratio"] == 0.08
        assert existing.params["similarity_threshold"] == 0.55  # 保留原值
        assert existing.version == 2  # version 递增
        mock_db.commit.assert_awaited_once()

    def test_update_multiple_control_types(self, client, mock_db, fake_redis, reset_cache) -> None:
        """同时更新多个控制类型的参数。"""
        saved_rows = [
            _make_param_row("fast_rate", "STABLE", {"settling_tolerance": 0.03}),
            _make_param_row("fast_rate", "FAST", {"settling_tolerance": 0.05}),
        ]
        mock_db.execute = AsyncMock(
            side_effect=_make_put_execute_side_effect(
                # touched sorted = [FAST, STABLE]，全部无存量记录（新建）
                row_results=[_make_scalar_none_result(), _make_scalar_none_result()],
                stored_rows=saved_rows,
            )
        )
        mock_db.add = MagicMock()

        body = {
            "items": [
                {"controlType": "STABLE", "params": {"settling_tolerance": 0.03}},
                {"controlType": "FAST", "params": {"settling_tolerance": 0.05}},
            ]
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/fast_rate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        stable = next(i for i in data["items"] if i["controlType"] == "STABLE")
        fast = next(i for i in data["items"] if i["controlType"] == "FAST")
        assert stable["overridden"] is True
        assert stable["params"]["settling_tolerance"] == 0.03
        assert fast["overridden"] is True
        assert fast["params"]["settling_tolerance"] == 0.05
        # 2 新建记录 + 审计 + LEGACY_SYNC 账本/审计 = 5 次 add（P2-02）
        assert mock_db.add.call_count == 5

    def test_update_empty_params_skipped(self, client, mock_db, fake_redis, reset_cache) -> None:
        """空 params 的控制类型被跳过（不新增记录；快照查询按 touched 全集）."""
        mock_db.execute = AsyncMock(
            side_effect=_make_put_execute_side_effect(
                # touched sorted = [SLOW, STABLE]（含空 params 的 STABLE）
                row_results=[_make_scalar_none_result(), _make_scalar_none_result()],
                stored_rows=[],
            )
        )
        mock_db.add = MagicMock()

        body = {
            "items": [
                {"controlType": "STABLE", "params": {}},
                {"controlType": "SLOW", "params": {"similarity_threshold": 0.5}},
            ]
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        # 仅 SLOW 新建 + 审计 + LEGACY_SYNC 账本/审计 = 4 次 add（P2-02）
        assert mock_db.add.call_count == 4

    def test_unknown_metric_404(self, client, mock_db, fake_redis, reset_cache) -> None:
        """未知指标代码返回 404（不查库）。"""
        mock_db.add = MagicMock()
        body = {"items": [{"controlType": "STABLE", "params": {"x": 1}}]}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/nonexistent_metric",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NOT_FOUND"
        mock_db.commit.assert_not_awaited()

    def test_commit_failure_rollback_500(self, client, mock_db, fake_redis, reset_cache) -> None:
        """事务提交失败时回滚，返回 500。"""
        mock_db.execute = AsyncMock(
            side_effect=_make_put_execute_side_effect(
                row_results=[_make_scalar_none_result()],
                stored_rows=None,  # 提交失败，不触发 commit 后缓存同步查询
            )
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))

        body = {"items": [{"controlType": "STABLE", "params": {"similarity_threshold": 0.6}}]}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 500
        assert resp.json()["code"] == "ERR_INTERNAL"
        mock_db.rollback.assert_awaited_once()

    def test_non_admin_forbidden_403(self, client, mock_db, fake_redis, reset_cache) -> None:
        """IC_ENGINEER 不能更新算法参数（403）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json={"items": [{"controlType": "STABLE", "params": {"x": 1}}]},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_pe_engineer_forbidden_403(self, client, mock_db, fake_redis, reset_cache) -> None:
        """PE_ENGINEER 不能更新算法参数（403）。"""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json={"items": [{"controlType": "STABLE", "params": {"x": 1}}]},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_reset_control_types_clears_overrides(
        self, client, mock_db, fake_redis, reset_cache
    ) -> None:
        """F6：resetControlTypes 将指定控制类型覆盖清空（params={} → 回落默认）。"""
        # P2-02 后调用序列：revision → before 快照（命中已覆盖记录）→ reset
        # 存量查询 → after 快照 → 影响回路数 → commit → 缓存同步三查询 →
        # 重置解释（metric_config.threshold + config_override 两查询）
        existing = _make_existing_param(
            "oscillation_rate", "STABLE", {"similarity_threshold": 0.55}
        )
        cleared_row = _make_param_row("oscillation_rate", "STABLE", {})
        existing_result = _make_scalar_result(existing)
        side_effect = _make_put_execute_side_effect(
            row_results=[existing_result],
            reset_results=[existing_result],
            stored_rows=[cleared_row],
        )
        # 重置解释：threshold 无覆盖（None）+ 无分层覆盖行（空）
        side_effect.append(_make_scalar_result(None))
        side_effect.append(_make_scalars_all_result([]))
        mock_db.execute = AsyncMock(side_effect=side_effect)

        body = {"items": [], "resetControlTypes": ["STABLE"]}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        # 覆盖被清空
        assert existing.params == {}
        data = resp.json()["data"]
        stable = next(i for i in data["items"] if i["controlType"] == "STABLE")
        assert stable["overridden"] is False
        assert stable["params"] == stable["defaults"]
        # paramMeta 同步下发
        assert "similarity_threshold" in data["paramMeta"]
        mock_db.commit.assert_awaited_once()

    def test_no_token_401(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.put(
            "/api/v1/configs/algorithm-params/oscillation_rate",
            json={"items": [{"controlType": "STABLE", "params": {"x": 1}}]},
        )
        assert resp.status_code == 401
