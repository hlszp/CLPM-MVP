"""批量配置接口测试 (IDS v3.2 §2.8/§2.9).

测试覆盖：
- GET /api/v1/configs/metrics     — 批量获取指标配置（3+1+8 三段式）
- PUT /api/v1/configs/metrics     — 批量更新指标配置（事务性 + 权重校验）
- GET /api/v1/configs/diagnosis   — 批量获取诊断配置（8 类标签）
- PUT /api/v1/configs/diagnosis   — 批量更新诊断配置（事务性）

设计依据：IDS §2.8.1/§2.8.2/§2.9.1/§2.9.2
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# Mock 工厂
# ---------------------------------------------------------------------------


def _make_metric_config(
    metric_id: str = "m-1",
    metric_code: str = "accuracy_rate",
    metric_name: str = "准确率",
    weight: float | None = 40.0,
    is_enabled: bool = True,
    control_type: str = "STABLE",
    formula: str | None = None,
    threshold: dict | None = None,
    updated_by: str = "admin",
    updated_at=None,
    version: int = 1,
) -> MagicMock:
    """构造 MetricConfig ORM mock."""
    c = MagicMock()
    c.id = metric_id
    c.metric_code = metric_code
    c.metric_name = metric_name
    c.weight = weight
    c.is_enabled = is_enabled
    c.control_type = control_type
    c.formula = formula
    c.threshold = threshold
    c.updated_by = updated_by
    c.updated_at = updated_at
    c.version = version
    return c


def _make_diagnosis_config(
    diag_id: str = "d-1",
    diag_code: str = "OSCILLATION",
    diag_name: str = "振荡",
    algorithm_type: str = "FFT",
    calc_method: str | None = "zero_crossing",
    params: dict | None = None,
    threshold: dict | None = None,
    is_enabled: bool = True,
    updated_by: str = "admin",
    updated_at=None,
    version: int = 1,
) -> MagicMock:
    """构造 DiagnosisConfig ORM mock."""
    c = MagicMock()
    c.id = diag_id
    c.diag_code = diag_code
    c.diag_name = diag_name
    c.algorithm_type = algorithm_type
    c.calc_method = calc_method
    c.params = params
    c.threshold = threshold
    c.is_enabled = is_enabled
    c.updated_by = updated_by
    c.updated_at = updated_at
    c.version = version
    return c


def _build_full_metric_set() -> list[MagicMock]:
    """构造 3+1+8 完整指标配置集合（12 项）."""
    return [
        # 3 核心
        _make_metric_config("m-1", "accuracy_rate", "准确率", weight=40.0),
        _make_metric_config("m-2", "fast_rate", "快速率", weight=30.0),
        _make_metric_config("m-3", "steady_rate", "稳定率", weight=30.0),
        # 1 投用（折扣因子）
        _make_metric_config("m-4", "effective_auto_rate", "有效自控率", weight=None),
        # 8 辅助诊断
        _make_metric_config("m-5", "good_value_rate", "好值率", weight=None),
        _make_metric_config("m-6", "oscillation_rate", "振荡率", weight=None),
        _make_metric_config("m-7", "saturation_rate", "饱和率", weight=None),
        _make_metric_config("m-8", "stiction_index", "粘滞指数", weight=None),
        _make_metric_config("m-9", "overaggressive_index", "过激指数", weight=None),
        _make_metric_config("m-10", "overconservative_index", "过保守指数", weight=None),
        _make_metric_config("m-11", "disturbance_index", "外扰指数", weight=None),
        _make_metric_config("m-12", "quality_abnormal_rate", "质量异常率", weight=None),
    ]


def _build_full_diagnosis_set() -> list[MagicMock]:
    """构造 8 类诊断标签配置集合."""
    labels = [
        ("OSCILLATION", "振荡", "FFT", "zero_crossing"),
        ("VALVE_STICTION", "阀门粘滞", "STICTION_CH", "pv_op_scatter"),
        ("OVERAGGRESSIVE", "过激", "PID_TUNING", "pid_param_check"),
        ("OVERCONSERVATIVE", "过保守", "PID_TUNING", "pid_param_check"),
        ("EXTERNAL_DISTURBANCE", "外部扰动", "DISTURBANCE", "spectral"),
        ("QUALITY_ABNORMAL", "质量异常", "QUALITY_CHECK", "qc_stats"),
        ("OUTPUT_SATURATION", "输出饱和", "SATURATION", "op_range"),
        ("MANUAL_REVIEW", "人工复核", "MANUAL", "manual"),
    ]
    return [
        _make_diagnosis_config(
            f"d-{i + 1}",
            diag_code=code,
            diag_name=name,
            algorithm_type=algo,
            calc_method=method,
        )
        for i, (code, name, algo, method) in enumerate(labels)
    ]


def _make_execute_return(items: list) -> MagicMock:
    """构造 db.execute() 返回值，scalars().all() 返回 items."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/configs/metrics
