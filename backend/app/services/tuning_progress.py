"""Tuning task progress tracker — Phase 2.2.

自包含的轻量进度跟踪，使用 Redis Hash（key: ``tuning:progress:{task_id}``）存储
辨识/整定任务的细粒度进度，不依赖共享 TaskTracker（避免触碰其他模块代码）。

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

logger = logging.getLogger(__name__)

_PROGRESS_PREFIX = "tuning:progress"
_PROGRESS_TTL = 7 * 24 * 60 * 60  # 7 天后自动过期

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


async def init_progress(task_id: str, *, task_type: str, loop_id: str) -> None:
    """初始化任务进度记录.

    Args:
        task_id: Celery 任务 ID
        task_type: 任务类型（identify / tune_and_simulate）
        loop_id: 回路 ID
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
