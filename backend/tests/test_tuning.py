"""S7 回路整定模块测试 — 模型辨识 + PID 整定 + 闭环仿真 + API."""

from __future__ import annotations

import math
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.services.tuning import _estimate_mv_step
from app.services.tuning_algorithms import (
    PIDParams,
    identify_fopdt,
    identify_ipdt,
    identify_sopdt,
    simulate_closed_loop,
    tune_cohen_coon,
    tune_imc,
    tune_lambda,
    tune_simc,
    tune_zn,
)
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _generate_fopdt_step_response(
    K: float, tau: float, theta: float, mv_step: float, duration: float = 300, dt: float = 1.0
) -> tuple[list[float], list[float]]:
    """生成 FOPDT 阶跃响应仿真数据。"""
    pv_values = []
    timestamps = []
    n = int(duration / dt)
    for i in range(n):
        t = i * dt
        timestamps.append(t)
        if t < theta:
            pv_values.append(0.0)
        else:
            pv_values.append(K * mv_step * (1.0 - math.exp(-(t - theta) / tau)))
    return pv_values, timestamps


def _make_loop_mock(
    loop_id: str = "00000000-0000-0000-0000-0000000000a1",
    tag_name: str = "TIC-101",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "测试回路"
    loop.status = "READY"
    return loop


def _make_scalar_one_or_none_mock(item) -> MagicMock:
    """构造返回单行或 None 的 mock。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=item)
    result.first = MagicMock(return_value=(item, "TIC-101") if item else None)
    return result


# ---------------------------------------------------------------------------
# 模型辨识算法测试
# ---------------------------------------------------------------------------


class TestFOPDTIdentification:
    """FOPDT 模型辨识测试。"""

    def test_fopdt_known_params(self):
        """已知参数 FOPDT 模型辨识，误差应 < 15%。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=300
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        assert result["K"] is not None
        assert result["tau"] is not None
        assert result["theta"] is not None
        assert result["fitting_score"] > 90.0

        # 误差 < 15%
        assert abs(result["K"] - K_true) / K_true < 0.15
        assert abs(result["tau"] - tau_true) / tau_true < 0.2

    def test_fopdt_two_point_method(self):
        """两点法辨识。"""
        K_true, tau_true, theta_true = 2.0, 50.0, 10.0
        mv_step = 5.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=500
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        assert result["K"] is not None
        assert result["fitting_score"] > 80.0

    def test_fopdt_area_method(self):
        """面积法辨识。"""
        K_true, tau_true, theta_true = 0.5, 60.0, 8.0
        mv_step = 20.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=500
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="AREA")

        assert result["K"] is not None
        assert result["fitting_score"] > 80.0

    def test_fopdt_insufficient_data(self):
        """数据不足时返回零值。"""
        result = identify_fopdt([1.0, 2.0], [0.0, 1.0], 10.0)
        assert result["K"] is None
        assert result["fitting_score"] == 0.0

    def test_fopdt_zero_mv_step(self):
        """MV 阶跃为零时返回零值。"""
        pv_values = [float(i) for i in range(50)]
        timestamps = [float(i) for i in range(50)]
        result = identify_fopdt(pv_values, timestamps, 0.0)
        assert result["K"] is None


