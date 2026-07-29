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
    method: str = "TWO_POINT",
) -> dict[str, Any]:
    """FOPDT 模型辨识。

    Args:
        pv_values: 阶跃响应 PV 数据
        timestamps: 时间戳数组（秒）
        mv_step: 阶跃输入幅值 ΔMV
        method: 辨识方法 TWO_POINT/AREA（默认 TWO_POINT）

    Returns:
        {K, tau, theta, fitting_score, fitted_pv, reason}
        辨识失败时 K/tau/theta 均为 None，reason 给出失败原因，
        禁止带病参数进入下游整定。
    """
    n = len(pv_values)
    if n < 10 or mv_step == 0:
        return _fopdt_failure("数据点不足（<10）或阶跃幅值为零")

    pv = np.array(pv_values, dtype=float)
    ts = np.array(timestamps, dtype=float)

    pv_initial = pv[0]
    # 稳态终值取末 N 点均值，减少单点漂移/噪声对 K 的影响
    n_final = min(10, n)
    pv_final = float(np.mean(pv[-n_final:]))
    delta_pv = pv_final - pv_initial

    # 过程增益
    K = delta_pv / mv_step
    if not math.isfinite(K) or K == 0:
        return _fopdt_failure("过程增益无效（K=0 或非有限值），PV 无有效阶跃响应")

    # 两点法
    result_two_point = _fopdt_two_point(pv, ts, pv_initial, pv_final, delta_pv)

    # 面积法
    result_area = _fopdt_area_method(pv, ts, pv_initial, pv_final, delta_pv, mv_step)

    # 选择方法（支持 TWO_POINT / AREA，未知方法默认 TWO_POINT）
    if method == "AREA":
        params = result_area
    else:  # TWO_POINT 或未知方法默认两点法
        params = result_two_point

    if params["tau"] is None or params["theta"] is None:
        return _fopdt_failure(params["reason"] or "辨识失败")

    fitted_pv = _fopdt_simulate_curve(K, params["tau"], params["theta"], ts, pv_initial, mv_step)
    fitting_score = _calc_r2(pv, fitted_pv)

    return {
        "K": round(K, 6),
        "tau": round(params["tau"], 4),
        "theta": round(params["theta"], 4),
        "fitting_score": round(fitting_score * 100, 2),
        "fitted_pv": fitted_pv.tolist(),
        "reason": None,
    }


def _fopdt_failure(reason: str) -> dict[str, Any]:
    """FOPDT 辨识失败统一返回：参数全 None，禁止带病参数进整定。"""
    logger.warning("FOPDT 辨识失败: %s", reason)
    return {
        "K": None,
        "tau": None,
        "theta": None,
        "fitting_score": 0.0,
        "fitted_pv": [],
        "reason": reason,
    }


def _fopdt_two_point(
    pv: np.ndarray,
    ts: np.ndarray,
    pv_initial: float,
    pv_final: float,
    delta_pv: float,
) -> dict[str, float | None]:
    """两点法：28.3% 和 63.2% 终值时间。

    失败时返回 {"tau": None, "theta": None, "reason": ...}，
    不再返回量纲错误的兜底参数。
    """
    target_283 = pv_initial + 0.283 * delta_pv
    target_632 = pv_initial + 0.632 * delta_pv

    t1 = _find_time_at_value(pv, ts, target_283)
    t2 = _find_time_at_value(pv, ts, target_632)

    if t1 is None or t2 is None or t2 <= t1:
        # 兜底：使用 20% 和 60%
        t1 = _find_time_at_value(pv, ts, pv_initial + 0.2 * delta_pv)
        t2 = _find_time_at_value(pv, ts, pv_initial + 0.6 * delta_pv)
        if t1 is None or t2 is None or t2 <= t1:
            return {
                "tau": None,
                "theta": None,
                "reason": "两点法失败：响应曲线未到达目标百分比或时间顺序异常",
            }

    tau = 1.5 * (t2 - t1)
    theta = t2 - tau

    if tau <= 0:
        return {
            "tau": None,
            "theta": None,
            "reason": f"两点法辨识失败：tau={tau:.4f} <= 0（t1={t1:.4f}, t2={t2:.4f}）",
        }
    if theta < 0:
        theta = 0.0

    return {"tau": tau, "theta": theta, "reason": None}


