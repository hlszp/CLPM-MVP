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
        timestamps: 时间戳数组（秒）。**原点契约（P3-03）**：t=0 为 MV 阶跃时刻
            （生产链 ``tuning.identify_model`` 已按此传参）；阶跃前稳定基线
            保留为负时间。θ 一律从阶跃时刻起算，前基线时长不改 θ；
            AREA 法面积只在 t≥0（真实阶跃后响应）上积分。
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

    # P3-03 辨识原点：只处理真实阶跃后响应（t≥0 视图）。
    # 两点法/面积法的 θ 均以阶跃时刻为原点，窗口前基线（负时间）不进 θ；
    # 拟合优度只在阶跃后响应段上计算（前基线平坦段不虚增 R²）。
    pv_post, ts_post = _post_step_view(pv, ts)
    if pv_post is None or len(pv_post) < 10:
        return _fopdt_failure("阶跃时刻（t=0）后无有效响应数据（需 ≥10 点）")

    # 两点法
    result_two_point = _fopdt_two_point(pv_post, ts_post, pv_initial, pv_final, delta_pv)

    # 面积法
    result_area = _fopdt_area_method(pv_post, ts_post, pv_initial, pv_final, delta_pv, mv_step)

    # 选择方法（支持 TWO_POINT / AREA，未知方法默认两点法）
    if method == "AREA":
        params = result_area
    else:  # TWO_POINT 或未知方法默认两点法
        params = result_two_point

    if params["tau"] is None or params["theta"] is None:
        return _fopdt_failure(params["reason"] or "辨识失败")

    fitted_pv = _fopdt_simulate_curve(K, params["tau"], params["theta"], ts, pv_initial, mv_step)
    # 拟合优度只在阶跃后响应段评价（前基线平坦段两者都平坦，计入会虚增 R²）
    fitting_score = _calc_r2(pv_post, fitted_pv[ts >= 0.0])

    return {
        "K": round(K, 6),
        "tau": round(params["tau"], 4),
        "theta": round(params["theta"], 4),
        "fitting_score": round(fitting_score * 100, 2),
        "fitted_pv": fitted_pv.tolist(),
        "reason": None,
    }


