"""激励检测与片段筛选（算法栈层 1）.

防止"垃圾进垃圾出"：无激励数据直接返回 INCONCLUSIVE，
不硬辨（硬辨会输出虚假模型误导整定）。
"""

from __future__ import annotations

import logging
import math

import numpy as np

from app.services.tuning_identification.types import (
    ConfidenceLevel,
    ExcitationCheckResult,
)

logger = logging.getLogger(__name__)

# 阈值（可后续迁入 algorithm_config 配置体系）
_MIN_DIRECTION_CHANGES = 2  # 最少方向变化次数（OP 非单调）
_COND_NUMBER_OK = 1e4  # PE 条件数合格阈值
_COND_NUMBER_LOW = 1e6  # PE 条件数 INCONCLUSIVE 阈值
_OP_RANGE_REL_THRESHOLD = 0.01  # OP range 相对 PV range 的最小占比（1%）


def check_excitation(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
) -> ExcitationCheckResult:
    """激励充分性检测.

    检查项：
    1. OP 变化范围（range）是否足够（绝对激励）
    2. OP 方向变化次数（非单调，反映频谱丰富度）
    3. 回归矩阵条件数（持久激励 PE 条件）

    注意：闭环下 OP 是 PID 输出，渐进变化（积分作用），
    相邻点跳变少但累计变化大，因此用 range + 方向变化次数，
    而非相邻点跳变次数。

    Args:
        u: 输入信号（OP 时序）
        y: 输出信号（PV 时序）
        d: 纯滞后采样数

    Returns:
        ExcitationCheckResult
    """
    if len(u) < 10 or len(y) < 10:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=0,
            condition_number=float("inf"),
            verdict="数据点不足（<10）",
            confidence=ConfidenceLevel.INCONCLUSIVE,
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
            confidence=ConfidenceLevel.INCONCLUSIVE,
        )
    # OP range 相对 PV range 占比（绝对激励强度）
    op_rel_range = u_range / (y_range + 1e-9) if y_range > 1e-9 else u_range
    if op_rel_range < _OP_RANGE_REL_THRESHOLD:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=0,
            condition_number=float("inf"),
            verdict=f"OP 变化范围过小（{op_rel_range:.4f} < {_OP_RANGE_REL_THRESHOLD}）",
            confidence=ConfidenceLevel.INCONCLUSIVE,
        )

    # OP 方向变化次数（拐点数，反映频谱丰富度）
    du = np.diff(u)
    direction_changes = int(np.sum(np.diff(np.sign(du)) != 0))
    significant_changes = direction_changes  # 保留字段名兼容

    # 回归矩阵条件数（PE 条件）
    n = len(y)
    max_lag = max(1, d + 1)
    rows = n - max_lag
    if rows < 3:
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=significant_changes,
            condition_number=float("inf"),
            verdict="回归数据不足",
            confidence=ConfidenceLevel.INCONCLUSIVE,
        )
    Phi = np.zeros((rows, 2))
    for i in range(rows):
        idx = max_lag + i
        Phi[i, 0] = -y[idx - 1]
        Phi[i, 1] = u[idx - d]
    cond = float(np.linalg.cond(Phi))

    # 判定
    if direction_changes < _MIN_DIRECTION_CHANGES:
        verdict = f"OP 方向变化不足（{direction_changes} < {_MIN_DIRECTION_CHANGES}，可能单调）"
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=significant_changes,
            condition_number=cond,
            verdict=verdict,
            confidence=ConfidenceLevel.INCONCLUSIVE,
        )
    if cond > _COND_NUMBER_LOW:
        verdict = f"PE 条件数过大（{cond:.2e} > {_COND_NUMBER_LOW:.0e}）"
        return ExcitationCheckResult(
            is_sufficient=False,
            significant_changes=significant_changes,
            condition_number=cond,
            verdict=verdict,
            confidence=ConfidenceLevel.INCONCLUSIVE,
        )
    if cond > _COND_NUMBER_OK:
        verdict = f"PE 条件数偏大（{cond:.2e} > {_COND_NUMBER_OK:.0e}），结果标注低可信度"
        return ExcitationCheckResult(
            is_sufficient=True,
            significant_changes=significant_changes,
            condition_number=cond,
            verdict=verdict,
            confidence=ConfidenceLevel.C,
        )
    # 激励充分
    return ExcitationCheckResult(
        is_sufficient=True,
        significant_changes=significant_changes,
        condition_number=cond,
        verdict="激励充分",
        confidence=ConfidenceLevel.A,
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