def _fopdt_area_method(
    pv: np.ndarray,
    ts: np.ndarray,
    pv_initial: float,
    pv_final: float,
    delta_pv: float,
    mv_step: float,
    pv_final_points: int = 10,
) -> dict[str, float | None]:
    """面积法：积分响应曲线下面积。

    归一化面积 A1* 是一阶加纯滞后过程的平均驻留时间，即 A1* = τ + θ，
    因此 τ = A1* − θ（θ 取响应开始时间），避免滞后双重计入。
    失败时返回 {"tau": None, "theta": None, "reason": ...}。

    Args:
        pv_final_points: 计算稳态终值使用的末尾采样点数（均值），减少漂移影响。
    """
    # 使用最后 N 个点的均值作为 pv_final，减少漂移影响
    n_final = min(pv_final_points, len(pv))
    y_inf = float(np.mean(pv[-n_final:]))
    diff = y_inf - pv
    # 梯形积分
    dt = np.diff(ts)
    a1 = float(np.sum(0.5 * (diff[:-1] + diff[1:]) * dt))

    K = delta_pv / mv_step
    if K == 0 or not math.isfinite(K):
        return {"tau": None, "theta": None, "reason": "面积法失败：过程增益为零或非有限值"}

    # 归一化面积
    a1_star = a1 / (K * mv_step)
    if not math.isfinite(a1_star):
        return {"tau": None, "theta": None, "reason": "面积法失败：归一化面积 A1* 非有限值"}

    # theta = response_start - step_input
    # 找响应开始点（PV 开始变化的时间）
    response_start_idx = _find_response_start(pv, pv_initial)
    theta = float(ts[response_start_idx]) - float(ts[0]) if response_start_idx > 0 else 0.0

    # A1* = τ + θ（平均驻留时间），故 τ = A1* − θ
    tau = a1_star - theta

    if tau <= 0:
        return {
            "tau": None,
            "theta": None,
            "reason": f"面积法辨识失败：tau = A1*({a1_star:.4f}) - θ({theta:.4f}) <= 0",
        }
    if theta < 0:
        theta = 0.0

    return {"tau": tau, "theta": theta, "reason": None}