class TestSOPDTIdentification:
    """SOPDT 模型辨识测试。"""

    def test_sopdt_identification(self):
        """SOPDT 辨识应返回合理参数。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=300
        )

        result = identify_sopdt(pv_values, timestamps, mv_step)

        assert result["K"] is not None
        assert result["T1"] is not None
        assert result["T2"] is not None
        assert result["fitting_score"] > 70.0

    def test_sopdt_insufficient_data(self):
        """数据不足时返回零值。"""
        result = identify_sopdt([1.0, 2.0], [0.0, 1.0], 10.0)
        assert result["K"] is None


class TestIPDTIdentification:
    """IPDT 模型辨识测试。"""

    def test_ipdt_identification(self):
        """IPDT 辨识：积分过程 PV 线性增长。"""
        K_true, theta_true = 0.1, 5.0
        mv_step = 10.0
        pv_values = []
        timestamps = []
        for i in range(200):
            t = float(i)
            timestamps.append(t)
            if t < theta_true:
                pv_values.append(0.0)
            else:
                pv_values.append(K_true * mv_step * (t - theta_true))

        result = identify_ipdt(pv_values, timestamps, mv_step)

        assert result["K"] is not None
        assert result["theta"] is not None
        assert result["fitting_score"] > 90.0
        assert abs(result["K"] - K_true) / K_true < 0.1


# ---------------------------------------------------------------------------
# PID 整定算法测试
# ---------------------------------------------------------------------------


class TestPIDTuning:
    """PID 整定算法测试。"""

    def test_imc_tuning(self):
        """IMC 整定公式验证（Morari & Zafiriou Padé-based IMC）。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_imc(K, tau, theta, lambda_ratio=1.0)

        lam = 5.0
        # 正确公式：Kp = (τ + θ/2) / (K · (λ + θ/2))
        expected_kp = (tau + theta / 2.0) / (K * (lam + theta / 2.0))
        expected_ti = tau + theta / 2.0
        expected_td = (tau * theta) / (2.0 * (tau + theta / 2.0))

        assert abs(pid.kp - expected_kp) < 0.001
        assert abs(pid.ti - expected_ti) < 0.001
        assert abs(pid.td - expected_td) < 0.001

    def test_lambda_tuning(self):
        """Lambda 整定公式验证。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_lambda(K, tau, theta, lambda_ratio=1.0)

        lam = 30.0
        expected_kc = tau / (K * (lam + theta))
        expected_ti = tau

        assert abs(pid.kp - expected_kc) < 0.001
        assert abs(pid.ti - expected_ti) < 0.001
        assert pid.td == 0.0

    def test_zn_tuning_pid(self):
        """Z-N 开环法 PID 整定。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_zn(K, tau, theta, controller_type="PID")

        R = K / tau
        expected_kp = 1.2 / (R * theta)
        expected_ti = 2.0 * theta
        expected_td = 0.5 * theta

        assert abs(pid.kp - expected_kp) < 0.001
        assert abs(pid.ti - expected_ti) < 0.001
        assert abs(pid.td - expected_td) < 0.001

    def test_zn_tuning_pi(self):
        """Z-N 开环法 PI 整定。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_zn(K, tau, theta, controller_type="PI")

        R = K / tau
        expected_kp = 0.9 / (R * theta)
        expected_ti = theta / 0.3

        assert abs(pid.kp - expected_kp) < 0.001
        assert abs(pid.ti - expected_ti) < 0.001
        assert pid.td == 0.0

    def test_cohen_coon_tuning(self):
        """Cohen-Coon PID 整定。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_cohen_coon(K, tau, theta, controller_type="PID")

        ratio = theta / tau
        expected_kp = (1.0 / K) * (tau / theta) * (1.35 + ratio / 3.0)
        expected_ti = theta * (32.0 + 6.0 * ratio) / (13.0 + 8.0 * ratio)
        expected_td = theta * 4.0 / (11.0 + 2.0 * ratio)

        assert abs(pid.kp - expected_kp) < 0.001
        assert abs(pid.ti - expected_ti) < 0.001
        assert abs(pid.td - expected_td) < 0.001

    def test_simc_tuning(self):
        """SIMC PID 整定（Skogestad 2001 — FOPDT 使用 PI，Td=0）。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_simc(K, tau, theta, tau_c_ratio=1.0)

        tau_c = 5.0
        expected_kc = (1.0 / K) * tau / (theta + tau_c)
        expected_ti = tau
        expected_td = 0.0  # FOPDT 时 SIMC 使用 PI 控制，Td=0

        assert abs(pid.kp - expected_kc) < 0.001
        assert abs(pid.ti - expected_ti) < 0.001
        assert abs(pid.td - expected_td) < 0.001

    def test_imc_zero_k_handling(self):
        """K=0 时的兜底处理。"""
        pid = tune_imc(0, 30, 5)
        assert pid.kp > 0

    def test_zn_zero_theta_handling(self):
        """theta=0 时的兜底处理。"""
        pid = tune_zn(1.0, 30.0, 0)
        assert pid.kp > 0


# ---------------------------------------------------------------------------
# 闭环仿真测试
# ---------------------------------------------------------------------------


class TestClosedLoopSimulation:
    """闭环仿真测试。"""

    def test_simulation_basic(self):
        """基本仿真：应返回完整响应数据。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=2.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=200.0,
            sim_step=1.0,
            setpoint_step=1.0,
        )

        assert "timestamps" in result
        assert "currentResponse" in result
        assert "recommendedResponse" in result
        assert "currentMetrics" in result
        assert "recommendedMetrics" in result
        assert "improvement" in result

        assert len(result["timestamps"]) == 201
        assert len(result["currentResponse"]["pv"]) == 201
        assert len(result["recommendedResponse"]["pv"]) == 201

    def test_simulation_metrics_extraction(self):
        """仿真性能指标提取。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.3, ti=50.0, td=0.0)
        recommended_pid = PIDParams(kp=1.5, ti=20.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=300.0,
            sim_step=1.0,
            setpoint_step=1.0,
        )

        rec_metrics = result["recommendedMetrics"]
        assert rec_metrics["overshoot"] is not None

    def test_simulation_improvement(self):
        """改善幅度计算。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.3, ti=50.0, td=0.0)
        recommended_pid = PIDParams(kp=1.5, ti=20.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=300.0,
        )

        improvement = result["improvement"]
        assert "riseTime" in improvement
        assert "overshoot" in improvement
        assert "settlingTime" in improvement
        assert "itae" in improvement