def _post_step_view(
    pv: np.ndarray, ts: np.ndarray, step_time: float = 0.0
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """阶跃后视图：t ≥ step_time 的同步切片（辨识原点契约，P3-03）.

    Args:
        pv: PV 序列
        ts: 相对时间戳（原点=MV 阶跃时刻；前基线为负时间）
        step_time: 阶跃时刻（默认 0.0，与生产链传参一致）

    Returns:
        (pv_post, ts_post)；无 t ≥ step_time 的采样时返回 (None, None)。
    """
    mask = ts >= step_time
    if not bool(mask.any()):
        return None, None
    return pv[mask], ts[mask]


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
    """面积法：积分响应曲线下面积（P3-03：入参为**阶跃后视图** t≥0）.

    归一化面积 A1* 是一阶加纯滞后过程的平均驻留时间，即 A1* = τ + θ，
    因此 τ = A1* − θ（θ 取响应开始时间），避免滞后双重计入。
    θ 从阶跃时刻（ts[0]=0，原点契约）起算，不回看窗口前基线。
    失败时返回 {"tau": None, "theta": None, "reason": ...}。

    Args:
        pv: 阶跃后 PV 序列（t≥0；由 ``identify_fopdt`` 的 ``_post_step_view`` 切出）
        ts: 阶跃后相对时间戳（首点≈0，原点=MV 阶跃时刻）
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

    **原点契约（P3-03）**：t=0 为 MV 阶跃时刻，阶跃前基线为负时间；
    θ 从阶跃时刻起算（前基线时长不改 θ），斜率拟合与拟合优度只在
    真实阶跃后响应（t≥0）上进行。
    """
    n = len(pv_values)
    if n < 10 or mv_step == 0:
        return {"K": None, "theta": None, "fitting_score": 0.0, "fitted_pv": []}

    pv = np.array(pv_values, dtype=float)
    ts = np.array(timestamps, dtype=float)
    pv_initial = pv[0]

    # P3-03 辨识原点：只处理真实阶跃后响应
    pv_post, ts_post = _post_step_view(pv, ts)
    if pv_post is None or len(pv_post) < 10:
        return {
            "K": None,
            "theta": None,
            "fitting_score": 0.0,
            "fitted_pv": [],
            "reason": "阶跃时刻（t=0）后无有效响应数据（需 ≥10 点）",
        }

    # 找响应开始点 → theta（从阶跃时刻起算，不回看前基线）
    response_start_idx = _find_response_start(pv_post, pv_initial)
    theta = (
        float(ts_post[response_start_idx]) - float(ts_post[0]) if response_start_idx > 0 else 0.0
    )

    # 线性拟合响应段斜率 → K（只在阶跃后响应段）
    response_ts = ts_post[response_start_idx:] - ts_post[response_start_idx]
    response_pv = pv_post[response_start_idx:]
    if len(response_ts) > 2:
        # 线性回归: pv = slope * t + intercept
        slope = float(np.polyfit(response_ts, response_pv, 1)[0])
        K = slope / mv_step
    else:
        K = 1.0

    # 仿真拟合曲线（全窗输出供绘图；前基线段为平坦 pv_initial）
    fitted_pv = np.full_like(ts, pv_initial, dtype=float)
    for i, t in enumerate(ts):
        if t < theta:
            fitted_pv[i] = pv_initial
        else:
            fitted_pv[i] = pv_initial + K * mv_step * (t - theta)

    # 拟合优度只在阶跃后响应段评价（前基线平坦段计入会虚增 R²）
    fitting_score = _calc_r2(pv_post, fitted_pv[ts >= 0.0])

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
# 仿真动力学分派与工作量预算（P3-04）
# ---------------------------------------------------------------------------

#: 仿真引擎版本（P3-04：显式模型分派 + 扰动真实施加 + 预算 + 候选去重；
#: v1.0 = 旧行为：非 SOPDT 一律按 FOPDT 仿真、disturbance_type 形参被静默忽略）
SIMULATION_ENGINE_VERSION = "SIM_ENGINE_v2.0"

#: 闭环仿真支持的模型类型（显式分派；未知类型明确拒绝，禁止 FOPDT 冒充）
SUPPORTED_SIMULATION_MODELS: tuple[str, ...] = ("FOPDT", "SOPDT", "IPDT")

#: 候选去重相对容差（oracle 冻结草案 B-12：参数相对差 <1% 视为重复候选，
#: 候选清单保留全部原始条目 + 去重标记，重复项不重复仿真）
SIM_CANDIDATE_DEDUP_REL_TOL = 0.01


class SimulationError(ValueError):
    """仿真内核错误基类（参数/约束非法，明确失败而非静默降级）。"""


class SimulationNotSupportedError(SimulationError):
    """模型类型或参数不被仿真内核支持。

    P3-04 红线：IPDT 必须按真实积分动力学仿真或明确拒绝，
    未知模型类型/缺失关键参数禁止按 FOPDT 默认参数冒充。
    """


class SimulationBudgetError(SimulationError):
    """仿真工作量超出预算（明确拒绝，不静默截断）。

    Attributes:
        requested: 各预算维度的请求值（含实际子步需求/总积分步数估算）。
        limits: 命中的预算上限（SimWorkBudget.describe() 形）。
    """

    def __init__(self, message: str, *, requested: dict[str, Any], limits: dict[str, Any]):
        super().__init__(message)
        self.requested = requested
        self.limits = limits


@dataclass(frozen=True)
class SimWorkBudget:
    """仿真工作量预算（输入时间/步长/候选/子步四维 + 总积分步数）。"""

    max_duration_seconds: float
    max_steps: int
    max_candidates: int
    max_substeps_per_step: int
    max_total_integration_steps: int

    def describe(self) -> dict[str, Any]:
        return {
            "maxDurationSeconds": self.max_duration_seconds,
            "maxSteps": self.max_steps,
            "maxCandidates": self.max_candidates,
            "maxSubstepsPerStep": self.max_substeps_per_step,
            "maxTotalIntegrationSteps": self.max_total_integration_steps,
        }


#: 内核绝对上限（任何调用方传入的预算不可逾越的结构性护栏）
SIM_BUDGET_KERNEL = SimWorkBudget(
    max_duration_seconds=7200.0,
    max_steps=200_000,
    max_candidates=32,
    max_substeps_per_step=1_000,
    max_total_integration_steps=20_000_000,
)

#: 同步 API 预算（/tuning/simulate、/tuning/compare）：边界外明确拒绝并
#: 引导改用异步任务，保证预算边界不阻塞普通 API
SIM_BUDGET_SYNC = SimWorkBudget(
    max_duration_seconds=7200.0,
    max_steps=20_000,
    max_candidates=16,
    max_substeps_per_step=256,
    max_total_integration_steps=400_000,
)

#: 异步任务预算（tune_and_simulate_task）：比同步宽一档，
#: 墙钟由既有 Celery time_limit=120s / soft_time_limit=100s 兜底（复用现有设施）
SIM_BUDGET_ASYNC = SimWorkBudget(
    max_duration_seconds=7200.0,
    max_steps=100_000,
    max_candidates=24,
    max_substeps_per_step=512,
    max_total_integration_steps=4_000_000,
)


@dataclass(frozen=True)
class DisturbanceSpec:
    """仿真期负载扰动（施加于对象输入通道，与 OP 同单位）。

    P3-04：扰动真实进入仿真（旧实现 disturbance_type 形参被静默忽略）。
    step 型扰动在 ``time_seconds`` 时刻向对象输入叠加恒定幅值
    （u_total = delayed_op + d(t)，作用于传输时滞之后的对象动态入口）。
    """

    kind: str  # 目前仅 "STEP"
    amplitude: float
    time_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind.lower(),
            "amplitude": self.amplitude,
            "timeSeconds": self.time_seconds,
        }


