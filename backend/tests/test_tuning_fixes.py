"""整定算法 Phase 1 修复验证测试.

覆盖 5 项修复：
- ① FOPDT 面积法滞后双重计入：tau = A1* − theta
- ② 两点法兜底语义：失败返回 None 参数 + reason，K 取末 10 点均值
- ③ SOPDT 辨识：收敛与拟合质量检查，失败带 identification_failed 标志
- ④ SIMC PI 分支：Ti = min(τ, 4(θ+τc))
- ⑤ RK4 仿真：微分对 PV 消除 derivative kick；sim_step > tau/4 自动细分
"""

from __future__ import annotations

import logging
import math
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.tuning import _detect_valid_step, identify_model
from app.services.tuning_algorithms import (
    PIDParams,
    _fopdt_two_point,
    identify_fopdt,
    identify_sopdt,
    simulate_closed_loop,
    tune_simc,
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _generate_fopdt_step_response(
    K: float, tau: float, theta: float, mv_step: float, duration: float, dt: float = 1.0
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


def _generate_valid_step_window() -> tuple[list[float], list[float]]:
    """稳定基线、单次 MV 阶跃、稳定保持以及显著一阶 PV 响应。"""
    n = 180
    step_idx = 40
    op = [40.0] * step_idx + [50.0] * (n - step_idx)
    pv = [100.0] * n
    for i in range(step_idx + 3, n):
        pv[i] = 100.0 + 15.0 * (1.0 - math.exp(-(i - step_idx - 3) / 20.0))
    return op, pv


class TestAutoFallbackStepValidation:
    """AUTO 只接受真实、稳定且唯一的 MV 单阶跃窗口。"""

    def test_stable_single_step_with_response_is_accepted(self):
        op, pv = _generate_valid_step_window()

        validation = _detect_valid_step(op, pv)

        assert validation["valid"] is True
        assert validation["mv_step"] == pytest.approx(10.0)
        assert validation["step_index"] == 40

    async def test_valid_step_window_can_complete_identification(self):
        """真实稳定单阶跃可完成辨识，且 theta 不包含阶跃前基线时长。"""
        op, pv = _generate_valid_step_window()
        loop = MagicMock(tag_name="TIC-101")
        waveform = {
            "op": op,
            "pv": pv,
            "timestamps": [i * 1000 for i in range(len(op))],
        }

        with (
            patch("app.services.tuning._get_loop", new=AsyncMock(return_value=loop)),
            patch("app.services.tuning.get_waveform", new=AsyncMock(return_value=waveform)),
        ):
            result = await identify_model(
                db=MagicMock(),
                loop_id="loop-1",
                start_time="2026-07-28T00:00:00Z",
                end_time="2026-07-28T01:00:00Z",
            )

        assert result["stepValidationPassed"] is True
        assert result["mvStep"] == pytest.approx(10.0)
        assert result["params"]["K"] == pytest.approx(1.5, rel=0.01)
        assert result["params"]["theta"] == pytest.approx(3.0, abs=0.2)
        assert result["fittingScore"] > 95.0

    def test_pv_change_must_not_impersonate_mv_step(self):
        op = [50.0] * 180
        pv = [100.0 + 0.1 * i for i in range(180)]

        validation = _detect_valid_step(op, pv)

        assert validation["valid"] is False
        assert "MV" in validation["reason"]

    @pytest.mark.parametrize(
        "op,pv",
        [
            (
                np.linspace(40.0, 50.0, 180).tolist(),
                np.linspace(100.0, 110.0, 180).tolist(),
            ),
            (
                [40.0] * 40 + [50.0] * 50 + [45.0] * 90,
                [100.0] * 43
                + [100.0 + 12.0 * (1.0 - math.exp(-(i - 43) / 15.0)) for i in range(43, 180)],
            ),
        ],
        ids=["slow-drift", "multiple-steps"],
    )
    def test_non_single_step_windows_are_rejected(self, op, pv):
        validation = _detect_valid_step(op, pv)

        assert validation["valid"] is False

    def test_step_without_significant_pv_response_is_rejected(self):
        op = [40.0] * 40 + [50.0] * 140
        pv = [100.0 + (0.001 if i % 2 else -0.001) for i in range(180)]

        validation = _detect_valid_step(op, pv)

        assert validation["valid"] is False
        assert "PV" in validation["reason"]


# ---------------------------------------------------------------------------
# ① FOPDT 面积法：tau = A1* − theta
# ---------------------------------------------------------------------------


class TestAreaMethodLagDoubleCount:
    """面积法不再将滞后双重计入（A1* = τ + θ）。"""

    def test_area_method_tau_accuracy(self):
        """已知 K/τ/θ 合成阶跃，面积法辨识 τ 偏差应 < 10%。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 10.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=400.0, dt=0.5
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="AREA")

        assert result["tau"] is not None
        tau_err = abs(result["tau"] - tau_true) / tau_true
        assert tau_err < 0.10, (
            f"面积法 tau 误差 {tau_err:.2%} 超过 10%（辨识值={result['tau']}，真值={tau_true}）"
        )

    def test_area_method_no_double_count_theta(self):
        """回归：旧实现 tau=A1*=τ+θ 会把滞后计入两次，修复后 tau 应接近 τ 而非 τ+θ。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 10.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=400.0, dt=0.5
        )

        result = identify_fopdt(pv_values, timestamps, mv_step, method="AREA")

        # 旧实现会给出 tau ≈ τ + θ = 40；修复后应明显小于 τ + θ
        assert result["tau"] < tau_true + theta_true * 0.5, (
            f"面积法 tau={result['tau']} 仍接近 τ+θ={tau_true + theta_true}，滞后疑似双重计入"
        )

    def test_area_method_failure_returns_none(self):
        """A1* <= θ（tau<=0）时判定辨识失败，返回 None 参数 + reason。

        故障注入：PV 在 t=50 瞬时跳变到终值（无一阶动态），
        面积 A1* ≈ 跳变时间 ≈ θ，tau = A1* − θ <= 0。
        """
        pv_values = [0.0] * 50 + [10.0] * 50
        timestamps = [float(i) for i in range(100)]

        result = identify_fopdt(pv_values, timestamps, 10.0, method="AREA")

        assert result["K"] is None
        assert result["tau"] is None
        assert result["theta"] is None
        assert result["fitting_score"] == 0.0
        assert result["reason"] is not None


# ---------------------------------------------------------------------------
# ② 两点法失败语义 + K 末 10 点均值
# ---------------------------------------------------------------------------


class TestTwoPointFailureSemantics:
    """两点法失败返回 None 参数，不再输出量纲错误的兜底值。"""

    def test_two_point_unreachable_targets_fail(self):
        """目标百分比不可达时返回 tau/theta=None + reason。"""
        # delta_pv=3.0 与数据（终值 1.0）不一致 → 63.2%/60% 目标均不可达
        pv = np.array([0.0] + [1.0] * 19, dtype=float)
        ts = np.arange(20, dtype=float)

        result = _fopdt_two_point(pv, ts, 0.0, 1.0, 3.0)

        assert result["tau"] is None
        assert result["theta"] is None
        assert result["reason"] is not None

    def test_identify_failure_no_sick_params(self):
        """辨识失败时 K/tau/theta 全 None，禁止带病参数进整定。"""
        # 恒定 PV：过程增益为零
        pv_values = [50.0] * 100
        timestamps = [float(i) for i in range(100)]

        for method in ("TWO_POINT", "AREA"):
            result = identify_fopdt(pv_values, timestamps, 10.0, method=method)
            assert result["K"] is None, f"{method} 失败时 K 应为 None"
            assert result["tau"] is None, f"{method} 失败时 tau 应为 None"
            assert result["theta"] is None, f"{method} 失败时 theta 应为 None"
            assert result["reason"] is not None

    def test_k_uses_tail_mean_not_last_point(self):
        """K 应取末 10 点均值，单点跳变不应主导增益估计。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=300.0
        )
        # 末点叠加异常跳变（故障注入：变送器单点毛刺）
        pv_values[-1] += 5.0

        result = identify_fopdt(pv_values, timestamps, mv_step, method="TWO_POINT")

        # 若用单点 pv[-1]，K 会偏到 (10+5)/10=1.5；均值法 K 应仍接近 1.0
        assert result["K"] is not None
        assert abs(result["K"] - K_true) / K_true < 0.10, (
            f"K={result['K']} 偏离真值 {K_true}，末点毛刺未被均值抑制"
        )


# ---------------------------------------------------------------------------
# ③ SOPDT 辨识收敛检查
# ---------------------------------------------------------------------------


class TestSopdtConvergenceCheck:
    """SOPDT 辨识失败返回 None 参数 + identification_failed 标志。"""

    def test_sopdt_success_marks_not_failed(self):
        """正常数据辨识成功，identification_failed=False。"""
        K_true, tau_true, theta_true = 1.0, 30.0, 5.0
        mv_step = 10.0
        pv_values, timestamps = _generate_fopdt_step_response(
            K_true, tau_true, theta_true, mv_step, duration=300.0
        )

        result = identify_sopdt(pv_values, timestamps, mv_step)

        assert result["identification_failed"] is False
        assert result["reason"] is None
        assert result["T1"] is not None and result["T1"] > 0
        assert result["T2"] is not None and result["T2"] > 0
        assert result["theta"] is not None and result["theta"] >= 0
        assert result["fitting_score"] > 70.0

    def test_sopdt_constant_pv_failure(self):
        """恒定 PV（K=0）辨识失败：参数全 None + identification_failed=True。"""
        pv_values = [50.0] * 100
        timestamps = [float(i) for i in range(100)]

        result = identify_sopdt(pv_values, timestamps, 10.0)

        assert result["identification_failed"] is True
        assert result["K"] is None
        assert result["T1"] is None
        assert result["T2"] is None
        assert result["theta"] is None
        assert result["reason"] is not None

    def test_sopdt_nan_data_no_crash(self):
        """含 NaN 数据不崩溃，且不产出带病参数。"""
        pv_values = [float(i) for i in range(50)]
        pv_values[10] = float("nan")
        pv_values[20] = float("nan")
        timestamps = [float(i) for i in range(50)]

        result = identify_sopdt(pv_values, timestamps, 10.0)

        assert isinstance(result, dict)
        assert "identification_failed" in result
        # 失败时不得输出参数
        if result["identification_failed"]:
            assert result["T1"] is None
            assert result["T2"] is None


# ---------------------------------------------------------------------------
# ④ SIMC PI 分支 Ti = min(τ, 4(θ+τc))
# ---------------------------------------------------------------------------


class TestSimcPiTiRule:
    """SIMC PI 积分时间规则（§6.7）。"""

    def test_simc_ti_capped_by_four_theta_plus_tauc(self):
        """τ > 4(θ+τc) 时 Ti 取 4(θ+τc)（滞后相对显著过程的积分封顶）。"""
        K, tau, theta = 1.0, 100.0, 10.0
        pid = tune_simc(K, tau, theta, tau_c_ratio=1.0)

        tau_c = theta  # tau_c_ratio=1.0 → τc = θ
        expected_ti = 4.0 * (theta + tau_c)  # 80.0
        assert abs(pid.ti - expected_ti) < 0.001, f"Ti={pid.ti}，期望 4(θ+τc)={expected_ti}"

    def test_simc_ti_equals_tau_when_smaller(self):
        """τ < 4(θ+τc) 时 Ti 取 τ。"""
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_simc(K, tau, theta, tau_c_ratio=1.0)

        # 4(θ+τc) = 40 > τ = 30 → Ti = τ
        assert abs(pid.ti - tau) < 0.001

    def test_simc_td_remains_zero(self):
        """FOPDT 的 SIMC 恒为 PI 控制，Td=0。"""
        pid = tune_simc(1.0, 100.0, 10.0, tau_c_ratio=1.0)
        assert pid.td == 0.0


# ---------------------------------------------------------------------------
# ⑤ RK4 仿真：微分对 PV + 步长自动细分
# ---------------------------------------------------------------------------


class TestSimulationDerivativeKick:
    """SP 阶跃不应产生微分冲击（derivative kick）。"""

    def test_no_derivative_kick_on_setpoint_step(self):
        """首步 OP 增量不含 Td/Ts 冲击项。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        pid = PIDParams(kp=1.0, ti=10.0, td=5.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=pid,
            recommended_pid=pid,
            sim_duration=100.0,
            sim_step=1.0,
            setpoint_step=1.0,
        )

        op = result["currentResponse"]["op"]
        # 首步：PV 尚未变化，微分项应为 0，Δu = Kp·(Δe + Ts/Ti·e) = 1·(1 + 0.1) = 1.1
        # 旧实现（微分对误差）首步含 Td/Ts=5.0 冲击，Δu = 6.1
        assert abs(op[1] - 1.1) < 1e-6, (
            f"首步 OP={op[1]}，期望 1.1（无微分冲击）；若≈6.1 则 derivative kick 未消除"
        )
        assert op[1] < 2.0

    def test_step_subdivision_warning_and_convergence(self, caplog):
        """sim_step > tau/4 时自动细分并告警，响应仍收敛。"""
        model_params = {"K": 1.0, "tau": 2.0, "theta": 0.0}
        pid = PIDParams(kp=1.0, ti=5.0, td=0.0)

        with caplog.at_level(logging.WARNING, logger="app.services.tuning_algorithms"):
            result = simulate_closed_loop(
                model_type="FOPDT",
                model_params=model_params,
                current_pid=pid,
                recommended_pid=pid,
                sim_duration=100.0,
                sim_step=1.0,  # tau/4 = 0.5 < 1.0 → 触发细分
                setpoint_step=1.0,
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("自动细分" in r.message for r in warnings), "sim_step > tau/4 应有自动细分告警"

        pv = result["recommendedResponse"]["pv"]
        assert abs(pv[-1] - 1.0) < 0.1, f"细分后 PV 末值 {pv[-1]} 未收敛到设定值"

    def test_small_step_no_subdivision_warning(self, caplog):
        """sim_step <= tau/4 时不触发细分告警。"""
        model_params = {"K": 1.0, "tau": 30.0, "theta": 5.0}
        pid = PIDParams(kp=1.0, ti=10.0, td=0.0)

        with caplog.at_level(logging.WARNING, logger="app.services.tuning_algorithms"):
            simulate_closed_loop(
                model_type="FOPDT",
                model_params=model_params,
                current_pid=pid,
                recommended_pid=pid,
                sim_duration=50.0,
                sim_step=1.0,  # tau/4 = 7.5 > 1.0 → 不细分
                setpoint_step=1.0,
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("自动细分" in r.message for r in warnings)
