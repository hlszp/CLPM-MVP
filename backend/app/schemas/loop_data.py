"""回路数据管理 Schema — 历史数据导入（Phase 3）.

定义历史数据导入 API 的请求/响应模型，支持：
- 批量选择回路 + 时间范围从远端 HTTP API 拉取历史数据
- 冲突策略：overwrite（覆盖）/ skip（跳过）
- 导入完成后可选触发 KPI 回算

设计依据：data-architecture-optimization-spec §5
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.schemas.base import CamelModel


class ImportStatus(StrEnum):
    """导入任务状态."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ConflictStrategy(StrEnum):
    """冲突处理策略."""

    OVERWRITE = "overwrite"  # 先 DELETE 再 INSERT（手工优先）
    SKIP = "skip"  # 直接 INSERT，依赖 UPSERT


class ImportRequest(CamelModel):
    """历史数据导入请求.

    Attributes:
        loopIds: 目标回路 ID 列表
        tsStart: 导入时间范围起始（ISO 8601）
        tsEnd: 导入时间范围结束（ISO 8601）
        interval: 采样间隔（秒），默认 1
        conflictStrategy: 冲突策略，overwrite 或 skip
        triggerBackfill: 导入完成后是否触发 KPI 回算
    """

    loopIds: list[str] = Field(..., description="目标回路 ID 列表")
    tsStart: str = Field(..., description="导入时间范围起始（ISO 8601）")
    tsEnd: str = Field(..., description="导入时间范围结束（ISO 8601）")
    interval: int = Field(1, ge=1, description="采样间隔（秒）")
    conflictStrategy: ConflictStrategy = Field(ConflictStrategy.OVERWRITE, description="冲突策略")
    triggerBackfill: bool = Field(False, description="导入完成后触发 KPI 回算")


class ImportTaskResponse(CamelModel):
    """导入任务响应.

    Attributes:
        taskId: 任务 ID
        status: 任务状态
        progress: 进度 0~1
        loopCount: 总回路数
        importedCount: 已导入回路数
        errorCount: 失败回路数
        tsStart: 导入时间范围起始
        tsEnd: 导入时间范围结束
        createdAt: 创建时间
        startedAt: 开始执行时间
        finishedAt: 完成时间
        errorMessage: 失败原因
        createdBy: 创建人
        conflictStrategy: 冲突策略
        triggerBackfill: 是否触发回算
    """

    taskId: str
    status: ImportStatus
    progress: float = 0.0
    loopCount: int = 0
    importedCount: int = 0
    errorCount: int = 0
    tsStart: str
    tsEnd: str
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    errorMessage: str | None = None
    createdBy: str | None = None
    conflictStrategy: str = "overwrite"
    triggerBackfill: bool = False


class ImportTaskListResponse(CamelModel):
    """导入任务列表响应."""

    items: list[ImportTaskResponse]
    total: int


__all__ = [
    "ConflictStrategy",
    "ImportRequest",
    "ImportStatus",
    "ImportTaskListResponse",
    "ImportTaskResponse",
]
