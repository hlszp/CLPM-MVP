"""任务跟踪服务 — 抽取任务记录与通知逻辑，供 API 层和 Celery 任务层共用。

本服务解决两个 Phase 5 补齐需求：

1. **cron 定时触发跟踪**：Celery Beat 每小时触发 ``calculate_hourly_kpi``，
   原本直接执行计算逻辑，不会在任务管理 API 中留下记录。本服务在任务
   执行前后创建/更新任务记录，使定时任务也出现在 ``GET /tasks`` 列表中，
   ``triggered_by=system``。

2. **完成通知机制**：任务进入终态（SUCCESS/FAILED/CANCELLED）时，
   自动推送通知到 Redis List（``task:notifications:{user_id}``），
   用户通过 ``GET /tasks/notifications`` 查询自己的通知。

任务状态存储在 Redis Hash（key: ``task:{task_id}``），索引使用 Sorted
Set（key: ``task:index``，score 为创建时间戳）。

设计依据：IDS §2.7.6, PRD §4.3.7
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.redis import redis_client
from app.schemas.task import TaskResponse, TaskStatus, TaskType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis Key 常量
# ---------------------------------------------------------------------------

_TASK_PREFIX = "task"
_TASK_INDEX_KEY = "task:index"  # Sorted set: score=创建时间戳, member=task_id
_NOTIFICATION_PREFIX = "task:notifications"
_NOTIFICATION_MAX = 100  # 每用户最多保留 100 条通知

_STATUS_CAS_LUA = r"""
-- CLPM_TASK_STATUS_CAS_V2
local task_key = KEYS[1]
if redis.call('EXISTS', task_key) == 0 then
  return {'MISSING', ''}
end

local new_status = ARGV[1]
local old_status = redis.call('HGET', task_key, 'status') or ''
local terminal = {SUCCESS=true, FAILED=true, CANCELLED=true}
if terminal[old_status] and old_status ~= new_status then
  return {'BLOCKED', old_status}
end

redis.call('HSET', task_key, 'status', new_status)
for index = 2, #ARGV, 2 do
  local field = ARGV[index]
  local value = ARGV[index + 1]
  if field == 'progress' or field == 'loops_done' or field == 'work_items_done' then
    local current = tonumber(redis.call('HGET', task_key, field) or '')
    local incoming = tonumber(value)
    if current == nil or incoming == nil or incoming >= current then
      redis.call('HSET', task_key, field, value)
    end
  else
    redis.call('HSET', task_key, field, value)
  end
end
return {'UPDATED', old_status}
"""

_BACKFILL_DISPATCH_RESERVE_LUA = r"""
-- CLPM_BACKFILL_DISPATCH_RESERVE_V1
local task_key = KEYS[1]
if redis.call('EXISTS', task_key) == 0 then
  return {'MISSING', '[]', ''}
end

local state = redis.call('HGET', task_key, 'backfill_dispatch_state')
local existing_ids = redis.call('HGET', task_key, 'backfill_child_task_ids')
local existing_callback = redis.call('HGET', task_key, 'backfill_callback_task_id')
local legacy_ids = redis.call('HGET', task_key, 'celery_task_ids')
if state == 'DISPATCHED' or (not state and (existing_ids or existing_callback or legacy_ids)) then
  return {'EXISTING', existing_ids or legacy_ids or '[]', existing_callback or ''}
end
if state == 'DISPATCHING' then
  redis.call('HSET', task_key, 'backfill_dispatch_token', ARGV[1])
  return {'RECOVER', existing_ids or '[]', existing_callback or ''}
end

redis.call(
  'HSET', task_key,
  'backfill_dispatch_state', 'DISPATCHING',
  'backfill_dispatch_token', ARGV[1],
  'backfill_child_task_ids', ARGV[2],
  'backfill_callback_task_id', ARGV[3],
  'celery_task_ids', ARGV[4]
)
return {'CLAIMED', ARGV[2], ARGV[3]}
"""

_BACKFILL_DISPATCH_COMPLETE_LUA = r"""
-- CLPM_BACKFILL_DISPATCH_COMPLETE_V1
local task_key = KEYS[1]
if redis.call('HGET', task_key, 'backfill_dispatch_state') ~= 'DISPATCHING' then
  return 0
end
if redis.call('HGET', task_key, 'backfill_dispatch_token') ~= ARGV[1] then
  return 0
