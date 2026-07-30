"""辨识算法栈共享数据结构."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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


class ConfidenceLevel(StrEnum):
    """可信度等级（对齐 ConfidenceEvaluator A/B/C/D/E）."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    INCONCLUSIVE = "INCONCLUSIVE"


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
    n_mc_samples: int = 0  # 有效 Monte Carlo 采样数（转换成功）

    def to_dict(self) -> dict[str, Any]:
        return {
            "K": {"ci95": [round(self.K_ci_lower, 4), round(self.K_ci_upper, 4)]},
            "tau": {"ci95": [round(self.tau_ci_lower, 4), round(self.tau_ci_upper, 4)]},
            "theta": {"ci95": [round(self.theta_ci_lower, 4), round(self.theta_ci_upper, 4)]},
            "nMcSamples": self.n_mc_samples,
        }


@dataclass
class ExcitationCheckResult:
    """激励检测结果."""

    is_sufficient: bool
    significant_changes: int
    condition_number: float
    verdict: str
    confidence: ConfidenceLevel = ConfidenceLevel.INCONCLUSIVE


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
        }


@dataclass
class CandidateModel:
    """候选模型（多阶次并行辨识结果之一）."""

    params: ModelParams
    fitting_score: float  # R² × 100（P2-002 起为验证集自由仿真 R²）
    confidence: ConfidenceLevel
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
    algorithm_version: str = "TUNE_IDENT_v1.0"

    def to_dict(self) -> dict[str, Any]:
        """转 dict（API 响应）."""
        if not self.success or self.best_model is None:
            result = {
                "success": False,
                "reason": self.reason,
                "algorithmVersion": self.algorithm_version,
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
            "algorithmVersion": self.algorithm_version,
            "reason": self.best_model.reason,
        }
        if self.theta_source is not None:
            result["thetaSource"] = self.theta_source.value
        return result