# ---------------------------------------------------------------------------
# API 端点测试
# ---------------------------------------------------------------------------


class TestTuningAPI:
    """整定 API 端点测试。"""

    def test_get_methods_no_token(self, client) -> None:
        """未认证访问应返回 401。"""
        resp = client.get("/api/v1/tuning/methods")
        assert resp.status_code == 401

    def test_get_methods_authorized(self, client) -> None:
        """认证后应返回整定方法列表。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/methods",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        methods = data["data"]
        assert len(methods) == 5
        codes = [m["code"] for m in methods]
        assert "IMC" in codes
        assert "LAMBDA" in codes
        assert "ZN" in codes
        assert "COHEN_COON" in codes
        assert "SIMC" in codes

    def test_tune_pid_api(self, client) -> None:
        """PID 整定 API。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "IMC",
                    "algorithmParams": {"lambdaRatio": 1.0},
                    "modelSource": "MANUAL",
                    "riskConfirmed": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        result = data["data"]
        assert result["algorithm"] == "IMC"
        assert "kp" in result["recommendedPid"]
        assert "ti" in result["recommendedPid"]
        assert "td" in result["recommendedPid"]

    def test_simulate_api(self, client) -> None:
        """闭环仿真 API。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/simulate",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "currentPid": {"kp": 0.5, "ti": 30.0, "td": 0.0},
                    "recommendedPid": {"kp": 2.0, "ti": 15.0, "td": 2.0},
                    "simDuration": 100.0,
                    "simStep": 1.0,
                    "setpointStep": 1.0,
                    "modelSource": "MANUAL",
                    "riskConfirmed": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        result = data["data"]
        assert "timestamps" in result
        assert "currentResponse" in result
        assert "recommendedResponse" in result

    def test_tune_invalid_algorithm(self, client) -> None:
        """无效算法应返回校验错误（S4-C3 枚举约束 → 422）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "INVALID_ALGO",
                },
            )
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == "ERR_VALIDATION"

    def test_tune_missing_k(self, client) -> None:
        """K 缺失应返回错误。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"tau": 30.0, "theta": 5.0},
                    "algorithm": "IMC",
                    "modelSource": "MANUAL",
                    "riskConfirmed": True,
                },
            )
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == "ERR_MODEL_PARAMS_MISSING"

    def test_list_tasks_empty(self, client, mock_db) -> None:
        """空任务列表。"""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)
        list_result = MagicMock()
        list_result.all = MagicMock(return_value=[])

        mock_db.execute = AsyncMock(side_effect=[count_result, list_result])

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/tasks",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        assert "items" in data["data"]
        assert "total" in data["data"]

    def test_history_stats(self, client, mock_db) -> None:
        """整定历史统计。"""
        total_result = MagicMock()
        total_result.scalar = MagicMock(return_value=5)

        algo_result = MagicMock()
        algo_result.all = MagicMock(return_value=[("IMC", 3), ("ZN", 2)])

        status_result = MagicMock()
        status_result.all = MagicMock(return_value=[("SIMULATED", 5)])

        avg_result = MagicMock()
        avg_result.scalar = MagicMock(return_value=Decimal("92.50"))

        recent_result = MagicMock()
        recent_result.all = MagicMock(return_value=[])

        mock_db.execute = AsyncMock(
            side_effect=[total_result, algo_result, status_result, avg_result, recent_result]
        )

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tuning/history",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        assert "totalTasks" in data["data"]
        assert "byAlgorithm" in data["data"]

    def test_identify_loop_not_found(self, client, mock_db) -> None:
        """模型辨识：回路不存在。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/identify",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": "00000000-0000-0000-0000-000000000000",
                    "startTime": "2026-01-01T00:00:00",
                    "endTime": "2026-01-01T01:00:00",
                    "modelType": "FOPDT",
                },
            )
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == "ERR_LOOP_NOT_FOUND"

    def test_tune_sponsor_forbidden(self, client) -> None:
        """SPONSOR 角色不能整定（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "IMC",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_create_task_success(self, client, mock_db) -> None:
        """保存整定任务。"""
        loop = _make_loop_mock()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(loop))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tasks",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": "00000000-0000-0000-0000-0000000000a1",
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "IMC",
                    "recommendedPid": {"kp": 6.5, "ti": 32.5, "td": 2.31},
                    "fittingScore": 95.5,
                    "status": "SIMULATED",
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == "0"


# ---------------------------------------------------------------------------
# 边界条件与异常场景测试
# ---------------------------------------------------------------------------


class TestBoundaryConditions:
    """整定模块边界条件与异常场景覆盖测试。"""

    # ---- FOPDT 辨识边界 ----

    def test_fopdt_constant_pv(self):
        """PV 恒定不变（delta_pv=0, K=0）应判定辨识失败，返回 None 参数而非兜底值。"""
        pv_values = [50.0] * 100
        timestamps = [float(i) for i in range(100)]
        result = identify_fopdt(pv_values, timestamps, 10.0)
        # K=0 时判定辨识失败，禁止带病参数进整定
        assert result["K"] is None
        assert result["tau"] is None
        assert result["theta"] is None
        assert result["fitting_score"] == 0.0
        assert result["reason"] is not None

    def test_fopdt_negative_mv_step(self):
        """负 MV 阶跃（反向作用过程）应正确辨识。"""
        K_true, tau_true, theta_true = -1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values = []
        timestamps = []
        for i in range(300):
            t = float(i)
            timestamps.append(t)
            if t < theta_true:
                pv_values.append(0.0)
            else:
                pv_values.append(K_true * mv_step * (1.0 - math.exp(-(t - theta_true) / tau_true)))
        result = identify_fopdt(pv_values, timestamps, mv_step)
        assert result["K"] is not None
        # 反向作用 K 应为负
        assert result["K"] < 0

    def test_fopdt_large_dead_time(self):
        """大纯滞后（theta 接近数据时长一半）应安全辨识。"""
        K_true, tau_true, theta_true = 1.0, 20.0, 100.0
        mv_step = 10.0
        pv_values = []
        timestamps = []
        for i in range(300):
            t = float(i)
            timestamps.append(t)
            if t < theta_true:
                pv_values.append(0.0)
            else:
                pv_values.append(K_true * mv_step * (1.0 - math.exp(-(t - theta_true) / tau_true)))
        result = identify_fopdt(pv_values, timestamps, mv_step)
        assert result["K"] is not None
        assert result["theta"] is not None

    def test_fopdt_empty_data(self):
        """空数据应返回零值。"""
        result = identify_fopdt([], [], 10.0)
        assert result["K"] is None
        assert result["fitting_score"] == 0.0

    def test_fopdt_single_point(self):
        """单点数据应返回零值。"""
        result = identify_fopdt([50.0], [0.0], 10.0)
        assert result["K"] is None

    # ---- SOPDT 辨识边界 ----

    def test_sopdt_critical_damping(self):
        """SOPDT 临界阻尼（T1 ≈ T2）应安全辨识。"""
        K_true, T_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values = []
        timestamps = []
        for i in range(300):
            t = float(i)
            timestamps.append(t)
            if t < theta_true:
                pv_values.append(0.0)
            else:
                td = t - theta_true
                # 临界阻尼解析解：y = K*ΔMV*(1 - (1 + t/T)*exp(-t/T))
                response = 1.0 - (1.0 + td / T_true) * math.exp(-td / T_true)
                pv_values.append(K_true * mv_step * response)
        result = identify_sopdt(pv_values, timestamps, mv_step)
        assert result["K"] is not None
        assert result["T1"] is not None
        assert result["T2"] is not None

    def test_sopdt_empty_data(self):
        """SOPDT 空数据应返回零值。"""
        result = identify_sopdt([], [], 10.0)
        assert result["K"] is None

    # ---- IPDT 辨识边界 ----

    def test_ipdt_insufficient_response(self):
        """IPDT 响应段数据不足（≤2 点）应使用 K=1.0 兜底。"""
        pv_values = [0.0] * 15
        timestamps = [float(i) for i in range(15)]
        result = identify_ipdt(pv_values, timestamps, 10.0)
        # 响应段不足时 K 兜底为 1.0
        assert result["K"] is not None

    def test_ipdt_empty_data(self):
        """IPDT 空数据应返回零值。"""
        result = identify_ipdt([], [], 10.0)
        assert result["K"] is None

    # ---- PID 整定算法边界 ----

    def test_lambda_zero_k_handling(self):
        """Lambda 整定 K=0 兜底。"""
        pid = tune_lambda(0, 30, 5)
        assert pid.kp > 0

    def test_lambda_zero_tau_handling(self):
        """Lambda 整定 tau=0 兜底。"""
        pid = tune_lambda(1.0, 0, 5)
        assert pid.kp > 0

    def test_cohen_coon_zero_theta_handling(self):
        """Cohen-Coon theta=0 兜底。"""
        pid = tune_cohen_coon(1.0, 30.0, 0)
        assert pid.kp > 0

    def test_cohen_coon_zero_k_handling(self):
        """Cohen-Coon K=0 兜底。"""
        pid = tune_cohen_coon(0, 30.0, 5.0)
        assert pid.kp > 0

    def test_simc_zero_theta_handling(self):
        """SIMC theta=0 兜底。"""
        pid = tune_simc(1.0, 30.0, 0)
        assert pid.kp > 0

    def test_simc_zero_k_handling(self):
        """SIMC K=0 兜底。"""
        pid = tune_simc(0, 30.0, 5.0)
        assert pid.kp > 0

    def test_zn_p_controller(self):
        """Z-N P 控制器类型。"""
        pid = tune_zn(1.0, 30.0, 5.0, controller_type="P")
        assert pid.kp > 0
        assert pid.ti == 0.0
        assert pid.td == 0.0

    def test_cohen_coon_p_controller(self):
        """Cohen-Coon P 控制器类型。"""
        pid = tune_cohen_coon(1.0, 30.0, 5.0, controller_type="P")
        assert pid.kp > 0
        assert pid.ti == 0.0
        assert pid.td == 0.0

    def test_cohen_coon_pi_controller(self):
        """Cohen-Coon PI 控制器类型。"""
        pid = tune_cohen_coon(1.0, 30.0, 5.0, controller_type="PI")
        assert pid.kp > 0
        assert pid.ti > 0
        assert pid.td == 0.0

    def test_imc_negative_lambda_ratio(self):
        """IMC 负 lambda_ratio 兜底为 0.1。"""
        pid = tune_imc(1.0, 30.0, 5.0, lambda_ratio=-1.0)
        assert pid.kp > 0

    def test_all_algorithms_negative_k(self):
        """所有算法对负 K（反向作用）应安全返回。"""
        for tune_fn in [tune_imc, tune_lambda, tune_simc]:
            pid = tune_fn(-1.0, 30.0, 5.0)
            assert isinstance(pid.kp, float)
        for tune_fn in [tune_zn, tune_cohen_coon]:
            pid = tune_fn(-1.0, 30.0, 5.0)
            assert isinstance(pid.kp, float)

    # ---- 闭环仿真边界 ----

    def test_simulation_p_only_controller(self):
        """纯 P 控制器（ti=0）应安全仿真。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=0.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=0.0, td=0.0)
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )
        assert len(result["timestamps"]) > 0
        assert len(result["currentResponse"]["pv"]) == len(result["timestamps"])

    def test_simulation_zero_setpoint(self):
        """零设定值阶跃应安全返回（指标为空）。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
            setpoint_step=0.0,
        )
        # setpoint=0 时指标应为 None
        assert result["currentMetrics"]["riseTime"] is None

    def test_simulation_negative_k(self):
        """负 K（反向作用过程）应安全仿真。"""
        model_params = {"K": -1.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )
        assert len(result["timestamps"]) > 0

    def test_simulation_zero_tau(self):
        """tau=0 兜底为 1.0 应安全仿真。"""
        model_params = {"K": 1.0, "tau": 0.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )
        assert len(result["timestamps"]) > 0

    def test_simulation_large_dead_time(self):
        """大纯滞后（theta > 仿真时长一半）应安全仿真。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 80.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=200.0,
        )
        assert len(result["timestamps"]) > 0

    def test_simulation_op_saturation(self):
        """OP 输出饱和限幅（[-100, 100]）验证。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        # 极大 kp 触发 OP 饱和
        current_pid = PIDParams(kp=1000.0, ti=1.0, td=0.0)
        recommended_pid = PIDParams(kp=2000.0, ti=1.0, td=0.0)
        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=50.0,
        )
        op_values = result["currentResponse"]["op"]
        assert all(-100.0 <= op <= 100.0 for op in op_values)

    # ---- MV 阶跃估算边界 ----

    def test_estimate_mv_step_empty(self):
        """空 OP 数据应返回 0。"""
        assert _estimate_mv_step([]) == 0.0

    def test_estimate_mv_step_single_value(self):
        """单值 OP 数据应返回 0。"""
        assert _estimate_mv_step([50.0]) == 0.0

    def test_estimate_mv_step_with_none(self):
        """含 None 的 OP 数据应过滤后估算。"""
        assert _estimate_mv_step([None, None, None]) == 0.0

    def test_estimate_mv_step_normal(self):
        """正常 OP 阶跃估算。"""
        ops = [50.0] * 20 + [70.0] * 20
        step = _estimate_mv_step(ops)
        assert step == 20.0

    def test_estimate_mv_step_decreasing(self):
        """OP 下降阶跃估算。"""
        ops = [80.0] * 20 + [40.0] * 20
        step = _estimate_mv_step(ops)
        assert step == 40.0


# ---------------------------------------------------------------------------
# S3-C1: 算法对标验证测试（Åström-Hägglund 基准）
# ---------------------------------------------------------------------------


class TestAlgorithmBenchmark:
    """算法对标验证测试 — 使用 Åström-Hägglund 基准 FOPDT 模型。

    基准原理：给定已知 FOPDT 参数 K、tau、theta，生成阶跃响应数据，
    调用 identify_fopdt 辨识参数，验证辨识精度。
    """

    @staticmethod
    def _generate_fopdt_response(
        K: float, tau: float, theta: float, mv_step: float, duration: float, dt: float = 1.0
    ) -> tuple[list[float], list[float]]:
        """生成 FOPDT 阶跃响应仿真数据（用于基准对标）。"""
        pv_values: list[float] = []
        timestamps: list[float] = []
        n = int(duration / dt)
        for i in range(n):
            t = i * dt
            timestamps.append(t)
            if t < theta:
                pv_values.append(0.0)
            else:
                pv_values.append(K * mv_step * (1.0 - math.exp(-(t - theta) / tau)))
        return pv_values, timestamps

    def test_benchmark_case_1(self):
        """基准案例 1：K=1.0, tau=10.0, theta=2.0（Åström-Hägglund 经典参数）。"""
        K_true, tau_true, theta_true = 1.0, 10.0, 2.0
        mv_step = 10.0
        # 仿真时长需远大于 tau 以保证响应充分进入稳态
        pv_values, timestamps = self._generate_fopdt_response(
            K_true, tau_true, theta_true, mv_step, duration=200.0, dt=0.5
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        assert result["K"] is not None, "K 辨识失败"
        assert result["tau"] is not None, "tau 辨识失败"
        assert result["theta"] is not None, "theta 辨识失败"

        # K 误差 < 5%
        K_err = abs(result["K"] - K_true) / abs(K_true)
        assert K_err < 0.05, f"K 误差 {K_err:.2%} 超过 5%（辨识值={result['K']}，真值={K_true}）"

        # tau 误差 < 5%
        tau_err = abs(result["tau"] - tau_true) / abs(tau_true)
        assert tau_err < 0.05, (
            f"tau 误差 {tau_err:.2%} 超过 5%（辨识值={result['tau']}，真值={tau_true}）"
        )

        # theta 误差 < 10%
        theta_err = abs(result["theta"] - theta_true) / abs(theta_true)
        assert theta_err < 0.10, (
            f"theta 误差 {theta_err:.2%} 超过 10%（辨识值={result['theta']}，真值={theta_true}）"
        )

    def test_benchmark_case_2(self):
        """基准案例 2：K=2.0, tau=20.0, theta=4.0（大增益大滞后）。"""
        K_true, tau_true, theta_true = 2.0, 20.0, 4.0
        mv_step = 5.0
        pv_values, timestamps = self._generate_fopdt_response(
            K_true, tau_true, theta_true, mv_step, duration=400.0, dt=0.5
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        assert result["K"] is not None
        assert result["tau"] is not None
        assert result["theta"] is not None

        K_err = abs(result["K"] - K_true) / abs(K_true)
        assert K_err < 0.05, f"K 误差 {K_err:.2%} 超过 5%"

        tau_err = abs(result["tau"] - tau_true) / abs(tau_true)
        assert tau_err < 0.05, f"tau 误差 {tau_err:.2%} 超过 5%"

        theta_err = abs(result["theta"] - theta_true) / abs(theta_true)
        assert theta_err < 0.10, f"theta 误差 {theta_err:.2%} 超过 10%"

    def test_benchmark_case_3(self):
        """基准案例 3：K=0.5, tau=50.0, theta=5.0（慢过程）。"""
        K_true, tau_true, theta_true = 0.5, 50.0, 5.0
        mv_step = 20.0
        pv_values, timestamps = self._generate_fopdt_response(
            K_true, tau_true, theta_true, mv_step, duration=800.0, dt=1.0
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        assert result["K"] is not None
        assert result["tau"] is not None
        assert result["theta"] is not None

        K_err = abs(result["K"] - K_true) / abs(K_true)
        assert K_err < 0.05, f"K 误差 {K_err:.2%} 超过 5%"

        tau_err = abs(result["tau"] - tau_true) / abs(tau_true)
        assert tau_err < 0.05, f"tau 误差 {tau_err:.2%} 超过 5%"

        theta_err = abs(result["theta"] - theta_true) / abs(theta_true)
        assert theta_err < 0.10, f"theta 误差 {theta_err:.2%} 超过 10%"

    def test_benchmark_fitting_score(self):
        """基准案例：拟合度应 > 95%（理想无噪声数据）。"""
        K_true, tau_true, theta_true = 1.0, 10.0, 2.0
        mv_step = 10.0
        pv_values, timestamps = self._generate_fopdt_response(
            K_true, tau_true, theta_true, mv_step, duration=200.0, dt=0.5
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        # 无噪声理想数据，拟合度应非常高
        assert result["fitting_score"] > 95.0, (
            f"拟合度 {result['fitting_score']} 低于 95%（理想无噪声数据应 > 95%）"
        )


# ---------------------------------------------------------------------------
# S4-B1~B5 算法修复验证测试
# ---------------------------------------------------------------------------


class TestS4B1CombinedRemoved:
    """S4-B1: COMBINED 模式死代码已删除。"""

    def test_combined_method_defaults_to_two_point(self):
        """COMBINED 方法不再存在，传入 COMBINED 应回退为 TWO_POINT。"""
        K_true, tau_true, theta_true = 2.0, 50.0, 10.0
        mv_step = 5.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=500
        )

        # 传入已删除的 COMBINED 方法，应回退为 TWO_POINT
        result_combined = identify_fopdt(pv_values, timestamps, mv_step, method="COMBINED")
        result_two_point = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        # 两者结果应完全一致（COMBINED 回退为 TWO_POINT）
        assert result_combined["K"] == result_two_point["K"]
        assert result_combined["tau"] == result_two_point["tau"]
        assert result_combined["theta"] == result_two_point["theta"]

    def test_default_method_is_two_point(self):
        """默认方法应为 TWO_POINT。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=300
        )

        result_default = identify_fopdt(pv_values, timestamps, mv_step)
        result_explicit = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        assert result_default["tau"] == result_explicit["tau"]
        assert result_default["theta"] == result_explicit["theta"]