end
redis.call('HSET', task_key, 'backfill_dispatch_state', 'DISPATCHED')
redis.call('HDEL', task_key, 'backfill_dispatch_token')
return 1
"""

_BACKFILL_DISPATCH_RELEASE_LUA = r"""
-- CLPM_BACKFILL_DISPATCH_RELEASE_V1
local task_key = KEYS[1]
if redis.call('HGET', task_key, 'backfill_dispatch_state') ~= 'DISPATCHING' then
  return 0
end
if redis.call('HGET', task_key, 'backfill_dispatch_token') ~= ARGV[1] then
  return 0
end
redis.call(
  'HDEL', task_key,
  'backfill_dispatch_state',
  'backfill_dispatch_token',
  'backfill_child_task_ids',
  'backfill_callback_task_id',
  'celery_task_ids'
)
return 1
"""

_BACKFILL_PROGRESS_LUA = r"""
-- CLPM_BACKFILL_PROGRESS_V2
local task_key = KEYS[1]
local event_key = KEYS[2]
if redis.call('EXISTS', task_key) == 0 then
  return {'MISSING', '0', '0'}
end

local status = redis.call('HGET', task_key, 'status') or ''
local terminal = {SUCCESS=true, FAILED=true, CANCELLED=true}
local current_done = (tonumber(redis.call('HGET', task_key, 'backfill_done') or '') or 0)
local current_progress = (tonumber(redis.call('HGET', task_key, 'progress') or '') or 0)
if terminal[status] then
  return {'TERMINAL', tostring(current_done), tostring(current_progress)}
end
local added = redis.call('SADD', event_key, ARGV[1])
redis.call('EXPIRE', event_key, tonumber(ARGV[4]))
if added == 0 then
  return {'DUPLICATE', tostring(current_done), tostring(current_progress)}
end

local total = tonumber(ARGV[2]) or 0
local done = redis.call('HINCRBY', task_key, 'backfill_done', 1)
if total > 0 and done > total then
  done = total
  redis.call('HSET', task_key, 'backfill_done', tostring(done))
end
local progress = 0
if total > 0 then
  progress = done / total
end
if progress < current_progress then
  progress = current_progress
end
-- V2：细粒度进度按「回路×窗口」工作项写入独立字段 work_items_total/work_items_done。
-- loops_total 恒为回路数（创建时写入），loops_done 语义为回路完成数，
-- 两者均不再被工作项数覆盖（2026-07-21 P0 根因修复）。
redis.call(
  'HSET', task_key,
  'status', 'RUNNING',
  'progress', tostring(progress),
  'work_items_done', tostring(done),
  'work_items_total', tostring(total),
  'current_stage', ARGV[3]
)
return {'COUNTED', tostring(done), tostring(progress)}
"""

_BACKFILL_BATCH_CLAIM_LUA = r"""
-- CLPM_BACKFILL_BATCH_CLAIM_V1
local batch_key = KEYS[1]
local state = redis.call('HGET', batch_key, 'state')
if state == 'DONE' then
  return {'DONE', redis.call('HGET', batch_key, 'result') or '{}'}
end
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1])
local lease_until = tonumber(redis.call('HGET', batch_key, 'lease_until') or '0')
if state == 'RUNNING' and lease_until > now then
  return {'BUSY', ''}
end
redis.call(
  'HSET', batch_key,
  'state', 'RUNNING',
  'token', ARGV[1],
  'lease_until', tostring(now + tonumber(ARGV[2]))
)
redis.call('EXPIRE', batch_key, tonumber(ARGV[3]))
return {'CLAIMED', ''}
"""

_BACKFILL_BATCH_COMPLETE_LUA = r"""
-- CLPM_BACKFILL_BATCH_COMPLETE_V1
local batch_key = KEYS[1]
if redis.call('HGET', batch_key, 'state') ~= 'RUNNING' then
  return 0
end
if redis.call('HGET', batch_key, 'token') ~= ARGV[1] then
  return 0
end
redis.call(
  'HSET', batch_key,
  'state', 'DONE',
  'result', ARGV[2]
)
redis.call('HDEL', batch_key, 'token', 'lease_until')
redis.call('EXPIRE', batch_key, tonumber(ARGV[3]))
return 1
"""

_BACKFILL_BATCH_RELEASE_LUA = r"""
-- CLPM_BACKFILL_BATCH_RELEASE_V1
local batch_key = KEYS[1]
if redis.call('HGET', batch_key, 'state') ~= 'RUNNING' then
  return 0
