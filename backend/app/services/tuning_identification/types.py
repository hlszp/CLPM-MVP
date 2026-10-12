"""辨识算法栈共享数据结构."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


class ModelType(StrEnum):
    """模型类型."""

    FOPDT = "FOPDT"
    SOPDT = "SOPDT"
    IPDT = "IPDT"


class IdentifyMethod(StrEnum):
    """辨识方法（记录在 TuningRecord.identify_method 字段）."""

    HISTORICAL_ARX = "HISTORICAL_ARX"
    HISTORICAL_ARMAX = "HISTORICAL_ARMAX"
    HISTORICAL_IV = "HISTORICAL_IV"
    STEP_TWO_POINT = "STEP_TWO_POINT"
    STEP_AREA = "STEP_AREA"
    STEP_NLS = "STEP_NLS"


class AlgorithmConfidenceLevel(StrEnum):
    """算法可信度等级（辨识专用，对齐 ConfidenceEvaluator A/B/C/D/E + INCONCLUSIVE）.

    可信度统一 Phase 3（P3-1）：原 ``ConfidenceLevel`` 改名为
    ``AlgorithmConfidenceLevel``，与平台级数据可信度
    ``app.contracts.data_types.ConfidenceLevel``（A/B/C/D/E，无 INCONCLUSIVE）区分。

    判定维度：算法拟合度（R²）+ 残差 + 激励，与数据可信度（valid_rate）独立。
    最终整定可信度 = ``min(data_confidence, algorithm_confidence)``，逻辑不变。

    对外 API 字段名不变：仍为 ``confidenceLevel``（见 ``to_dict`` 序列化）。
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    INCONCLUSIVE = "INCONCLUSIVE"


# .. deprecated:: v6.2 可信度统一 Phase 3（P3-1）
#     改用 ``AlgorithmConfidenceLevel``。此别名仅做向后兼容过渡，
#     新代码应使用 ``AlgorithmConfidenceLevel``。
ConfidenceLevel = AlgorithmConfidenceLevel


class ThetaSource(StrEnum):
    """纯滞后参数来源.

    EXPLICIT 表示调用方提供并可追溯；HEURISTIC_2TS 表示仅使用 2 个采样周期的
    保守启发值，不能获得高于 C 的可信度；SEARCHED 表示通过 BIC 候选搜索
    （d=0..d_max）数据驱动确定，可信度不封顶。
    """

    EXPLICIT = "EXPLICIT"
    HEURISTIC_2TS = "HEURISTIC_2TS"
    SEARCHED = "SEARCHED"


@dataclass
class ModelParams:
    """模型参数（对齐 tuning_algorithms.py 的 FOPDTParams/SOPDTParams 字段名）."""

    model_type: ModelType
    K: float
    tau: float = 0.0  # FOPDT 时间常数
    theta: float = 0.0  # 纯滞后
    T1: float = 0.0  # SOPDT 第一时间常数
    T2: float = 0.0  # SOPDT 第二时间常数

    def to_dict(self) -> dict[str, Any]:
        """转 dict（与 TuningRecord.model_params JSON 结构对齐）."""
        if self.model_type == ModelType.FOPDT:
            return {"K": round(self.K, 6), "tau": round(self.tau, 4), "theta": round(self.theta, 4)}
        if self.model_type == ModelType.SOPDT:
            return {
                "K": round(self.K, 6),
                "T1": round(self.T1, 4),
                "T2": round(self.T2, 4),
                "theta": round(self.theta, 4),
            }
        if self.model_type == ModelType.IPDT:
            return {"K": round(self.K, 6), "theta": round(self.theta, 4)}
        return {"K": round(self.K, 6)}


