"""激励检测与片段筛选（算法栈层 1）.

防止"垃圾进垃圾出"：无激励数据直接返回 INCONCLUSIVE，
不硬辨（硬辨会输出虚假模型误导整定）。
"""

from __future__ import annotations

import logging
import math

import numpy as np

from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    ExcitationCheckResult,
)

logger = logging.getLogger(__name__)

# 阈值（可后续迁入 algorithm_config 配置体系）
# V62-P1-010: 死区过滤微噪声后，真实方向变化数暴露；闭环 SP 阶跃同向时
# OP 方向变号可能仅 1 次，阈值从 2 降为 1（至少 1 次变号证明非纯单调）。
_MIN_DIRECTION_CHANGES = 1  # 最少方向变化次数（OP 非单调）
_COND_NUMBER_OK = 1e4  # PE 条件数合格阈值（标准化后）
_COND_NUMBER_LOW = 1e6  # PE 条件数 INCONCLUSIVE 阈值（标准化后）
_OP_RANGE_REL_THRESHOLD = 0.01  # OP range 相对量程的最小占比（1%）
_OP_DIRECTION_DEADBAND_RATIO = 0.01  # 方向变化死区 = ratio × u_range（1%）


def check_excitation(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
    op_span: float | None = None,
) -> ExcitationCheckResult:
    """激励充分性检测.

    检查项：
    1. OP 变化范围（range）是否足够（绝对激励，按量程归一化）
    2. OP 方向变化次数（非单调，反映频谱丰富度，带死区过滤微噪声）
    3. 回归矩阵条件数（持久激励 PE 条件，列标准化消除单位影响）

    注意：闭环下 OP 是 PID 输出，渐进变化（积分作用），
    相邻点跳变少但累计变化大，因此用 range + 方向变化次数，
    而非相邻点跳变次数。

    V62-P1-009: OP 激励按量程归一化（``u_range / op_span``），消除
    OP/PV 跨量纲比值（如 OP% 与 PV 温度比值无物理意义）；未提供 op_span
    时回退到 u_range/y_range（兼容旧路径，不推荐）。
    V62-P1-010: 方向变化加入死区（``ratio × u_range``），不把零值/微噪声
    算作有效激励方向变化。
    V62-P1-011: 回归矩阵列标准化后算条件数，单位缩放不变。

    Args:
        u: 输入信号（OP 时序）
        y: 输出信号（PV 时序）
        d: 纯滞后采样数
        op_span: OP 量程（op_max - op_min），用于归一化；None 时回退到
            u_range/y_range（兼容，跨量纲，不推荐）

    Returns:
        ExcitationCheckResult
    """
    if len(u) < 10 or len(y) < 10:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=0,
            condition_number=float("inf"),
            verdict="数据点不足（<10）",
            confidence=AlgorithmConfidenceLevel.INCONCLUSIVE,
        )

    # OP 变化范围
    u_range = float(np.max(u) - np.min(u))
    y_range = float(np.max(y) - np.min(y))
    if u_range < 1e-9:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=0,
            condition_number=float("inf"),
            verdict="OP 无变化（恒定）",
            confidence=AlgorithmConfidenceLevel.INCONCLUSIVE,
        )
    # V62-P1-009: OP 激励按量程归一化（消除 OP/PV 跨量纲比值）。
    # 有 op_span 时用 u_range/op_span（物理意义明确：OP 走过量程的比例）；
    # 无 op_span 时回退到 u_range/y_range（兼容旧路径，跨量纲，不推荐）。
    if op_span is not None and op_span > 0:
        op_rel_range = u_range / op_span
        range_basis = "量程"
    else:
        op_rel_range = u_range / (y_range + 1e-9) if y_range > 1e-9 else u_range
        range_basis = "PV范围"
    if op_rel_range < _OP_RANGE_REL_THRESHOLD:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=0,
            condition_number=float("inf"),
            verdict=(
                f"OP 变化范围过小（相对{range_basis} {op_rel_range:.4f} "
                f"< {_OP_RANGE_REL_THRESHOLD}）"
            ),
            confidence=AlgorithmConfidenceLevel.INCONCLUSIVE,
        )

    # V62-P1-010: OP 方向变化次数（带死区，过滤零值/微噪声）。
    # 死区 = ratio × u_range，只有 |du| > deadband 的跳变才参与方向变化计数，
    # 避免数值噪声/量化误差被误判为有效激励方向变化。
    du = np.diff(u)
    deadband = max(1e-9, _OP_DIRECTION_DEADBAND_RATIO * u_range)
    du_significant = du[np.abs(du) > deadband]
    if len(du_significant) > 1:
        direction_changes = int(np.sum(np.diff(np.sign(du_significant)) != 0))
    else:
        direction_changes = 0
    significant_changes = direction_changes  # 保留字段名兼容

    # V62-P1-011: 回归矩阵列标准化后算条件数（单位缩放不变）。
    # 标准化前 OP（%）与 PV（温度）量级差异会使条件数受单位选择影响；
    # 每列除以 2-范数后，条件数仅反映列相关性，与单位无关。
    n = len(y)
    max_lag = max(1, d + 1)
    rows = n - max_lag
    if rows < 3:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=significant_changes,
            condition_number=float("inf"),
            verdict="回归数据不足",
            confidence=AlgorithmConfidenceLevel.INCONCLUSIVE,
        )
    Phi = np.zeros((rows, 2))
    for i in range(rows):
        idx = max_lag + i
        Phi[i, 0] = -y[idx - 1]
        Phi[i, 1] = u[idx - d]
    # 列标准化（每列除以 2-范数，零列保持零）
    col_norms = np.linalg.norm(Phi, axis=0)
    safe_norms = np.where(col_norms > 1e-12, col_norms, 1.0)
    Phi_norm = Phi / safe_norms
    cond = float(np.linalg.cond(Phi_norm))

    # 判定
    if direction_changes < _MIN_DIRECTION_CHANGES:
        verdict = f"OP 方向变化不足（{direction_changes} < {_MIN_DIRECTION_CHANGES}，可能单调）"
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=significant_changes,
            condition_number=cond,
            verdict=verdict,
            confidence=AlgorithmConfidenceLevel.INCONCLUSIVE,
        )
    if cond > _COND_NUMBER_LOW:
        verdict = f"PE 条件数过大（{cond:.2e} > {_COND_NUMBER_LOW:.0e}）"
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=significant_changes,
            condition_number=cond,
            verdict=verdict,
            confidence=AlgorithmConfidenceLevel.INCONCLUSIVE,
        )
    if cond > _COND_NUMBER_OK:
        verdict = f"PE 条件数偏大（{cond:.2e} > {_COND_NUMBER_OK:.0e}），结果标注低可信度"
        return ExcitationCheckResult(
            is_sufficient=True,
            significant_changes=significant_changes,
            condition_number=cond,
            verdict=verdict,
            confidence=AlgorithmConfidenceLevel.C,
        )
    # 激励充分
    return ExcitationCheckResult(
        is_sufficient=True,
        significant_changes=significant_changes,
        condition_number=cond,
        verdict="激励充分",
        confidence=AlgorithmConfidenceLevel.A,
    )


def excitation_score(cond: float, significant_changes: int) -> float:
    """激励充分性得分（0-100，用于 TuningRecord.excitation_score 字段）.

    得分 = w1 * 变化次数分 + w2 * 条件数分
    """
    change_score = min(100.0, significant_changes * 10.0)
    if math.isfinite(cond) and cond > 0:
        cond_score = max(0.0, 100.0 - 100.0 * math.log10(cond) / math.log10(_COND_NUMBER_LOW))
    else:
        cond_score = 0.0
    return round(0.5 * change_score + 0.5 * cond_score, 2)
