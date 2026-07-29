"""算法栈编排（层 1→6 串联）.

入口函数 identify_from_history：接收 OP/PV/SP/MODE 时序，
执行激励检测→非参数粗估→参数化辨识→阶次选择→离散转换→可信度评估，
返回 IdentificationResult。
"""

from __future__ import annotations

import logging
import math

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
    ThetaSource,
)

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "TUNE_IDENT_v1.0"

# 可信度阈值
_R2_A = 0.90
_R2_B = 0.80
_R2_C = 0.70
_R2_D = 0.50

# 残差-输入互相关检验（P1-4）：95% 置信界 ±1.96/√N 下允许的越界 lag 占比
_XCORR_MAX_LAG = 20
_XCORR_EXCEED_TOLERANCE = 0.10
# 残差可忽略阈值（P1-4 轨 1）：R² 高于此值时残差量级远低于信号，直接判通过
_R2_RESIDUAL_NEGLIGIBLE = 0.999


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

    入口对 OP/PV（及 SP）做去均值处理：过程模型描述增量关系 Δy = G·Δu，
    无截距回归要求偏差变量输入；输出的 K/tau/theta 为增量参数，无需还原。
    偏置量（去均值前的样本均值）记录在 best_model.reason 中。

    Args:
        op: OP 时序（过程对象输入）
        pv: PV 时序（过程对象输出）
        sp: SP 时序（保留用于后续经验证的闭环辨识方法；Phase 0 不参与生产选模）
        mode: MODE 时序（可选，用于判断 AUTO/MANUAL）
        ts: 采样周期（秒）
        theta_estimate: 纯滞后预估值（秒），None 时使用 2Ts 启发值并将可信度封顶 C
        candidate_models: 候选模型阶次列表，默认 [FOPDT, SOPDT]

    Returns:
        IdentificationResult
    """
    u_raw = np.array(op, dtype=float)
    y_raw = np.array(pv, dtype=float)
    theta_source = ThetaSource.EXPLICIT if theta_estimate is not None else ThetaSource.HEURISTIC_2TS
    if len(u_raw) != len(y_raw):
        return IdentificationResult(
            success=False,
            reason=f"OP/PV 长度不匹配（{len(u_raw)} vs {len(y_raw)}）",
            theta_source=theta_source,
        )
    if len(u_raw) < 50:
        return IdentificationResult(
            success=False,
            reason=f"数据不足（{len(u_raw)} 点，需 ≥ 50）",
            theta_source=theta_source,
        )
    if not np.all(np.isfinite(u_raw)) or not np.all(np.isfinite(y_raw)):
        return IdentificationResult(
            success=False,
            reason="OP/PV 包含 NaN 或无穷值",
            theta_source=theta_source,
        )

    candidates = candidate_models or [ModelType.FOPDT, ModelType.SOPDT]
    if ModelType.IPDT in candidates:
        return IdentificationResult(
            success=False,
            reason="历史数据辨识暂不支持 IPDT；请使用阶跃实验路径",
            theta_source=theta_source,
        )
    if not math.isfinite(ts) or ts <= 0:
        return IdentificationResult(
            success=False,
            reason="采样周期 ts 必须为有限正数",
            theta_source=theta_source,
        )
    if theta_estimate is not None and (not math.isfinite(theta_estimate) or theta_estimate < 0):
        return IdentificationResult(
            success=False,
            reason="纯滞后预估值必须为有限非负数",
            theta_source=theta_source,
        )

    if sp is not None:
        sp_raw = np.array(sp, dtype=float)
        if len(sp_raw) != len(u_raw):
            return IdentificationResult(
                success=False,
                reason=f"SP 与 OP/PV 长度不匹配（{len(sp_raw)} vs {len(u_raw)}）",
                theta_source=theta_source,
            )
        if not np.all(np.isfinite(sp_raw)):
            return IdentificationResult(
                success=False,
                reason="SP 包含 NaN 或无穷值",
                theta_source=theta_source,
            )
        sp_range = float(np.ptp(sp_raw))
        change_threshold = max(1e-9, 0.01 * sp_range)
        significant_sp_changes = int(np.sum(np.abs(np.diff(sp_raw)) > change_threshold))
        if significant_sp_changes > 0:
            return IdentificationResult(
                success=False,
                reason=(
                    "CLOSED_LOOP_METHOD_UNVERIFIED: 检测到动态 SP 闭环激励；"
                    "现有实验性 IV 不作为发布依据，请使用合格闭环方法或受控阶跃实验"
                ),
                theta_source=theta_source,
            )

    # P0-2：入口去均值（偏置消除）。
    # ARX/ARMAX/IV 回归均无截距项，工业数据（如 PV≈450/OP≈60）若不去均值，
    # 增益 K 会按原点割线（≈ȳ/ū）收敛而非增量增益，闭环+偏置场景全灭。
    # 物理模型是增量关系 Δy = G·Δu，去均值后 K 无需还原。
    u_mean = float(np.mean(u_raw))
    y_mean = float(np.mean(y_raw))
    u = u_raw - u_mean
    y = y_raw - y_mean
    # Phase 0：缺省 theta 仅作为显式标注的 2Ts 启发值；不能冒充已估计参数。
    # 使用 is not None 保留调用方明确给出的 theta=0。
    theta_seconds = theta_estimate if theta_estimate is not None else 2 * ts
    d = max(0, round(theta_seconds / ts))

    results: list[CandidateModel] = []

    # ── 层 1：激励检测 ──
    exc = check_excitation(u, y, d)
    if not exc.is_sufficient:
        return IdentificationResult(
            success=False,
            reason=f"激励不足：{exc.verdict}",
            segments=[],
            theta_source=theta_source,
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

    # ── 层 3：参数化辨识（生产候选）──
    # IV 实现保留为实验代码，但其工具变量外生性和收敛性尚未完成工业验证，
    # Phase 0 不允许进入生产候选集或影响最终排名。
    identification_runs: list[tuple[IdentifyMethod, object]] = []
    # 总是跑 ARX（初值 + 基线）
    identification_runs.append((IdentifyMethod.HISTORICAL_ARX, "arx"))
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

            # 残差检验（P1-4：三轨制）
            # 旧实现对一切算法用方程误差 e = A·y − B·u 做 Ljung-Box 白性检验；
            # 闭环下 e = A·ν（ARMAX 中 e = C·ε）天然有色，好模型也过不了，
            # 可信度永封顶 C，排名退化为 R² 使闭环有偏的 ARX 反而胜出。
            #   轨 1（残差可忽略）：R² ≥ 0.999 时模型已解释几乎全部方差，
            #     残差仅是去均值常数偏置 c 与数值残渣（量级 ≪ 信号），
            #     对其做相关检验只会误判，直接判通过。
            #   轨 2（ARMAX，有噪声模型 C）：检验一步预测误差白性
            #     ε = (A/C)·y − (B/C)·u = e/C（C 反滤波方程误差）。
            #   轨 3（ARX/IV，无噪声模型，C=1）：白性检验不适用（e 恒有色），
            #     改残差-输入独立性检验——模型充分 ⇔ 残差不再含输入动态。
            r2 = max(0.0, min(1.0, res.r_squared))
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
            c_coeffs = getattr(res, "c_coeffs", None)
            if r2 >= _R2_RESIDUAL_NEGLIGIBLE:
                residual_white = True
                test_note = f"residual_negligible(R²={r2:.4f})"
            elif rows > 0 and c_coeffs:
                eps = _equation_error_to_prediction_error(residuals, c_coeffs)
                _, lb_p = ljung_box_test(eps, max_lag=min(10, len(eps) // 3))
                residual_white = lb_p > 0.05
                test_note = f"LB_p={lb_p:.3f}"
            else:
                exceed_ratio = _residual_input_exceed_ratio(residuals, u)
                residual_white = exceed_ratio <= _XCORR_EXCEED_TOLERANCE
                test_note = f"xcorr_exceed={exceed_ratio:.3f}"

            # 可信度评估
            confidence = _assess_confidence(exc, r2, residual_white, exc_score)
            if theta_source == ThetaSource.HEURISTIC_2TS:
                confidence = _cap_confidence(confidence, ConfidenceLevel.C)

            candidate = CandidateModel(
                params=params,
                fitting_score=round(r2 * 100, 2),
                confidence=confidence,
                identify_method=method_used,
                residual_test_passed=residual_white,
                excitation_score=exc_score,
                reason=f"R²={r2:.3f}, {test_note}, iters={getattr(res, 'iterations', 1)}",
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
            theta_source=theta_source,
        )

    # 选最优模型
    best = max(results, key=_candidate_sort_key)

    # 整体可信度检查
    if best.confidence == ConfidenceLevel.INCONCLUSIVE:
        return IdentificationResult(
            success=False,
            reason=f"辨识可信度不足：{best.reason}",
            candidates=results,
            theta_source=theta_source,
        )

    # P0-2：记录去均值偏置量（增量模型的零位基准，供结果审计追溯）
    offset_note = f"去均值偏置 PV={y_mean:.6g}, OP={u_mean:.6g}"
    best.reason = f"{best.reason}; {offset_note}" if best.reason else offset_note

    return IdentificationResult(
        success=True,
        best_model=best,
        candidates=results,
        reason=f"辨识成功（{offset_note}）",
        theta_source=theta_source,
    )


def _residual_input_exceed_ratio(
    residuals: np.ndarray,
    u: np.ndarray,
    max_lag: int = _XCORR_MAX_LAG,
) -> float:
    """残差-输入互相关越界率（P1-4，ARX/IV 模型充分性检验）.

    归一化互相关 ρ_k = Σ_t ε(t)·u(t−k) / (‖ε‖·‖u‖)，k = 0..max_lag；
    白噪声假设下 95% 置信界 ±1.96/√N。返回越界 lag 占比：
    残差仍含未建模输入动态（如有偏 ARX）时低阶 lag 大面积越界 → 占比趋 1；
    模型充分时仅随机越界 → 占比 ≈ 5%。

    Args:
        residuals: 残差序列（方程误差）
        u: 输入序列（已去均值）
        max_lag: 最大互相关滞后

    Returns:
        越界 lag 占比 0~1（数据不足或退化时返回 0.0，不拒绝）
    """
    n = len(residuals)
    if n < max_lag + 10 or len(u) < n:
        return 0.0
    # 残差第 i 点对应原序列索引 max_lag+i（回归起点之后），与 u 尾部对齐
    u = u[-n:]
    r = residuals - float(np.mean(residuals))
    uu = u - float(np.mean(u))
    denom = math.sqrt(float(np.sum(r * r) * np.sum(uu * uu)))
    if denom < 1e-12:
        return 0.0
    bound = 1.96 / math.sqrt(n)
    exceed = 0
    for k in range(max_lag + 1):
        cov = float(np.dot(r[k:], uu[: n - k])) if k else float(np.dot(r, uu))
        if abs(cov / denom) > bound:
            exceed += 1
    return exceed / (max_lag + 1)


def _equation_error_to_prediction_error(
    equation_error: np.ndarray,
    c_coeffs: list[float],
) -> np.ndarray:
    """方程误差 e = A·y − B·u 经 1/C 反滤波得一步预测误差 ε（ARMAX：e = C·ε）.

    递推：ε(t) = e(t) − Σ_j c_j·ε(t−j)，初始 ε(t<0) = 0。

    Args:
        equation_error: 方程误差序列 e(t) = A·y − B·u
        c_coeffs: ARMAX C 多项式系数 [c1, c2, ...]（C = 1 + c1·z⁻¹ + ...）

    Returns:
        一步预测误差序列 ε(t)
    """
    eps = np.zeros_like(equation_error)
    for i in range(len(equation_error)):
        val = equation_error[i]
        for j, cj in enumerate(c_coeffs):
            if i - 1 - j >= 0:
                val -= cj * eps[i - 1 - j]
        eps[i] = val
    return eps


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


def _cap_confidence(
    confidence: ConfidenceLevel,
    maximum: ConfidenceLevel,
) -> ConfidenceLevel:
    """将可信度限制在 maximum，不提升原有低可信度结果."""
    level_order = {
        ConfidenceLevel.A: 5,
        ConfidenceLevel.B: 4,
        ConfidenceLevel.C: 3,
        ConfidenceLevel.D: 2,
        ConfidenceLevel.E: 1,
        ConfidenceLevel.INCONCLUSIVE: 0,
    }
    if level_order[confidence] > level_order[maximum]:
        return maximum
    return confidence