class TestS4B3AreaMethodMeanPvFinal:
    """S4-B3: FOPDT 面积法 pv_final 用均值。"""

    def test_area_method_with_drift(self):
        """带漂移的响应数据，均值法 pv_final 应更稳定。"""
        K_true, tau_true, theta_true = 0.5, 60.0, 8.0
        mv_step = 20.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=500
        )

        # 在末尾添加小幅漂移（模拟测量噪声/漂移）
        for i in range(len(pv_values) - 10, len(pv_values)):
            pv_values[i] += 0.5  # 末尾漂移

        result = identify_fopdt(pv_values, timestamps, mv_step, method="AREA")

        # 均值法应仍能给出合理的辨识结果
        assert result["K"] is not None
        assert result["tau"] is not None
        # K 应接近真实值（均值法减少漂移影响）
        assert abs(result["K"] - K_true) / K_true < 0.3

    def test_area_method_pv_final_points_param(self):
        """面积法应接受 pv_final_points 参数（默认 10）。"""
        import numpy as np

        from app.services.tuning_algorithms import _fopdt_area_method

        K_true, tau_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=300
        )
        pv = np.array(pv_values, dtype=float)
        ts = np.array(timestamps, dtype=float)

        # 使用 5 个点均值
        result = _fopdt_area_method(
            pv, ts, pv[0], pv[-1], pv[-1] - pv[0], mv_step, pv_final_points=5
        )
        assert result["tau"] > 0
        assert result["theta"] >= 0


