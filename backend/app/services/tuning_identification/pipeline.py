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
    compute_aic,
    compute_bic,
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

# P2-006 Occam 削减：SOPDT 升级门禁
# SOPDT 优于 FOPDT 当且仅当 R²_val 相对提升 > 5% 且 BIC 下降（更复杂模型须显著更优）
_OCCAM_R2_RELATIVE_GAIN = 0.05


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
    d_explicit = max(0, round(theta_seconds / ts))

    # P2-001：延迟候选搜索范围
    # 用户给 theta 时在 d_explicit ±3 邻域精搜；未给时全域搜索 0..d_max。
    # d_max 上限 60 覆盖 P2-003 的 θ=60 Ts 场景，同时不超过数据长度 1/4。
    n_u = len(u)
    if theta_estimate is not None:
        d_search_max = min(d_explicit + 3, max(0, n_u // 4))
        theta_source = ThetaSource.EXPLICIT
    else:
        d_search_max = min(max(0, n_u // 4), 60)
        theta_source = ThetaSource.SEARCHED

    # 激励检测用 d_explicit（粗略估计，搜索在参数化阶段做）
    d = d_explicit

    # P2-002：时间顺序留出集分割（不随机打乱，保留时序自相关）
    # 60% train / 20% val / 20% test；数据不足时退化为 70/30 train/val（无 test）
    n_total = len(y)
    n_train = int(n_total * 0.6)
    n_val = int(n_total * 0.2)
    if n_train < 50 or n_val < 20:
        # 短数据退化为 70/30 train/val
        n_train = int(n_total * 0.7)
        n_val = n_total - n_train
    u_train, y_train = u[:n_train], y[:n_train]
    u_val, y_val = u[n_train : n_train + n_val], y[n_train : n_train + n_val]

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

        # P2-001：延迟候选搜索 — 对 d=0..d_max 跑 ARX，用 BIC 选最优 d
        # P2-002：搜索用训练集（不泄漏留出集信息）
        best_d, delay_search_trace = _search_delay(
            u_train, y_train, na=na, nb=nb, d_max=d_search_max
        )
        d_model = best_d
        logger.debug(
            "P2-001 %s 延迟搜索: d_max=%d → best_d=%d, trace=%s",
            model_type,
            d_search_max,
            d_model,
            delay_search_trace[:5],
        )

        best_candidate: CandidateModel | None = None
        for method, algo_key in identification_runs:
            try:
                if algo_key == "arx":
                    res = identify_arx(u_train, y_train, d_model, na=na, nb=nb)
                    method_used = method
                elif algo_key == "armax":
                    res = identify_armax(u_train, y_train, d_model, na=na, nb=nb, nc=1)
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

            # P2-002：验证集自由仿真 R²（留出集泛化能力，替代训练集方程误差 R²）
            r2_train = max(0.0, min(1.0, res.r_squared))
            _, r2_val = _free_run_simulation(u_val, y_val, res.a_coeffs, res.b_coeffs, d_model)

            # 残差检验（P1-4：三轨制，在训练集方程误差上做）
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
            max_lag_na = max(na, nb + d_model)
            rows = len(y_train) - max_lag_na
            residuals = np.zeros(max(rows, 0))
            if rows > 0:
                for i in range(rows):
                    idx = max_lag_na + i
                    val = y_train[idx]
                    for j in range(na):
                        val += res.a_coeffs[j] * y_train[idx - 1 - j]
                    for j in range(nb):
                        val -= res.b_coeffs[j] * u_train[idx - d_model - j]
                    residuals[i] = val
            c_coeffs = getattr(res, "c_coeffs", None)
            if r2_train >= _R2_RESIDUAL_NEGLIGIBLE:
                residual_white = True
                test_note = f"residual_negligible(R²_train={r2_train:.4f})"
            elif rows > 0 and c_coeffs:
                eps = _equation_error_to_prediction_error(residuals, c_coeffs)
                _, lb_p = ljung_box_test(eps, max_lag=min(10, len(eps) // 3))
                residual_white = lb_p > 0.05
                test_note = f"LB_p={lb_p:.3f}"
            else:
                exceed_ratio = _residual_input_exceed_ratio(residuals, u_train)
                residual_white = exceed_ratio <= _XCORR_EXCEED_TOLERANCE
                test_note = f"xcorr_exceed={exceed_ratio:.3f}"

            # 可信度评估（P2-002：用验证集自由仿真 R²）
            confidence = _assess_confidence(exc, r2_val, residual_white, exc_score)
            if theta_source == ThetaSource.HEURISTIC_2TS:
                confidence = _cap_confidence(confidence, ConfidenceLevel.C)

            # P2-006：AIC/BIC 信息准则（训练集残差方差，用于 Occam 削减与证据输出）
            # n_params = na + nb（+ nc for ARMAX），复杂模型须用更小残差补偿参数惩罚
            n_params = na + nb + len(c_coeffs) if c_coeffs else na + nb
            aic_val = compute_aic(res.n_samples, res.residual_var, n_params)
            bic_val = compute_bic(res.n_samples, res.residual_var, n_params)

            candidate = CandidateModel(
                params=params,
                fitting_score=round(r2_val * 100, 2),
                confidence=confidence,
                identify_method=method_used,
                residual_test_passed=residual_white,
                excitation_score=exc_score,
                reason=(
                    f"R²_val={r2_val:.3f}, R²_train={r2_train:.3f},"
                    f" {test_note}, AIC={aic_val:.1f}, BIC={bic_val:.1f},"
                    f" iters={getattr(res, 'iterations', 1)}"
                ),
                aic=round(aic_val, 2),
                bic=round(bic_val, 2),
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

    # P2-006：Occam 削减 — SOPDT 优于 FOPDT 当且仅当 R²_val 相对提升 > 5% 且 BIC 下降
    best = _select_with_occam(results)

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


def _search_delay(
    u: np.ndarray,
    y: np.ndarray,
    na: int,
    nb: int,
    d_max: int,
) -> tuple[int, list[tuple[int, float]]]:
    """延迟候选搜索（P2-001）— 对 d=0..d_max 跑 ARX，用 BIC 选最优 d.

    BIC = n·ln(σ²) + k·ln(n)，其中 σ² 为残差方差，n 为样本数，k=na+nb。
    BIC 越小越好：惩罚模型复杂度，避免过大的 d 过拟合。

    Args:
        u: 输入信号（已去均值）
        y: 输出信号（已去均值）
        na: A 多项式阶次
        nb: B 多项式阶次
        d_max: 最大延迟候选（采样数）

    Returns:
        (best_d, search_trace) — search_trace = [(d, bic), ...] 供证据输出
    """
    best_d = 0
    best_bic = float("inf")
    search_trace: list[tuple[int, float]] = []
    for d in range(d_max + 1):
        try:
            res = identify_arx(u, y, d, na=na, nb=nb)
            n = res.n_samples
            k = na + nb
            bic = n * math.log(max(res.residual_var, 1e-12)) + k * math.log(n)
            bic_rounded = round(bic, 2)
            search_trace.append((d, bic_rounded))
            if bic < best_bic:
                best_bic = bic
                best_d = d
        except Exception:
            search_trace.append((d, float("inf")))
            continue
    return best_d, search_trace


def _free_run_simulation(
    u: np.ndarray,
    y: np.ndarray,
    a_coeffs: list[float],
    b_coeffs: list[float],
    d: int,
) -> tuple[np.ndarray, float]:
    """自由仿真（P2-002）— 用预测输出反馈计算留出集 R².

    自由仿真：ŷ(t) = -Σ a_j·ŷ(t-1-j) + Σ b_j·u(t-d-j)
    初始条件用真实 y 值（前 max_lag 个点），避免初始瞬态污染。

    与训练集方程误差 R² 的区别：
    - 方程误差用真实 y(t-1) 反馈，只检验回归拟合，偏乐观
    - 自由仿真用预测 ŷ(t-1) 反馈，误差会累积，检验泛化能力

    Args:
        u: 留出集输入
        y: 留出集输出
        a_coeffs: ARX A 多项式系数 [a1, a2, ...]
        b_coeffs: ARX B 多项式系数 [b1, b2, ...]
        d: 延迟（采样数）

    Returns:
        (y_pred, r2_free) — r2_free 为自由仿真 R²（[0, 1]）
    """
    na = len(a_coeffs)
    nb = len(b_coeffs)
    n = len(y)
    max_lag = max(na, nb + d)
    if n < max_lag + 5:
        return np.zeros(n), 0.0

    y_pred = np.zeros(n)
    # 初始条件：前 max_lag 个点用真实值
    y_pred[:max_lag] = y[:max_lag]

    for i in range(max_lag, n):
        val = 0.0
        for j in range(na):
            val -= a_coeffs[j] * y_pred[i - 1 - j]
        for j in range(nb):
            idx_u = i - d - j
            if idx_u >= 0:
                val += b_coeffs[j] * u[idx_u]
        y_pred[i] = val

    residuals = y[max_lag:] - y_pred[max_lag:]
    ss_res = float(np.sum(residuals**2))
    y_val = y[max_lag:]
    ss_tot = float(np.sum((y_val - np.mean(y_val)) ** 2))
    r2_free = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return y_pred, max(0.0, min(1.0, r2_free))


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


def _select_with_occam(results: list[CandidateModel]) -> CandidateModel:
    """P2-006 Occam 削减：在 sort_key 选优基础上对 SOPDT 升级施加门禁.

    规则（对齐 order_selection.py 文档）：SOPDT 优于 FOPDT 当且仅当
    R²_val 相对提升 > 5% 且 BIC 下降。否则保留更简单的 FOPDT。

    仅当 sort_key 选出的最优为 SOPDT 且存在 FOPDT 候选时触发；
    其余情况（FOPDT 最优 / 无 FOPDT 候选 / BIC 缺失）直接返回 sort_key 最优。

    Args:
        results: 各 model_type 的最优候选列表

    Returns:
        Occam 削减后的最优候选
    """
    best = max(results, key=_candidate_sort_key)
    if best.params.model_type != ModelType.SOPDT:
        return best
    fopdt_candidates = [c for c in results if c.params.model_type == ModelType.FOPDT]
    if not fopdt_candidates:
        return best
    fopdt = max(fopdt_candidates, key=_candidate_sort_key)
    # SOPDT 须显著更优：R²_val 相对提升 > 5% 且 BIC 下降
    r2_gain = (best.fitting_score - fopdt.fitting_score) / max(fopdt.fitting_score, 1.0)
    bic_lower = best.bic is not None and fopdt.bic is not None and best.bic < fopdt.bic
    if r2_gain > _OCCAM_R2_RELATIVE_GAIN and bic_lower:
        return best
    # Occam：SOPDT 未显著优于 FOPDT，保留简单模型
    logger.debug(
        "P2-006 Occam 削减：SOPDT R²_gain=%.1f%% BIC=%s vs FOPDT BIC=%s → 选 FOPDT",
        r2_gain * 100,
        best.bic,
        fopdt.bic,
    )
    return fopdt


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
