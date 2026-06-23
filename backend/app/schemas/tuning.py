"""Tuning center schemas (IDS v3.2 §2.5 — S7-TUNE-006).

对齐关键算法设计说明 v1.0 §6：
- FOPDT/SOPDT/IPDT 模型辨识
- IMC/Lambda/Z-N/Cohen-Coon/SIMC PID 整定
- 闭环仿真（RK4 + 增量式 PID）
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 模型辨识
# ---------------------------------------------------------------------------


class ModelIdentifyRequest(CamelModel):
    """POST /tuning/identify 请求体。"""

    loopId: str = Field(..., description="回路 ID")
    startTime: str = Field(..., description="起始时间 ISO 8601")
    endTime: str = Field(..., description="结束时间 ISO 8601")
    modelType: str = Field("FOPDT", description="模型类型: FOPDT/SOPDT/IPDT")
    method: str | None = Field(None, description="辨识方法: TWO_POINT/AREA/COMBINED（仅 FOPDT）")


class ModelParams(CamelModel):
    """模型参数。"""

    K: float | None = Field(None, description="过程增益")
    tau: float | None = Field(None, description="时间常数（秒）")
    theta: float | None = Field(None, description="死区时间（秒）")
    T1: float | None = Field(None, description="SOPDT 第一时间常数（秒）")
    T2: float | None = Field(None, description="SOPDT 第二时间常数（秒）")


class ModelIdentifyResult(CamelModel):
    """模型辨识结果。"""

    modelType: str
    params: ModelParams
    fittingScore: float = Field(..., description="拟合度 R²（%）")
    algorithmVersion: str
    dataPoints: int = Field(..., description="参与辨识的数据点数")
    # 拟合曲线（用于前端可视化）
    fittedCurve: dict[str, list[Any]] | None = Field(
        None, description="拟合曲线 {timestamps: [], pv: [], fitted: []}"
    )


# ---------------------------------------------------------------------------
# PID 整定
# ---------------------------------------------------------------------------


class PidParams(CamelModel):
    """PID 参数。"""

    kp: float = Field(..., description="比例增益")
    ti: float = Field(..., description="积分时间（秒）")
    td: float = Field(0.0, description="微分时间（秒）")


class TuneRequest(CamelModel):
    """POST /tuning/tune 请求体。"""

    modelType: str = Field(..., description="模型类型: FOPDT/SOPDT/IPDT")
    modelParams: ModelParams
    algorithm: str = Field(..., description="整定算法: IMC/LAMBDA/ZN/COHEN_COON/SIMC")
    algorithmParams: dict[str, Any] | None = Field(
        None, description="算法参数（如 lambda 比例系数）"
    )
    currentPid: PidParams | None = Field(None, description="当前 PID 参数（用于对比）")
    loopId: str | None = Field(None, description="回路 ID（可选，用于记录）")


class TuneResult(CamelModel):
    """PID 整定结果。"""

    algorithm: str
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

    modelType: str = Field("FOPDT", description="模型类型")
    modelParams: ModelParams
    currentPid: PidParams
    recommendedPid: PidParams
    simDuration: float = Field(600.0, description="仿真时长（秒）")
    simStep: float = Field(1.0, description="仿真步长（秒）")
    setpointStep: float = Field(1.0, description="设定值阶跃幅值")
    disturbanceType: str = Field("step", description="扰动类型: step/none")


class SimulationMetrics(CamelModel):
    """仿真性能指标。"""

    riseTime: float | None = Field(None, description="上升时间（秒）")
    overshoot: float = Field(None, description="超调量（%）")
    settlingTime: float | None = Field(None, description="稳定时间（秒）")
    itae: float | None = Field(None, description="ITAE 积分")


class SimulationResult(CamelModel):
    """闭环仿真结果。"""

    timestamps: list[float]
    currentResponse: dict[str, list[float]]
    recommendedResponse: dict[str, list[float]]
    currentMetrics: SimulationMetrics
    recommendedMetrics: SimulationMetrics
    improvement: dict[str, float | None]


# ---------------------------------------------------------------------------
# 整定任务记录
# ---------------------------------------------------------------------------


class TuningTaskItem(CamelModel):
    """整定任务列表项。"""

    id: str
    loopId: str
    tagName: str | None = None
    modelType: str
    modelParams: dict[str, Any] | None = None
    algorithm: str
    recommendedPid: dict[str, Any] | None = None
    fittingScore: float | None = None
    status: str
    createdBy: str | None = None
    createdAt: str


class TuningTaskDetail(TuningTaskItem):
    """整定任务详情。"""

    simulationResult: dict[str, Any] | None = None
    currentPid: dict[str, Any] | None = None


class CreateTuningTaskRequest(CamelModel):
    """创建整定任务（保存整定结果）。"""

    loopId: str
    modelType: str
    modelParams: ModelParams
    algorithm: str
    recommendedPid: PidParams
    currentPid: PidParams | None = None
    fittingScore: float | None = None
    simulationResult: dict[str, Any] | None = None
    status: str = Field("SIMULATED", description="任务状态")


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
