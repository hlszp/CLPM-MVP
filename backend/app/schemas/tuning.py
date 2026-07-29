"""Tuning center schemas (IDS v3.2 §2.5 — S7-TUNE-006).

对齐关键算法设计说明 v1.0 §6：
- FOPDT/SOPDT/IPDT 模型辨识
- IMC/Lambda/Z-N/Cohen-Coon/SIMC PID 整定
- 闭环仿真（RK4 + 增量式 PID）

Phase 2 扩展（2026-07-28）：
- 历史数据辨识（identifyStrategy/candidateModelTypes/confidenceLevel）
- 多 PID 对比（pidCandidates/candidateResponses）
- 异步任务（TaskProgress）
- 状态机对齐实现契约（DRAFT/RUNNING/IDENTIFIED/SIMULATED/COMPLETED/INCONCLUSIVE/ROLLED_BACK）
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 枚举类型定义（S4-C3）
# 与数据库 CheckConstraint 保持一致（app/models/tuning.py）
# ---------------------------------------------------------------------------

# 模型类型：FOPDT/SOPDT/IPDT
ModelType = Literal["FOPDT", "SOPDT", "IPDT"]

# 历史算法当前仅对 FOPDT/SOPDT 有真实转换链；IPDT 保留在阶跃实验路径。
HistoryModelType = Literal["FOPDT", "SOPDT"]

# 整定算法：IMC/LAMBDA/ZN/COHEN_COON/SIMC
TuningAlgorithm = Literal["IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"]

# 整定任务状态（Phase 2 新枚举 + 兼容旧枚举）
TuningTaskStatus = Literal[
    # Phase 2 新枚举
    "DRAFT",
    "RUNNING",
    "IDENTIFIED",
    "SIMULATED",
    "COMPLETED",
    "INCONCLUSIVE",
    "ROLLED_BACK",
    # 旧枚举（兼容期保留）
    "PENDING",
    "APPLIED",
    "VERIFIED",
]

# 辨识策略
IdentifyStrategy = Literal["AUTO", "HISTORY_ONLY", "STEP_ONLY"]

# 辨识方法
IdentifyMethod = Literal[
    "HISTORICAL_ARX",
    "HISTORICAL_ARMAX",
    "HISTORICAL_IV",
    "STEP_TWO_POINT",
    "STEP_AREA",
    "STEP_NLS",
]

# 数据来源（fallback_step = AUTO 策略历史辨识失败/数据不足后的阶跃兜底标记，P1-6）
DataSource = Literal["HISTORY", "STEP_EXPERIMENT", "fallback_step"]

# 可信度等级
ConfidenceLevel = Literal["A", "B", "C", "D", "E", "INCONCLUSIVE"]

# 纯滞后参数来源
ThetaSource = Literal["EXPLICIT", "HEURISTIC_2TS"]

# 推荐链模型来源。STEP_EXPERIMENT 只能由服务端验证过的阶跃记录/内部链路放行。
ModelSource = Literal["IDENTIFICATION_RECORD", "STEP_EXPERIMENT", "MANUAL"]


# ---------------------------------------------------------------------------
# 模型辨识
# ---------------------------------------------------------------------------


class ModelIdentifyRequest(CamelModel):
    """POST /tuning/identify 请求体（阶跃实验路径，保留向后兼容）。"""

    loopId: str = Field(..., description="回路 ID")
    startTime: str = Field(..., description="起始时间 ISO 8601")
    endTime: str = Field(..., description="结束时间 ISO 8601")
    modelType: ModelType = Field("FOPDT", description="模型类型: FOPDT/SOPDT/IPDT")
    method: str | None = Field(
        None, description="辨识方法: TWO_POINT/AREA（仅 FOPDT，默认 TWO_POINT）"
    )


class ModelIdentifyHistoryRequest(CamelModel):
    """POST /tuning/identify/history 请求体（Phase 2 历史数据辨识路径）."""

    loopId: str = Field(..., description="回路 ID")
    startTime: str = Field(..., description="起始时间 ISO 8601")
    endTime: str = Field(..., description="结束时间 ISO 8601")
    identifyStrategy: IdentifyStrategy = Field(
        "AUTO", description="辨识策略: AUTO(优先历史,失败兜底阶跃)/HISTORY_ONLY/STEP_ONLY"
    )
    candidateModelTypes: list[HistoryModelType] | None = Field(
        None, description="候选模型阶次列表，默认 [FOPDT, SOPDT]"
    )
    thetaEstimate: float | None = Field(
        None,
        ge=0,
        description="纯滞后预估值（秒）；None 使用 2Ts 启发值并将可信度封顶 C",
    )


class ModelParams(CamelModel):
    """模型参数。"""

    K: float | None = Field(None, description="过程增益")
    tau: float | None = Field(None, description="时间常数（秒）")
    theta: float | None = Field(None, description="死区时间（秒）")
    T1: float | None = Field(None, description="SOPDT 第一时间常数（秒）")
    T2: float | None = Field(None, description="SOPDT 第二时间常数（秒）")


class CandidateModel(CamelModel):
    """候选模型（多阶次并行辨识结果之一）。"""

    modelType: ModelType
    params: ModelParams
    fittingScore: float = Field(..., description="拟合度 R²（%）")
    confidence: ConfidenceLevel
    identifyMethod: IdentifyMethod | None = None
    residualTestPassed: bool | None = None
    excitationScore: float | None = None
    reason: str | None = None


class ModelIdentifyResult(CamelModel):
    """模型辨识结果（阶跃实验路径）。"""

    modelType: ModelType
    params: ModelParams
    fittingScore: float = Field(..., description="拟合度 R²（%）")
    algorithmVersion: str
    dataPoints: int = Field(..., description="参与辨识的数据点数")
    recordId: str | None = Field(None, description="服务端持久化的阶跃辨识记录 ID")
    # 拟合曲线（用于前端可视化）
    fittedCurve: dict[str, list[Any]] | None = Field(
        None, description="拟合曲线 {timestamps: [], pv: [], fitted: []}"
    )


class ModelIdentifyHistoryResult(CamelModel):
    """历史数据辨识结果（Phase 2 路径）."""

    success: bool
    modelType: str | None = None
    params: dict[str, Any] | None = None
    fittingScore: float | None = Field(None, description="拟合度 R²（%）")
    confidenceLevel: ConfidenceLevel | None = None
    dataConfidenceLevel: ConfidenceLevel | None = Field(
        None, description="数据质量可信度（基于 valid_rate）"
    )
    confidenceReason: str | None = None
    thetaSource: ThetaSource | None = Field(None, description="纯滞后参数来源")
    excitationScore: float | None = None
    residualTestPassed: bool | None = None
    identifyMethod: IdentifyMethod | None = None
    candidateModels: list[CandidateModel] | None = None
    algorithmVersion: str | None = None
    dataPoints: int | None = None
    validRate: float | None = Field(None, description="有效数据率 0~1")
    samplingFreq: float | None = Field(None, description="采样频率（Hz）")
    reason: str | None = None
    tagName: str | None = None


# ---------------------------------------------------------------------------
# PID 整定
# ---------------------------------------------------------------------------


class PidParams(CamelModel):
    """PID 参数。"""

    kp: float = Field(..., description="比例增益")
    ti: float = Field(..., description="积分时间（秒）")
    td: float = Field(0.0, description="微分时间（秒）")


class PidParamsWithLabel(CamelModel):
    """带标签的 PID 参数（用于多 PID 对比）."""

    label: str = Field(..., description="PID 标签（如 IMC λ=1.0）")
    kp: float = Field(..., description="比例增益")
    ti: float = Field(..., description="积分时间（秒）")
    td: float = Field(0.0, description="微分时间（秒）")


class TuneRequest(CamelModel):
    """POST /tuning/tune 请求体。"""

    modelType: ModelType = Field(..., description="模型类型: FOPDT/SOPDT/IPDT")
    modelParams: ModelParams
    algorithm: TuningAlgorithm = Field(..., description="整定算法: IMC/LAMBDA/ZN/COHEN_COON/SIMC")
    algorithmParams: dict[str, Any] | None = Field(
        None, description="算法参数（如 lambda 比例系数）"
    )
    currentPid: PidParams | None = Field(None, description="当前 PID 参数（用于对比）")
    loopId: str | None = Field(None, description="回路 ID（可选，用于记录）")
    sourceRecordId: str | None = Field(None, description="模型辨识记录 ID")
    modelSource: ModelSource | None = Field(
        None,
        description="模型来源；旧请求可解析但不会绕过服务端安全门禁",
    )
    riskConfirmed: bool = Field(False, description="是否已显式确认 C 级/人工模型风险")


class TuneResult(CamelModel):
    """PID 整定结果。"""

    algorithm: TuningAlgorithm
    recommendedPid: PidParams
    currentPid: PidParams | None = None
    algorithmParams: dict[str, Any] | None = None
    algorithmVersion: str
    notes: str | None = Field(None, description="整定说明")


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


class SimulateRequest(CamelModel):
    """POST /tuning/simulate 请求体。"""

    modelType: ModelType = Field("FOPDT", description="模型类型")
    modelParams: ModelParams
    currentPid: PidParams
    recommendedPid: PidParams
    pidCandidates: list[PidParamsWithLabel] | None = Field(
        None, description="多组候选 PID 参数（Phase 2 新增，向后兼容）"
    )
    simDuration: float = Field(600.0, description="仿真时长（秒）")
    simStep: float = Field(1.0, description="仿真步长（秒）")
    setpointStep: float = Field(1.0, description="设定值阶跃幅值")
    disturbanceType: str = Field("step", description="扰动类型: step/none")
    loopId: str | None = Field(None, description="回路 ID（模型记录校验用）")
    sourceRecordId: str | None = Field(None, description="模型辨识记录 ID")
    modelSource: ModelSource | None = Field(
        None,
        description="模型来源；旧请求可解析但不会绕过服务端安全门禁",
    )
    riskConfirmed: bool = Field(False, description="是否已显式确认 C 级/人工模型风险")


class SimulationMetrics(CamelModel):
    """仿真性能指标。"""

    riseTime: float | None = Field(None, description="上升时间（秒）")
    overshoot: float | None = Field(None, description="超调量（%）")
    settlingTime: float | None = Field(None, description="稳定时间（秒）")
    itae: float | None = Field(None, description="ITAE 积分")


class CandidateResponse(CamelModel):
    """候选 PID 响应（多 PID 对比）."""

    label: str = Field(..., description="PID 标签")
    response: dict[str, list[float]]
    metrics: SimulationMetrics


class SimulationResult(CamelModel):
    """闭环仿真结果。"""

    timestamps: list[float]
    currentResponse: dict[str, list[float]]
    recommendedResponse: dict[str, list[float]]
    currentMetrics: SimulationMetrics
    recommendedMetrics: SimulationMetrics
    improvement: dict[str, float | None]
    # Phase 2 新增：多 PID 对比
    candidateResponses: list[CandidateResponse] | None = None


# ---------------------------------------------------------------------------
# 整定任务记录
# ---------------------------------------------------------------------------


class TuningTaskItem(CamelModel):
    """整定任务列表项。"""

    id: str
    loopId: str
    tagName: str | None = None
    modelType: ModelType
    modelParams: dict[str, Any] | None = None
    algorithm: TuningAlgorithm
    recommendedPid: dict[str, Any] | None = None
    fittingScore: float | None = None
    status: TuningTaskStatus
    createdBy: str | None = None
    createdAt: str
    # Phase 2.2 新增字段
    identifyMethod: IdentifyMethod | None = None
    dataSource: DataSource | None = None
    confidenceLevel: ConfidenceLevel | None = None
    confidenceReason: str | None = None
    excitationScore: float | None = None
    residualTestPassed: bool | None = None
    taskId: str | None = None
    completedAt: str | None = None


class TuningTaskDetail(TuningTaskItem):
    """整定任务详情。"""

    simulationResult: dict[str, Any] | None = None
    currentPid: dict[str, Any] | None = None
    # Phase 2.2 新增
    pidCandidates: dict[str, Any] | None = None
    candidateResults: dict[str, Any] | None = None


class CreateTuningTaskRequest(CamelModel):
    """创建整定任务（保存整定结果）。"""

    loopId: str
    modelType: ModelType
    modelParams: ModelParams
    algorithm: TuningAlgorithm
    recommendedPid: PidParams
    currentPid: PidParams | None = None
    fittingScore: float | None = None
    simulationResult: dict[str, Any] | None = None
    status: TuningTaskStatus = Field("SIMULATED", description="任务状态")
    # Phase 2.2 新增
    identifyMethod: IdentifyMethod | None = None
    dataSource: DataSource | None = None
    confidenceLevel: ConfidenceLevel | None = None
    confidenceReason: str | None = None
    excitationScore: float | None = None
    residualTestPassed: bool | None = None
    pidCandidates: dict[str, Any] | None = None
    candidateResults: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 异步任务进度（Phase 2.2 新增）
# ---------------------------------------------------------------------------


class TaskProgress(CamelModel):
    """异步任务进度。"""

    taskId: str
    status: str = Field(..., description="任务状态: PENDING/RUNNING/SUCCESS/FAILED")
    progress: float = Field(0.0, description="进度 0~100")
    stage: str | None = Field(None, description="当前阶段: excitation/nonparametric/identify/...")
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# 辨识片段预览（Phase 2.2 新增）
# ---------------------------------------------------------------------------


class IdentifySegment(CamelModel):
    """可辨识片段预览。"""

    startIdx: int
    endIdx: int
    mode: str | None = None
    excitationScore: float | None = None
    conditionNumber: float | None = None
    isSufficient: bool = False


class IdentifySegmentsRequest(CamelModel):
    """POST /tuning/identify/segments 请求体."""

    loopId: str
    startTime: str
    endTime: str


class IdentifySegmentsResult(CamelModel):
    """可辨识片段预览结果。"""

    loopId: str
    totalSegments: int
    segments: list[IdentifySegment]
    sufficientCount: int = Field(0, description="激励充分片段数")


# ---------------------------------------------------------------------------
# 整定效果统计
# ---------------------------------------------------------------------------


class TuningHistoryStats(CamelModel):
    """整定历史统计。"""

    totalTasks: int
    byAlgorithm: dict[str, int]
    byStatus: dict[str, int]
    avgFittingScore: float | None = None
    recentTasks: list[TuningTaskItem]


# ---------------------------------------------------------------------------
# 整定方法信息
# ---------------------------------------------------------------------------


class TuningMethodInfo(CamelModel):
    """整定方法信息。"""

    code: str
    name: str
    description: str
    applicableModel: str
    params: list[dict[str, Any]]
