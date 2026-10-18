"""Tuning task progress tracker — Phase 2.2.

自包含的轻量进度跟踪，使用 Redis Hash（key: ``tuning:progress:{task_id}``）存储
辨识/整定任务的细粒度进度。

V62-P1-013/014: 桥接 TaskTracker 统一合同。
- ``init_progress`` 同时在 TaskTracker 创建 TUNING 类型任务，
  使整定任务出现在统一 ``GET /tasks`` 列表并获得终态通知。
- ``update_progress`` 进入终态（SUCCESS/FAILED）时同步 TaskTracker 状态。
- 本模块仍保留为兼容适配层：不新增数据库实体，Progress 细粒度阶段
  （excitation/nonparametric/identify/...）仍由本模块独有，TaskTracker
  只跟踪 PENDING→RUNNING→SUCCESS/FAILED/CANCELLED 的粗粒度状态。

进度阶段（对齐技术方案 §3.4.4）：
    excitation(10%) → nonparametric(25%) → identify(50%) →
    order_selection(65%) → discrete_to_continuous(75%) → tune(85%) → simulate(100%)

设计依据：tuning-phase2-technical-plan-2026-07-28.md §3.4
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.redis import redis_client
from app.schemas.task import TaskStatus, TaskType

logger = logging.getLogger(__name__)

_PROGRESS_PREFIX = "tuning:progress"
_PROGRESS_TTL = 7 * 24 * 60 * 60  # 7 天后自动过期
_TRACKER_TASK_ID_FIELD = "tracker_task_id"  # Hash 字段：关联的 TaskTracker 任务 ID

# 终态集合：进入终态时同步 TaskTracker 并触发通知
_TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "CANCELLED"})

# tuning_progress status → TaskStatus 映射
_STATUS_MAP: dict[str, TaskStatus] = {
    "PENDING": TaskStatus.PENDING,
    "RUNNING": TaskStatus.RUNNING,
    "SUCCESS": TaskStatus.SUCCESS,
    "FAILED": TaskStatus.FAILED,
    "CANCELLED": TaskStatus.CANCELLED,
}

# 阶段 → 进度百分比映射（对齐技术方案 §3.4.4 细粒度阶段）
STAGE_PROGRESS: dict[str, float] = {
    "excitation": 10.0,
    "nonparametric": 25.0,
    "identify": 50.0,
    "order_selection": 65.0,
    "discrete_to_continuous": 75.0,
    "tune": 85.0,
    "simulate": 100.0,
}


def _key(task_id: str) -> str:
    """构造进度 Redis Hash key."""
    return f"{_PROGRESS_PREFIX}:{task_id}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def init_progress(
    task_id: str,
    *,
    task_type: str,
    loop_id: str,
    created_by: str = "system",
    created_by_id: str = "",
    ts_start: str | None = None,
    ts_end: str | None = None,
) -> None:
    """初始化任务进度记录.

    V62-P1-013: 同时在 TaskTracker 创建 TUNING 类型任务，使整定任务出现在
    统一任务列表并获得终态通知。若 created_by_id 为空（定时任务/旧调用方），
    跳过 TaskTracker 桥接，仅保留 tuning_progress 自包含进度。

    Args:
        task_id: Celery 任务 ID
        task_type: 任务类型（identify / tune_and_simulate）
        loop_id: 回路 ID
        created_by: 创建人用户名
        created_by_id: 创建人用户 ID（为空则不桥接 TaskTracker）
        ts_start: 时间窗起始
        ts_end: 时间窗结束
    """
    data = {
        "task_id": task_id,
        "task_type": task_type,
        "loop_id": loop_id,
        "status": "PENDING",
        "progress": "0.0",
        "stage": "",
        "message": "任务已提交",
        "result": "",
        "error": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    # V62-P1-013: 桥接 TaskTracker（仅在有关联用户时）
    if created_by_id:
        try:
            from app.services.task_tracker import create_task

            tracker_task_id = await create_task(
                task_type=TaskType.TUNING,
                created_by=created_by,
                created_by_id=created_by_id,
                celery_task_id=task_id,
                ts_start=ts_start,
                ts_end=ts_end,
                loop_ids=[loop_id] if loop_id else None,
                current_stage=task_type,
                triggered_by="user",
                title=f"整定{'辨识' if task_type == 'identify' else '整定仿真'}:{loop_id}",
            )
            data[_TRACKER_TASK_ID_FIELD] = tracker_task_id
            logger.info(
                "整定任务已桥接 TaskTracker: celery_id=%s, tracker_id=%s",
                task_id,
                tracker_task_id,
            )
        except Exception:
            # TaskTracker 桥接失败不应阻断整定任务本身
            logger.warning(
                "TaskTracker 桥接失败，仅保留 tuning_progress: %s", task_id, exc_info=True
            )

    await redis_client.hset(_key(task_id), mapping=data)
    await redis_client.expire(_key(task_id), _PROGRESS_TTL)
    logger.info("整定任务进度已初始化: task_id=%s, type=%s", task_id, task_type)


async def update_progress(
    task_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    message: str | None = None,
    progress: float | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """更新任务进度.

    V62-P1-013: 进入终态时同步 TaskTracker 状态（含通知）。

    Args:
        task_id: 任务 ID
        status: 任务状态 PENDING/RUNNING/SUCCESS/FAILED
        stage: 当前阶段（excitation/nonparametric/identify/...）
        message: 人类可读消息
        progress: 进度 0~100（若 None 且 stage 已知，自动查表）
        result: 成功时的结果（JSON）
        error: 失败原因
    """
    updates: dict[str, str] = {"updated_at": _now_iso()}
    if status:
        updates["status"] = status
    if stage:
        updates["stage"] = stage
        if progress is None:
            progress = STAGE_PROGRESS.get(stage)
    if message is not None:
        updates["message"] = message
    if progress is not None:
        updates["progress"] = str(round(progress, 1))
    if result is not None:
        updates["result"] = json.dumps(result, ensure_ascii=False, default=str)
    if error is not None:
        updates["error"] = error

    await redis_client.hset(_key(task_id), mapping=updates)
    await redis_client.expire(_key(task_id), _PROGRESS_TTL)

    # V62-P1-013: 终态同步 TaskTracker
    if status and status in _TERMINAL_STATUSES:
        await _sync_tracker_terminal(task_id, status, message, error)


async def _sync_tracker_terminal(
    celery_task_id: str,
    status: str,
    message: str | None,
    error: str | None,
) -> None:
    """将终态同步到 TaskTracker，触发通知.

    若 tuning_progress hash 中无 tracker_task_id（未桥接），静默跳过。
    """
    raw = await redis_client.hgetall(_key(celery_task_id))
    if not raw:
        return
    tracker_task_id = raw.get(_TRACKER_TASK_ID_FIELD, "")
    if not tracker_task_id:
        return

    tracker_status = _STATUS_MAP.get(status)
    if tracker_status is None:
        return

    try:
        from app.services.task_tracker import update_status

        await update_status(
            tracker_task_id,
            tracker_status,
            progress=1.0 if status == "SUCCESS" else None,
            current_stage=raw.get("stage") or None,
            error_message=error or message,
            finished_at=_now_iso(),
        )
        logger.info(
            "TaskTracker 终态同步: celery=%s, tracker=%s, status=%s",
            celery_task_id,
            tracker_task_id,
            status,
        )
    except Exception:
        logger.warning(
            "TaskTracker 终态同步失败: celery=%s, status=%s",
            celery_task_id,
            status,
            exc_info=True,
        )


async def get_progress(task_id: str) -> dict[str, Any] | None:
    """读取任务进度.

    Returns:
        进度字典（含 taskId/status/progress/stage/message/result/error）；
        不存在返回 None
    """
    raw = await redis_client.hgetall(_key(task_id))
    if not raw:
        return None

    result_raw = raw.get("result", "")
    result: dict[str, Any] | None = None
    if result_raw:
        try:
            result = json.loads(result_raw)
        except (json.JSONDecodeError, TypeError):
            result = None

    progress_str = raw.get("progress", "0.0")
    try:
        progress_val = float(progress_str)
    except (ValueError, TypeError):
        progress_val = 0.0

    return {
        "taskId": raw.get("task_id", ""),
        "taskType": raw.get("task_type", ""),
        "loopId": raw.get("loop_id", ""),
        "status": raw.get("status", "PENDING"),
        "progress": progress_val,
        "stage": raw.get("stage") or None,
        "message": raw.get("message") or None,
        "result": result,
        "error": raw.get("error") or None,
        "createdAt": raw.get("created_at", ""),
        "updatedAt": raw.get("updated_at", ""),
    }


__all__ = [
    "STAGE_PROGRESS",
    "get_progress",
    "init_progress",
    "update_progress",
]
