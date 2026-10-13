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

from pydantic import Field, model_validator

from app.schemas.base import CamelModel
from app.services.tuning_algorithms import validate_tuning_params

# ---------------------------------------------------------------------------
# 枚举类型定义（S4-C3）
# 与数据库 CheckConstraint 保持一致（app/models/tuning.py）
# ---------------------------------------------------------------------------

# 模型类型：FOPDT/SOPDT/IPDT
ModelType = Literal["FOPDT", "SOPDT", "IPDT"]

# 历史辨识支持的模型类型（P2-008：IPDT 差分辨识链已接入历史路径）
HistoryModelType = Literal["FOPDT", "SOPDT", "IPDT"]

# 整定算法：IMC/LAMBDA/ZN/COHEN_COON/SIMC（FOPDT 五法）+
# IPDT_PI/SOPDT_IMC（P3-07：IPDT/SOPDT 专用方法，注册于 TUNING_METHODS_INFO）
# V62-P3-006：新增 IDENTIFICATION_ONLY——纯辨识记录不再用 IMC 占位
# MANUAL_TUNING：矩阵第 6 行"手动整定"——参数由工程师手工设定，不经算法计算
TuningAlgorithm = Literal[
    "IMC",
    "LAMBDA",
    "ZN",
    "COHEN_COON",
    "SIMC",
    "IPDT_PI",
    "SOPDT_IMC",
    "IDENTIFICATION_ONLY",
    "MANUAL_TUNING",
]

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

# 纯滞后参数来源（P2-001：SEARCHED = BIC 候选搜索数据驱动确定）
ThetaSource = Literal["EXPLICIT", "HEURISTIC_2TS", "SEARCHED"]

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


class IdentifyHistoryAsyncResponse(CamelModel):
    """历史辨识异步任务提交响应（V62-P1-012 typed response）.

    AUTO/HISTORY_ONLY 策略提交异步 Celery 任务后返回此模型；
    STEP_ONLY 策略走同步路径，返回 ``ModelIdentifyResult``。
    """

    taskId: str = Field(..., description="Celery 异步任务 ID")
    status: str = Field("PENDING", description="任务初始状态")
    identifyStrategy: IdentifyStrategy | None = Field(
        None, description="辨识策略（AUTO/HISTORY_ONLY）"
    )


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

    @model_validator(mode="after")
    def _validate_algorithm_params(self) -> TuneRequest:
        """P2-01 TUN-05：algorithmParams 按方法注册表（TUNING_METHODS_INFO）校验.

        未知键、非数值/非有限、越界、非法枚举（如非法 controllerType）
        在 schema 层原子拒绝（422）；合法输入形状不变。
        """
        if self.algorithmParams:
            errors = validate_tuning_params(self.algorithm, self.algorithmParams)
            if errors:
                raise ValueError("；".join(errors))
        return self


class TuningRisk(CamelModel):
    """V62-P3-007 整定风险评估。"""

    riskLevel: str = Field(..., description="风险等级：LOW/MEDIUM/HIGH")
    factors: list[str] = Field(
        default_factory=list, description="风险因素列表（如 PID 变化幅度大、模型可信度低）"
    )
    description: str | None = Field(None, description="风险说明与注意事项")


class TuneResult(CamelModel):
    """PID 整定结果（V62-P3-007 含人工实施清单字段）."""

    algorithm: TuningAlgorithm
    recommendedPid: PidParams
    currentPid: PidParams | None = None
    algorithmParams: dict[str, Any] | None = None
    algorithmVersion: str
    notes: str | None = Field(None, description="整定说明")
    # V62-P3-007：人工实施清单
    risk: TuningRisk | None = Field(None, description="风险评估")
    rollbackPid: PidParams | None = Field(None, description="回退 PID 值（实施失败时恢复）")
    unitConversion: dict[str, Any] | None = Field(
        None, description="单位转换说明（如时间单位秒↔分、量程转换）"
    )
    # P3-07（P-35 来源保存）：建议来源绑定——模型版本/终验结论/整定方法与
    # 参数版本随建议携带（B2 冻结链兼容：加性键）
    recommendationSource: dict[str, Any] | None = Field(
        None, description="建议来源绑定（模型来源/版本/终验结论/方法与参数版本）"
    )
    # R03（G3 增量纠偏 2026-10-13）：适用性限制加性携带——低 SNR
    # （LOW_SNR_PARAM_UNCOMMITTED，不承诺参数级精度）与未校准 ci95
    # （CI95_UNCALIBRATED，仅证明被测集合成员）；S1-b 放行政策保持
    # （提示不进门禁）；缺键=授权来源无已知限制
    applicabilityLimitations: dict[str, Any] | None = Field(
        None, description="适用性限制（低 SNR 参数精度不承诺 / ci95 未校准集合成员边界）"
    )