@dataclass
class ParameterUncertainty:
    """P2-015：参数不确定度摘要（95% 置信区间）.

    通过解析协方差（ARX: σ²(ΦᵀΦ)⁻¹；CLIVC: σ²(ZᵀΦ)⁻¹(ZᵀZ)(ΦᵀZ)⁻¹）
    + Monte Carlo 传播到连续域参数得到。仅 ARX/CLIVC 有解析协方差；
    ARMAX/IPDT 暂不输出（需 bootstrap，后续按需补）。
    """

    K_ci_lower: float = 0.0
    K_ci_upper: float = 0.0
    tau_ci_lower: float = 0.0
    tau_ci_upper: float = 0.0
    theta_ci_lower: float = 0.0
    theta_ci_upper: float = 0.0
    # T3 修复（2026-10-01）：SOPDT 时间常数是 T1/T2（ModelParams.tau 恒 0），
    # 原实现采样 p.tau → tau CI 恒 [0,0] 的假置信区间
    t1_ci_lower: float = 0.0
    t1_ci_upper: float = 0.0
    t2_ci_lower: float = 0.0
    t2_ci_upper: float = 0.0
    n_mc_samples: int = 0  # 有效 Monte Carlo 采样数（转换成功）

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "K": {"ci95": [round(self.K_ci_lower, 4), round(self.K_ci_upper, 4)]},
            "theta": {"ci95": [round(self.theta_ci_lower, 4), round(self.theta_ci_upper, 4)]},
            "nMcSamples": self.n_mc_samples,
        }
        # 仅输出实际采样的参数 CI（FOPDT=tau；SOPDT=T1/T2），
        # 避免未采样维度输出 [0,0] 误导
        if self.tau_ci_lower != 0.0 or self.tau_ci_upper != 0.0:
            d["tau"] = {"ci95": [round(self.tau_ci_lower, 4), round(self.tau_ci_upper, 4)]}
        if self.t1_ci_lower != 0.0 or self.t1_ci_upper != 0.0:
            d["T1"] = {"ci95": [round(self.t1_ci_lower, 4), round(self.t1_ci_upper, 4)]}
            d["T2"] = {"ci95": [round(self.t2_ci_lower, 4), round(self.t2_ci_upper, 4)]}
        return d


@dataclass
class ExcitationCheckResult:
    """激励检测结果."""

    is_sufficient: bool
    significant_changes: int
    condition_number: float
    verdict: str
    confidence: AlgorithmConfidenceLevel = AlgorithmConfidenceLevel.INCONCLUSIVE


@dataclass
class SegmentInfo:
    """可辨识片段信息."""

    start_idx: int
    end_idx: int
    mode: str
    excitation: ExcitationCheckResult