# ---------------------------------------------------------------------------


class TestGetMetricConfigs:
    """GET /api/v1/configs/metrics tests."""

    def test_success(self, client, mock_db, fake_redis) -> None:
        """批量获取指标配置返回 3+1+8 三段式结构."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return(_build_full_metric_set()))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/metrics",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        # 3 核心
        assert len(data["coreMetrics"]) == 3
        assert data["coreMetrics"][0]["metricKey"] == "accuracy_rate"
        assert data["coreMetrics"][0]["category"] == "CORE"
        assert data["coreMetrics"][0]["isDiscountFactor"] is None
        # 1 投用
        assert data["commissioningMetric"] is not None
        assert data["commissioningMetric"]["metricKey"] == "effective_auto_rate"
        assert data["commissioningMetric"]["category"] == "COMMISSIONING"
        assert data["commissioningMetric"]["isDiscountFactor"] is True
        # 8 辅助诊断
        assert len(data["auxiliaryDiagnosticMetrics"]) == 8
        assert data["auxiliaryDiagnosticMetrics"][0]["category"] == "AUXILIARY_DIAGNOSTIC"
        # 权重总和与校验状态
        assert data["coreTotalWeight"] == 100.0
        assert data["coreWeightValid"] is True
        assert data["structureVersion"] == "3+1+8"

    def test_weight_invalid(self, client, mock_db, fake_redis) -> None:
        """核心权重总和不等于 100 时 coreWeightValid=False."""
        configs = _build_full_metric_set()
        # 修改核心权重：40 + 30 + 20 = 90
        configs[0].weight = 40.0
        configs[1].weight = 30.0
        configs[2].weight = 20.0
        mock_db.execute = AsyncMock(return_value=_make_execute_return(configs))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/metrics",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["coreTotalWeight"] == 90.0
        assert data["coreWeightValid"] is False

    def test_empty_db(self, client, mock_db, fake_redis) -> None:
        """空数据库返回空三段式结构."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/metrics",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["coreMetrics"] == []
        assert data["commissioningMetric"] is None
        assert data["auxiliaryDiagnosticMetrics"] == []
        assert data["coreTotalWeight"] == 0.0
        assert data["coreWeightValid"] is True

    def test_ic_engineer_allowed(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 可以查看指标配置（只读权限）."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return(_build_full_metric_set()))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/configs/metrics",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/configs/metrics")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/v1/configs/metrics
# ---------------------------------------------------------------------------


class TestUpdateMetricConfigs:
    """PUT /api/v1/configs/metrics tests."""

    def test_update_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 批量更新核心指标权重（总和=100）成功."""
        full_set = _build_full_metric_set()
        # 第一次 execute：按 ID 查询（返回被更新的 3 个核心指标）
        # 第二次 execute：查询全部（返回完整 12 项）
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_execute_return(full_set[:3]),
                _make_execute_return(full_set),
            ]
        )
        mock_db.add = MagicMock()  # db.add 是同步方法

        body = {
            "coreMetrics": [
                {"metricId": "m-1", "weight": 50.0},
                {"metricId": "m-2", "weight": 25.0},
                {"metricId": "m-3", "weight": 25.0},
            ],
        }
        with (
            patch(
                "app.services.performance._invalidate_metric_config_cache",
                AsyncMock(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["coreWeightValid"] is True
        assert data["coreTotalWeight"] == 100.0
        assert data["updatedCount"] == 3
        # 验证事务提交
        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_not_awaited()

    def test_update_with_auxiliary(self, client, mock_db, fake_redis) -> None:
        """更新核心 + 辅助诊断指标（权重仅核心参与校验）."""
        full_set = _build_full_metric_set()
        # 第一次返回核心 3 + 辅助 1，第二次返回全部
        updated_items = full_set[:3] + [full_set[4]]  # accuracy/fast/steady + good_value
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_execute_return(updated_items),
                _make_execute_return(full_set),
            ]
        )
        mock_db.add = MagicMock()

        body = {
            "coreMetrics": [
                {"metricId": "m-1", "weight": 40.0},
                {"metricId": "m-2", "weight": 30.0},
                {"metricId": "m-3", "weight": 30.0},
            ],
            "auxiliaryDiagnosticMetrics": [
                {"metricId": "m-5", "isEnabled": False},
            ],
        }
        with (
            patch(
                "app.services.performance._invalidate_metric_config_cache",
                AsyncMock(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["updatedCount"] == 4
        assert data["coreWeightValid"] is True

    def test_update_commissioning(self, client, mock_db, fake_redis) -> None:
        """更新投用指标（折扣因子，weight 不参与校验）."""
        full_set = _build_full_metric_set()
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_execute_return([full_set[3]]),  # effective_auto_rate
                _make_execute_return(full_set),
            ]
        )
        mock_db.add = MagicMock()

        body = {
            "commissioningMetric": {"metricId": "m-4", "isEnabled": False},
        }
        with (
            patch(
                "app.services.performance._invalidate_metric_config_cache",
                AsyncMock(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["updatedCount"] == 1

    def test_weight_sum_invalid_rollback(self, client, mock_db, fake_redis) -> None:
        """核心权重总和≠100 时事务回滚，返回 ERR_METRIC_WEIGHT_SUM."""
        _build_full_metric_set()
        # 返回 2 个核心指标（权重 50+60=110）
        updated = [
            _make_metric_config("m-1", "accuracy_rate", "准确率", weight=40.0),
            _make_metric_config("m-2", "fast_rate", "快速率", weight=30.0),
        ]
        mock_db.execute = AsyncMock(return_value=_make_execute_return(updated))
        mock_db.add = MagicMock()

        body = {
            "coreMetrics": [
                {"metricId": "m-1", "weight": 50.0},
                {"metricId": "m-2", "weight": 60.0},
            ],
        }
        with (
            patch(
                "app.services.performance._invalidate_metric_config_cache",
                AsyncMock(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_METRIC_WEIGHT_SUM"
        # 验证回滚
        mock_db.rollback.assert_awaited_once()
        mock_db.commit.assert_not_awaited()

    def test_empty_update_list(self, client, mock_db, fake_redis) -> None:
        """空更新列表返回 400 ERR_VALIDATION."""
        mock_db.add = MagicMock()
        body = {"coreMetrics": [], "auxiliaryDiagnosticMetrics": []}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_metric_not_found(self, client, mock_db, fake_redis) -> None:
        """metricId 不存在返回 404 ERR_METRIC_NOT_FOUND."""
        mock_db.execute = AsyncMock(
            return_value=_make_execute_return([])  # 空列表，模拟 ID 不存在
        )
        mock_db.add = MagicMock()
        body = {
            "coreMetrics": [
                {"metricId": "nonexistent-id", "weight": 50.0},
            ],
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_METRIC_NOT_FOUND"

    def test_commit_failure_rollback(self, client, mock_db, fake_redis) -> None:
        """事务提交失败时回滚，返回 500 ERR_INTERNAL."""
        full_set = _build_full_metric_set()
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_execute_return(full_set[:3]),
                _make_execute_return(full_set),
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))

        body = {
            "coreMetrics": [
                {"metricId": "m-1", "weight": 40.0},
                {"metricId": "m-2", "weight": 30.0},
                {"metricId": "m-3", "weight": 30.0},
            ],
        }
        with (
            patch(
                "app.services.performance._invalidate_metric_config_cache",
                AsyncMock(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/configs/metrics",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 500
        assert resp.json()["code"] == "ERR_INTERNAL"
        mock_db.rollback.assert_awaited_once()

    def test_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能更新指标配置（403）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/configs/metrics",
                json={"coreMetrics": [{"metricId": "m-1", "weight": 50.0}]},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.put(
            "/api/v1/configs/metrics",
            json={"coreMetrics": [{"metricId": "m-1", "weight": 50.0}]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/configs/diagnosis
# ---------------------------------------------------------------------------


class TestGetDiagnosisConfigs:
    """GET /api/v1/configs/diagnosis tests."""

    def test_success(self, client, mock_db, fake_redis) -> None:
        """批量获取诊断配置返回 8 类标签."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return(_build_full_diagnosis_set()))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/diagnosis",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert len(data["items"]) == 8
        assert data["items"][0]["diagKey"] == "OSCILLATION"
        assert data["items"][0]["label"] == "OSCILLATION"
        assert data["items"][0]["algorithmType"] == "FFT"

    def test_empty_db(self, client, mock_db, fake_redis) -> None:
        """空数据库返回空列表."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/diagnosis",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []

    def test_ic_engineer_allowed(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 可以查看诊断配置."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return(_build_full_diagnosis_set()))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/configs/diagnosis",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/configs/diagnosis")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/v1/configs/diagnosis
# ---------------------------------------------------------------------------


class TestUpdateDiagnosisConfigs:
    """PUT /api/v1/configs/diagnosis tests."""

    def test_update_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 批量更新诊断配置成功."""
        full_set = _build_full_diagnosis_set()
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_execute_return(full_set[:2]),  # 按 ID 查询
                _make_execute_return(full_set),  # 重新查询全部
            ]
        )
        mock_db.add = MagicMock()

        body = {
            "items": [
                {"diagId": "d-1", "isEnabled": False},
                {"diagId": "d-2", "threshold": {"min": 0.1, "max": 0.9}},
            ],
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/diagnosis",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["updatedCount"] == 2
        assert len(data["items"]) == 8
        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_not_awaited()

    def test_empty_items(self, client, mock_db, fake_redis) -> None:
        """空更新列表返回 400 ERR_VALIDATION."""
        mock_db.add = MagicMock()
        body = {"items": []}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/diagnosis",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_diag_not_found(self, client, mock_db, fake_redis) -> None:
        """diagId 不存在返回 404 ERR_DIAG_CONFIG_NOT_FOUND."""
        mock_db.execute = AsyncMock(return_value=_make_execute_return([]))
        mock_db.add = MagicMock()
        body = {
            "items": [
                {"diagId": "nonexistent-id", "isEnabled": False},
            ],
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/diagnosis",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_CONFIG_NOT_FOUND"

    def test_commit_failure_rollback(self, client, mock_db, fake_redis) -> None:
        """事务提交失败时回滚，返回 500 ERR_INTERNAL."""
        full_set = _build_full_diagnosis_set()
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_execute_return(full_set[:1]),
                _make_execute_return(full_set),
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock(side_effect=RuntimeError("commit failed"))

        body = {
            "items": [
                {"diagId": "d-1", "isEnabled": False},
            ],
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/diagnosis",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 500
        assert resp.json()["code"] == "ERR_INTERNAL"
        mock_db.rollback.assert_awaited_once()

    def test_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能更新诊断配置（403）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/configs/diagnosis",
                json={"items": [{"diagId": "d-1", "isEnabled": False}]},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.put(
            "/api/v1/configs/diagnosis",
            json={"items": [{"diagId": "d-1", "isEnabled": False}]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# P2 #30 B7: /configs/loop-type-weights 与 /configs/loop-level-weights 路由可达性
# ---------------------------------------------------------------------------


class TestLoopTypeWeightRoutesReachable:
    """P2 #30 B7: 验证 /configs/loop-type-weights 路由前缀已生效。

    旧路径 /config/loop-type-weights（单数）应返回 404，
    新路径 /configs/loop-type-weights（复数）应可访问。
    """

    def test_new_path_list_reachable(self, client, mock_db, fake_redis) -> None:
        """新复数路径 GET /configs/loop-type-weights 返回 200。"""
        # mock list_loop_type_weights 返回空列表（路由可达即可）
        mock_db.execute = AsyncMock(return_value=_make_execute_return([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/loop-type-weights",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"] == []

    def test_old_path_list_not_found(self, client, mock_db, fake_redis) -> None:
        """旧单数路径 GET /config/loop-type-weights 已不存在（404）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/config/loop-type-weights",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404


class TestLoopLevelWeightRoutesReachable:
    """P2 #30 B7: 验证 /configs/loop-level-weights 路由前缀已生效。"""

    def test_new_path_list_reachable(self, client, mock_db, fake_redis) -> None:
        """新复数路径 GET /configs/loop-level-weights 返回 200。"""
        mock_db.execute = AsyncMock(return_value=_make_execute_return([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/loop-level-weights",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"] == []

    def test_old_path_list_not_found(self, client, mock_db, fake_redis) -> None:
        """旧单数路径 GET /config/loop-level-weights 已不存在（404）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/config/loop-level-weights",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