class TuneMatrixRequest(CamelModel):
    """POST /tuning/tune/matrix 请求体（09 设计方案 §4.2 全算法矩阵）。

    与 TuneRequest 相同但无 algorithm 单值字段——5 种算法一次全算，
    单行失败不阻断（该行返回错误标记，前端置灰）。
    """

    modelType: ModelType = Field(..., description="模型类型: FOPDT/SOPDT/IPDT")
    modelParams: ModelParams
    algorithmParams: dict[str, Any] | None = Field(
        None, description="算法参数（如 lambda 比例系数），5 算法共用"
    )
    currentPid: PidParams | None = Field(None, description="当前 PID 参数（用于对比）")
    loopId: str | None = Field(None, description="回路 ID（可选，用于记录）")
    sourceRecordId: str | None = Field(None, description="模型辨识记录 ID")
    modelSource: ModelSource | None = Field(
        None,
        description="模型来源；旧请求可解析但不会绕过服务端安全门禁",
    )
    riskConfirmed: bool = Field(False, description="是否已显式确认 C 级/人工模型风险")

    @model_validator(mode="after")
    def _validate_algorithm_params_union(self) -> TuneMatrixRequest:
        """P2-01 TUN-05：矩阵共用参数按全算法联合注册表校验.

        同一份 algorithmParams 跨 5 算法共用：键在任一算法注册即合法
        （如 controllerType 属于 ZN/COHEN_COON、lambdaRatio 属于 IMC/LAMBDA），
        值须满足其所属算法的类型/范围/枚举约束；全算法都不认识的键拒绝。
        """
        if self.algorithmParams:
            errors = validate_tuning_params(None, self.algorithmParams)
            if errors:
                raise ValueError("；".join(errors))
        return self


class RobustVerifyRequest(CamelModel):
    """POST /tuning/tune/robustness 请求体（P3-07 R15 鲁棒验证链入口）.

    对服务端已验证来源的模型 + 整定建议，在可信模型集合（终验通过模型
    ± 参数 95% CI 采样，≤64 成员）上按 R15 冻结指标（GM≥2.0/PM≥30°/
    超调≤20%/OP 实际限幅）验证；未知 DCS 表示、未核实 OP 限位、终验
    未通过的模型明确拒绝（O-D6/P-34）。
    """

    loopId: str
    modelType: ModelType
    modelParams: ModelParams
    algorithm: TuningAlgorithm
    algorithmParams: dict[str, Any] | None = Field(
        None, description="算法参数（如 lambdaRatio）；按方法注册表校验"
    )
    recommendedPid: PidParams | None = Field(
        None,
        description="待验证建议 PID；缺省=按 algorithm+algorithmParams 服务端现算",
    )
    simDuration: float = Field(
        600.0, ge=1.0, le=600.0, description="集合单成员仿真时长（秒，B-11 冻结 ≤600）"
    )
    simStep: float = Field(1.0, gt=0.0, le=600.0, description="仿真步长（秒）")
    setpointStep: float = Field(1.0, description="设定值阶跃幅值")
    noiseStd: float | None = Field(
        None, ge=0.0, description="扰动响应证据的测量噪声标准差（缺省 0.05）"
    )
    sourceRecordId: str | None = Field(None, description="模型辨识记录 ID")
    modelSource: ModelSource | None = Field(
        None, description="模型来源；旧请求可解析但不会绕过服务端安全门禁"
    )
    riskConfirmed: bool = Field(False, description="C 级辨识结果风险确认（与推荐链同门禁）")

    @model_validator(mode="after")
    def _validate_algorithm_params(self) -> RobustVerifyRequest:
        """P2-01 TUN-05：algorithmParams 按方法注册表校验（与 TuneRequest 同源）."""
        if self.algorithmParams:
            errors = validate_tuning_params(self.algorithm, self.algorithmParams)
            if errors:
                raise ValueError("；".join(errors))
        return self


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


