"""算法栈编排（层 1→6 串联）.

入口函数 identify_from_history：接收 OP/PV/SP/MODE 时序，
执行激励检测→非参数粗估→参数化辨识→阶次选择→离散转换→可信度评估，
返回 IdentificationResult。
"""

from __future__ import annotations

import logging

import numpy as np

from app.services.tuning_identification.armax import identify_armax
from app.services.tuning_identification.arx import identify_arx
from app.services.tuning_identification.discrete_to_continuous import (
    arx_to_fopdt,
    arx_to_sopdt,
)
from app.services.tuning_identification.excitation import (
    check_excitation,
    excitation_score,
)
from app.services.tuning_identification.iv import identify_iv
from app.services.tuning_identification.nonparametric import correlation_analysis
from app.services.tuning_identification.order_selection import (
    ljung_box_test,
)
from app.services.tuning_identification.types import (
    CandidateModel,
    ConfidenceLevel,
    IdentificationResult,
    IdentifyMethod,
    ModelType,
)

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "TUNE_IDENT_v1.0"

# 可信度阈值
_R2_A = 0.90
_R2_B = 0.80
_R2_C = 0.70
_R2_D = 0.50


def identify_from_history(
    op: list[float],
    pv: list[float],
    sp: list[float] | None = None,
    mode: list[int] | None = None,
    ts: float = 1.0,
    theta_estimate: float | None = None,
    candidate_models: list[ModelType] | None = None,
) -> IdentificationResult:
    """基于历史数据辨识过程对象 G_plant = PV/OP.

    Args:
        op: OP 时序（过程对象输入）
        pv: PV 时序（过程对象输出）
        sp: SP 时序（可选，用于 IV 法工具变量；闭环必需）
        mode: MODE 时序（可选，用于判断 AUTO/MANUAL）
        ts: 采样周期（秒）
        theta_estimate: 纯滞后预估值（秒），None 则自动估计
        candidate_models: 候选模型阶次列表，默认 [FOPDT, SOPDT]

    Returns:
        IdentificationResult
    """
    u = np.array(op, dtype=float)
    y = np.array(pv, dtype=float)
    sp_arr = np.array(sp, dtype=float) if sp is not None else None

    if len(u) != len(y):
        return IdentificationResult(
            success=False,
            reason=f"OP/PV 长度不匹配（{len(u)} vs {len(y)}）",
        )
    if len(u) < 50:
        return IdentificationResult(
            success=False,
            reason=f"数据不足（{len(u)} 点，需 ≥ 50）",
        )

    # 纯滞后估计（若无预估，默认 2 个采样周期）
    d = max(1, round((theta_estimate or 2 * ts) / ts))

    candidates = candidate_models or [ModelType.FOPDT, ModelType.SOPDT]
    results: list[CandidateModel] = []

    # ── 层 1：激励检测 ──
    exc = check_excitation(u, y, d)
    if not exc.is_sufficient:
        return IdentificationResult(
            success=False,
            reason=f"激励不足：{exc.verdict}",
            segments=[],
        )
    exc_score = excitation_score(exc.condition_number, exc.significant_changes)

    # ── 层 2：非参数粗估（用于初值和交叉校验）──
    try:
        nonparam = correlation_analysis(u, y, ts)
        K_rough = nonparam.gain_estimate
        logger.debug("非参数粗估 K=%s, tau+theta=%s", K_rough, nonparam.time_constant_estimate)
    except Exception:
        logger.debug("非参数粗估失败，跳过", exc_info=True)
        K_rough = None

    # ── 层 3：参数化辨识（根据数据场景选择算法）──
    has_sp = sp_arr is not None and len(sp_arr) == len(u)
    # SP 外生性检验（简化：SP 变化次数）
    sp_exogenous = False
    if has_sp:
        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        sp_threshold = 0.01 * (sp_range + 1e-9)
        sp_changes = int(np.sum(np.abs(np.diff(sp_arr)) > sp_threshold))
        sp_exogenous = sp_changes >= 3

    # 选择主算法
    identification_runs: list[tuple[IdentifyMethod, object]] = []
    # 总是跑 ARX（初值 + 基线）
    identification_runs.append((IdentifyMethod.HISTORICAL_ARX, "arx"))
    # 闭环且有外生 SP → IV
    if has_sp and sp_exogenous:
        identification_runs.append((IdentifyMethod.HISTORICAL_IV, "iv"))
    # 总是跑 ARMAX（扰动建模）
    identification_runs.append((IdentifyMethod.HISTORICAL_ARMAX, "armax"))

    # ── 层 4-5：阶次选择 + 离散→连续 ──
    for model_type in candidates:
        na = 1 if model_type == ModelType.FOPDT else 2
        nb = 1

        best_candidate: CandidateModel | None = None
        for method, algo_key in identification_runs:
            try:
                if algo_key == "arx":
                    res = identify_arx(u, y, d, na=na, nb=nb)
                    method_used = method
                elif algo_key == "iv" and has_sp:
                    res = identify_iv(u, y, sp_arr, d, na=na, nb=nb)
                    method_used = method
                elif algo_key == "armax":
                    res = identify_armax(u, y, d, na=na, nb=nb, nc=1)
                    method_used = method
                else:
                    continue
            except Exception as exc_err:
                logger.debug("%s 辨识失败：%s", method, exc_err)
                continue

            # 离散→连续转换
            try:
                if model_type == ModelType.FOPDT:
                    params = arx_to_fopdt(res.a_coeffs[0], res.b_coeffs[0], res.d, ts)
                else:
                    if len(res.a_coeffs) < 2:
                        continue
                    params = arx_to_sopdt(
                        res.a_coeffs[0], res.a_coeffs[1], res.b_coeffs[0], res.d, ts
                    )
            except Exception as conv_err:
                logger.debug("%s 离散→连续转换失败：%s", method, conv_err)
                continue

            # 残差白噪声检验
            max_lag_na = max(na, nb + d)
            rows = len(y) - max_lag_na
            residuals = np.zeros(max(rows, 0))
            if rows > 0:
                for i in range(rows):
                    idx = max_lag_na + i
                    val = y[idx]
                    for j in range(na):
                        val += res.a_coeffs[j] * y[idx - 1 - j]
                    for j in range(nb):
                        val -= res.b_coeffs[j] * u[idx - d - j]
                    residuals[i] = val
            _, lb_p = ljung_box_test(residuals, max_lag=min(10, len(residuals) // 3))
            residual_white = lb_p > 0.05

            # 可信度评估
            r2 = max(0.0, min(1.0, res.r_squared))
            confidence = _assess_confidence(exc, r2, residual_white, exc_score)

            candidate = CandidateModel(
                params=params,
                fitting_score=round(r2 * 100, 2),
                confidence=confidence,
                identify_method=method_used,
                residual_test_passed=residual_white,
                excitation_score=exc_score,
                reason=f"R²={r2:.3f}, LB_p={lb_p:.3f}, iters={getattr(res, 'iterations', 1)}",
            )
            # 选最优候选（可信度优先，其次拟合度）
            if best_candidate is None or _candidate_better(candidate, best_candidate):
                best_candidate = candidate

        if best_candidate is not None:
            results.append(best_candidate)

    if not results:
        return IdentificationResult(
            success=False,
            reason="所有算法/阶次辨识均失败",
        )

    # 选最优模型
    best = max(results, key=_candidate_sort_key)

    # 整体可信度检查
    if best.confidence == ConfidenceLevel.INCONCLUSIVE:
        return IdentificationResult(
            success=False,
            reason=f"辨识可信度不足：{best.reason}",
            candidates=results,
        )

    return IdentificationResult(
        success=True,
        best_model=best,
        candidates=results,
    )


def _assess_confidence(
    exc,
    r2: float,
    residual_white: bool,
    exc_score: float,
) -> ConfidenceLevel:
    """可信度综合评估."""
    if not exc.is_sufficient:
        return ConfidenceLevel.INCONCLUSIVE
    if r2 >= _R2_A and residual_white and exc_score >= 60:
        return ConfidenceLevel.A
    if r2 >= _R2_B and residual_white:
        return ConfidenceLevel.B
    if r2 >= _R2_C:
        return ConfidenceLevel.C
    if r2 >= _R2_D:
        return ConfidenceLevel.D
    return ConfidenceLevel.E


def _candidate_better(a: CandidateModel, b: CandidateModel) -> bool:
    """a 是否优于 b（可信度优先，其次拟合度）."""
    return _candidate_sort_key(a) > _candidate_sort_key(b)


def _candidate_sort_key(c: CandidateModel) -> tuple:
    """排序键：可信度等级 > 拟合度 > 残差检验."""
    level_order = {
        ConfidenceLevel.A: 5,
        ConfidenceLevel.B: 4,
        ConfidenceLevel.C: 3,
        ConfidenceLevel.D: 2,
        ConfidenceLevel.E: 1,
        ConfidenceLevel.INCONCLUSIVE: 0,
    }
    return (level_order.get(c.confidence, 0), c.fitting_score, c.residual_test_passed)
