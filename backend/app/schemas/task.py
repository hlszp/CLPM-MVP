"""任务管理 Schema (IDS v3.2 §2.7.6).

定义标准评估任务和自定义评估任务的全生命周期管理接口模型。
任务状态存储在 Redis 中（key: ``task:{task_id}``）。

任务状态机（PRD §4.3.7.C）::

    PENDING → RUNNING → SUCCESS
                       → FAILED
                       → CANCELLED

设计依据：IDS §2.7.6, PRD §4.3.7
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.schemas.base import CamelModel


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class TaskType(str, Enum):
    """任务类型.

    Attributes:
        STANDARD: 标准评估任务（每小时定时，全量回路覆盖）
        CUSTOM: 自定义评估任务（用户按需触发，选定回路/指标/时间范围）
    """

    STANDARD = "STANDARD"
    CUSTOM = "CUSTOM"


class TaskStatus(str, Enum):
    """任务状态机（PRD §4.3.7.C）.

    Attributes:
        PENDING: 已创建待执行
        RUNNING: 执行中
        SUCCESS: 成功完成
        FAILED: 执行失败
        CANCELLED: 已取消
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# 请求 Schema
# ---------------------------------------------------------------------------


class StandardTaskCreate(CamelModel):
    """标准评估任务创建请求.

    Attributes:
        tsStart: 评估时间窗起始（ISO 8601），None 表示当前小时
    """

    tsStart: str | None = Field(
        None, description="评估时间窗起始（ISO 8601），None=当前小时"
    )


class CustomTaskCreate(CamelModel):
    """自定义评估任务创建请求.

    Attributes:
        loopIds: 目标回路 ID 列表
        metrics: 目标指标子集
        tsStart: 评估时间窗起始（ISO 8601）
        tsEnd: 评估时间窗结束（ISO 8601）
    """

    loopIds: list[str] = Field(..., description="目标回路 ID 列表")
    metrics: list[str] = Field(..., description="目标指标子集")
    tsStart: str = Field(..., description="评估时间窗起始（ISO 8601）")
    tsEnd: str = Field(..., description="评估时间窗结束（ISO 8601）")


# ---------------------------------------------------------------------------
# 响应 Schema
# ---------------------------------------------------------------------------


class TaskResponse(CamelModel):
    """任务响应.

    Attributes:
        taskId: 任务 ID
        taskType: 任务类型（STANDARD/CUSTOM）
        status: 任务状态（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）
        progress: 进度 0~1
        currentStage: 当前阶段（取数/预处理/指标计算/可信度判定）
        loopsTotal: 总回路数
        loopsDone: 已完成回路数
        createdAt: 创建时间（ISO 8601）
        startedAt: 开始执行时间
        finishedAt: 完成时间
        errorMessage: 失败原因
        createdBy: 创建人用户名
    """

    taskId: str
    taskType: TaskType
    status: TaskStatus
    progress: float | None = Field(None, description="进度 0~1")
    currentStage: str | None = Field(
        None, description="当前阶段：取数/预处理/指标计算/可信度判定"
    )
    loopsTotal: int | None = None
    loopsDone: int | None = None
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    errorMessage: str | None = None
    createdBy: str


class TaskListResponse(CamelModel):
    """任务列表响应.

    Attributes:
        items: 任务响应列表
        total: 符合筛选条件的总数
    """

    items: list[TaskResponse]
    total: int


__all__ = [
    "CustomTaskCreate",
    "StandardTaskCreate",
    "TaskListResponse",
    "TaskResponse",
    "TaskStatus",
    "TaskType",
]