class TestS4B4CohenCoonRangeCheck:
    """S4-B4: Cohen-Coon 适用范围检查。"""

    def test_warning_when_ratio_too_small(self, caplog):
        """θ/τ < 0.1 时应记录 WARNING 日志。"""
        import logging

        # theta/tau = 0.5/30 ≈ 0.017 < 0.1
        with caplog.at_level(logging.WARNING, logger="app.services.tuning_algorithms"):
            tune_cohen_coon(K=1.0, tau=30.0, theta=0.5, controller_type="PID")

        # 应有范围超出的警告
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("超出推荐范围" in r.message for r in warnings)

    def test_warning_when_ratio_too_large(self, caplog):
        """θ/τ > 2.0 时应记录 WARNING 日志。"""
        import logging

        # theta/tau = 50/10 = 5.0 > 2.0
        with caplog.at_level(logging.WARNING, logger="app.services.tuning_algorithms"):
            tune_cohen_coon(K=1.0, tau=10.0, theta=50.0, controller_type="PID")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("超出推荐范围" in r.message for r in warnings)

    def test_no_warning_when_ratio_in_range(self, caplog):
        """θ/τ 在 [0.1, 2.0] 范围内不应记录 WARNING。"""
        import logging

        # theta/tau = 5/30 ≈ 0.167，在范围内
        with caplog.at_level(logging.WARNING, logger="app.services.tuning_algorithms"):
            tune_cohen_coon(K=1.0, tau=30.0, theta=5.0, controller_type="PID")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("超出推荐范围" in r.message for r in warnings)