end
if redis.call('HGET', batch_key, 'token') ~= ARGV[1] then
  return 0
end
redis.call('DEL', batch_key)
return 1
"""

# 活跃状态集合（用于并发计数）
ACTIVE_STATUSES = frozenset({TaskStatus.PENDING.value, TaskStatus.RUNNING.value})

# 终态集合（不可取消，进入终态时触发通知）
TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


def _task_key(task_id: str) -> str:
    """构造任务 Redis Hash key."""
    return f"{_TASK_PREFIX}:{task_id}"


def _notification_key(user_id: str) -> str:
    """构造用户通知 Redis List key."""
    return f"{_NOTIFICATION_PREFIX}:{user_id}"


def _to_str(value: Any) -> str:
    """将值转为 Redis Hash 可存储的字符串（None → 空字符串）."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _to_int(value: Any) -> int | None:
    """将 Redis Hash 字段值转为 int，空值返回 None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> float | None:
    """将 Redis Hash 字段值转为 float，空值返回 None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_str_or_none(value: Any) -> str | None:
    """将 Redis Hash 字段值转为 str，空值返回 None."""
    if value is None or value == "":
        return None
    return str(value)


# ---------------------------------------------------------------------------
# 核心服务：任务记录
# ---------------------------------------------------------------------------


async def create_task(
    task_type: TaskType,
    created_by: str,
    created_by_id: str,
    *,
    celery_task_id: str | None = None,
    celery_task_ids: list[str] | None = None,
    ts_start: str | None = None,
    ts_end: str | None = None,
    loop_ids: list[str] | None = None,
    metrics: list[str] | None = None,
    loops_total: int | None = None,
    current_stage: str | None = None,
    triggered_by: str = "user",
    title: str | None = None,
) -> str:
    """创建任务记录，返回 task_id.

    Args:
        task_type: 任务类型（STANDARD/CUSTOM）
        created_by: 创建人用户名（定时任务为 "system"）
        created_by_id: 创建人用户 ID（定时任务为空字符串）
        celery_task_id: 单个 Celery 任务 ID（STANDARD 任务）
        celery_task_ids: 多个 Celery 任务 ID 列表（CUSTOM 任务）
        ts_start: 评估时间窗起始（ISO 8601）
        ts_end: 评估时间窗结束（ISO 8601）
        loop_ids: 目标回路 ID 列表（CUSTOM 任务）
        metrics: 目标指标列表（CUSTOM 任务）
        loops_total: 总回路数
        current_stage: 当前阶段
        triggered_by: 触发来源（"user"/"system"）

    Returns:
        任务 ID (UUID)
    """
    task_id = str(uuid4())
    now = _now_iso()
    task_data: dict[str, str] = {
        "task_id": task_id,
        "task_type": task_type.value,
        "status": TaskStatus.PENDING.value,
        "title": _to_str(title),
        "progress": "",
        "current_stage": _to_str(current_stage),
        "loops_total": _to_str(loops_total),
        "loops_done": "",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_message": "",
        "created_by": created_by,
        "created_by_id": created_by_id,
        "triggered_by": triggered_by,
    }
    if celery_task_id:
        task_data["celery_task_id"] = celery_task_id
    if celery_task_ids:
        task_data["celery_task_ids"] = json.dumps(celery_task_ids)
    if ts_start:
        task_data["ts_start"] = ts_start
    if ts_end:
        task_data["ts_end"] = ts_end
    if loop_ids:
        task_data["loop_ids"] = json.dumps(loop_ids)
    if metrics:
        task_data["metrics"] = json.dumps(metrics)
    await _save_task(task_data)
    logger.info(
        "任务已创建: task_id=%s, type=%s, by=%s, triggered_by=%s",
        task_id,
        task_type.value,
        created_by,
        triggered_by,
    )
    return task_id


async def _save_task(task_data: dict[str, str]) -> None:
    """保存任务状态到 Redis Hash 并更新索引."""
    task_id = task_data["task_id"]
    await redis_client.hset(_task_key(task_id), mapping=task_data)
    created_at = task_data.get("created_at", "")
    try:
        score = datetime.fromisoformat(created_at).timestamp()
    except (ValueError, TypeError):
        score = datetime.now(UTC).timestamp()
    await redis_client.zadd(_TASK_INDEX_KEY, {task_id: score})