def _resolve_disturbance(
    disturbance_type: str | None,
    amplitude: float | None,
    time_seconds: float | None,
    sim_duration: float,
) -> DisturbanceSpec | None:
    """解析扰动输入；none→None，step→显式规格，未知类型/非法值明确拒绝。"""
    kind = str(disturbance_type or "none").strip().lower()
    if kind == "none":
        return None
    if kind != "step":
        raise SimulationNotSupportedError(
            f"不支持的扰动类型: {disturbance_type!r}（支持 none/step）"
        )
    amp = 0.5 if amplitude is None else float(amplitude)
    if not math.isfinite(amp) or amp <= 0:
        raise SimulationError(f"扰动幅值必须为正数，当前 {amp}")
    t = 0.4 * sim_duration if time_seconds is None else float(time_seconds)
    if not (math.isfinite(t) and 0.0 < t < sim_duration):
        raise SimulationError(f"扰动施加时间必须位于 (0, {sim_duration}s) 内，当前 {t}s")
    return DisturbanceSpec(kind="STEP", amplitude=amp, time_seconds=t)


def _relative_pid_difference(a: PIDParams, b: PIDParams) -> float:
    """两组 PID 参数的最大相对差（kp/ti/td 逐项，同号同值→0）。"""

    def _rel(x: float, y: float) -> float:
        if x == y:
            return 0.0
        denom = max(abs(x), abs(y))
        return abs(x - y) / denom if denom > 0 else 0.0

    return max(_rel(a.kp, b.kp), _rel(a.ti, b.ti), _rel(a.td, b.td))


