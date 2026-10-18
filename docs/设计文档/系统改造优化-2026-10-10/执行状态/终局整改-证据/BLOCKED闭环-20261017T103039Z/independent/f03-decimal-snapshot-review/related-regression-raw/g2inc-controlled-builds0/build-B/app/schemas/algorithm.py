"""算法服务接口 Schema (IDS v3.2 §2.7).

包装现有 services 层（MetricCalculator / diagnosis_engine / tuning_algorithms）
为外部系统提供同步计算接口。同时提供算法任务状态查询（Celery AsyncResult）。

设计依据：IDS §2.7.1/§2.7.2/§2.7.3/§2.7.4
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# §2.7.1 KPI 计算
# ---------------------------------------------------------------------------


class KpiCalculateRequest(CamelModel):
    """独立 KPI 计算请求（同步计算单回路单指标）.

    Attributes:
        loopId: 目标回路 ID
        metric: 指标代码（如 accuracy_rate/steady_rate/good_value_rate 等）
        startTime: 时间窗起始（ISO 8601）
        endTime: 时间窗结束（ISO 8601）
        forceRecalculate: 是否强制重算（忽略缓存），默认 false
    """

    loopId: str = Field(..., description="目标回路 ID")
    metric: str = Field(..., description="指标代码")
    startTime: str = Field(..., description="时间窗起始（ISO 8601）")
    endTime: str = Field(..., description="时间窗结束（ISO 8601）")
    forceRecalculate: bool = Field(False, description="是否强制重算")


class KpiMetricResult(CamelModel):
    """单指标计算结果（含数据血缘与置信度）.

    Attributes:
        loopId: 回路 ID
        metric: 指标代码
        value: 指标值
        confidenceLevel: 置信度等级 A/B/C/D/E
        validRate: 有效数据率 0~1
        dataLineage: 数据血缘 JSON 对象
        algorithmVersion: 算法版本号
    """

    loopId: str
    metric: str
    value: float | None = None
    confidenceLevel: str | None = None
    validRate: float | None = None
    dataLineage: dict[str, Any] | None = None
    algorithmVersion: str = "KPI_CALC_v1.0"


# ---------------------------------------------------------------------------
# §2.7.2 诊断分析
# ---------------------------------------------------------------------------


class DiagnosisAnalyzeRequest(CamelModel):
    """独立诊断分析请求（同步分析单回路）.

    Attributes:
        loopId: 目标回路 ID
        startTime: 时间窗起始（ISO 8601）
        endTime: 时间窗结束（ISO 8601）
        labels: 启用的诊断标签子集（空列表表示启用全部，MANUAL_REVIEW 除外）
        enableFusion: 是否启用 DS 证据融合，默认 true
    """

    loopId: str = Field(..., description="目标回路 ID")
    startTime: str = Field(..., description="时间窗起始（ISO 8601）")
    endTime: str = Field(..., description="时间窗结束（ISO 8601）")
    labels: list[str] = Field(
        default_factory=list,
        description="诊断标签子集；空列表表示全部",
    )
    enableFusion: bool = Field(True, description="是否启用证据融合")


class DiagnosisLabelResult(CamelModel):
    """诊断标签结果项.

    Attributes:
        label: 诊断标签枚举
        confidence: 置信度 0~1
        evidence: 证据对象
        algorithm: 算法标识
        fusedConfidence: 融合后置信度（启用融合时返回）
    """

    label: str
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    algorithm: str | None = None
    fusedConfidence: float | None = None


class DiagnosisAnalyzeResponse(CamelModel):
    """诊断分析响应.

    Attributes:
        loopId: 回路 ID
        tagName: 位号
        diagnosisLabels: 诊断标签列表
        algorithmVersion: 算法版本号
    """

    loopId: str
    tagName: str | None = None
    diagnosisLabels: list[DiagnosisLabelResult] = Field(default_factory=list)
    algorithmVersion: str = "DIAG_ENGINE_v1.0"


# ---------------------------------------------------------------------------
# §2.7.3 整定计算
# ---------------------------------------------------------------------------


class IdentificationDataSegment(CamelModel):
    """辨识数据段时间窗.

    Attributes:
        startTime: 起始时间（ISO 8601）
        endTime: 结束时间（ISO 8601）
    """

    startTime: str
    endTime: str


class IdentificationParams(CamelModel):
    """模型辨识参数.

    Attributes:
        dataSegment: 数据段时间窗
        samplePeriod: 采样周期（秒）
        modelType: 模型类型 FOPDT/SOPDT/IPDT
        method: 辨识方法 TWO_POINT/AREA/COMBINED
    """

    dataSegment: IdentificationDataSegment
    samplePeriod: float = Field(1.0, gt=0, description="采样周期（秒）")
    modelType: str = Field("FOPDT", description="模型类型：FOPDT/SOPDT/IPDT")
    method: str = Field("TWO_POINT", description="辨识方法：TWO_POINT/AREA/COMBINED")


class TuningParams(CamelModel):
    """PID 整定参数.

    Attributes:
        method: 整定方法 IMC/LAMBDA/ZIEGLER_NICHOLS/COHEN_COON/SIMC
        params: 方法参数（如 IMC 的 lambda）
    """

    method: str = Field("IMC", description="整定方法")
    params: dict[str, Any] = Field(default_factory=dict, description="方法参数")


class SimulationConfig(CamelModel):
    """闭环仿真配置.

    Attributes:
        disturbanceType: 扰动类型 step
        simulationDuration: 仿真时长（秒）
    """

    disturbanceType: str = Field("step", description="扰动类型")
    simulationDuration: float = Field(300.0, gt=0, description="仿真时长（秒）")


class TuningCalculateRequest(CamelModel):
    """独立整定计算请求（同步计算 PID 参数）.

    Attributes:
        loopId: 目标回路 ID
        identificationParams: 模型辨识参数
        tuningParams: PID 整定参数
        enableSimulation: 是否执行闭环仿真
        simulationConfig: 仿真配置（enableSimulation=true 时生效）
    """

    loopId: str = Field(..., description="目标回路 ID")
    identificationParams: IdentificationParams
    tuningParams: TuningParams
    enableSimulation: bool = Field(True, description="是否执行闭环仿真")
    simulationConfig: SimulationConfig | None = None


class ModelParamsSchema(CamelModel):
    """模型参数."""

    # to_camel lowercases the first char (K→k, T1→t1); use explicit aliases
    # to preserve standard control-theory notation.
    K: float | None = Field(None, alias="K")
    tau: float | None = None
    theta: float | None = None
    T1: float | None = Field(None, alias="T1")
    T2: float | None = Field(None, alias="T2")


class PIDParamsSchema(CamelModel):
    """推荐 PID 参数."""

    Kp: float | None = Field(None, alias="Kp")
    Ti: float | None = Field(None, alias="Ti")
    Td: float | None = Field(None, alias="Td")


class SimulationResultSchema(CamelModel):
    """仿真性能指标."""

    riseTime: float | None = None
    overshoot: float | None = None
    settlingTime: float | None = None
    itae: float | None = None


class TuningCalculateResponse(CamelModel):
    """整定计算响应.

    Attributes:
        loopId: 回路 ID
        modelType: 模型类型
        modelParams: 模型参数
        fittingScore: 模型拟合度 R²（0-1）
        pidParams: 推荐 PID 参数
        simulationResult: 仿真性能指标
        algorithmVersion: 算法版本号
    """

    loopId: str
    modelType: str | None = None
    modelParams: ModelParamsSchema | None = None
    fittingScore: float | None = None
    pidParams: PIDParamsSchema | None = None
    simulationResult: SimulationResultSchema | None = None
    algorithmVersion: str = "TUNE_ENGINE_v1.0"


# ---------------------------------------------------------------------------
# §2.7.4 算法任务状态查询
# ---------------------------------------------------------------------------


class AlgorithmTaskStatus(CamelModel):
    """算法任务状态响应（查询 Celery AsyncResult）.

    Attributes:
        taskId: 任务 ID（Celery task id）
        status: 任务状态（PENDING/STARTED/SUCCESS/FAILURE/REVOKED）
        progress: 进度 0~1（若可用）
        result: 任务结果（SUCCESS 时返回）
        error: 错误信息（FAILURE 时返回）
        receivedAt: 任务接收时间
    """

    taskId: str
    status: str
    progress: float | None = None
    result: Any = None
    error: str | None = None
    receivedAt: str | None = None


__all__ = [
    "AlgorithmTaskStatus",
    "DiagnosisAnalyzeRequest",
    "DiagnosisAnalyzeResponse",
    "DiagnosisLabelResult",
    "IdentificationDataSegment",
    "IdentificationParams",
    "KpiCalculateRequest",
    "KpiMetricResult",
    "ModelParamsSchema",
    "PIDParamsSchema",
    "SimulationConfig",
    "SimulationResultSchema",
    "TuningCalculateRequest",
    "TuningCalculateResponse",
    "TuningParams",
]