async def get_task(task_id: str) -> dict[str, str] | None:
    """从 Redis Hash 读取任务状态.

    Args:
        task_id: 任务 ID

    Returns:
        任务字段字典；不存在返回 None
    """
    data = await redis_client.hgetall(_task_key(task_id))
    if not data:
        return None
    return data


async def set_celery_task_ids(task_id: str, celery_task_ids: list[str]) -> None:
    """Persist child/callback IDs so status sync and cancellation cover the whole canvas."""
    await redis_client.hset(
        _task_key(task_id),
        mapping={"celery_task_ids": json.dumps(celery_task_ids)},
    )


async def reserve_backfill_dispatch(
    task_id: str,
    *,
    reservation_token: str,
    child_task_ids: list[str],
    callback_task_id: str,
) -> tuple[str, list[str], str]:
    """Atomically reserve one Celery canvas per tracked backfill task."""
    child_json = json.dumps(child_task_ids)
    all_ids_json = json.dumps([*child_task_ids, callback_task_id])
    raw = await redis_client.eval(
        _BACKFILL_DISPATCH_RESERVE_LUA,
        1,
        _task_key(task_id),
        reservation_token,
        child_json,
        callback_task_id,
        all_ids_json,
    )
    code = str(raw[0])
    stored_ids = json.loads(raw[1] or "[]")
    stored_callback = str(raw[2] or "")
    if code == "MISSING":
        raise RuntimeError(f"回填任务不存在: {task_id}")
    if not stored_callback and stored_ids:
        stored_callback = str(stored_ids[-1])
        stored_ids = stored_ids[:-1]
    return code, [str(item) for item in stored_ids], stored_callback


async def complete_backfill_dispatch(task_id: str, reservation_token: str) -> bool:
    """Mark a reserved canvas as dispatched when the reservation still belongs to caller."""
    result = await redis_client.eval(
        _BACKFILL_DISPATCH_COMPLETE_LUA,
        1,
        _task_key(task_id),
        reservation_token,
    )
    return bool(result)


async def release_backfill_dispatch(task_id: str, reservation_token: str) -> bool:
    """Release a pre-dispatch reservation after synchronous broker dispatch failure."""
    result = await redis_client.eval(
        _BACKFILL_DISPATCH_RELEASE_LUA,
        1,
        _task_key(task_id),
        reservation_token,
    )
    return bool(result)


async def record_backfill_progress_once(
    task_id: str,
    *,
    event_id: str,
    total_work_items: int,
    current_stage: str,
) -> tuple[bool, int, float]:
    """Atomically count one unique backfill completion and persist monotonic progress.

    进度按「回路×窗口」工作项计数，写入任务 hash 的 ``work_items_total`` /
    ``work_items_done`` 字段；``loops_total``（回路数）不被覆盖。
    """
    raw = await redis_client.eval(
        _BACKFILL_PROGRESS_LUA,
        2,
        _task_key(task_id),
        f"{_task_key(task_id)}:backfill_progress_events",
        event_id,
        str(total_work_items),
        current_stage,
        str(7 * 24 * 60 * 60),
    )
    code = str(raw[0])
    return code == "COUNTED", int(float(raw[1])), float(raw[2])


async def delete_task_auxiliary_keys(task_id: str) -> None:
    """Delete enumerable per-task Redis keys; batch claim keys expire after seven days."""
    await redis_client.delete(f"{_task_key(task_id)}:backfill_progress_events")


async def claim_backfill_batch(
    task_id: str,
    batch_id: str,
    *,
    execution_token: str,
    lease_seconds: int = 35 * 60,
) -> tuple[str, dict[str, Any] | None]:
    """Claim a deterministic child batch or return its cached completed result."""
    raw = await redis_client.eval(
        _BACKFILL_BATCH_CLAIM_LUA,
        1,
        f"{_task_key(task_id)}:backfill_batch:{batch_id}",
        execution_token,
        str(lease_seconds),
        str(7 * 24 * 60 * 60),
    )
    state = str(raw[0])
    result = json.loads(raw[1]) if state == "DONE" and raw[1] else None
    return state, result