class SimulateRequest(CamelModel):
    """POST /tuning/simulate 请求体。

    P3-04 行为变更：``disturbanceType`` 默认 "none"（旧默认 "step" 形参被
    静默忽略=等效无扰动，默认曲线值不变）；显式 "step" 时负载阶跃扰动
    真实施加于对象输入（可配幅值/施加时刻）。超同步预算明确拒绝。
    """

    modelType: ModelType = Field("FOPDT", description="模型类型")
    modelParams: ModelParams
    currentPid: PidParams
    recommendedPid: PidParams
    pidCandidates: list[PidParamsWithLabel] | None = Field(
        None, description="多组候选 PID 参数（Phase 2 新增，向后兼容）"
    )
    simDuration: float = Field(600.0, ge=1.0, le=7200.0, description="仿真时长（秒，≤7200）")
    simStep: float = Field(1.0, gt=0.0, le=600.0, description="仿真步长（秒）")
    setpointStep: float = Field(1.0, description="设定值阶跃幅值")
    disturbanceType: Literal["none", "step"] = Field(
        "none", description="扰动类型: none=无扰动 / step=负载阶跃扰动（真实施加）"
    )
    disturbanceAmplitude: float | None = Field(
        None, gt=0.0, le=100.0, description="step 扰动幅值（OP 同单位，缺省 0.5）"
    )
    disturbanceTime: float | None = Field(
        None, gt=0.0, description="step 扰动施加时刻（秒，须 < simDuration，缺省 40% 时长）"
    )
    loopId: str | None = Field(None, description="回路 ID（模型记录校验用）")
    sourceRecordId: str | None = Field(None, description="模型辨识记录 ID")
    modelSource: ModelSource | None = Field(
        None,
        description="模型来源；旧请求可解析但不会绕过服务端安全门禁",
    )
    riskConfirmed: bool = Field(False, description="是否已显式确认 C 级/人工模型风险")

    @model_validator(mode="after")
    def _validate_disturbance_window(self) -> SimulateRequest:
        """P3-04：step 扰动施加时刻必须落在仿真窗口内。"""
        if self.disturbanceType == "step" and self.disturbanceTime is not None:
            if self.disturbanceTime >= self.simDuration:
                raise ValueError(
                    f"disturbanceTime（{self.disturbanceTime}s）必须小于 simDuration"
                    f"（{self.simDuration}s）"
                )
        return self


class CompareRequest(CamelModel):
    """POST /tuning/compare 请求体（Phase 2 多 PID 对比，独立 schema V62-P0-030）.

    与 ``SimulateRequest`` 的差异：
    - ``recommendedPid`` 不再要求（``/compare`` 端点从不消费该字段）
    - ``currentPid`` 可选（仅作为对比基线，缺失时仅返回候选响应）
    - ``pidCandidates`` 必填且 ≥2 组（schema 层 ``min_length=2`` 强校验）

    P3-04：候选参数相对差 <1%（B-12）去重标记；扰动参数同 SimulateRequest。
    """

    modelType: ModelType = Field("FOPDT", description="模型类型")
    modelParams: ModelParams
    currentPid: PidParams | None = Field(None, description="当前 PID 参数（对比基线，可选）")
    pidCandidates: list[PidParamsWithLabel] = Field(
        ..., min_length=2, description="多组候选 PID 参数（至少 2 组）"
    )
    simDuration: float = Field(600.0, ge=1.0, le=7200.0, description="仿真时长（秒，≤7200）")
    simStep: float = Field(1.0, gt=0.0, le=600.0, description="仿真步长（秒）")
    setpointStep: float = Field(1.0, description="设定值阶跃幅值")
    disturbanceType: Literal["none", "step"] = Field(
        "none", description="扰动类型: none=无扰动 / step=负载阶跃扰动（真实施加）"
    )
    disturbanceAmplitude: float | None = Field(
        None, gt=0.0, le=100.0, description="step 扰动幅值（OP 同单位，缺省 0.5）"
    )
    disturbanceTime: float | None = Field(
        None, gt=0.0, description="step 扰动施加时刻（秒，须 < simDuration，缺省 40% 时长）"
    )
    loopId: str | None = Field(None, description="回路 ID（模型记录校验用）")
    sourceRecordId: str | None = Field(None, description="模型辨识记录 ID")
    modelSource: ModelSource | None = Field(
        None,
        description="模型来源；旧请求可解析但不会绕过服务端安全门禁",
    )
    riskConfirmed: bool = Field(False, description="是否已显式确认 C 级/人工模型风险")

    @model_validator(mode="after")
    def _validate_disturbance_window(self) -> CompareRequest:
        """P3-04：step 扰动施加时刻必须落在仿真窗口内。"""
        if self.disturbanceType == "step" and self.disturbanceTime is not None:
            if self.disturbanceTime >= self.simDuration:
                raise ValueError(
                    f"disturbanceTime（{self.disturbanceTime}s）必须小于 simDuration"
                    f"（{self.simDuration}s）"
                )
        return self


class SimulationMetrics(CamelModel):
    """仿真性能指标。"""

    riseTime: float | None = Field(None, description="上升时间（秒）")
    overshoot: float | None = Field(None, description="超调量（%）")
    settlingTime: float | None = Field(None, description="稳定时间（秒）")
    itae: float | None = Field(None, description="ITAE 积分")