class _PlantCore:
    """被控对象显式分派内核（FOPDT/SOPDT/IPDT 共用，不建第二仿真器）。

    - FOPDT: G(s) = K·e^{-θs}/(τs+1)，状态 [x]
    - SOPDT 标准形（T1/T2 成对）: G(s) = K·e^{-θs}/((T1·s+1)(T2·s+1))，状态 [x1,x2]
      （PV=x2）；旧 τ/ξ 形 G(s) = K·e^{-θs}/(τ²s²+2τξs+1) 兼容（PV=x1）
    - IPDT: G(s) = K·e^{-θs}/s **真实积分动力学**，状态 [x]，
      dx/dt = K·u（恒定输入 → 线性趋势；RK4 对恒输入积分器精确）
    - 未知模型类型 / IPDT 缺 K → SimulationNotSupportedError（禁止默认参数冒充）
    """

    __slots__ = ("kind", "K", "tau", "xi", "t1", "t2", "theta", "tau_sq", "fastest_tc")

    def __init__(self, model_type: str, model_params: dict[str, Any]):
        kind = str(model_type or "").strip().upper()
        if kind not in SUPPORTED_SIMULATION_MODELS:
            raise SimulationNotSupportedError(
                f"不支持的模型类型 {model_type!r}（仿真仅支持 "
                f"{'/'.join(SUPPORTED_SIMULATION_MODELS)}，明确拒绝而非按 FOPDT 冒充）"
            )
        params = model_params or {}
        self.kind = kind

        if kind == "IPDT":
            k_raw = params.get("K")
            if not isinstance(k_raw, int | float) or not math.isfinite(float(k_raw)):
                raise SimulationNotSupportedError(
                    "IPDT 模型缺少有限积分增益 K（禁止默认值冒充积分动力学）"
                )
            self.K = float(k_raw)
            self.theta = max(0.0, float(params.get("theta", 0.0) or 0.0))
            self.tau = 0.0
            self.xi = 0.0
            self.t1 = 0.0
            self.t2 = 0.0
            self.tau_sq = 0.0
            self.fastest_tc: float | None = None  # 积分器无时间常数，RK4 精确
            return

        # FOPDT / SOPDT：沿用既有参数回退（兼容历史调用形状）
        self.K = float(params.get("K", 1.0))
        self.tau = float(params.get("tau", 30.0))
        self.theta = float(params.get("theta", 5.0))
        if self.tau <= 0:
            self.tau = 1.0
        if self.theta < 0:
            self.theta = 0.0
        self.xi = float(params.get("xi", 1.0)) if kind == "SOPDT" else 0.0
        if self.xi < 0:
            self.xi = 0.0
        self.t1 = 0.0
        self.t2 = 0.0
        t1_raw = params.get("T1") if kind == "SOPDT" else None
        t2_raw = params.get("T2") if kind == "SOPDT" else None
        if t1_raw is not None and t2_raw is not None:
            # T1/T2 ≤ 0 箝位为 1.0（P1-3 既有契约，与 tau ≤ 0 → 1.0 同口径）
            self.t1 = float(t1_raw)
            self.t2 = float(t2_raw)
            if self.t1 <= 0:
                self.t1 = 1.0
            if self.t2 <= 0:
                self.t2 = 1.0
        self.tau_sq = self.tau * self.tau if kind == "SOPDT" else 1.0
        if kind == "SOPDT" and t1_raw is not None and t2_raw is not None:
            self.fastest_tc = min(self.t1, self.t2)
        else:
            self.fastest_tc = self.tau

    @property
    def dispatch_kind(self) -> str:
        """分派标识（SOPDT 细分标准形/旧形，供上下文记录）。"""
        if self.kind == "SOPDT":
            return "SOPDT_T1T2" if (self.t1 > 0 and self.t2 > 0) else "SOPDT_LEGACY"
        return self.kind

    def substeps_for(self, sim_step: float) -> int:
        """积分步长过大时自动细分子步（子步长 ≤ 最快时间常数/4）。

        IPDT 无时间常数（RK4 对恒输入积分器精确）→ 1 子步。
        """
        if self.fastest_tc is None:
            return 1
        n_sub = max(1, math.ceil(4.0 * sim_step / self.fastest_tc))
        if n_sub > 1:
            logger.warning(
                "仿真步长 sim_step=%.4f 超过最快时间常数/4=%.4f，RK4 自动细分为 %d 子步",
                sim_step,
                self.fastest_tc / 4.0,
                n_sub,
            )
        return n_sub

    def derivs(self, state: list[float], u: float) -> list[float]:
        """状态导数（u 为对象输入 = OP(经时滞) + 扰动）。"""
        if self.kind == "IPDT":
            return [self.K * u]
        if self.kind == "SOPDT":
            if self.t1 > 0 and self.t2 > 0:
                d1 = (self.K * u - state[0]) / self.t1
                d2 = (state[0] - state[1]) / self.t2
                return [d1, d2]
            d1 = state[1]
            d2 = (-state[0] - 2.0 * self.tau * self.xi * state[1] + self.K * u) / self.tau_sq
            return [d1, d2]
        # FOPDT
        return [(-state[0] + self.K * u) / self.tau]

    def rk4_step(self, state: list[float], u: float, h: float) -> list[float]:
        """RK4 单步（保持既有各分量运算次序）。"""
        k1 = self.derivs(state, u)
        k2 = self.derivs([s + 0.5 * h * d for s, d in zip(state, k1, strict=True)], u)
        k3 = self.derivs([s + 0.5 * h * d for s, d in zip(state, k2, strict=True)], u)
        k4 = self.derivs([s + h * d for s, d in zip(state, k3, strict=True)], u)
        return [
            s + h / 6.0 * (a + 2.0 * b + 2.0 * c + d)
            for s, a, b, c, d in zip(state, k1, k2, k3, k4, strict=True)
        ]

    def initial_state(self) -> list[float]:
        """零初始状态（FOPDT/IPDT 单态，SOPDT 双态）。"""
        if self.kind == "SOPDT":
            return [0.0, 0.0]
        return [0.0]

    def pv_of(self, state: list[float]) -> float:
        if self.kind == "SOPDT":
            return state[1] if (self.t1 > 0 and self.t2 > 0) else state[0]
        return state[0]


