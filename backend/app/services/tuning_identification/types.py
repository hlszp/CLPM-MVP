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
    保守启发值，不能获得高于 C 的可信度。
    """

    EXPLICIT = "EXPLICIT"
    HEURISTIC_2TS = "HEURISTIC_2TS"


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
class CandidateModel:
    """候选模型（多阶次并行辨识结果之一）."""

    params: ModelParams
    fitting_score: float  # R² × 100
    confidence: ConfidenceLevel
    identify_method: IdentifyMethod
    residual_test_passed: bool
    excitation_score: float
    reason: str | None = None


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
            "candidateModels": [
                {
                    "modelType": c.params.model_type.value,
                    "params": c.params.to_dict(),
                    "fittingScore": round(c.fitting_score, 2),
                    "confidence": c.confidence.value,
                }
                for c in self.candidates
            ],
            "algorithmVersion": self.algorithm_version,
            "reason": self.best_model.reason,
        }
        if self.theta_source is not None:
            result["thetaSource"] = self.theta_source.value
        return result
