"""PID 整定算法实现（关键算法设计说明 v1.0 §6）.

模块结构：
- ``identify_fopdt``: FOPDT 模型辨识（两点法 + 面积法）
- ``identify_sopdt``: SOPDT 模型辨识（非线性最小二乘）
- ``identify_ipdt``: IPDT 模型辨识
- ``tune_imc`` / ``tune_lambda`` / ``tune_zn`` / ``tune_cohen_coon`` / ``tune_simc``: PID 整定
- ``simulate_closed_loop``: 闭环仿真（RK4 + 增量式 PID）

所有算法严格对齐关键算法设计说明 v1.0 的数学公式与计算步骤。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize

logger = logging.getLogger(__name__)

# 算法版本号
TUNING_ALGORITHM_VERSION = "TUNE_ENGINE_v1.0"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FOPDTParams:
    """FOPDT 模型参数 G(s) = K * exp(-theta*s) / (tau*s + 1)."""

    K: float
    tau: float
    theta: float


@dataclass
class SOPDTParams:
    """SOPDT 模型参数 G(s) = K * exp(-theta*s) / (T1*T2*s^2 + (T1+T2)*s + 1)."""

    K: float
    T1: float
    T2: float
    theta: float


@dataclass
class IPDTParams:
    """IPDT 模型参数 G(s) = K * exp(-theta*s) / s."""

    K: float
    theta: float


@dataclass
class PIDParams:
    """PID 参数。"""

    kp: float
    ti: float
    td: float = 0.0


@dataclass
class SimulationMetrics:
    """仿真性能指标。"""

    rise_time: float | None = None
    overshoot: float | None = None
    settling_time: float | None = None
    itae: float | None = None


# ---------------------------------------------------------------------------
# FOPDT 模型辨识（§6.1）
# ---------------------------------------------------------------------------


def identify_fopdt(
    pv_values: list[float],
    timestamps: list[float],
    mv_step: float,
    method: str = "COMBINED",
) -> dict[str, Any]:
    """FOPDT 模型辨识。

    Args:
        pv_values: 阶跃响应 PV 数据
        timestamps: 时间戳数组（秒）
        mv_step: 阶跃输入幅值 ΔMV
        method: 辨识方法 TWO_POINT/AREA/COMBINED

    Returns:
        {K, tau, theta, fitting_score, fitted_pv}
    """
    n = len(pv_values)
    if n < 10 or mv_step == 0:
        return {
            "K": None,
            "tau": None,
            "theta": None,
            "fitting_score": 0.0,
            "fitted_pv": [],
        }

    pv = np.array(pv_values, dtype=float)
    ts = np.array(timestamps, dtype=float)

    pv_initial = pv[0]
    pv_final = pv[-1]
    delta_pv = pv_final - pv_initial

    # 过程增益
    K = delta_pv / mv_step

    # 两点法
    result_two_point = _fopdt_two_point(pv, ts, pv_initial, pv_final, delta_pv)

    # 面积法
    result_area = _fopdt_area_method(pv, ts, pv_initial, pv_final, delta_pv, mv_step)

    # 选择方法
    if method == "TWO_POINT":
        params = result_two_point
    elif method == "AREA":
        params = result_area
    else:  # COMBINED
        # 两种方法各计算一次，取拟合度高的
        fitted_two = _fopdt_simulate_curve(
            K, result_two_point["tau"], result_two_point["theta"], ts, pv_initial, mv_step
        )
        score_two = _calc_r2(pv, fitted_two)

        fitted_area = _fopdt_simulate_curve(
            K, result_area["tau"], result_area["theta"], ts, pv_initial, mv_step
        )
        score_area = _calc_r2(pv, fitted_area)

        if score_two >= score_area:
            params = result_two_point
            fitted_pv = fitted_two
            fitting_score = score_two
        else:
            params = result_area
            fitted_pv = fitted_area
            fitting_score = score_area

        return {
            "K": round(K, 6),
            "tau": round(params["tau"], 4),
            "theta": round(params["theta"], 4),
            "fitting_score": round(fitting_score * 100, 2),
            "fitted_pv": fitted_pv.tolist(),
        }

    fitted_pv = _fopdt_simulate_curve(
        K, params["tau"], params["theta"], ts, pv_initial, mv_step
    )
    fitting_score = _calc_r2(pv, fitted_pv)

    return {
        "K": round(K, 6),
        "tau": round(params["tau"], 4),
        "theta": round(params["theta"], 4),
        "fitting_score": round(fitting_score * 100, 2),
        "fitted_pv": fitted_pv.tolist(),
    }


def _fopdt_two_point(
    pv: np.ndarray,
    ts: np.ndarray,
    pv_initial: float,
    pv_final: float,
    delta_pv: float,
) -> dict[str, float]:
    """两点法：28.3% 和 63.2% 终值时间。"""
    target_283 = pv_initial + 0.283 * delta_pv
    target_632 = pv_initial + 0.632 * delta_pv

    t1 = _find_time_at_value(pv, ts, target_283)
    t2 = _find_time_at_value(pv, ts, target_632)

    if t1 is None or t2 is None or t2 <= t1:
        # 兜底：使用 20% 和 60%
        t1 = _find_time_at_value(pv, ts, pv_initial + 0.2 * delta_pv)
        t2 = _find_time_at_value(pv, ts, pv_initial + 0.6 * delta_pv)
        if t1 is None or t2 is None or t2 <= t1:
            return {"tau": float(t2 or 30.0), "theta": float(t1 or 5.0)}

    tau = 1.5 * (t2 - t1)
    theta = t2 - tau

    if tau <= 0:
        tau = abs(tau) + 1.0
    if theta < 0:
        theta = 0.0

    return {"tau": tau, "theta": theta}


def _fopdt_area_method(
    pv: np.ndarray,
    ts: np.ndarray,
    pv_initial: float,
    pv_final: float,
    delta_pv: float,
    mv_step: float,
) -> dict[str, float]:
    """面积法：积分响应曲线下面积。"""
    # A1 = integral(y(inf) - y(t)) dt
    y_inf = pv_final
    diff = y_inf - pv
    # 梯形积分
    dt = np.diff(ts)
    a1 = float(np.sum(0.5 * (diff[:-1] + diff[1:]) * dt))

    K = delta_pv / mv_step
    if K == 0:
        return {"tau": 30.0, "theta": 5.0}

    # 归一化面积
    a1_star = a1 / (K * mv_step)

    # tau = A1*
    tau = a1_star

    # theta = response_start - step_input
    # 找响应开始点（PV 开始变化的时间）
    response_start_idx = _find_response_start(pv, pv_initial)
    theta = float(ts[response_start_idx]) - float(ts[0]) if response_start_idx > 0 else 0.0

    if tau <= 0:
        tau = 30.0
    if theta < 0:
        theta = 0.0

    return {"tau": tau, "theta": theta}


def _find_time_at_value(
    pv: np.ndarray, ts: np.ndarray, target: float
) -> float | None:
    """找到 PV 首次到达 target 值的时间（线性插值）。"""
    for i in range(1, len(pv)):
        if (pv[i - 1] <= target <= pv[i]) or (pv[i - 1] >= target >= pv[i]):
            # 线性插值
            if pv[i] == pv[i - 1]:
                return float(ts[i])
            ratio = (target - pv[i - 1]) / (pv[i] - pv[i - 1])
            return float(ts[i - 1] + ratio * (ts[i] - ts[i - 1]))
    return None


def _find_response_start(pv: np.ndarray, pv_initial: float, threshold: float = 0.01) -> int:
    """找到 PV 开始响应的索引（变化超过阈值）。"""
    for i in range(1, len(pv)):
        if abs(pv[i] - pv_initial) > threshold:
            return i
    return 0


def _fopdt_simulate_curve(
    K: float, tau: float, theta: float, ts: np.ndarray, pv_initial: float, mv_step: float
) -> np.ndarray:
    """根据 FOPDT 模型参数仿真阶跃响应曲线。"""
    result = np.full_like(ts, pv_initial, dtype=float)
    for i, t in enumerate(ts):
        if t < theta:
            result[i] = pv_initial
        else:
            # y(t) = y0 + K * ΔMV * (1 - exp(-(t-theta)/tau))
            result[i] = pv_initial + K * mv_step * (1.0 - math.exp(-(t - theta) / tau))
    return result


def _calc_r2(actual: np.ndarray, predicted: np.ndarray) -> float:
    """计算拟合度 R²。"""
    ss_res = float(np.sum((actual - predicted) ** 2))
    ss_tot = float(np.sum((actual - np.mean(actual)) ** 2))
    if ss_tot == 0:
        return 0.0
    return max(0.0, 1.0 - ss_res / ss_tot)


# ---------------------------------------------------------------------------
# SOPDT 模型辨识（§6.2）
# ---------------------------------------------------------------------------


def identify_sopdt(
    pv_values: list[float],
    timestamps: list[float],
    mv_step: float,
) -> dict[str, Any]:
    """SOPDT 模型辨识（非线性最小二乘拟合）。

    G(s) = K * exp(-theta*s) / (T1*T2*s^2 + (T1+T2)*s + 1)
    """
    n = len(pv_values)
    if n < 10 or mv_step == 0:
        return {
            "K": None,
            "T1": None,
            "T2": None,
            "theta": None,
            "fitting_score": 0.0,
            "fitted_pv": [],
        }

    pv = np.array(pv_values, dtype=float)
    ts = np.array(timestamps, dtype=float)
    pv_initial = pv[0]
    pv_final = pv[-1]
    K = (pv_final - pv_initial) / mv_step

    # 先用 FOPDT 估计初始值
    fopdt_result = identify_fopdt(pv_values, timestamps, mv_step, method="AREA")
    tau_init = fopdt_result.get("tau") or 30.0
    theta_init = fopdt_result.get("theta") or 5.0

    # 初始猜测：T1=tau, T2=tau*0.3, theta=theta_init
    x0 = [max(tau_init, 1.0), max(tau_init * 0.3, 0.5), max(theta_init, 0.0)]

    def objective(x):
        T1, T2, theta = x
        if T1 <= 0 or T2 <= 0 or theta < 0:
            return 1e10
        fitted = _sopdt_simulate_curve(K, T1, T2, theta, ts, pv_initial, mv_step)
        return float(np.sum((pv - fitted) ** 2))

    try:
        result = minimize(
            objective,
            x0,
            method="Nelder-Mead",
            options={"maxiter": 5000, "xatol": 1e-6, "fatol": 1e-10},
        )
        T1, T2, theta = result.x
    except Exception as exc:  # noqa: BLE001
        logger.warning("SOPDT 辨识失败: %s", exc)
        T1, T2, theta = x0

    T1 = max(T1, 0.01)
    T2 = max(T2, 0.01)
    theta = max(theta, 0.0)

    fitted_pv = _sopdt_simulate_curve(K, T1, T2, theta, ts, pv_initial, mv_step)
    fitting_score = _calc_r2(pv, fitted_pv)

    return {
        "K": round(K, 6),
        "T1": round(T1, 4),
        "T2": round(T2, 4),
        "theta": round(theta, 4),
        "fitting_score": round(fitting_score * 100, 2),
        "fitted_pv": fitted_pv.tolist(),
    }


def _sopdt_simulate_curve(
    K: float,
    T1: float,
    T2: float,
    theta: float,
    ts: np.ndarray,
    pv_initial: float,
    mv_step: float,
) -> np.ndarray:
    """SOPDT 阶跃响应仿真（解析解）。"""
    result = np.full_like(ts, pv_initial, dtype=float)
    denom = T1 - T2
    for i, t in enumerate(ts):
        if t < theta:
            result[i] = pv_initial
        else:
            td = t - theta
            if abs(denom) < 1e-8:
                # 临界阻尼（T1 ≈ T2）
                result[i] = pv_initial + K * mv_step * (
                    1.0 - (1.0 + td / T1) * math.exp(-td / T1)
                )
            else:
                result[i] = pv_initial + K * mv_step * (
                    1.0
                    - (T1 * math.exp(-td / T1) - T2 * math.exp(-td / T2)) / (T1 - T2)
                )
    return result


# ---------------------------------------------------------------------------
# IPDT 模型辨识
# ---------------------------------------------------------------------------


def identify_ipdt(
    pv_values: list[float],
    timestamps: list[float],
    mv_step: float,
) -> dict[str, Any]:
    """IPDT 模型辨识 G(s) = K * exp(-theta*s) / s.

    积分过程：PV 随时间线性增长，斜率 = K * ΔMV。
    """
    n = len(pv_values)
    if n < 10 or mv_step == 0:
        return {"K": None, "theta": None, "fitting_score": 0.0, "fitted_pv": []}

    pv = np.array(pv_values, dtype=float)
    ts = np.array(timestamps, dtype=float)
    pv_initial = pv[0]

    # 找响应开始点 → theta
    response_start_idx = _find_response_start(pv, pv_initial)
    theta = float(ts[response_start_idx]) - float(ts[0]) if response_start_idx > 0 else 0.0

    # 线性拟合响应段斜率 → K
    response_ts = ts[response_start_idx:] - ts[response_start_idx]
    response_pv = pv[response_start_idx:]
    if len(response_ts) > 2:
        # 线性回归: pv = slope * t + intercept
        slope = float(np.polyfit(response_ts, response_pv, 1)[0])
        K = slope / mv_step
    else:
        K = 1.0

    # 仿真拟合曲线
    fitted_pv = np.full_like(ts, pv_initial, dtype=float)
    for i, t in enumerate(ts):
        if t < theta:
            fitted_pv[i] = pv_initial
        else:
            fitted_pv[i] = pv_initial + K * mv_step * (t - theta)

    fitting_score = _calc_r2(pv, fitted_pv)

    return {
        "K": round(K, 6),
        "theta": round(theta, 4),
        "fitting_score": round(fitting_score * 100, 2),
        "fitted_pv": fitted_pv.tolist(),
    }


# ---------------------------------------------------------------------------
# PID 整定算法（§6.3~§6.7）
# ---------------------------------------------------------------------------


def tune_imc(
    K: float, tau: float, theta: float, lambda_ratio: float = 1.0
) -> PIDParams:
    """IMC 整定算法（§6.3）。

    Kp = (tau + theta/2) / (K * lambda)
    Ti = tau + theta/2
    Td = (tau * theta) / (2 * (tau + theta/2))

    lambda = lambda_ratio * theta（默认 lambda_ratio=1.0）
    """
    lam = lambda_ratio * theta if theta > 0 else 0.1
    if lam <= 0:
        lam = 0.1
    if K == 0:
        K = 1.0

    kp = (tau + theta / 2.0) / (K * lam)
    ti = tau + theta / 2.0
    td = (tau * theta) / (2.0 * (tau + theta / 2.0)) if (tau + theta / 2.0) > 0 else 0.0

    return PIDParams(kp=round(kp, 6), ti=round(ti, 4), td=round(td, 4))


def tune_lambda(
    K: float, tau: float, theta: float, lambda_ratio: float = 1.0
) -> PIDParams:
    """Lambda 整定算法（§6.4）— 一阶自调节过程 PI。

    Kc = tau / (K * (lambda + theta))
    Ti = tau
    Td = 0

    lambda = lambda_ratio * tau（默认 lambda_ratio=1.0，即 lambda=tau）
    """
    lam = lambda_ratio * tau if tau > 0 else 1.0
    if lam <= 0:
        lam = 1.0
    if K == 0:
        K = 1.0

    kc = tau / (K * (lam + theta))
    ti = tau
    td = 0.0

    return PIDParams(kp=round(kc, 6), ti=round(ti, 4), td=round(td, 4))


def tune_zn(
    K: float, tau: float, theta: float, controller_type: str = "PID"
) -> PIDParams:
    """Ziegler-Nichols 开环反应曲线法（§6.5.2）。

    响应率 R = K / tau

    P:  Kp = 1/(R*theta)
    PI: Kp = 0.9/(R*theta), Ti = theta/0.3
    PID: Kp = 1.2/(R*theta), Ti = 2*theta, Td = 0.5*theta
    """
    if tau <= 0:
        tau = 1.0
    if theta <= 0:
        theta = 0.1
    if K == 0:
        K = 1.0

    R = K / tau
    r_theta = R * theta

    if r_theta == 0:
        r_theta = 0.01

    if controller_type == "P":
        kp = 1.0 / r_theta
        ti = 0.0
        td = 0.0
    elif controller_type == "PI":
        kp = 0.9 / r_theta
        ti = theta / 0.3
        td = 0.0
    else:  # PID
        kp = 1.2 / r_theta
        ti = 2.0 * theta
        td = 0.5 * theta

    return PIDParams(kp=round(kp, 6), ti=round(ti, 4), td=round(td, 4))


def tune_cohen_coon(
    K: float, tau: float, theta: float, controller_type: str = "PID"
) -> PIDParams:
    """Cohen-Coon 整定算法（§6.6）。

    PID:
    Kp = (1/K) * (tau/theta) * (1.35 + theta/(3*tau))
    Ti = theta * (32 + 6*theta/tau) / (13 + 8*theta/tau)
    Td = theta * 4 / (11 + 2*theta/tau)
    """
    if tau <= 0:
        tau = 1.0
    if theta <= 0:
        theta = 0.1
    if K == 0:
        K = 1.0

    ratio = theta / tau

    if controller_type == "P":
        kp = (1.0 / K) * (tau / theta) * (1.0 + ratio / 3.0)
        ti = 0.0
        td = 0.0
    elif controller_type == "PI":
        kp = (1.0 / K) * (tau / theta) * (0.9 + ratio / 12.0)
        ti = theta * (30.0 + 3.0 * ratio) / (9.0 + 20.0 * ratio)
        td = 0.0
    else:  # PID
        kp = (1.0 / K) * (tau / theta) * (1.35 + ratio / 3.0)
        ti = theta * (32.0 + 6.0 * ratio) / (13.0 + 8.0 * ratio)
        td = theta * 4.0 / (11.0 + 2.0 * ratio)

    return PIDParams(kp=round(kp, 6), ti=round(ti, 4), td=round(td, 4))


def tune_simc(
    K: float, tau: float, theta: float, tau_c_ratio: float = 1.0
) -> PIDParams:
    """SIMC 整定算法（§6.7）— Skogestad 简化 IMC。

    PID:
    Kc = (1/K) * tau / (theta + tau_c)
    Ti = tau
    Td = theta

    tau_c = tau_c_ratio * theta（默认 tau_c_ratio=1.0）
    """
    if tau <= 0:
        tau = 1.0
    if theta <= 0:
        theta = 0.1
    if K == 0:
        K = 1.0

    tau_c = tau_c_ratio * theta
    if tau_c <= 0:
        tau_c = 0.1

    kc = (1.0 / K) * tau / (theta + tau_c)
    ti = tau
    td = theta

    return PIDParams(kp=round(kc, 6), ti=round(ti, 4), td=round(td, 4))


# ---------------------------------------------------------------------------
# 闭环仿真（§6.8）
# ---------------------------------------------------------------------------


def simulate_closed_loop(
    model_type: str,
    model_params: dict[str, Any],
    current_pid: PIDParams,
    recommended_pid: PIDParams,
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    disturbance_type: str = "step",
) -> dict[str, Any]:
    """闭环仿真。

    使用增量式 PID + FOPDT 被控对象 + 四阶 Runge-Kutta 积分。

    Returns:
        包含 timestamps/currentResponse/recommendedResponse/currentMetrics/
        recommendedMetrics/improvement 六个键的字典。
    """
    # 生成时间序列
    n_steps = int(sim_duration / sim_step)
    timestamps = [i * sim_step for i in range(n_steps + 1)]

    # 仿真当前 PID
    current_response = _simulate_pid_response(
        model_type,
        model_params,
        current_pid,
        n_steps,
        sim_step,
        setpoint_step,
        disturbance_type,
    )

    # 仿真推荐 PID
    recommended_response = _simulate_pid_response(
        model_type,
        model_params,
        recommended_pid,
        n_steps,
        sim_step,
        setpoint_step,
        disturbance_type,
    )

    # 提取性能指标
    current_metrics = _extract_metrics(current_response, setpoint_step, sim_step)
    recommended_metrics = _extract_metrics(recommended_response, setpoint_step, sim_step)

    # 改善幅度
    improvement = _calc_improvement(current_metrics, recommended_metrics)

    return {
        "timestamps": timestamps,
        "currentResponse": current_response,
        "recommendedResponse": recommended_response,
        "currentMetrics": _metrics_to_dict(current_metrics),
        "recommendedMetrics": _metrics_to_dict(recommended_metrics),
        "improvement": improvement,
    }


def _simulate_pid_response(
    model_type: str,
    model_params: dict[str, Any],
    pid: PIDParams,
    n_steps: int,
    sim_step: float,
    setpoint_step: float,
    disturbance_type: str,
) -> dict[str, list[float]]:
    """仿真单组 PID 的阶跃响应。"""
    pv = [0.0] * (n_steps + 1)
    op = [0.0] * (n_steps + 1)
    sp = [0.0] * (n_steps + 1)

    # 设定值阶跃
    for i in range(n_steps + 1):
        sp[i] = setpoint_step if i > 0 else 0.0

    # PID 状态（增量式）
    e_prev = 0.0
    e_prev2 = 0.0

    # 被控对象参数
    K = float(model_params.get("K", 1.0))
    tau = float(model_params.get("tau", 30.0))
    theta = float(model_params.get("theta", 5.0))

    if tau <= 0:
        tau = 1.0
    if theta < 0:
        theta = 0.0

    # 死区步数
    theta_steps = max(0, int(round(theta / sim_step)))
    # 延迟队列
    op_delay_queue: list[float] = [0.0] * (theta_steps + 1)

    # 被控对象状态（一阶系统）
    x = 0.0  # 状态变量

    for k in range(1, n_steps + 1):
        # 误差
        e = sp[k] - pv[k - 1]

        # 增量式 PID
        delta_u = 0.0
        if pid.ti > 0:
            delta_u = pid.kp * (
                (e - e_prev)
                + sim_step / pid.ti * e
                + pid.td / sim_step * (e - 2 * e_prev + e_prev2)
            )
        else:
            delta_u = pid.kp * (
                (e - e_prev) + pid.td / sim_step * (e - 2 * e_prev + e_prev2)
            )

        op[k] = op[k - 1] + delta_u

        # 输出限幅
        op[k] = max(-100.0, min(100.0, op[k]))

        # 延迟队列
        op_delay_queue.append(op[k])
        if len(op_delay_queue) > theta_steps + 1:
            op_delay_queue.pop(0)
        delayed_op = op_delay_queue[0] if op_delay_queue else op[k]

        # 被控对象：一阶系统 dx/dt = (-x + K*u) / tau
        # RK4 积分
        def _deriv(state: float, u: float) -> float:
            return (-state + K * u) / tau

        k1 = _deriv(x, delayed_op)
        k2 = _deriv(x + 0.5 * sim_step * k1, delayed_op)
        k3 = _deriv(x + 0.5 * sim_step * k2, delayed_op)
        k4 = _deriv(x + sim_step * k3, delayed_op)
        x = x + sim_step / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)

        pv[k] = x

        # 更新误差历史
        e_prev2 = e_prev
        e_prev = e

    return {"pv": pv, "op": op, "sp": sp}


def _extract_metrics(
    response: dict[str, list[float]], setpoint_step: float, sim_step: float
) -> SimulationMetrics:
    """提取仿真性能指标。"""
    pv = response["pv"]
    n = len(pv)

    if n < 2 or setpoint_step == 0:
        return SimulationMetrics()

    # 上升时间：PV 从 10% 到 90% 终值的时间
    target_10 = 0.1 * setpoint_step
    target_90 = 0.9 * setpoint_step
    t_10 = None
    t_90 = None
    for i in range(n):
        if t_10 is None and pv[i] >= target_10:
            t_10 = i * sim_step
        if t_90 is None and pv[i] >= target_90:
            t_90 = i * sim_step
            break
    rise_time = (t_90 - t_10) if (t_10 is not None and t_90 is not None) else None

    # 超调量
    pv_peak = max(pv)
    overshoot = max(0.0, (pv_peak - setpoint_step) / setpoint_step * 100.0)

    # 稳定时间：PV 进入 SP ± 2% 范围的时间
    settling_time = None
    band = 0.02 * abs(setpoint_step)
    for i in range(n - 1, -1, -1):
        if abs(pv[i] - setpoint_step) > band:
            settling_time = (i + 1) * sim_step
            break
    if settling_time is not None and settling_time >= n * sim_step:
        settling_time = None

    # ITAE
    sp_val = setpoint_step
    itae = 0.0
    for i in range(1, n):
        t = i * sim_step
        e = abs(sp_val - pv[i])
        itae += t * e * sim_step

    return SimulationMetrics(
        rise_time=round(rise_time, 2) if rise_time is not None else None,
        overshoot=round(overshoot, 2),
        settling_time=round(settling_time, 2) if settling_time is not None else None,
        itae=round(itae, 4),
    )


def _calc_improvement(
    current: SimulationMetrics, recommended: SimulationMetrics
) -> dict[str, float | None]:
    """计算改善幅度。"""
    def _pct_change(curr: float | None, rec: float | None) -> float | None:
        if curr is None or rec is None or curr == 0:
            return None
        return round((curr - rec) / curr * 100.0, 2)

    return {
        "riseTime": _pct_change(current.rise_time, recommended.rise_time),
        "overshoot": _pct_change(current.overshoot, recommended.overshoot),
        "settlingTime": _pct_change(current.settling_time, recommended.settling_time),
        "itae": _pct_change(current.itae, recommended.itae),
    }


def _metrics_to_dict(m: SimulationMetrics) -> dict[str, float | None]:
    """SimulationMetrics → dict。"""
    return {
        "riseTime": m.rise_time,
        "overshoot": m.overshoot,
        "settlingTime": m.settling_time,
        "itae": m.itae,
    }


# ---------------------------------------------------------------------------
# 整定方法信息
# ---------------------------------------------------------------------------


TUNING_METHODS_INFO: list[dict[str, Any]] = [
    {
        "code": "IMC",
        "name": "IMC 内模控制",
        "description": "基于内模控制原理的 PID 整定，平衡性能与鲁棒性",
        "applicableModel": "FOPDT",
        "params": [
            {"name": "lambdaRatio", "label": "λ 比例系数", "default": 1.0, "min": 0.1, "max": 5.0},
        ],
    },
    {
        "code": "LAMBDA",
        "name": "Lambda 整定",
        "description": "基于期望闭环时间常数的整定方法，适合一阶自调节过程",
        "applicableModel": "FOPDT",
        "params": [
            {"name": "lambdaRatio", "label": "λ 比例系数", "default": 1.0, "min": 0.1, "max": 5.0},
        ],
    },
    {
        "code": "ZN",
        "name": "Ziegler-Nichols",
        "description": "经典开环反应曲线法，适用于大多数工业过程",
        "applicableModel": "FOPDT",
        "params": [
            {
                "name": "controllerType",
                "label": "控制器类型",
                "default": "PID",
                "options": ["P", "PI", "PID"],
            },
        ],
    },
    {
        "code": "COHEN_COON",
        "name": "Cohen-Coon",
        "description": "大滞后系统整定方法，θ/τ > 0.5 时优于 Z-N",
        "applicableModel": "FOPDT",
        "params": [
            {
                "name": "controllerType",
                "label": "控制器类型",
                "default": "PID",
                "options": ["P", "PI", "PID"],
            },
        ],
    },
    {
        "code": "SIMC",
        "name": "SIMC 简化 IMC",
        "description": "Skogestad 简化整定规则，工程实用性强",
        "applicableModel": "FOPDT",
        "params": [
            {"name": "tauCRatio", "label": "τc 比例系数", "default": 1.0, "min": 0.1, "max": 5.0},
        ],
    },
]


__all__ = [
    "TUNING_ALGORITHM_VERSION",
    "FOPDTParams",
    "SOPDTParams",
    "IPDTParams",
    "PIDParams",
    "SimulationMetrics",
    "identify_fopdt",
    "identify_sopdt",
    "identify_ipdt",
    "tune_imc",
    "tune_lambda",
    "tune_zn",
    "tune_cohen_coon",
    "tune_simc",
    "simulate_closed_loop",
    "TUNING_METHODS_INFO",
]