def _check_simulation_budget(
    *,
    budget: SimWorkBudget,
    sim_duration: float,
    n_steps: int,
    n_sub: int,
    n_responses: int,
    n_candidates_requested: int,
) -> int:
    """工作量预算核验：超限明确拒绝（requested/limits 随异常携带）。

    Returns:
        总积分步数估算 = n_steps × n_sub × n_responses。
    """
    requested: dict[str, Any] = {
        "durationSeconds": float(sim_duration),
        "steps": int(n_steps),
        "substepsPerStep": int(n_sub),
        "responses": int(n_responses),
        "candidatesRequested": int(n_candidates_requested),
    }
    total = int(n_steps) * int(n_sub) * int(n_responses)
    requested["totalIntegrationSteps"] = total
    violations: list[str] = []
    if sim_duration > budget.max_duration_seconds:
        violations.append(f"仿真时长 {sim_duration}s 超出上限 {budget.max_duration_seconds}s")
    if n_steps > budget.max_steps:
        violations.append(f"仿真步数 {n_steps} 超出上限 {budget.max_steps}")
    if n_candidates_requested > budget.max_candidates:
        violations.append(f"候选 PID 数 {n_candidates_requested} 超出上限 {budget.max_candidates}")
    if n_sub > budget.max_substeps_per_step:
        violations.append(
            f"单步 RK 子步需求 {n_sub} 超出上限 {budget.max_substeps_per_step}"
            "（步长相对对象最快时间常数过小，请增大步长）"
        )
    if total > budget.max_total_integration_steps:
        violations.append(f"总积分步数估算 {total} 超出上限 {budget.max_total_integration_steps}")
    if violations:
        raise SimulationBudgetError(
            "仿真工作量超出预算："
            + "；".join(violations)
            + "。请降低时长/增大步长/减少候选，或改用异步整定任务（预算更宽）",
            requested=requested,
            limits=budget.describe(),
        )
    return total


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
    disturbance_type: str = "none",
    pid_candidates: list[tuple[str, PIDParams]] | None = None,
    *,
    disturbance_amplitude: float | None = None,
    disturbance_time: float | None = None,
    budget: SimWorkBudget | None = None,
) -> dict[str, Any]:
    """闭环仿真（P3-04：模型显式分派 + 扰动真实施加 + 预算 + 候选去重）。

    使用增量式 PID + FOPDT/SOPDT/IPDT 被控对象 + 四阶 Runge-Kutta 积分。

    Args:
        disturbance_type: 扰动类型 none/step。**P3-04 行为变更**：默认 "none"
            （旧行为 "step" 形参被静默忽略=等效无扰动，默认曲线值不变）；
            显式传 "step" 时负载阶跃扰动真实施加于对象输入。
        disturbance_amplitude: step 扰动幅值（OP 同单位，须 >0；缺省 0.5）。
        disturbance_time: step 扰动施加时刻（秒，须在 (0, sim_duration) 内；
            缺省 0.4×sim_duration）。
        pid_candidates: Phase 2 新增 — 多组候选 PID（label + PIDParams），
            额外仿真每组并返回 candidateResponses（向后兼容，None 时不含）。
            P3-04：参数相对差 <1%（B-12）的候选去重标记，不重复仿真。
        budget: 工作量预算（None=内核绝对上限；同步 API 传 SIM_BUDGET_SYNC、
            异步任务传 SIM_BUDGET_ASYNC）。超限抛 SimulationBudgetError，
            明确拒绝不静默截断。

    Returns:
        包含 timestamps/currentResponse/recommendedResponse/currentMetrics/
        recommendedMetrics/improvement/simulationContext 七个键的字典；
        pid_candidates 非空时额外含 candidateResponses（每项带 deduplicated/
        duplicateOf 去重标记）。

    Raises:
        SimulationNotSupportedError: 未知模型类型 / IPDT 缺积分增益 K /
            未知扰动类型（明确拒绝，禁止按 FOPDT 冒充）。
        SimulationBudgetError: 工作量超出预算。
    """
    sim_duration = float(sim_duration)
    sim_step = float(sim_step)
    if sim_step <= 0:
        raise SimulationError(f"仿真步长必须为正数，当前 {sim_step}")
    if sim_duration <= 0:
        raise SimulationError(f"仿真时长必须为正数，当前 {sim_duration}")
    n_steps = int(sim_duration / sim_step)
    if n_steps < 2:
        raise SimulationError(
            f"仿真时长须至少覆盖 2 个步长（duration={sim_duration}, step={sim_step}）"
        )

    # 模型显式分派（未知类型在此明确拒绝）
    plant = _PlantCore(model_type, model_params)
    # 扰动解析（未知类型/非法值在此明确拒绝）
    disturbance = _resolve_disturbance(
        disturbance_type, disturbance_amplitude, disturbance_time, sim_duration
    )
    n_sub = plant.substeps_for(sim_step)

    # 候选去重（B-12：<1% 相对差视为重复，保留全部原始条目+标记）
    requested_candidates = list(pid_candidates or [])
    duplicate_of: dict[str, str] = {}
    unique_candidates: list[tuple[str, PIDParams]] = []
    for label, pid in requested_candidates:
        representative = next(
            (
                kept_label
                for kept_label, kept_pid in unique_candidates
                if _relative_pid_difference(pid, kept_pid) < SIM_CANDIDATE_DEDUP_REL_TOL
            ),
            None,
        )
        if representative is None:
            unique_candidates.append((label, pid))
        else:
            duplicate_of[label] = representative

    eff_budget = budget or SIM_BUDGET_KERNEL
    n_responses = 2 + len(unique_candidates)
    total_work = _check_simulation_budget(
        budget=eff_budget,
        sim_duration=sim_duration,
        n_steps=n_steps,
        n_sub=n_sub,
        n_responses=n_responses,
        n_candidates_requested=len(requested_candidates),
    )

    # 生成时间序列
    timestamps = [i * sim_step for i in range(n_steps + 1)]

    # 仿真当前 PID
    current_response = _simulate_pid_response(
        plant,
        current_pid,
        n_steps,
        sim_step,
        setpoint_step,
        disturbance,
    )

    # 仿真推荐 PID
    recommended_response = _simulate_pid_response(
        plant,
        recommended_pid,
        n_steps,
        sim_step,
        setpoint_step,
        disturbance,
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

    # Phase 2：多组候选 PID 仿真对比（P3-04：去重候选复用代表响应）
    if pid_candidates:
        # 已仿真响应按 label 索引（重复项引用代表项的响应副本）
        unique_responses: dict[str, dict[str, list[float]]] = {}
        candidate_responses: list[dict[str, Any]] = []
        for label, pid in unique_candidates:
            resp = _simulate_pid_response(
                plant,
                pid,
                n_steps,
                sim_step,
                setpoint_step,
                disturbance,
            )
            unique_responses[label] = resp
            metrics = _extract_metrics(resp, setpoint_step, sim_step)
            candidate_responses.append(
                {
                    "label": label,
                    "response": resp,
                    "metrics": _metrics_to_dict(metrics),
                    "deduplicated": False,
                }
            )
        for label, rep_label in duplicate_of.items():
            rep_resp = unique_responses[rep_label]
            candidate_responses.append(
                {
                    "label": label,
                    "response": {k: list(v) for k, v in rep_resp.items()},
                    "metrics": None,  # 延后按代表项指标填充
                    "deduplicated": True,
                    "duplicateOf": rep_label,
                }
            )
        if duplicate_of:
            rep_metrics = {
                c["label"]: c["metrics"] for c in candidate_responses if not c["deduplicated"]
            }
            for entry in candidate_responses:
                if entry["deduplicated"]:
                    entry["metrics"] = rep_metrics[entry["duplicateOf"]]
        # 保持原始请求顺序输出（保留全部原始候选 + 去重标记）
        order = {label: i for i, (label, _pid) in enumerate(requested_candidates)}
        candidate_responses.sort(key=lambda c: order.get(c["label"], len(order)))
        result["candidateResponses"] = candidate_responses

    # P3-04：仿真上下文（模型分派/扰动/去重/预算随结果携带，异步链可追溯）
    result["simulationContext"] = {
        "engineVersion": SIMULATION_ENGINE_VERSION,
        "modelType": plant.kind,
        "modelDispatch": plant.dispatch_kind,
        "modelParams": dict(model_params or {}),
        "simDuration": sim_duration,
        "simStep": sim_step,
        "setpointStep": float(setpoint_step),
        "disturbance": disturbance.to_dict() if disturbance is not None else None,
        "candidates": {
            "requested": len(requested_candidates),
            "simulated": len(unique_candidates),
            "duplicates": dict(duplicate_of),
        },
        "budget": {
            "steps": n_steps,
            "substepsPerStep": n_sub,
            "responses": n_responses,
            "totalIntegrationSteps": total_work,
            "limits": eff_budget.describe(),
        },
    }

    return result


def simulate_plant_open_loop(
    model_type: str,
    model_params: dict[str, Any],
    op_level: float,
    sim_duration: float,
    sim_step: float = 1.0,
    disturbance_type: str = "none",
    *,
    disturbance_amplitude: float | None = None,
    disturbance_time: float | None = None,
    budget: SimWorkBudget | None = None,
) -> dict[str, Any]:
    """开环对象响应（恒定 OP + 可选负载扰动）——P3-04 C20 积分动力学核验入口。

    与闭环仿真共用同一 ``_PlantCore`` 分派（不建第二仿真器）：
    u_total(t) = op_level + d(t)，恒定 OP 经时滞队列后进入对象动力学。

    IPDT 恒定输入 → 线性趋势（dx/dt = K·u，RK4 对恒输入积分器精确）。

    Returns:
        {"timestamps": [...], "op": [...], "pv": [...]}（op 为含扰动的对象输入）。

    Raises:
        SimulationNotSupportedError / SimulationError / SimulationBudgetError：同闭环。
    """
    sim_duration = float(sim_duration)
    sim_step = float(sim_step)
    op_level = float(op_level)
    if sim_step <= 0:
        raise SimulationError(f"仿真步长必须为正数，当前 {sim_step}")
    if sim_duration <= 0:
        raise SimulationError(f"仿真时长必须为正数，当前 {sim_duration}")
    n_steps = int(sim_duration / sim_step)
    if n_steps < 1:
        raise SimulationError(
            f"仿真时长须至少覆盖 1 个步长（duration={sim_duration}, step={sim_step}）"
        )
    plant = _PlantCore(model_type, model_params)
    disturbance = _resolve_disturbance(
        disturbance_type, disturbance_amplitude, disturbance_time, sim_duration
    )
    n_sub = plant.substeps_for(sim_step)
    eff_budget = budget or SIM_BUDGET_KERNEL
    _check_simulation_budget(
        budget=eff_budget,
        sim_duration=sim_duration,
        n_steps=n_steps,
        n_sub=n_sub,
        n_responses=1,
        n_candidates_requested=0,
    )

    theta_steps = max(0, int(round(plant.theta / sim_step)))
    # 恒定 OP 从 t=0 注入（与闭环同一时滞队列语义：t<θ 时对象输入=0，
    # θ 之后对象输入=op_level；PV 零初始）
    op_delay_queue: list[float] = [0.0] * (theta_steps + 1)
    state = plant.initial_state()
    pv = [0.0] * (n_steps + 1)
    op_out = [0.0] * (n_steps + 1)
    h = sim_step / n_sub

    for k in range(1, n_steps + 1):
        t = k * sim_step
        # 与闭环同一时滞队列语义：先入队再读队首
        op_delay_queue.append(op_level)
        if len(op_delay_queue) > theta_steps + 1:
            op_delay_queue.pop(0)
        delayed_op = op_delay_queue[0]
        d_k = (
            disturbance.amplitude
            if (disturbance is not None and t >= disturbance.time_seconds)
            else 0.0
        )
        u_total = delayed_op + d_k
        for _ in range(n_sub):
            state = plant.rk4_step(state, u_total, h)
        pv[k] = plant.pv_of(state)
        op_out[k] = u_total

    return {
        "timestamps": [i * sim_step for i in range(n_steps + 1)],
        "op": op_out,
        "pv": pv,
        "simulationContext": {
            "engineVersion": SIMULATION_ENGINE_VERSION,
            "modelType": plant.kind,
            "modelDispatch": plant.dispatch_kind,
            "openLoop": True,
        },
    }


def _simulate_pid_response(
    plant: _PlantCore,
    pid: PIDParams,
    n_steps: int,
    sim_step: float,
    setpoint_step: float,
    disturbance: DisturbanceSpec | None,
) -> dict[str, list[float]]:
    """仿真单组 PID 的阶跃响应（P3-04：对象动力学由 _PlantCore 显式分派）。

    plant 契约（_PlantCore）：FOPDT/SOPDT_T1T2/SOPDT_LEGACY/IPDT；
    IPDT 为真实积分动力学（dx/dt = K·u），非 FOPDT 冒充。
    disturbance 非 None 时负载扰动真实进入对象输入。

    SOPDT model_params 契约：优先标准形 {K, T1, T2, theta}（T1/T2 成对出现才生效，
    与两条辨识路径输出一致）；否则回退旧 τ/ξ 形 {K, tau, xi, theta}。
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

    # 死区步数与延迟队列
    theta_steps = max(0, int(round(plant.theta / sim_step)))
    op_delay_queue: list[float] = [0.0] * (theta_steps + 1)

    # 被控对象状态（FOPDT/IPDT 用 [x]，SOPDT 用 [x1, x2]）
    state = plant.initial_state()

    # 子步长（步长过大时细分，子步长 ≤ 最快时间常数/4；IPDT 恒 1 子步）
    n_sub = plant.substeps_for(sim_step)
    h = sim_step / n_sub

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

        # P3-04：扰动真实进入对象输入（负载扰动叠加于 OP 时滞之后）
        t = k * sim_step
        d_k = (
            disturbance.amplitude
            if (disturbance is not None and t >= disturbance.time_seconds)
            else 0.0
        )
        u_total = delayed_op + d_k

        # RK4 积分（必要时细分子步；模型动力学由 plant 显式分派）
        for _ in range(n_sub):
            state = plant.rk4_step(state, u_total, h)
        pv[k] = plant.pv_of(state)

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
# 整定方法信息（P2-01 TUN-05：算法参数注册表，schema/服务端校验单源）
# ---------------------------------------------------------------------------


TUNING_METHODS_INFO: list[dict[str, Any]] = [
    {
        "code": "IMC",
        "name": "IMC 内模控制",
        "description": "基于内模控制原理的 PID 整定，平衡性能与鲁棒性",
        "applicableModel": "FOPDT",
        "params": [
            {
                "name": "lambdaRatio",
                "label": "λ 比例系数",
                "type": "float",
                "unit": "",
                "description": "λ = lambdaRatio × θ（闭环时间常数相对纯滞后的倍数）",
                "default": 1.0,
                "min": 0.1,
                "max": 5.0,
                "kind": "business",
                "risk": "MEDIUM",
                "riskNote": "λ 越大越保守、越小越激进",
            },
        ],
    },
    {
        "code": "LAMBDA",
        "name": "Lambda 整定",
        "description": "基于期望闭环时间常数的整定方法，适合一阶自调节过程",
        "applicableModel": "FOPDT",
        "params": [
            {
                "name": "lambdaRatio",
                "label": "λ 比例系数",
                "type": "float",
                "unit": "",
                "description": "λ = lambdaRatio × τ（闭环时间常数相对过程时间常数的倍数）",
                "default": 1.0,
                "min": 0.1,
                "max": 5.0,
                "kind": "business",
                "risk": "MEDIUM",
                "riskNote": "λ 越大越保守、越小越激进",
            },
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
                "type": "enum",
                "unit": "",
                "description": "P/PI/PID 结构选择（公式系数按结构分派）",
                "default": "PID",
                "options": ["P", "PI", "PID"],
                "kind": "business",
                "risk": "HIGH",
                "riskNote": "控制器结构决定 P/I/D 三项的有无与系数",
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
                "type": "enum",
                "unit": "",
                "description": "P/PI/PID 结构选择（公式系数按结构分派）",
                "default": "PID",
                "options": ["P", "PI", "PID"],
                "kind": "business",
                "risk": "HIGH",
                "riskNote": "控制器结构决定 P/I/D 三项的有无与系数",
            },
        ],
    },
    {
        "code": "SIMC",
        "name": "SIMC 简化 IMC",
        "description": "Skogestad 简化整定规则，工程实用性强",
        "applicableModel": "FOPDT",
        "params": [
            {
                "name": "tauCRatio",
                "label": "τc 比例系数",
                "type": "float",
                "unit": "",
                "description": "τc = tauCRatio × θ（期望闭环时间常数相对纯滞后的倍数）",
                "default": 1.0,
                "min": 0.1,
                "max": 5.0,
                "kind": "business",
                "risk": "MEDIUM",
                "riskNote": "τc 越大越保守、越小越激进",
            },
        ],
    },
]


def get_method_info(algorithm: str | None) -> list[dict[str, Any]]:
    """按算法代码取注册信息（algorithm=None 返回全部，用于矩阵联合校验）."""
    if algorithm is None:
        return TUNING_METHODS_INFO
    return [m for m in TUNING_METHODS_INFO if m["code"] == algorithm]


def validate_tuning_params(
    algorithm: str | None,
    params: dict[str, Any],
) -> list[str]:
    """按 TUNING_METHODS_INFO 注册表校验整定算法参数（P2-01 TUN-05）.

    返回错误文案列表（空列表 = 通过）：
    - 未知键：键不在指定算法（algorithm=None 时为任一算法）的注册参数内
    - 类型：float 参数必须为有限数值（布尔/字符串拒绝）；enum 参数必须为字符串
    - 范围：min/max 闭区间
    - 非法枚举：enum 值不在 options 内（如非法 controllerType）

    Args:
        algorithm: 算法代码（IMC/LAMBDA/ZN/COHEN_COON/SIMC）；None = 联合模式
            （/tune/matrix 共用一份参数跨 5 算法，键在任一算法注册即合法，
            值须满足其所属算法的约束）
        params: 待校验参数字典
    """
    methods = get_method_info(algorithm)
    if not methods:
        return [f"不支持的整定算法: {algorithm}"]
    # 键 → 定义合并（联合模式下同名键取并集内最先定义；当前注册同名键约束一致）
    registry: dict[str, dict[str, Any]] = {}
    for m in methods:
        for p in m.get("params", []):
            registry.setdefault(p["name"], p)

    errors: list[str] = []
    for key, value in params.items():
        p = registry.get(key)
        if p is None:
            errors.append(
                f"未知算法参数键: {key}（算法 {algorithm or '全算法矩阵'} 注册键: "
                f"{sorted(registry)}）"
            )
            continue
        ptype = p.get("type", "float")
        if ptype == "enum":
            options = p.get("options", [])
            if not isinstance(value, str):
                errors.append(
                    f"参数 {key} 应为字符串枚举（{options}），收到 {type(value).__name__}"
                )
            elif value not in options:
                errors.append(f"参数 {key}={value} 非法，合法值: {options}")
            continue
        # 数值型
        if isinstance(value, bool):
            errors.append(f"参数 {key} 应为数值，收到布尔值")
            continue
        if not isinstance(value, (int, float)):
            errors.append(f"参数 {key} 应为数值，收到 {type(value).__name__}")
            continue
        if not math.isfinite(value):
            errors.append(f"参数 {key}={value} 不是有限数（NaN/Inf 拒绝）")
            continue
        if "min" in p and value < p["min"]:
            errors.append(f"参数 {key}={value} 低于下限 {p['min']}")
        if "max" in p and value > p["max"]:
            errors.append(f"参数 {key}={value} 超过上限 {p['max']}")
    return errors


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
    "get_method_info",
    "validate_tuning_params",
]