async def complete_backfill_batch(
    task_id: str,
    batch_id: str,
    *,
    execution_token: str,
    result: dict[str, Any],
) -> bool:
    """Persist one deterministic child result so redelivery can reuse it."""
    raw = await redis_client.eval(
        _BACKFILL_BATCH_COMPLETE_LUA,
        1,
        f"{_task_key(task_id)}:backfill_batch:{batch_id}",
        execution_token,
        json.dumps(result, ensure_ascii=False),
        str(7 * 24 * 60 * 60),
    )
    return bool(raw)


async def release_backfill_batch(
    task_id: str,
    batch_id: str,
    *,
    execution_token: str,
) -> bool:
    """Release a failed child claim; hard worker loss is recovered after the lease."""
    raw = await redis_client.eval(
        _BACKFILL_BATCH_RELEASE_LUA,
        1,
        f"{_task_key(task_id)}:backfill_batch:{batch_id}",
        execution_token,
    )
    return bool(raw)


async def update_status(
    task_id: str,
    status: TaskStatus,
    *,
    progress: float | None = None,
    loops_total: int | None = None,
    loops_done: int | None = None,
    work_items_total: int | None = None,
    work_items_done: int | None = None,
    current_stage: str | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, str] | None:
    """更新任务状态。若进入终态，自动发送通知.

    Args:
        task_id: 任务 ID
        status: 新状态
        progress: 进度 0~1
        loops_total: 总回路数（计算开始后才知道；恒为回路数，非工作项数）
        loops_done: 已完成回路数
        work_items_total: 总工作项数（回填场景 = 回路 × 窗口）
        work_items_done: 已完成工作项数（单调递增）
        current_stage: 当前阶段
        error_message: 失败原因
        started_at: 开始执行时间
        finished_at: 完成时间

    Returns:
        更新后的任务字段字典；任务不存在返回 None
    """
    updates: dict[str, str] = {"status": status.value}
    if progress is not None:
        updates["progress"] = str(round(progress, 4))
    if loops_total is not None:
        updates["loops_total"] = str(loops_total)
    if loops_done is not None:
        updates["loops_done"] = str(loops_done)
    if work_items_total is not None:
        updates["work_items_total"] = str(work_items_total)
    if work_items_done is not None:
        updates["work_items_done"] = str(work_items_done)
    if current_stage is not None:
        updates["current_stage"] = current_stage
    if error_message is not None:
        updates["error_message"] = error_message
    if started_at is not None:
        updates["started_at"] = started_at
    if finished_at is not None:
        updates["finished_at"] = finished_at

    script_args: list[str] = [status.value]
    for field, value in updates.items():
        if field != "status":
            script_args.extend((field, value))
    raw = await redis_client.eval(
        _STATUS_CAS_LUA,
        1,
        _task_key(task_id),
        *script_args,
    )
    result_code = str(raw[0])
    old_status = str(raw[1])
    if result_code == "MISSING":
        logger.warning("更新任务状态失败：任务不存在 task_id=%s", task_id)
        return None

    data = await get_task(task_id)
    if data is None:
        return None
    if result_code == "BLOCKED":
        logger.info(
            "忽略终态覆盖: task_id=%s, %s→%s",
            task_id,
            old_status,
            status.value,
        )
        return data

    # 终态转换时发送通知（仅在从非终态进入终态时）
    if status.value in TERMINAL_STATUSES and old_status not in TERMINAL_STATUSES:
        await _send_notification(data)

    logger.info(
        "任务状态更新: task_id=%s, %s→%s",
        task_id,
        old_status or "(none)",
        status.value,
    )
    return data


async def count_active_custom_tasks(user_id: str | None = None) -> int:
    """统计活跃的自定义任务数.

    遍历任务索引，统计状态为 PENDING/RUNNING 的自定义任务。
    若指定 user_id，仅统计该用户的活跃任务。

    Args:
        user_id: 用户 ID；None 表示统计全系统

    Returns:
        活跃任务数
    """
    task_ids = await redis_client.zrange(_TASK_INDEX_KEY, 0, -1)
    count = 0
    for tid in task_ids:
        data = await get_task(tid)
        if not data:
            continue
        if data.get("task_type") != TaskType.CUSTOM.value:
            continue
        if data.get("status") not in ACTIVE_STATUSES:
            continue
        if user_id is not None and data.get("created_by_id") != str(user_id):
            continue
        count += 1
    return count