class TestS4B5SopdtClosedLoopSimulation:
    """S4-B5: 闭环仿真支持 SOPDT。"""

    def test_sopdt_simulation_basic(self):
        """SOPDT 闭环仿真应返回完整响应数据。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0, "xi": 1.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=2.0)

        result = simulate_closed_loop(
            model_type="SOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=200.0,
            sim_step=1.0,
            setpoint_step=1.0,
        )

        assert "timestamps" in result
        assert "currentResponse" in result
        assert "recommendedResponse" in result
        assert len(result["timestamps"]) == 201
        assert len(result["currentResponse"]["pv"]) == 201
        assert len(result["recommendedResponse"]["pv"]) == 201

    def test_sopdt_simulation_pv_converges(self):
        """SOPDT 仿真 PV 应收敛到设定值附近。"""
        model_params = {"K": 1.0, "tau": 20.0, "theta": 3.0, "xi": 1.0}
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        result = simulate_closed_loop(
            model_type="SOPDT",
            model_params=model_params,
            current_pid=recommended_pid,
            recommended_pid=recommended_pid,
            sim_duration=500.0,
            sim_step=1.0,
            setpoint_step=1.0,
        )

        pv = result["recommendedResponse"]["pv"]
        # 仿真结束时 PV 应接近设定值 1.0
        assert abs(pv[-1] - 1.0) < 0.2, f"PV 末值 {pv[-1]} 未收敛到设定值"

    def test_sopdt_vs_fopdt_different_response(self):
        """SOPDT 和 FOPDT 仿真应产生不同的响应曲线。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0, "xi": 0.5}
        pid = PIDParams(kp=1.5, ti=20.0, td=1.0)

        result_fopdt = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=pid,
            recommended_pid=pid,
            sim_duration=200.0,
        )
        result_sopdt = simulate_closed_loop(
            model_type="SOPDT",
            model_params=model_params,
            current_pid=pid,
            recommended_pid=pid,
            sim_duration=200.0,
        )

        # 两种模型的 PV 响应应不同（SOPDT 有二阶动态）
        pv_fopdt = result_fopdt["recommendedResponse"]["pv"]
        pv_sopdt = result_sopdt["recommendedResponse"]["pv"]
        # 至少在某些时间点上应有显著差异
        max_diff = max(abs(f - s) for f, s in zip(pv_fopdt, pv_sopdt, strict=False))
        assert max_diff > 0.001, "SOPDT 与 FOPDT 响应完全相同，SOPDT 分支可能未生效"

    def test_sopdt_underdamped_overshoot(self):
        """欠阻尼 SOPDT（xi < 1）应出现过冲。"""
        model_params = {"K": 1.0, "tau": 20.0, "theta": 2.0, "xi": 0.3}
        pid = PIDParams(kp=3.0, ti=10.0, td=0.5)

        result = simulate_closed_loop(
            model_type="SOPDT",
            model_params=model_params,
            current_pid=pid,
            recommended_pid=pid,
            sim_duration=300.0,
            sim_step=0.5,
            setpoint_step=1.0,
        )

        pv = result["recommendedResponse"]["pv"]
        peak = max(pv)
        # 欠阻尼系统应有过冲（峰值超过设定值）
        assert peak > 1.0, f"欠阻尼 SOPDT 未出现过冲，峰值 {peak} <= 设定值 1.0"