@dataclass
class ModelEvidence:
    """辨识证据（P2-013~016）：留出集分割、自由仿真、残差检验、数据快照.

    无证据的模型不得进入整定（Phase 2 门禁）。
    to_dict 只输出摘要；原始序列保留在对象上供详细审计。
    """

    # P2-013：数据分区大小（时间顺序 60/20/20，短数据退化 70/30 时 n_test=0）
    n_train: int = 0
    n_val: int = 0
    n_test: int = 0
    # P2-013/014：验证集自由仿真指标
    r2_val: float = 0.0
    r2_train: float = 0.0
    nrmse_val: float = 0.0  # 归一化 RMSE = RMSE / range(y_val)
    # P2-014：残差检验摘要
    residual_test_note: str = ""
    # P2-005：Welch 相干辅助门禁（均值，None 表示未计算；低相干封顶可信度）
    mean_coherence: float | None = None
    # P2-016：元数据与可追溯性
    algorithm_version: str = ""
    data_hash: str = ""  # 输入 OP/PV 的 SHA256 摘要
    theta_source: str = ""
    delay_search_trace: list[tuple[int, float]] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    # P2-013：验证集序列（详细审计用，不进 to_dict 默认输出避免响应膨胀）
    y_val_observed: list[float] | None = None
    y_val_predicted: list[float] | None = None
    residuals_val: list[float] | None = None
    # P2-015：参数不确定度摘要（95% CI，仅 ARX/CLIVC 有解析协方差）
    parameter_uncertainty: ParameterUncertainty | None = None
    # P2-019：坏点清洗统计（None 表示无清洗/原始数据无坏点）
    cleaning_stats: dict[str, Any] | None = None
    # T2（2026-10-01）：物理可行性结构化输出（原仅拼进 reason 字符串，
    # 任务层读顶层 physicalFeasibility 恒 None → 版本表字段失效）
    physical_feasibility: dict[str, Any] | None = None
    # P3-06a（R4/R5，P-11）：终验段独立评估——四态 {PASSED, FAILED,
    # NO_DYNAMICS, NOT_RUN} + r2FinalTest + 段点数/极差；None=未运行该段
    # 评估（短数据退化 70/30 时显式 None，不与 NOT_RUN 状态混淆）
    final_test: dict[str, Any] | None = None
    # P3-06a（R6，P-06）：粗增益交叉校验结构化——verdict=PASS/SIGN_MISMATCH/
    # MAGNITUDE_MISMATCH/UNAVAILABLE；mode=CAP（封顶生效）/RECORD_ONLY
    # （有色 OP 闭环下降级为记录性证据不封顶）；basis=判定依据
    nonparam_check: dict[str, Any] | None = None
    # P3-06a（R7，P-07 方向 A）：IPDT 显式漂移项吸收记录（恒偏置/斜坡负载
    # 吸收进差分回归截距与斜坡项；None=非 IPDT 结构或回归未产出）
    ipdt_drift: dict[str, Any] | None = None
    # P3-06a（R17，估计身份层）：估计器 profile——结构/估计方法/阶次/时滞
    # 样本数/profileVersion（快照 manifest estimation.estimatorProfile 承接）
    estimator_profile: dict[str, Any] | None = None
    # P3-06a（B-01）：时滞搜索审计——边界命中（UPPER/LOWER/None）、稀疏化
    # 事实与候选数（07§3.2"物理时滞范围与边界命中"）
    delay_search_info: dict[str, Any] | None = None
    # P3-06c（P-21/R8）：SP 外生性三条件检测记录——verdict ∈ {EXOGENOUS,
    # TRACKING_LOCKSTEP, CASCADE_TEMPLATE, RESIDUAL_CORRELATED,
    # NO_SP_EXCITATION}；None=未提供 SP 或未运行检测（内层调用除外）
    sp_exogeneity: dict[str, Any] | None = None
    # P3-06c（P-22/R11/P-23）：多实验共同估计记录——段块堆叠回归（A/B 共享、
    # 每段独立偏置）；None=非共同估计候选（逐窗候选）
    joint_estimate: dict[str, Any] | None = None
    # P3-06c（P-24）：独立动态事件终验记录——与选模用窗不重叠的自然事件
    # 自由仿真（不参与选模）；None=无可用独立事件或未运行（外层调用才有）
    final_event_test: dict[str, Any] | None = None
    # P3-06c（P-26/R10）：重选时滞/结构的重复估计不确定性（48 次重选全链、
    # 参数分布直接取分位，覆盖全部方法）；None=未运行（数据不足门槛未过）
    reselect_uncertainty: dict[str, Any] | None = None
    # P3-03 终验节点（模型级终验结论）：消费 06a finalTest（尾部 20% 终验段）
    # + 06c finalEventTest（独立事件终验）双通道合成四态 PASSED/FAILED/
    # NO_DYNAMICS/NOT_RUN + 依据链（跨窗稳定性 P-25/重选不确定性 P-26 入
    # 链）；None=未运行终验装配（内层调用/旧结果/单元构造，不伪补）
    final_verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转 dict（摘要，不含原始序列）."""
        return {
            "split": {"train": self.n_train, "val": self.n_val, "test": self.n_test},
            "r2Val": round(self.r2_val, 4),
            "r2Train": round(self.r2_train, 4),
            "nrmseVal": round(self.nrmse_val, 4),
            "residualTest": self.residual_test_note,
            "meanCoherence": (
                round(self.mean_coherence, 4) if self.mean_coherence is not None else None
            ),
            "algorithmVersion": self.algorithm_version,
            "dataHash": self.data_hash,
            "thetaSource": self.theta_source,
            "delaySearchTrace": [{"d": d, "bic": b} for d, b in self.delay_search_trace],
            "reasonCodes": list(self.reason_codes),
            "parameterUncertainty": (
                self.parameter_uncertainty.to_dict()
                if self.parameter_uncertainty is not None
                else None
            ),
            "cleaningStats": self.cleaning_stats,
            "physicalFeasibility": self.physical_feasibility,
            # P3-06a 加性字段（R4~R7/R17/B-01；缺省 None=当时未记录）
            "finalTest": self.final_test,
            "nonparamCheck": self.nonparam_check,
            "ipdtDrift": self.ipdt_drift,
            "estimatorProfile": self.estimator_profile,
            "delaySearchInfo": self.delay_search_info,
            # P3-06c 加性字段（P-21/P-22/P-24/P-26；缺省 None=当时未记录）
            "spExogeneity": self.sp_exogeneity,
            "jointEstimate": self.joint_estimate,
            "finalEventTest": self.final_event_test,
            "reselectUncertainty": self.reselect_uncertainty,
            # P3-03 终验节点加性字段（模型级终验结论；缺省 None=未运行终验
            # 装配，不伪补）
            "finalVerification": self.final_verification,
        }


@dataclass
class CandidateModel:
    """候选模型（多阶次并行辨识结果之一）."""

    params: ModelParams
    fitting_score: float  # R² × 100（P2-002 起为验证集自由仿真 R²）
    confidence: AlgorithmConfidenceLevel
    identify_method: IdentifyMethod
    residual_test_passed: bool
    excitation_score: float
    reason: str | None = None
    # P2-006：信息准则（训练集残差方差计算），用于 Occam 削减与证据输出
    aic: float | None = None
    bic: float | None = None
    # P2-013~016：辨识证据（留出集分割、自由仿真、残差检验、数据快照）
    evidence: ModelEvidence | None = None


@dataclass
class IdentificationResult:
    """辨识结果（identify_from_history 的返回）."""

    success: bool
    best_model: CandidateModel | None = None
    candidates: list[CandidateModel] = field(default_factory=list)
    segments: list[SegmentInfo] = field(default_factory=list)
    reason: str | None = None
    theta_source: ThetaSource | None = None
    # 算法版本单一真相源（pipeline.py re-export 使用；
    # v1.2=IPDT 默认候选+val 稳态防护；
    # v1.5=P3-06a 正确性子卡（R1 时滞物理边界公式+B-01 网格稀疏化与边界
    # 命中标记 / R6 有色 OP 粗增益校验降级记录 / R7 IPDT 显式漂移项 /
    # R4+R5 final-test 三段分数与终验四态 / R17 估计身份证据节）；
    # v1.6=P3-06b 片段与时间尺度子卡（R2 合格连续段全保留+段目录 P-14 /
    # P-12 段最小点数物理化 / P-16 信息量通道 / P-17 自然事件锚点窗 /
    # P-19 采样分辨率标记 / O-A6 时滞边界命中出口 / B-05~B-08 搜索审计）；
    # v1.7=P3-06c 闭环估计与验证子卡（P-21/R8 SP 外生性三条件——CLIVC
    # 适用面收窄+O-C3 恒 SP 闭环无 IV 通道降级 / P-22+P-23 段块堆叠共同
    # 估计与每段偏置 / P-24 独立动态事件终验 / P-25 跨窗稳定性 25% 门禁 /
    # P-26+P-27 重选时滞-结构重复估计不确定性 / B-09~B-10 预算审计））
    algorithm_version: str = "TUNE_IDENT_v1.7"
    # P2-03（R2 §三 DEC-10 补表述）：结构化预处理参数——从 reason 字符串
    # 提升为结构化字段（三件套开关与参数/multi-window 入选窗位/分割比/
    # 清洗统计/后置 data_hash）；evidence.data_hash 等既有字段不受影响。
    preprocessing: dict[str, Any] | None = None

    # P3-03 B1-6：当前唯一可整定结构（整定公式仅定义于 FOPDT——G21 fail-closed；
    # SOPDT/IPDT 专用方法归 P3-07，未通过前不放开，03 号文 P3-07 边界）
    TUNABLE_MODEL_TYPES: ClassVar[tuple[str, ...]] = ("FOPDT",)

    # P3-03 终验节点：可整定判定 = 终验通过（finalVerification.status==PASSED）
    # + 结构支持（TUNABLE_MODEL_TYPES）——二者缺一不可（03 号文 P3-03
    # "独立终验连续模型"验收行 + 07§2"训练好、终验差的模型不得晋升"）
    VERIFICATION_PASSED: ClassVar[str] = "PASSED"

    @staticmethod
    def _confidence_rank(level: str) -> int:
        return {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}.get(level, 0)

    @staticmethod
    def _verification_status(c: CandidateModel) -> str | None:
        """候选的模型级终验结论状态；None=未运行终验装配（不伪补）."""
        fv = c.evidence.final_verification if c.evidence is not None else None
        return fv.get("status") if isinstance(fv, dict) else None

    def select_tunable_candidate(self) -> CandidateModel | None:
        """结构支持的可整定候选（P3-03 终验节点与 B1 modelRole 共用选择）.

        胜出模型本身属可整定结构时即其自身；否则从候选池取结构支持的最优
        候选（按可信度 > 拟合度）。选择**不消费终验结论**（终验不参与
        选择，07§6）；终验 gate 在 modelRole 判定与终验装配层施加（只降
        不升——终验失败的候选不出现在可整定输出，但不改选另一个候选）。
        """
        if not self.success or self.best_model is None:
            return None
        best = self.best_model
        if best.params.model_type.value in self.TUNABLE_MODEL_TYPES:
            return best
        return max(
            (c for c in self.candidates if c.params.model_type.value in self.TUNABLE_MODEL_TYPES),
            key=lambda c: (
                self._confidence_rank(c.confidence.value),
                c.fitting_score,
            ),
            default=None,
        )

    def _model_role(self) -> dict[str, Any] | None:
        """最佳解释模型 vs 可整定模型（字段级区分，P3-03 B1-6 + 终验节点）.

        - bestExplanatory：胜出模型（解释过程动态的最佳候选，可为任意结构）；
        - tunable：可整定模型——判定语义（P3-03 终验节点定稿）= **终验通过
          （模型级终验结论 PASSED）+ 结构支持（TUNABLE_MODEL_TYPES）**：
            - 终验 PASSED → 输出该候选（含其终验结论状态）；
            - 终验 FAILED/NO_DYNAMICS/NOT_RUN → tunable=None（终验差/未实际
              验证过的模型不可进整定链，07§2"训练好、终验差不提升可信度"；
              终验不参与选模故不回取次优候选）；
            - 终验结论缺失（finalVerification=None——内层调用/旧结果/未经
              终验装配的构造场景）→ 维持 B1 结构判定并显式标注缺失（不因
              信息缺失静默拒绝或静默放行）。
        可整定资格仍须经推荐链门禁（authorize_tuning_model 的可信度
        A/B/C 确认校验），此处只做结构+终验区分，不替代门禁。
        """
        if not self.success or self.best_model is None:
            return None
        best = self.best_model
        best_fv = self._verification_status(best)
        explanatory = {
            "modelType": best.params.model_type.value,
            "fittingScore": round(best.fitting_score, 2),
            "confidence": best.confidence.value,
            # P3-03 终验节点：最佳解释模型自身的终验结论（None=未运行装配）
            "finalVerification": best_fv,
        }
        tunable_cand = self.select_tunable_candidate()
        if tunable_cand is None:
            return {
                "bestExplanatory": explanatory,
                "tunable": None,
                "note": (
                    f"胜出模型 {best.params.model_type.value} 不可整定（整定公式仅定义于 "
                    f"{'/'.join(self.TUNABLE_MODEL_TYPES)}），且候选池无可整定结构——"
                    "只能作解释模型使用；SOPDT/IPDT 专用整定方法归 P3-07"
                ),
            }
        tun_fv = self._verification_status(tunable_cand)
        if tun_fv is not None and tun_fv != self.VERIFICATION_PASSED:
            # P3-03 终验节点：结构支持但终验未通过——不可整定（终验不参与
            # 选模，不回取次优候选；缺信息显式呈现终验状态）
            return {
                "bestExplanatory": explanatory,
                "tunable": None,
                "note": (
                    f"结构支持的可整定候选 {tunable_cand.params.model_type.value} 模型级"
                    f"终验未通过（finalVerification={tun_fv}）——可整定判定=终验通过"
                    f"+结构支持，二者缺一不可（07§2 训练好、终验差的模型不得晋升；"
                    "终验不参与选模，不回取次优候选）"
                ),
            }
        tunable_view = {
            "modelType": tunable_cand.params.model_type.value,
            "fittingScore": round(tunable_cand.fitting_score, 2),
            "confidence": tunable_cand.confidence.value,
            # P3-03 终验节点：可整定候选的终验结论（PASSED 或 None=未运行装配）
            "finalVerification": tun_fv,
        }
        if tunable_cand is best:
            fv_note = (
                "；模型级终验通过（PASSED）"
                if tun_fv == self.VERIFICATION_PASSED
                else "；终验结论缺失（终验装配未运行，如旧结果/单元构造）"
            )
            return {
                "bestExplanatory": explanatory,
                "tunable": {**tunable_view, "isBestModel": True},
                "note": (
                    f"胜出模型 {best.params.model_type.value} 为当前唯一可整定结构"
                    f"{fv_note}；可整定资格仍须经推荐链可信度门禁"
                    "（A/B 放行、C 需确认、D/E 拒绝）"
                ),
            }
        return {
            "bestExplanatory": explanatory,
            "tunable": {**tunable_view, "isBestModel": False},
            "note": (
                f"胜出解释模型 {best.params.model_type.value} 不可整定"
                f"（SOPDT/IPDT 专用方法归 P3-07，未过前不放开）；"
                f"可整定模型取候选池最优 {tunable_cand.params.model_type.value}"
                + (
                    "，模型级终验通过（PASSED）"
                    if tun_fv == self.VERIFICATION_PASSED
                    else "；终验结论缺失（终验装配未运行）"
                )
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """转 dict（API 响应）."""
        if not self.success or self.best_model is None:
            result = {
                "success": False,
                "reason": self.reason,
                "algorithmVersion": self.algorithm_version,
                "preprocessing": self.preprocessing,
            }
            if self.theta_source is not None:
                result["thetaSource"] = self.theta_source.value
            return result
        result = {
            "success": True,
            "modelType": self.best_model.params.model_type.value,
            "params": self.best_model.params.to_dict(),
            "fittingScore": round(self.best_model.fitting_score, 2),
            "confidenceLevel": self.best_model.confidence.value,
            "identifyMethod": self.best_model.identify_method.value,
            "excitationScore": round(self.best_model.excitation_score, 2),
            "residualTestPassed": self.best_model.residual_test_passed,
            "aic": round(self.best_model.aic, 2) if self.best_model.aic is not None else None,
            "bic": round(self.best_model.bic, 2) if self.best_model.bic is not None else None,
            "evidence": self.best_model.evidence.to_dict() if self.best_model.evidence else None,
            "candidateModels": [
                {
                    "modelType": c.params.model_type.value,
                    "params": c.params.to_dict(),
                    "fittingScore": round(c.fitting_score, 2),
                    "confidence": c.confidence.value,
                    "aic": round(c.aic, 2) if c.aic is not None else None,
                    "bic": round(c.bic, 2) if c.bic is not None else None,
                }
                for c in self.candidates
            ],
            # P3-03 B1-6：最佳解释模型与可整定模型显式区分（字段级）
            "modelRole": self._model_role(),
            "algorithmVersion": self.algorithm_version,
            "reason": self.best_model.reason,
            "preprocessing": self.preprocessing,
        }
        if self.theta_source is not None:
            result["thetaSource"] = self.theta_source.value
        return result