class CandidateResponse(CamelModel):
    """候选 PID 响应（多 PID 对比）.

    P3-04：``deduplicated``/``duplicateOf`` 为候选去重标记（B-12：参数
    相对差 <1% 的候选不重复仿真，复用代表项响应与指标）。
    """

    label: str = Field(..., description="PID 标签")
    response: dict[str, list[float]]
    metrics: SimulationMetrics
    deduplicated: bool | None = Field(None, description="是否为去重候选（重复项不再仿真）")
    duplicateOf: str | None = Field(None, description="去重代表项标签（deduplicated=true 时有值）")


class SimulationResult(CamelModel):
    """闭环仿真结果."""

    timestamps: list[float]
    currentResponse: dict[str, list[float]]
    recommendedResponse: dict[str, list[float]]
    currentMetrics: SimulationMetrics
    recommendedMetrics: SimulationMetrics
    improvement: dict[str, float | None]
    # Phase 2 新增：多 PID 对比
    candidateResponses: list[CandidateResponse] | None = None
    # P3-04 新增：仿真上下文（模型分派/扰动/去重/预算随结果携带）
    simulationContext: dict[str, Any] | None = None


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
    # P3-04：仿真验证状态（加性键；NOT_SIMULATED=无仿真结果，展示"未验证"）
    simulationStatus: Literal["SIMULATED", "NOT_SIMULATED"] | None = None
    # V62-P3-007 人工实施清单（2026-10-04 出参补齐）
    currentPid: dict[str, Any] | None = None
    rollbackPid: dict[str, Any] | None = None
    riskAssessment: dict[str, Any] | None = None
    # 辨识时间窗（异步历史辨识落库；同步阶跃/MANUAL 为 None）
    timeWindowStart: str | None = None
    timeWindowEnd: str | None = None
    # R03（G3 增量纠偏 2026-10-13）：适用性限制回读（保存时冻结于
    # calc_context；旧记录无该键 → None，显式未知不伪补）
    applicabilityLimitations: dict[str, Any] | None = Field(
        None,
        description="适用性限制（低 SNR 参数精度不承诺 / ci95 未校准集合成员边界）",
    )


class TuningTaskDetail(TuningTaskItem):
    """整定任务详情。"""

    simulationResult: dict[str, Any] | None = None
    # Phase 2.2 新增
    pidCandidates: dict[str, Any] | None = None
    candidateResults: dict[str, Any] | None = None


class CreateTuningTaskRequest(CamelModel):
    """创建整定任务（保存整定结果）。

    P3-B2 W4（G2-INC-c，2026-10-11）——最终选定方案保存链服务端校验冻结：

    - 新协议（携带 ``modelSource``）：来源/模型/参数/窗口/currentPid 快照全部
      由服务端从来源记录与工程事实权威派生；旧客户端申报字段
      （dataSource/identifyMethod/confidence*/fittingScore/currentPid/rollbackPid/
      pidCandidates/candidateResults/excitationScore/residualTestPassed）不得
      携带，携带即 ``ERR_TUNING_PROVENANCE_CONFLICT`` 拒绝（不静默忽略）。
    - 旧协议（缺 ``modelSource`` 的裸模型 payload）：显式拒绝
      ``ERR_TUNING_SOURCE_REQUIRED``——与 /tune /simulate /compare 推荐链
      "旧版裸模型请求已停止放行"同口径（Owner 级定稿，依据记录于 P3-B2 交接）。
    - 字段只加不改：旧字段保留解析形状（仅整批调用方为仓内前端，随本批同步
      升级为新协议）。
    """

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
    # V62-P3-007 人工实施清单（2026-10-04 P0：回退方案/风险评估随方案保存）
    rollbackPid: PidParams | None = None
    riskAssessment: dict[str, Any] | None = None
    # P3-B2 W4：来源身份（服务端校验冻结入口）
    modelSource: ModelSource | None = Field(
        None,
        description="模型来源 IDENTIFICATION_RECORD/STEP_EXPERIMENT/MANUAL；缺省=旧协议拒绝",
    )
    sourceRecordId: str | None = Field(
        None, description="来源辨识记录 ID（IDENTIFICATION_RECORD/STEP_EXPERIMENT 必填）"
    )
    riskConfirmed: bool = Field(
        False, description="C 级辨识结果/人工模型风险确认（与推荐链同门禁）"
    )


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
    # V62-P1-008: 真实片段切分新增字段（optional，兼容旧客户端）
    exclusionReason: str | None = None
    validSampleRatio: float | None = None
    pointCount: int | None = None


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