def task_to_response(data: dict[str, Any]) -> TaskResponse:
    """将 Redis Hash 字典转换为 TaskResponse.

    Args:
        data: Redis Hash 字段字典（值为字符串）

    Returns:
        TaskResponse 实例
    """
    return TaskResponse(
        taskId=data.get("task_id", ""),
        taskType=TaskType(data.get("task_type", TaskType.STANDARD.value)),
        status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
        progress=_to_float(data.get("progress")),
        currentStage=_to_str_or_none(data.get("current_stage")),
        loopsTotal=_to_int(data.get("loops_total")),
        loopsDone=_to_int(data.get("loops_done")),
        createdAt=data.get("created_at", ""),
        startedAt=_to_str_or_none(data.get("started_at")),
        finishedAt=_to_str_or_none(data.get("finished_at")),
        errorMessage=_to_str_or_none(data.get("error_message")),
        createdBy=data.get("created_by", ""),
    )


# ---------------------------------------------------------------------------
# 通知机制
# ---------------------------------------------------------------------------


async def _send_notification(task_data: dict[str, str]) -> None:
    """任务进入终态时推送通知到用户通知列表.

    通知存储在 Redis List（key: ``task:notifications:{user_id}``），
    每用户最多保留 ``_NOTIFICATION_MAX`` 条（FIFO，lpush+ltrim）。

    Args:
        task_data: 任务字段字典
    """
    user_id = task_data.get("created_by_id", "")
    if not user_id:
        # 定时任务无 created_by_id，跳过通知（系统任务不通知个人用户）
        return

    task_id = task_data.get("task_id", "")
    status = task_data.get("status", "")
    task_type = task_data.get("task_type", "")
    created_by = task_data.get("created_by", "")
    finished_at = task_data.get("finished_at") or _now_iso()
    error_message = task_data.get("error_message", "")

    notification = {
        "task_id": task_id,
        "task_type": task_type,
        "status": status,
        "created_by": created_by,
        "finished_at": finished_at,
        "error_message": error_message,
        "notification_time": _now_iso(),
    }
    await redis_client.lpush(
        _notification_key(user_id),
        json.dumps(notification, ensure_ascii=False),
    )
    # 保留最近 N 条通知（lpush 后列表头部为最新，ltrim 保留 [0, N-1]）
    await redis_client.ltrim(_notification_key(user_id), 0, _NOTIFICATION_MAX - 1)
    logger.info(
        "任务通知已发送: task_id=%s, status=%s, user=%s",
        task_id,
        status,
        user_id,
    )


async def get_notifications(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """查询用户任务通知列表.

    Args:
        user_id: 用户 ID
        limit: 返回条数（最多 100）

    Returns:
        通知字典列表（最新在前）
    """
    limit = max(1, min(limit, _NOTIFICATION_MAX))
    raw_list = await redis_client.lrange(_notification_key(user_id), 0, limit - 1)
    notifications: list[dict[str, Any]] = []
    for raw in raw_list:
        try:
            notifications.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return notifications


async def mark_notification_read(user_id: str, task_id: str) -> bool:
    """标记指定任务的通知为已读（从通知列表移除）.

    Args:
        user_id: 用户 ID
        task_id: 任务 ID

    Returns:
        是否成功移除（False 表示通知不存在）
    """
    notifications = await get_notifications(user_id, limit=_NOTIFICATION_MAX)
    removed = False
    for n in notifications:
        if n.get("task_id") == task_id:
            # 重建列表（移除匹配项）
            remaining = [n for n in notifications if n.get("task_id") != task_id]
            await redis_client.delete(_notification_key(user_id))
            if remaining:
                # lpush 顺序反转，所以倒序写入
                for item in reversed(remaining):
                    await redis_client.lpush(
                        _notification_key(user_id),
                        json.dumps(item, ensure_ascii=False),
                    )
            removed = True
            break
    return removed


__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "claim_backfill_batch",
    "complete_backfill_batch",
    "count_active_custom_tasks",
    "complete_backfill_dispatch",
    "create_task",
    "delete_task_auxiliary_keys",
    "get_notifications",
    "get_task",
    "mark_notification_read",
    "record_backfill_progress_once",
    "release_backfill_batch",
    "release_backfill_dispatch",
    "reserve_backfill_dispatch",
    "task_to_response",
    "update_status",
]
