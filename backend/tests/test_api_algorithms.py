"""算法服务接口测试 (IDS v3.2 §2.7).

测试覆盖：
- POST /api/v1/algorithms/kpi/calculate       — 同步 KPI 计算
- POST /api/v1/algorithms/diagnosis/analyze    — 已退役（14 号文 A4，断言 404 守护退役状态）
- POST /api/v1/algorithms/tuning/calculate     — 同步整定计算
- GET  /api/v1/algorithms/tasks/{task_id}      — 算法任务状态查询

设计依据：IDS §2.7.1/§2.7.2/§2.7.3/§2.7.4
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

_LOOP_ID = "00000000-0000-0000-0000-000000000201"
_START = "2026-06-22T08:00:00Z"
_END = "2026-06-22T09:00:00Z"

_KPI_BODY = {
    "loopId": _LOOP_ID,
    "metric": "accuracy_rate",
    "startTime": _START,
    "endTime": _END,
    "forceRecalculate": False,
}

_DIAG_BODY = {
    "loopId": _LOOP_ID,
    "startTime": _START,
    "endTime": _END,
    "labels": ["OSCILLATION", "VALVE_STICTION"],
    "enableFusion": True,
}

_TUNING_BODY = {
    "loopId": _LOOP_ID,
    "identificationParams": {
        "dataSegment": {"startTime": _START, "endTime": _END},
        "samplePeriod": 1.0,
        "modelType": "FOPDT",
        "method": "TWO_POINT",
    },
    "tuningParams": {"method": "IMC", "params": {"lambda": 10.0}},
    "enableSimulation": True,
    "simulationConfig": {
        "disturbanceType": "step",
        "simulationDuration": 300.0,
    },
}


# ---------------------------------------------------------------------------
# Mock 工厂
# ---------------------------------------------------------------------------


def _make_bundle_mock(
    metric_code: str = "accuracy_rate",
    valid_rate: float = 0.95,
    tag_group: str = "BASE",
) -> MagicMock:
    """构造 MetricDataBundle mock."""
    bundle = MagicMock()
    bundle.metric_code = metric_code
    bundle.data_block = MagicMock(tag_group=tag_group, sampling_freq="1s")
    bundle.lineage = MagicMock(
        sampling_freq="1s",
        quality_policy="KEEP_ALL_WITH_VALIDITY",
        tag_group=tag_group,
        valid_rate=valid_rate,
    )
    return bundle


def _make_metric_result(
    value: float | None = 0.95,
    valid_rate: float = 0.95,
) -> MagicMock:
    """构造 MetricResult mock."""
    r = MagicMock()
    r.value = value
    r.valid_rate = valid_rate
    return r


def _make_planner_mock(bundles: list | None = None) -> MagicMock:
    """构造 DataPlanner mock."""
    planner = MagicMock()
    planner.request_bundles = AsyncMock(
        return_value=bundles if bundles is not None else [_make_bundle_mock()]
    )
    return planner


# ---------------------------------------------------------------------------
# POST /api/v1/algorithms/kpi/calculate
# ---------------------------------------------------------------------------


class TestKpiCalculate:
    """POST /api/v1/algorithms/kpi/calculate tests."""

    def test_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以同步计算单回路单指标."""
        calculator = MagicMock()
        calculator.calculate = MagicMock(
            return_value=_make_metric_result(value=0.92, valid_rate=0.95)
        )
        planner = _make_planner_mock([_make_bundle_mock(valid_rate=0.95)])

        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=calculator,
            ),
            patch(
                "app.api.v1.endpoints.dataplanner._build_data_planner",
                return_value=planner,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == _LOOP_ID
        assert data["metric"] == "accuracy_rate"
        assert data["value"] == 0.92
        # valid_rate=0.95 → 置信度 A
        assert data["confidenceLevel"] == "A"
        assert data["validRate"] == 0.95
        assert data["algorithmVersion"] == "KPI_CALC_v1.0"
        # 数据血缘（lineage_dict 使用 snake_case 键，非 CamelModel 转换）
        assert data["dataLineage"] is not None
        assert data["dataLineage"]["tag_group"] == "BASE"
        assert data["dataLineage"]["valid_rate"] == 0.95

    def test_confidence_level_b(self, client, mock_db, fake_redis) -> None:
        """valid_rate=0.92 → 置信度 B."""
        calculator = MagicMock()
        calculator.calculate = MagicMock(
            return_value=_make_metric_result(value=0.88, valid_rate=0.92)
        )
        planner = _make_planner_mock([_make_bundle_mock(valid_rate=0.92)])

        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=calculator,
            ),
            patch(
                "app.api.v1.endpoints.dataplanner._build_data_planner",
                return_value=planner,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["confidenceLevel"] == "B"

    def test_confidence_level_e(self, client, mock_db, fake_redis) -> None:
        """valid_rate=0.5 → 置信度 E."""
        calculator = MagicMock()
        calculator.calculate = MagicMock(
            return_value=_make_metric_result(value=0.5, valid_rate=0.5)
        )
        planner = _make_planner_mock([_make_bundle_mock(valid_rate=0.5)])

        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=calculator,
            ),
            patch(
                "app.api.v1.endpoints.dataplanner._build_data_planner",
                return_value=planner,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["confidenceLevel"] == "E"

    def test_unknown_metric(self, client, mock_db, fake_redis) -> None:
        """未知指标代码返回 400 ERR_ALGORITHM_INVALID_PARAMS."""
        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=None,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json={**_KPI_BODY, "metric": "unknown_metric"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_ALGORITHM_INVALID_PARAMS"

    def test_invalid_time_format(self, client, mock_db, fake_redis) -> None:
        """无效时间格式返回 400."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json={**_KPI_BODY, "startTime": "not-a-time"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_ALGORITHM_INVALID_PARAMS"

    def test_invalid_time_window(self, client, mock_db, fake_redis) -> None:
        """起始时间不早于结束时间返回 400."""
        body = {**_KPI_BODY, "startTime": _END, "endTime": _START}
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_ALGORITHM_INVALID_PARAMS"

    def test_no_bundles(self, client, mock_db, fake_redis) -> None:
        """DataPlanner 返回空 Bundle 列表时返回 INCONCLUSIVE 结果."""
        calculator = MagicMock()
        planner = _make_planner_mock([])

        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=calculator,
            ),
            patch(
                "app.api.v1.endpoints.dataplanner._build_data_planner",
                return_value=planner,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["value"] is None
        assert data["confidenceLevel"] == "E"
        assert data["validRate"] == 0.0

    def test_dataplanner_exception(self, client, mock_db, fake_redis) -> None:
        """DataPlanner 取数异常返回 422 ERR_ALGORITHM_DATA_INSUFFICIENT."""
        calculator = MagicMock()
        planner = MagicMock()
        planner.request_bundles = AsyncMock(side_effect=RuntimeError("TDengine down"))

        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=calculator,
            ),
            patch(
                "app.api.v1.endpoints.dataplanner._build_data_planner",
                return_value=planner,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_ALGORITHM_DATA_INSUFFICIENT"

    def test_calculator_exception(self, client, mock_db, fake_redis) -> None:
        """指标计算异常返回 500."""
        calculator = MagicMock()
        calculator.calculate = MagicMock(side_effect=RuntimeError("calc boom"))
        planner = _make_planner_mock([_make_bundle_mock()])

        with (
            patch(
                "app.services.metric_calculator.get_calculator",
                return_value=calculator,
            ),
            patch(
                "app.api.v1.endpoints.dataplanner._build_data_planner",
                return_value=planner,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 500
        assert resp.json()["code"] == "ERR_ALGORITHM_INVALID_PARAMS"

    def test_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能调用 KPI 计算（403）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/algorithms/kpi/calculate",
                json=_KPI_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/algorithms/kpi/calculate", json=_KPI_BODY)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/algorithms/diagnosis/analyze — 已退役（14 号文 A4）
# ---------------------------------------------------------------------------


class TestDiagnosisAnalyzeRetired:
    """POST /api/v1/algorithms/diagnosis/analyze 已于 2026-08-27 退役.

    旧诊断引擎唯一活跃写入口，路由已解除注册（endpoints/algorithms.py
    归档注释），替代入口为 POST /diagnosis/run（诊断 v2）。
    详见 docs/MVP设计/14-诊断引擎统一方案.md §4 阶段 A4。
    """

    def test_endpoint_retired_returns_404(self, client, mock_db, fake_redis) -> None:
        """端点已解除注册，认证后请求返回 404（守护退役状态防误恢复）."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/algorithms/diagnosis/analyze",
                json=_DIAG_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_endpoint_retired_no_token_returns_404(self, client) -> None:
        """未认证请求同样 404（路由未注册，不进入认证依赖）."""
        resp = client.post("/api/v1/algorithms/diagnosis/analyze", json=_DIAG_BODY)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/algorithms/tuning/calculate
# ---------------------------------------------------------------------------


class TestTuningCalculate:
    """POST /api/v1/algorithms/tuning/calculate tests."""

    def test_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以同步计算 PID 整定参数."""
        identify_result = {
            "modelType": "FOPDT",
            "params": {"K": 1.2, "tau": 30.0, "theta": 5.0},
            "fittingScore": 0.95,
            "stepValidationPassed": True,
        }
        tune_result = {
            "recommended_pid": {"Kp": 1.5, "Ti": 25.0, "Td": 5.0},
            "current_pid": {"Kp": 1.0, "Ti": 20.0, "Td": 0.0},
            "algorithmVersion": "TUNE_ENGINE_v1.0",
        }
        sim_result = {
            "metrics": {
                "riseTime": 12.5,
                "overshoot": 0.1,
                "settlingTime": 60.0,
                "itae": 150.0,
            }
        }

        with (
            patch(
                "app.services.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(return_value=tune_result),
            ),
            patch(
                "app.services.tuning.run_simulation",
                AsyncMock(return_value=sim_result),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=_TUNING_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == _LOOP_ID
        assert data["modelType"] == "FOPDT"
        assert data["modelParams"]["K"] == 1.2
        assert data["modelParams"]["tau"] == 30.0
        assert data["fittingScore"] == 0.95
        assert data["pidParams"]["Kp"] == 1.5
        assert data["pidParams"]["Ti"] == 25.0
        assert data["pidParams"]["Td"] == 5.0
        assert data["simulationResult"]["riseTime"] == 12.5
        assert data["simulationResult"]["overshoot"] == 0.1
        assert data["algorithmVersion"] == "TUNE_ENGINE_v1.0"

    def test_identify_failure(self, client, mock_db, fake_redis) -> None:
        """模型辨识失败返回 422 ERR_ALGORITHM_DATA_INSUFFICIENT."""
        with (
            patch(
                "app.services.tuning.identify_model",
                AsyncMock(side_effect=RuntimeError("identify failed")),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=_TUNING_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_ALGORITHM_DATA_INSUFFICIENT"

    def test_tune_failure(self, client, mock_db, fake_redis) -> None:
        """PID 整定失败返回 500 ERR_ALGORITHM_INVALID_PARAMS."""
        identify_result = {
            "modelType": "FOPDT",
            "params": {"K": 1.0},
            "fittingScore": 0.9,
            "stepValidationPassed": True,
        }
        with (
            patch(
                "app.services.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(side_effect=RuntimeError("tune failed")),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=_TUNING_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 500
        assert resp.json()["code"] == "ERR_ALGORITHM_INVALID_PARAMS"

    def test_simulation_failure_does_not_block(self, client, mock_db, fake_redis) -> None:
        """仿真失败不阻断整定计算，simulationResult 为 null."""
        identify_result = {
            "modelType": "FOPDT",
            "params": {"K": 1.0, "tau": 10.0, "theta": 2.0},
            "fittingScore": 0.9,
            "stepValidationPassed": True,
        }
        tune_result = {
            "recommended_pid": {"Kp": 1.0, "Ti": 10.0, "Td": 0.0},
            "current_pid": {"Kp": 0.5, "Ti": 5.0, "Td": 0.0},
            "algorithmVersion": "TUNE_ENGINE_v1.0",
        }

        with (
            patch(
                "app.services.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(return_value=tune_result),
            ),
            patch(
                "app.services.tuning.run_simulation",
                AsyncMock(side_effect=RuntimeError("sim failed")),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=_TUNING_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["pidParams"]["Kp"] == 1.0
        assert data["simulationResult"] is None

    def test_disable_simulation(self, client, mock_db, fake_redis) -> None:
        """enableSimulation=False 时不调用 run_simulation."""
        identify_result = {
            "modelType": "FOPDT",
            "params": {"K": 1.0, "tau": 10.0},
            "fittingScore": 0.9,
            "stepValidationPassed": True,
        }
        tune_result = {
            "recommended_pid": {"Kp": 1.0, "Ti": 10.0, "Td": 0.0},
            "current_pid": {},
            "algorithmVersion": "TUNE_ENGINE_v1.0",
        }
        body = {**_TUNING_BODY, "enableSimulation": False}

        with (
            patch(
                "app.services.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(return_value=tune_result),
            ),
            patch(
                "app.services.tuning.run_simulation",
                AsyncMock(),
            ) as sim_mock,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["simulationResult"] is None
        sim_mock.assert_not_called()

    def test_ic_engineer_allowed(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 角色允许调用整定计算."""
        identify_result = {
            "modelType": "FOPDT",
            "params": {},
            "fittingScore": 0.9,
            "stepValidationPassed": True,
        }
        tune_result = {
            "recommended_pid": {},
            "current_pid": {},
            "algorithmVersion": "v1",
        }

        with (
            patch(
                "app.services.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.services.tuning.tune_pid",
                AsyncMock(return_value=tune_result),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json={**_TUNING_BODY, "enableSimulation": False},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能调用整定计算（403）."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=_TUNING_BODY,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_invalid_time_window(self, client, mock_db, fake_redis) -> None:
        """辨识数据段时间窗非法返回 400."""
        body = {
            **_TUNING_BODY,
            "identificationParams": {
                **_TUNING_BODY["identificationParams"],
                "dataSegment": {"startTime": _END, "endTime": _START},
            },
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/algorithms/tuning/calculate",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_ALGORITHM_INVALID_PARAMS"

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/algorithms/tuning/calculate", json=_TUNING_BODY)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/algorithms/tasks/{task_id}
# ---------------------------------------------------------------------------


def _make_async_result(
    state: str = "PENDING",
    result: object = None,
    info: object = None,
) -> MagicMock:
    """构造 Celery AsyncResult mock."""
    ar = MagicMock()
    ar.state = state
    ar.result = result
    ar.info = info
    return ar


class TestAlgorithmTaskStatus:
    """GET /api/v1/algorithms/tasks/{task_id} tests."""

    def test_pending(self, client, mock_db, fake_redis) -> None:
        """查询 PENDING 状态任务."""
        ar = _make_async_result(state="PENDING", info=None)
        with (
            patch("celery.result.AsyncResult", return_value=ar),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/tasks/task-pending",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["taskId"] == "task-pending"
        assert data["status"] == "PENDING"
        assert data["progress"] is None
        assert data["result"] is None
        assert data["error"] is None

    def test_success(self, client, mock_db, fake_redis) -> None:
        """查询 SUCCESS 状态任务."""
        ar = _make_async_result(
            state="SUCCESS",
            result={"loopId": _LOOP_ID, "score": 85.0},
            info={"progress": 1.0, "received_at": "2026-06-22T08:00:00Z"},
        )
        with (
            patch("celery.result.AsyncResult", return_value=ar),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/tasks/task-success",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "SUCCESS"
        assert data["progress"] == 1.0
        assert data["result"]["score"] == 85.0
        assert data["receivedAt"] == "2026-06-22T08:00:00Z"

    def test_failure(self, client, mock_db, fake_redis) -> None:
        """查询 FAILURE 状态任务."""
        ar = _make_async_result(
            state="FAILURE",
            result=ValueError("task boom"),
        )
        with (
            patch("celery.result.AsyncResult", return_value=ar),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/tasks/task-failure",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "FAILURE"
        assert "boom" in data["error"]

    def test_revoked(self, client, mock_db, fake_redis) -> None:
        """查询 REVOKED 状态任务."""
        ar = _make_async_result(state="REVOKED", result="REVOKED")
        with (
            patch("celery.result.AsyncResult", return_value=ar),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/tasks/task-revoked",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "REVOKED"

    def test_ic_engineer_allowed(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 允许查询任务状态."""
        ar = _make_async_result(state="PENDING")
        with (
            patch("celery.result.AsyncResult", return_value=ar),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.get(
                "/api/v1/algorithms/tasks/task-001",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能查询任务状态（403）."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/algorithms/tasks/task-002",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/algorithms/tasks/task-003")
        assert resp.status_code == 401
