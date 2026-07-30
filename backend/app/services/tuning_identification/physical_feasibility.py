"""物理可行性门禁（P2-012）.

防止数值辨识产出物理不可行的模型被当作稳定工程模型输出：

- 负增益 K（逆向过程 / 符号错误）—— 允许但标记并封顶可信度，需人工复核
- 非最小相位零点（RHP 零点 / 逆向响应）—— 允许但标记并封顶可信度
- 不稳定极点 / 复共轭极点 —— 在离散→连续转换阶段已拒绝（raise ValueError），
  本模块不再重复检查，只对转换成功后的参数做增益符号与零点检查

设计原则：负增益与 NMP 零点在物理上是可能的（逆向作用过程、液位假响应等），
不应直接拒绝，但绝不能伪装成正常正增益最小相位模型静默放行。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from app.services.tuning_identification.types import ModelParams

logger = logging.getLogger(__name__)


@dataclass
class PhysicalFeasibilityResult:
    """物理可行性检查结果."""

    passed: bool
    reason_code: str  # OK / NEGATIVE_GAIN / NMP_ZERO / UNSTABLE / COMPLEX_POLES
    details: str = ""


def check_physical_feasibility(
    params: ModelParams,
    b_coeffs: list[float],
    ts: float,
) -> PhysicalFeasibilityResult:
    """检查转换后模型参数的物理可行性.

    极点稳定性与复极点检查在 discrete_to_continuous 转换阶段完成（raise）；
    本函数只检查转换成功后的增益符号与 B 多项式零点。

    Args:
        params: 转换后的连续模型参数
        b_coeffs: ARX/ARMAX B 多项式系数 [b1, b2, ...]
        ts: 采样周期（秒）

    Returns:
        PhysicalFeasibilityResult
    """
    # 1. 负增益检查 —— 逆向过程或符号错误，标记但允许（封顶可信度由 pipeline 处理）
    if params.K < 0:
        return PhysicalFeasibilityResult(
            passed=False,
            reason_code="NEGATIVE_GAIN",
            details=f"K={params.K:.4g} < 0，过程增益为负（逆向过程或符号错误）",
        )

    # 2. 非最小相位零点检查（B 多项式阶次 > 1 时才有零点）
    # nb=1 时 B(z⁻¹)=b1·z⁻¹ 无有限零点，跳过
    if len(b_coeffs) > 1:
        nmp = _has_nmp_zero(b_coeffs, ts)
        if nmp is not None:
            return PhysicalFeasibilityResult(
                passed=False,
                reason_code="NMP_ZERO",
                details=f"RHP 零点 s={nmp:.4g}，非最小相位（逆向响应）",
            )

    return PhysicalFeasibilityResult(passed=True, reason_code="OK")


def _has_nmp_zero(b_coeffs: list[float], ts: float) -> float | None:
    """检测 B 多项式是否有右半平面（连续）零点.

    B(z⁻¹) = b1 + b2·z⁻¹ + ... + bn·z⁻(n-1)
    离散零点 z_i 由 b1·z^(n-1) + b2·z^(n-2) + ... + bn = 0 求解
    连续零点 s_i = ln(z_i)/ts；RHP 零点 s_i > 0 即非最小相位。

    Returns:
        RHP 连续零点值（>0），无 RHP 零点时返回 None
    """
    n = len(b_coeffs)
    if n < 2:
        return None
    # 构造多项式系数（numpy roots 接受最高次在前）
    # b1·z^(n-1) + b2·z^(n-2) + ... + bn = 0
    poly = list(b_coeffs)
    try:
        import numpy as np

        zeros = np.roots(poly)
    except Exception:
        return None
    for z in zeros:
        if abs(z) < 1e-12:
            continue
        s = math.log(abs(z)) / ts
        if s > 0:
            return float(s)
    return None
