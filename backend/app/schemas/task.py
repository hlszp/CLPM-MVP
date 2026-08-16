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

from enum import StrEnum

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class TaskType(StrEnum):
    """任务类型.

    Attributes:
        STANDARD: 标准评估任务（每小时定时，全量回路覆盖）
        CUSTOM: 自定义评估任务（用户按需触发，选定回路/指标/时间范围）
        BACKFILL: 历史重算任务（按时间窗批量重算，覆盖标准快照）
        TUNING: 回路整定任务（辨识/整定/仿真异步任务，V62-P1-013 接入 TaskTracker）
        REPORT: 报告导出任务（诊断建议书 PDF 等异步导出，P3-33 接入 TaskTracker）
        DIAGNOSIS: 回路诊断任务（MVP v2 诊断模块手动批量触发）
    """

    STANDARD = "STANDARD"
    CUSTOM = "CUSTOM"
    BACKFILL = "BACKFILL"
    TUNING = "TUNING"
    REPORT = "REPORT"
    DIAGNOSIS = "DIAGNOSIS"


class TaskStatus(StrEnum):
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

    tsStart: str | None = Field(None, description="评估时间窗起始（ISO 8601），None=当前小时")


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


class BackfillTaskCreate(CamelModel):
    """历史重算任务创建请求.

    Attributes:
        title: 任务标题（必填）
        tsStart: 重算时间窗起始（ISO 8601）
        tsEnd: 重算时间窗结束（ISO 8601，不包含）
        plantNodeIds: 装置 ID 列表（可选，不传=全部装置）
        loopIds: 回路 ID 列表（可选，优先级高于 plantNodeIds；不传=对应装置全部回路）
        dryRun: True=只返回影响范围预览，不实际触发 Celery 任务
    """

    title: str = Field(..., min_length=1, max_length=100, description="任务标题")
    tsStart: str = Field(..., description="重算时间窗起始（ISO 8601）")
    tsEnd: str = Field(..., description="重算时间窗结束（ISO 8601，不包含）")
    plantNodeIds: list[str] | None = Field(None, description="装置 ID 列表（可选）")
    loopIds: list[str] | None = Field(
        None, description="回路 ID 列表（可选，优先级高于 plantNodeIds）"
    )
    dryRun: bool = Field(False, description="True=只返回预览不提交")


# ---------------------------------------------------------------------------
# 响应 Schema
# ---------------------------------------------------------------------------


class TaskResponse(CamelModel):
    """任务响应.

    Attributes:
        taskId: 任务 ID
        taskType: 任务类型（STANDARD/CUSTOM/BACKFILL/TUNING/REPORT）
        status: 任务状态（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）
        progress: 进度 0~1
        currentStage: 当前阶段（取数/预处理/指标计算/可信度判定）
        loopsTotal: 总回路数
        loopsDone: 已完成回路数（BACKFILL 为按进度折算的等效完成回路数）
        windowCount: 小时窗口数（仅 BACKFILL，用于显示计算量）
        createdAt: 创建时间（ISO 8601）
        startedAt: 开始执行时间
        finishedAt: 完成时间
        errorMessage: 失败原因
        createdBy: 创建人用户名
        fileName: 产出文件名（仅 REPORT 任务，如 CLPM-诊断建议书-xxx.pdf）
        resultUrl: 产物下载路径（仅 REPORT/其他有产物的任务，如 /api/v1/tasks/{taskId}/download）
    """

    taskId: str
    taskType: TaskType
    status: TaskStatus
    title: str | None = Field(None, description="任务标题")
    progress: float | None = Field(None, description="进度 0~1")
    currentStage: str | None = Field(None, description="当前阶段：取数/预处理/指标计算/可信度判定")
    loopsTotal: int | None = None
    loopsDone: int | None = None
    windowCount: int | None = Field(None, description="小时窗口数（仅 BACKFILL）")
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    errorMessage: str | None = None
    createdBy: str
    # 历史重算任务额外字段（其他任务类型为 None）
    tsStart: str | None = Field(None, description="重算时间窗起始（仅 BACKFILL）")
    tsEnd: str | None = Field(None, description="重算时间窗结束（仅 BACKFILL）")
    loopIds: list[str] | None = Field(None, description="回路 ID 列表（仅 BACKFILL）")
    plantNodeIds: list[str] | None = Field(None, description="装置 ID 列表（仅 BACKFILL）")
    # V62-P3-33：报告导出任务产物（异步 PDF 下载用）
    fileName: str | None = Field(None, description="产出文件名（仅 REPORT 等带文件产物的任务）")
    resultUrl: str | None = Field(
        None, description="产物下载路径，如 /api/v1/tasks/{taskId}/download（仅带产物的任务）"
    )


class BackfillPreviewResult(CamelModel):
    """历史重算 dry-run 预览结果.

    Attributes:
        loopCount: 影响回路数
        windowCount: 影响小时窗口数
        estimatedDurationSec: 预估耗时（秒，按回填内层并发批次估算）
        sampleLoopNames: 前 5 个回路名预览
    """

    loopCount: int = Field(..., description="影响回路数")
    windowCount: int = Field(..., description="影响小时窗口数")
    estimatedDurationSec: int = Field(..., description="预估耗时（秒）")
    sampleLoopNames: list[str] = Field(default_factory=list, description="前 5 个回路名预览")


class TaskListResponse(CamelModel):
    """任务列表响应.

    Attributes:
        items: 任务响应列表
        total: 符合筛选条件的总数
    """

    items: list[TaskResponse]
    total: int


__all__ = [
    "BackfillPreviewResult",
    "BackfillTaskCreate",
    "CustomTaskCreate",
    "StandardTaskCreate",
    "TaskListResponse",
    "TaskResponse",
    "TaskStatus",
    "TaskType",
]
