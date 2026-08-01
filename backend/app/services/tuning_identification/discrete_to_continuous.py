"""离散→连续参数转换（算法栈层 5）.

将离散 ARX/IV/ARMAX 参数转换为连续时间 FOPDT/SOPDT 模型参数，
输出 K/tau/theta 或 K/T1/T2/theta，对齐现有 tune_imc/lambda/zn 等整定公式输入。
"""

from __future__ import annotations

import logging
import math

from app.services.tuning_identification.types import ModelParams, ModelType

logger = logging.getLogger(__name__)


def arx_to_fopdt(
    a1: float,
    b1: float,
    d: int,
    ts: float = 1.0,
) -> ModelParams:
    """ARX 一阶离散参数转 FOPDT 连续参数.

    y(t) + a1*y(t-1) = b1*u(t-d) 对应 G(s) = K*exp(-theta*s)/(tau*s+1)

    转换公式：
        tau = -Ts / ln(-a1)    （要求 a1 < 0 即稳定系统）
        K = b1 / (1 + a1)
        theta = d * Ts
    """
    if a1 >= 0:
        raise ValueError(f"a1={a1} >= 0，无法转 FOPDT（要求稳定系统 a1<0）")
    if abs(1 + a1) < 1e-12:
        raise ValueError(f"1+a1={1 + a1} 接近零，K 计算发散")
    ln_neg_a1 = math.log(-a1)
    if abs(ln_neg_a1) < 1e-12:
        raise ValueError(f"ln(-a1)={ln_neg_a1} 接近零，tau 计算发散")
    tau = -ts / ln_neg_a1
    K = b1 / (1 + a1)
    theta = d * ts
    if tau <= 0:
        raise ValueError(f"tau={tau} <= 0，模型不稳定")
    if K == 0:
        raise ValueError("K=0，过程增益为零")
    return ModelParams(
        model_type=ModelType.FOPDT,
        K=K,
        tau=tau,
        theta=theta,
    )


def arx_to_sopdt(
    a1: float,
    a2: float,
    b1: float,
    d: int,
    ts: float = 1.0,
) -> ModelParams:
    """ARX 二阶离散参数转 SOPDT 连续参数.

    y(t) + a1*y(t-1) + a2*y(t-2) = b1*u(t-d)
    对应 G(s) = K*exp(-theta*s)/((T1*s+1)(T2*s+1))

    转换：
    1. 离散极点 p1, p2 = roots([1, a1, a2])
    2. 连续极点 s_i = ln(p_i)/Ts
    3. T_i = -1/s_i（要求 s_i < 0 即稳定）
    4. K = b1 / (1 + a1 + a2)
    5. theta = d * Ts
    """
    if abs(1 + a1 + a2) < 1e-12:
        raise ValueError("1+a1+a2 接近零，K 计算发散")
    K = b1 / (1 + a1 + a2)
    theta = d * ts
    # P2-012：复极点检测 —— 共轭复极点表示振荡（欠阻尼）系统，
    # SOPDT 仅适用于过阻尼（两个实极点）系统，不得取模伪装为实极点。
    disc = a1 * a1 - 4.0 * a2
    if disc < 0:
        raise ValueError(
            f"复共轭极点（disc={disc:.4g} < 0，振荡/欠阻尼系统），"
            "SOPDT 仅适用于过阻尼系统；请改用 FOPDT 或振荡模型"
        )
    # 离散极点
    roots = _solve_quadratic(1.0, a1, a2)
    if roots is None:
        raise ValueError("二阶系统极点求解失败")
    p1, p2 = roots
    # 连续极点
    s1 = math.log(abs(p1)) / ts if p1 != 0 else float("-inf")
    s2 = math.log(abs(p2)) / ts if p2 != 0 else float("-inf")
    if s1 >= 0 or s2 >= 0:
        raise ValueError(f"连续极点 s1={s1}, s2={s2} 非负，系统不稳定")
    T1 = -1.0 / s1
    T2 = -1.0 / s2
    if T1 <= 0 or T2 <= 0:
        raise ValueError(f"T1={T1}, T2={T2} <= 0")
    if K == 0:
        raise ValueError("K=0，过程增益为零")
    return ModelParams(
        model_type=ModelType.SOPDT,
        K=K,
        T1=T1,
        T2=T2,
        theta=theta,
    )


def _solve_quadratic(a: float, b: float, c: float) -> tuple[float, float] | None:
    """求解二次方程 a*x^2 + b*x + c = 0（仅实根，复根由调用方提前检测）."""
    if abs(a) < 1e-12:
        if abs(b) < 1e-12:
            return None
        x = -c / b
        return (x, x)
    disc = b * b - 4 * a * c
    if disc < 0:
        # 复根 —— 不应到达此处（arx_to_sopdt 已提前检测并 raise）
        return None
    sqrt_disc = math.sqrt(disc)
    x1 = (-b + sqrt_disc) / (2 * a)
    x2 = (-b - sqrt_disc) / (2 * a)
    return (x1, x2)