def _find_time_at_value(pv: np.ndarray, ts: np.ndarray, target: float) -> float | None:
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

    辨识失败时参数全 None 并置 identification_failed=True，
    禁止带病参数进入下游整定。
    """
    n = len(pv_values)
    if n < 10 or mv_step == 0:
        return _sopdt_failure("数据点不足（<10）或阶跃幅值为零")

    pv = np.array(pv_values, dtype=float)
    ts = np.array(timestamps, dtype=float)
    pv_initial = pv[0]
    pv_final = pv[-1]
    K = (pv_final - pv_initial) / mv_step
    if not math.isfinite(K) or K == 0:
        return _sopdt_failure("过程增益无效（K=0 或非有限值），PV 无有效阶跃响应")

    # 先用 FOPDT 估计初始值
    fopdt_result = identify_fopdt(pv_values, timestamps, mv_step, method="AREA")
    tau_init = fopdt_result.get("tau") or 30.0
    theta_init = fopdt_result.get("theta") or 5.0

    # 初始猜测：T1=tau, T2=tau*0.3, theta=theta_init
    x0 = [max(tau_init, 1.0), max(tau_init * 0.3, 0.5), max(theta_init, 0.0)]

    def objective(x):
        T1, T2, theta = x
        fitted = _sopdt_simulate_curve(K, T1, T2, theta, ts, pv_initial, mv_step)
        sse = float(np.sum((pv - fitted) ** 2))
        # 非有限 SSE（如输入含 NaN/Inf）以大惩罚值驱赶单纯形离开
        return sse if math.isfinite(sse) else 1e10

    try:
        result = minimize(
            objective,
            x0,
            method="Nelder-Mead",
            # 物理边界：时间常数为正、滞后非负（替代原 1e10 惩罚）
            bounds=[(1e-3, None), (1e-3, None), (0.0, None)],
            options={"maxiter": 5000, "xatol": 1e-6, "fatol": 1e-10},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("SOPDT 辨识异常: %s", exc)
        return _sopdt_failure(f"非线性最小二乘求解异常: {exc}")

    T1, T2, theta = float(result.x[0]), float(result.x[1]), float(result.x[2])
    sse_final = float(result.fun)
    if not result.success:
        # 未收敛不直接判失败：T1≈T2 可交换脊线上 xatol/fatol 判据可能不触发，
        # 但单纯形已到达最优点；以最终 SSE 与拟合度门槛做质量闸口
        logger.warning(
            "SOPDT Nelder-Mead 未完全收敛（%s），以最终 SSE/拟合度校验结果", result.message
        )
    if not math.isfinite(sse_final):
        return _sopdt_failure("最终 SSE 非有限值，优化失败")
    if not all(math.isfinite(v) for v in (T1, T2, theta)) or T1 <= 0 or T2 <= 0 or theta < 0:
        return _sopdt_failure("优化结果参数越界（T1/T2 非正或 θ 为负）")

    fitted_pv = _sopdt_simulate_curve(K, T1, T2, theta, ts, pv_initial, mv_step)
    fitting_score = _calc_r2(pv, fitted_pv)
    if not math.isfinite(fitting_score) or fitting_score < _SOPDT_MIN_R2:
        return _sopdt_failure(f"拟合度过低：R²={fitting_score:.4f} < {_SOPDT_MIN_R2}")

    return {
        "K": round(K, 6),
        "T1": round(T1, 4),
        "T2": round(T2, 4),
        "theta": round(theta, 4),
        "fitting_score": round(fitting_score * 100, 2),
        "fitted_pv": fitted_pv.tolist(),
        "identification_failed": False,
        "reason": None,
    }


# SOPDT 辨识可接受的最低拟合度（R²），低于此值判定辨识失败
_SOPDT_MIN_R2 = 0.5


def _sopdt_failure(reason: str) -> dict[str, Any]:
    """SOPDT 辨识失败统一返回：参数全 None + identification_failed 标志。"""
    logger.warning("SOPDT 辨识失败: %s", reason)
    return {
        "K": None,
        "T1": None,
        "T2": None,
        "theta": None,
        "fitting_score": 0.0,
        "fitted_pv": [],
        "identification_failed": True,
        "reason": reason,
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
                result[i] = pv_initial + K * mv_step * (1.0 - (1.0 + td / T1) * math.exp(-td / T1))
            else:
                result[i] = pv_initial + K * mv_step * (
                    1.0 - (T1 * math.exp(-td / T1) - T2 * math.exp(-td / T2)) / (T1 - T2)
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


def tune_imc(K: float, tau: float, theta: float, lambda_ratio: float = 1.0) -> PIDParams:
    """IMC 整定算法（§6.3）— 基于 Morari & Zafiriou 内模控制原理。

    使用一阶 Padé 近似迟延，FOPDT 模型 G(s)=K·exp(-θs)/(τs+1) 的 IMC-PID 整定：
    Kp = (τ + θ/2) / (K · (λ + θ/2))
    Ti = τ + θ/2
    Td = (τ · θ) / (2·(τ + θ/2))

    λ = lambda_ratio × θ（默认 lambda_ratio=1.0）
    """
    lam = lambda_ratio * theta if theta > 0 else 0.1
    if lam <= 0:
        lam = 0.1
    if K == 0:
        K = 1.0

    # Padé 近似后的 IMC 公式：分母必须含 θ/2 项
    denom = lam + theta / 2.0
    if denom <= 0:
        denom = 0.1
    kp = (tau + theta / 2.0) / (K * denom)
    ti = tau + theta / 2.0
    td = (tau * theta) / (2.0 * (tau + theta / 2.0)) if (tau + theta / 2.0) > 0 else 0.0

    return PIDParams(kp=round(kp, 6), ti=round(ti, 4), td=round(td, 4))


def tune_lambda(K: float, tau: float, theta: float, lambda_ratio: float = 1.0) -> PIDParams:
    """Lambda 整定算法（§6.4）— 一阶自调节过程 PI。

    Kc = tau / (K * (lambda + theta))
    Ti = tau
    Td = 0

    lambda = lambda_ratio * tau（默认 lambda_ratio=1.0，即 lambda=tau）
    """
    if tau <= 0:
        tau = 1.0
    lam = lambda_ratio * tau if tau > 0 else 1.0
    if lam <= 0:
        lam = 1.0
    if K == 0:
        K = 1.0

    kc = tau / (K * (lam + theta))
    ti = tau
    td = 0.0

    return PIDParams(kp=round(kc, 6), ti=round(ti, 4), td=round(td, 4))


def tune_zn(K: float, tau: float, theta: float, controller_type: str = "PID") -> PIDParams:
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


def tune_cohen_coon(K: float, tau: float, theta: float, controller_type: str = "PID") -> PIDParams:
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

    # Cohen-Coon 在 θ/τ 超出 [0.1, 2.0] 范围时精度较差，记录警告
    if ratio < 0.1 or ratio > 2.0:
        logger.warning(
            "Cohen-Coon 整定 θ/τ=%.4f 超出推荐范围 [0.1, 2.0]，整定精度可能下降",
            ratio,
        )

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


def tune_simc(K: float, tau: float, theta: float, tau_c_ratio: float = 1.0) -> PIDParams:
    """SIMC 整定算法（§6.7）— Skogestad 2001 简化 IMC。

    FOPDT 模型 G(s)=K·exp(-θs)/(τs+1) 的 SIMC-PI 整定规则：
    Kc = (1/K) · τ / (θ + τc)
    Ti = min(τ, 4(θ + τc))
    Td = 0  （FOPDT 使用 PI 控制，无微分项）

    τc = tau_c_ratio × θ（默认 tau_c_ratio=1.0）
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
    # PI 分支（§6.7）：Ti = min(τ, 4(θ+τc))，大 τ 过程积分时间被封顶
    ti = min(tau, 4.0 * (theta + tau_c))
    td = 0.0  # FOPDT 时 SIMC 使用 PI 控制，Td=0（Skogestad 2001）

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
    pid_candidates: list[tuple[str, PIDParams]] | None = None,
) -> dict[str, Any]:
    """闭环仿真。

    使用增量式 PID + FOPDT/SOPDT 被控对象 + 四阶 Runge-Kutta 积分。

    Args:
        pid_candidates: Phase 2 新增 — 多组候选 PID（label + PIDParams），
            额外仿真每组并返回 candidateResponses（向后兼容，None 时不含）。

    Returns:
        包含 timestamps/currentResponse/recommendedResponse/currentMetrics/
        recommendedMetrics/improvement 六个键的字典；
        pid_candidates 非空时额外含 candidateResponses。
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

    result: dict[str, Any] = {
        "timestamps": timestamps,
        "currentResponse": current_response,
        "recommendedResponse": recommended_response,
        "currentMetrics": _metrics_to_dict(current_metrics),
        "recommendedMetrics": _metrics_to_dict(recommended_metrics),
        "improvement": improvement,
    }

    # Phase 2：多组候选 PID 仿真对比
    if pid_candidates:
        candidate_responses: list[dict[str, Any]] = []
        for label, pid in pid_candidates:
            resp = _simulate_pid_response(
                model_type,
                model_params,
                pid,
                n_steps,
                sim_step,
                setpoint_step,
                disturbance_type,
            )
            metrics = _extract_metrics(resp, setpoint_step, sim_step)
            candidate_responses.append(
                {
                    "label": label,
                    "response": resp,
                    "metrics": _metrics_to_dict(metrics),
                }
            )
        result["candidateResponses"] = candidate_responses

    return result


def _simulate_pid_response(
    model_type: str,
    model_params: dict[str, Any],
    pid: PIDParams,
    n_steps: int,
    sim_step: float,
    setpoint_step: float,
    disturbance_type: str,
) -> dict[str, list[float]]:
    """仿真单组 PID 的阶跃响应。

    model_params 契约：
    - FOPDT: {K, tau, theta}
    - SOPDT: 优先标准形 {K, T1, T2, theta}（T1/T2 成对出现才生效，
      与两条辨识路径输出一致）；否则回退旧 τ/ξ 形 {K, tau, xi, theta}
    """
    pv = [0.0] * (n_steps + 1)
    op = [0.0] * (n_steps + 1)
    sp = [0.0] * (n_steps + 1)

    # 设定值阶跃
    for i in range(n_steps + 1):
        sp[i] = setpoint_step if i > 0 else 0.0

    # PID 状态（增量式）
    e_prev = 0.0
    # PV 历史（微分对测量值，消除 SP 阶跃时的 derivative kick）
    y_prev = 0.0
    y_prev2 = 0.0

    # 被控对象参数
    K = float(model_params.get("K", 1.0))
    tau = float(model_params.get("tau", 30.0))
    theta = float(model_params.get("theta", 5.0))

    if tau <= 0:
        tau = 1.0
    if theta < 0:
        theta = 0.0

    is_sopdt = model_type == "SOPDT"

    # SOPDT 参数契约（P1-3 修复）：优先标准形 {K, T1, T2, theta}
    #   G(s) = K·e^(-θs) / ((T1·s+1)(T2·s+1))
    # 两条辨识路径（阶跃 identify_sopdt / 历史 tuning_identification）均输出该形；
    # 兼容旧 τ/ξ 形 G(s) = K·e^(-θs) / (τ²s²+2τξs+1)。
    # T1/T2 需成对出现才生效，否则回退 τ/ξ 形。
    t1_raw = model_params.get("T1") if is_sopdt else None
    t2_raw = model_params.get("T2") if is_sopdt else None
    use_t1_t2 = t1_raw is not None and t2_raw is not None
    sopdt_t1 = 0.0
    sopdt_t2 = 0.0
    if use_t1_t2:
        sopdt_t1 = float(t1_raw)
        sopdt_t2 = float(t2_raw)
        if sopdt_t1 <= 0:
            sopdt_t1 = 1.0
        if sopdt_t2 <= 0:
            sopdt_t2 = 1.0

    # SOPDT 旧形参数：阻尼比 xi（默认 1.0 即临界阻尼）
    xi = float(model_params.get("xi", 1.0))
    if xi < 0:
        xi = 0.0
    # SOPDT 旧形辅助常量（在循环外计算）
    tau_sq = tau * tau if is_sopdt else 1.0

    # 死区步数
    theta_steps = max(0, int(round(theta / sim_step)))
    # 延迟队列
    op_delay_queue: list[float] = [0.0] * (theta_steps + 1)

    # 被控对象状态
    # τ/ξ 旧形：x1=输出，x2=输出导数
    # T1/T2 标准形：x1=第一惯性环节输出，x2=第二惯性环节输出（即 PV）
    x1 = 0.0
    x2 = 0.0
    x = 0.0  # FOPDT 状态变量

    # 导数函数在循环外定义（避免 B023 闭包警告）
    def _deriv_sopdt(state1: float, state2: float, u: float) -> tuple[float, float]:
        """SOPDT 旧形状态空间导数: x1' = x2, x2' = (-x1 - 2τξ*x2 + K*u) / τ²."""
        d1 = state2
        d2 = (-state1 - 2.0 * tau * xi * state2 + K * u) / tau_sq
        return d1, d2

    def _deriv_sopdt_t1t2(state1: float, state2: float, u: float) -> tuple[float, float]:
        """SOPDT 标准形导数（双一阶惯性串联）: x1' = (K*u - x1)/T1, x2' = (x1 - x2)/T2."""
        d1 = (K * u - state1) / sopdt_t1
        d2 = (state1 - state2) / sopdt_t2
        return d1, d2

    def _deriv_fopdt(state: float, u: float) -> float:
        """FOPDT 导数: dx/dt = (-x + K*u) / tau."""
        return (-state + K * u) / tau

    # SOPDT 按参数形选择状态方程（标准形优先）
    _deriv_sopdt_active = _deriv_sopdt_t1t2 if use_t1_t2 else _deriv_sopdt

    # 积分步长过大时自动细分子步，保证 RK4 精度（子步长 ≤ 最快时间常数/4）
    fastest_tc = min(sopdt_t1, sopdt_t2) if use_t1_t2 else tau
    n_sub = max(1, math.ceil(4.0 * sim_step / fastest_tc))
    h = sim_step / n_sub
    if n_sub > 1:
        logger.warning(
            "仿真步长 sim_step=%.4f 超过最快时间常数/4=%.4f，RK4 自动细分为 %d 子步",
            sim_step,
            fastest_tc / 4.0,
            n_sub,
        )

    for k in range(1, n_steps + 1):
        # 误差
        e = sp[k] - pv[k - 1]

        # 增量式 PID：比例/积分对误差，微分对测量值 PV（消除 SP 阶跃 derivative kick）
        d2_pv = pv[k - 1] - 2.0 * y_prev + y_prev2
        if pid.ti > 0:
            delta_u = pid.kp * ((e - e_prev) + sim_step / pid.ti * e - pid.td / sim_step * d2_pv)
        else:
            delta_u = pid.kp * ((e - e_prev) - pid.td / sim_step * d2_pv)

        op[k] = op[k - 1] + delta_u

        # 输出限幅
        op[k] = max(-100.0, min(100.0, op[k]))

        # 延迟队列
        op_delay_queue.append(op[k])
        if len(op_delay_queue) > theta_steps + 1:
            op_delay_queue.pop(0)
        delayed_op = op_delay_queue[0] if op_delay_queue else op[k]

        if is_sopdt:
            # SOPDT 标准形: G(s) = K·e^(-θs) / ((T1·s+1)(T2·s+1))（优先）
            # SOPDT 旧形:   G(s) = K·e^(-θs) / (τ²s² + 2τξs + 1)（兼容）
            # RK4 积分（必要时细分子步）
            for _ in range(n_sub):
                k1_1, k1_2 = _deriv_sopdt_active(x1, x2, delayed_op)
                k2_1, k2_2 = _deriv_sopdt_active(
                    x1 + 0.5 * h * k1_1, x2 + 0.5 * h * k1_2, delayed_op
                )
                k3_1, k3_2 = _deriv_sopdt_active(
                    x1 + 0.5 * h * k2_1, x2 + 0.5 * h * k2_2, delayed_op
                )
                k4_1, k4_2 = _deriv_sopdt_active(x1 + h * k3_1, x2 + h * k3_2, delayed_op)
                x1 = x1 + h / 6.0 * (k1_1 + 2 * k2_1 + 2 * k3_1 + k4_1)
                x2 = x2 + h / 6.0 * (k1_2 + 2 * k2_2 + 2 * k3_2 + k4_2)
            pv[k] = x2 if use_t1_t2 else x1
        else:
            # FOPDT: dx/dt = (-x + K*u) / tau
            # RK4 积分（必要时细分子步）
            for _ in range(n_sub):
                k1 = _deriv_fopdt(x, delayed_op)
                k2 = _deriv_fopdt(x + 0.5 * h * k1, delayed_op)
                k3 = _deriv_fopdt(x + 0.5 * h * k2, delayed_op)
                k4 = _deriv_fopdt(x + h * k3, delayed_op)
                x = x + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
            pv[k] = x

        # 更新误差与 PV 历史
        e_prev = e
        y_prev2 = y_prev
        y_prev = pv[k - 1]

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
