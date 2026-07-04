"""任务管理接口 (IDS v3.2 §2.7.6).

支持标准评估任务和自定义评估任务的全生命周期管理。
任务状态存储在 Redis 中（key: ``task:{task_id}``），索引使用有序集合
（key: ``task:index``，score 为创建时间戳）。

路由清单：
- POST /api/v1/tasks/standard/evaluate     — 触发标准评估任务（手动）
- POST /api/v1/tasks/custom/evaluate       — 触发自定义评估任务
- GET  /api/v1/tasks/{taskId}              — 查询单个任务状态
- GET  /api/v1/tasks                       — 查询任务列表（按类型/状态/时间筛选）
- POST /api/v1/tasks/{taskId}/cancel       — 取消运行中的任务

任务状态机（PRD §4.3.7.C）::

    PENDING → RUNNING → SUCCESS
                       → FAILED
                       → CANCELLED

设计依据：IDS §2.7.6, PRD §4.3.7
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotCustom
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.task import (
    CustomTaskCreate,
    StandardTaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskStatus,
    TaskType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

# ---------------------------------------------------------------------------
# Redis Key 常量
# ---------------------------------------------------------------------------

_TASK_PREFIX = "task"
_TASK_INDEX_KEY = "task:index"  # Sorted set: score=创建时间戳, member=task_id

# 并发限制（PRD §4.3.7.B）
MAX_CUSTOM_PER_USER = 3
MAX_CUSTOM_SYSTEM = 20

# 允许创建任务的角色（PRD §4.3.7）
_TASK_CREATOR_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN")

# 活跃状态集合（用于并发计数）
_ACTIVE_STATUSES = frozenset({TaskStatus.PENDING.value, TaskStatus.RUNNING.value})

# 终态集合（不可取消）
_TERMINAL_STATUSES = frozenset(
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


async def _save_task(task_data: dict[str, str]) -> None:
    """保存任务状态到 Redis Hash 并更新索引.

    Args:
        task_data: 任务字段字典（所有值已转为字符串）
    """
    task_id = task_data["task_id"]
    await redis_client.hset(_task_key(task_id), mapping=task_data)

    # 加入有序集合索引（score 为创建时间戳）
    created_at = task_data.get("created_at", "")
    try:
        score = datetime.fromisoformat(created_at).timestamp()
    except (ValueError, TypeError):
        score = datetime.now(UTC).timestamp()
    await redis_client.zadd(_TASK_INDEX_KEY, {task_id: score})


async def _get_task(task_id: str) -> dict[str, str] | None:
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


def _task_to_response(data: dict[str, Any]) -> TaskResponse:
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


async def _count_active_custom_tasks(user_id: str | None = None) -> int:
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
        data = await _get_task(tid)
        if not data:
            continue
        if data.get("task_type") != TaskType.CUSTOM.value:
            continue
        if data.get("status") not in _ACTIVE_STATUSES:
            continue
        if user_id is not None and data.get("created_by_id") != str(user_id):
            continue
        count += 1
    return count


async def _sync_task_status(task_id: str, data: dict[str, Any]) -> None:
    """从 Celery 同步任务状态到 Redis（惰性更新）.

    查询关联的 Celery 任务状态，更新 Redis 中的任务进度和状态。
    终态任务不再同步。

    Args:
        task_id: 任务 ID
        data: Redis Hash 字段字典（会被原地更新）
    """
    from celery.result import AsyncResult

    from app.tasks.celery_app import celery_app

    current_status = data.get("status", "")
    if current_status in _TERMINAL_STATUSES:
        return

    # 获取关联的 Celery 任务 ID
    celery_ids_raw = data.get("celery_task_id") or data.get("celery_task_ids")
    if not celery_ids_raw:
        return

    try:
        if data.get("task_type") == TaskType.STANDARD.value:
            celery_ids: list[str] = [celery_ids_raw]
        else:
            celery_ids = json.loads(celery_ids_raw)
    except (json.JSONDecodeError, TypeError):
        return

    if not celery_ids:
        return

    total = len(celery_ids)
    done = 0
    failed = 0
    for cid in celery_ids:
        result = AsyncResult(cid, app=celery_app)
        celery_state = result.state
        if celery_state == "SUCCESS":
            done += 1
        elif celery_state in ("FAILURE", "REVOKED"):
            failed += 1

    progress = done / total if total > 0 else 0.0
    updates: dict[str, str] = {"progress": str(round(progress, 4))}

    if done == total:
        updates["status"] = TaskStatus.SUCCESS.value
        updates["finished_at"] = _now_iso()
        updates["progress"] = "1.0"
    elif failed > 0 and (done + failed) == total:
        updates["status"] = TaskStatus.FAILED.value
        updates["finished_at"] = _now_iso()
        updates["error_message"] = f"{failed}/{total} 子任务失败"
    elif done > 0 or failed > 0:
        updates["status"] = TaskStatus.RUNNING.value
        if not data.get("started_at"):
            updates["started_at"] = _now_iso()
        updates["loops_done"] = str(done)

    if updates:
        await redis_client.hset(_task_key(task_id), mapping=updates)
        data.update(updates)
        # 终态转换时发送通知（Phase 5 补齐）
        new_status = updates.get("status", "")
        if new_status in _TERMINAL_STATUSES and current_status not in _TERMINAL_STATUSES:
            try:
                from app.services import task_tracker

                await task_tracker._send_notification(data)
            except Exception:
                logger.warning("任务通知发送失败: task_id=%s", task_id, exc_info=True)


# ---------------------------------------------------------------------------
# 接口：触发标准评估任务
# ---------------------------------------------------------------------------


@router.post("/standard/evaluate", response_model=ApiResponse[TaskResponse])
async def trigger_standard_evaluation(
    body: StandardTaskCreate,
    user: SysUser = Depends(require_roles(*_TASK_CREATOR_ROLES)),
) -> dict:
    """触发标准评估任务（手动触发每小时定时评估）.

    调用 Celery 任务 ``calculate_hourly_kpi``，全量计算所有 ACTIVE 回路。
    标准任务结果写入标准快照表，参与装置级/单元级/工厂级评分聚合。

    设计依据：IDS §2.7.6.1, PRD §4.3.7.A
    """
    from app.tasks.kpi_calc import calculate_hourly_kpi

    task_id = str(uuid4())
    now = _now_iso()

    # 触发 Celery 任务（P1 #11: 透传 body.tsStart，None 时取上一个完整计算周期）
    celery_result = calculate_hourly_kpi.delay(ts_start=body.tsStart)
    celery_task_id = celery_result.id

    task_data: dict[str, str] = {
        "task_id": task_id,
        "task_type": TaskType.STANDARD.value,
        "status": TaskStatus.PENDING.value,
        "progress": "",
        "current_stage": "",
        "loops_total": "",
        "loops_done": "",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_message": "",
        "created_by": user.username,
        "created_by_id": str(user.id),
        "celery_task_id": celery_task_id,
        "ts_start": _to_str(body.tsStart),
    }
    await _save_task(task_data)

    logger.info(
        "标准评估任务已触发: task_id=%s, celery_id=%s, user=%s",
        task_id,
        celery_task_id,
        user.username,
    )

    resp = _task_to_response(task_data)
    return success(data=resp.model_dump(), message="标准评估任务已触发")


# ---------------------------------------------------------------------------
# 接口：触发自定义评估任务
# ---------------------------------------------------------------------------


@router.post("/custom/evaluate", response_model=ApiResponse[TaskResponse])
async def trigger_custom_evaluation(
    body: CustomTaskCreate,
    user: SysUser = Depends(require_roles(*_TASK_CREATOR_ROLES)),
) -> dict:
    """触发自定义评估任务（按需触发）.

    对每个目标回路调用 Celery 任务 ``calculate_loop_kpi``。
    自定义任务结果写入自定义快照表，不参与聚合统计。
    并发限制：单用户 ≤3，全系统 ≤20（PRD §4.3.7.B）。

    设计依据：IDS §2.7.6.2, PRD §4.3.7.B
    """
    # 参数校验
    if not body.loopIds:
        raise BizError(
            code="ERR_INVALID_REQUEST",
            message="目标回路列表不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not body.metrics:
        raise BizError(
            code="ERR_INVALID_REQUEST",
            message="目标指标列表不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 并发限制检查
    user_active = await _count_active_custom_tasks(user_id=str(user.id))
    if user_active >= MAX_CUSTOM_PER_USER:
        raise BizError(
            code="ERR_TASK_CONCURRENCY_LIMIT",
            message=(
                f"您当前已有 {user_active} 个活跃自定义任务，超过单用户上限 {MAX_CUSTOM_PER_USER}"
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    system_active = await _count_active_custom_tasks()
    if system_active >= MAX_CUSTOM_SYSTEM:
        raise BizError(
            code="ERR_TASK_CONCURRENCY_LIMIT",
            message=(
                f"系统当前有 {system_active} 个活跃自定义任务，超过系统上限 {MAX_CUSTOM_SYSTEM}"
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    from app.tasks.kpi_calc import calculate_custom_loop_kpi

    task_id = str(uuid4())
    now = _now_iso()

    # 对每个回路触发 Celery 任务（自定义任务，写入 kpi_snapshot_custom 不参与聚合）
    # P1 #12: 透传 body.tsEnd，使用户指定的时间窗能传递到实际计算逻辑
    celery_task_ids: list[str] = []
    for loop_id in body.loopIds:
        result = calculate_custom_loop_kpi.delay(
            task_id, loop_id, body.tsStart, body.tsEnd
        )
        celery_task_ids.append(result.id)

    task_data: dict[str, str] = {
        "task_id": task_id,
        "task_type": TaskType.CUSTOM.value,
        "status": TaskStatus.PENDING.value,
        "progress": "0",
        "current_stage": "取数",
        "loops_total": str(len(body.loopIds)),
        "loops_done": "0",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_message": "",
        "created_by": user.username,
        "created_by_id": str(user.id),
        "celery_task_ids": json.dumps(celery_task_ids),
        "loop_ids": json.dumps(body.loopIds),
        "metrics": json.dumps(body.metrics),
        "ts_start": body.tsStart,
        "ts_end": body.tsEnd,
    }
    await _save_task(task_data)

    logger.info(
        "自定义评估任务已触发: task_id=%s, loops=%d, celery_ids=%d, user=%s",
        task_id,
        len(body.loopIds),
        len(celery_task_ids),
        user.username,
    )

    resp = _task_to_response(task_data)
    return success(data=resp.model_dump(), message="自定义评估任务已触发")


# ---------------------------------------------------------------------------
# 接口：查询任务通知（Phase 5 补齐）
# ---------------------------------------------------------------------------
# 注意：本组端点必须注册在 GET /{task_id} 之前，否则 /notifications
# 会被 /{task_id} 路径参数拦截（task_id="notifications"）。


@router.get("/notifications", response_model=ApiResponse[dict])
async def list_notifications(
    limit: int = Query(20, ge=1, le=100, description="返回条数（最多 100）"),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """查询当前用户的任务完成通知.

    任务进入终态（SUCCESS/FAILED/CANCELLED）时自动推送通知到用户通知列表。
    通知按时间倒序排列（最新在前），每用户最多保留 100 条。

    设计依据：IDS §2.7.6, PRD §4.3.7
    """
    from app.services import task_tracker

    notifications = await task_tracker.get_notifications(str(user.id), limit=limit)
    return success(data={"items": notifications, "total": len(notifications)})


@router.post("/notifications/{task_id}/read", response_model=ApiResponse[dict])
async def mark_notification_read(
    task_id: str,
    user: SysUser = Depends(get_current_user),
) -> dict:
    """标记指定任务的通知为已读（从通知列表移除）.

    设计依据：IDS §2.7.6
    """
    from app.services import task_tracker

    removed = await task_tracker.mark_notification_read(str(user.id), task_id)
    if not removed:
        raise BizError(
            code="ERR_NOTIFICATION_NOT_FOUND",
            message=f"通知不存在或已读: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return success(data={"taskId": task_id, "read": True}, message="通知已标记为已读")


# ---------------------------------------------------------------------------
# 接口：查询单个任务状态
# ---------------------------------------------------------------------------


@router.get("/{task_id}", response_model=ApiResponse[TaskResponse])
async def get_task_status(
    task_id: str,
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询单个任务状态.

    从 Redis 读取任务状态，并惰性同步 Celery 任务状态（PENDING/RUNNING 时）。

    设计依据：IDS §2.7.6.3, PRD §4.3.7.C
    """
    data = await _get_task(task_id)
    if data is None:
        raise BizError(
            code="ERR_TASK_NOT_FOUND",
            message=f"任务不存在: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 惰性同步 Celery 任务状态
    await _sync_task_status(task_id, data)

    resp = _task_to_response(data)
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# 接口：查询任务列表
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[TaskListResponse])
async def list_tasks(
    taskType: str | None = Query(None, description="按任务类型筛选：STANDARD/CUSTOM"),
    status_filter: str | None = Query(
        None, alias="status", description="按状态筛选：PENDING/RUNNING/SUCCESS/FAILED/CANCELLED"
    ),
    startTime: str | None = Query(None, description="创建时间起始（ISO 8601）"),
    endTime: str | None = Query(None, description="创建时间结束（ISO 8601）"),
    limit: int = Query(50, ge=1, le=200, description="返回条数（最多 200）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询任务列表（按类型/状态/时间筛选）.

    按创建时间倒序排列，支持分页。

    设计依据：IDS §2.7.6.4, PRD §4.3.7.C
    """
    # 从索引获取所有 task_id（按创建时间倒序）
    task_ids = await redis_client.zrevrange(_TASK_INDEX_KEY, 0, -1)

    items: list[TaskResponse] = []
    for tid in task_ids:
        data = await _get_task(tid)
        if data is None:
            continue

        # 筛选：任务类型
        if taskType and data.get("task_type") != taskType:
            continue
        # 筛选：状态
        if status_filter and data.get("status") != status_filter:
            continue
        # 筛选：创建时间范围
        created_at = data.get("created_at", "")
        if startTime and created_at < startTime:
            continue
        if endTime and created_at > endTime:
            continue

        items.append(_task_to_response(data))

    total = len(items)
    paginated = items[offset : offset + limit]

    resp = TaskListResponse(items=paginated, total=total)
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# 接口：取消任务
# ---------------------------------------------------------------------------


@router.post("/{task_id}/cancel", response_model=ApiResponse[TaskResponse])
async def cancel_task(
    task_id: str,
    user: SysUser = Depends(require_roles(*_TASK_CREATOR_ROLES)),
) -> dict:
    """取消运行中的任务.

    撤销关联的 Celery 任务，并将任务状态更新为 CANCELLED。
    终态任务（SUCCESS/FAILED/CANCELLED）不可取消。

    设计依据：IDS §2.7.6.5, PRD §4.3.7.C
    """
    data = await _get_task(task_id)
    if data is None:
        raise BizError(
            code="ERR_TASK_NOT_FOUND",
            message=f"任务不存在: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    current_status = data.get("status", "")
    if current_status in _TERMINAL_STATUSES:
        raise BizError(
            code="ERR_TASK_NOT_CANCELLABLE",
            message=f"任务已处于终态: {current_status}，无法取消",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 撤销关联的 Celery 任务
    from app.tasks.celery_app import celery_app

    celery_ids_raw = data.get("celery_task_id") or data.get("celery_task_ids")
    if celery_ids_raw:
        try:
            if data.get("task_type") == TaskType.STANDARD.value:
                celery_ids = [celery_ids_raw]
            else:
                celery_ids = json.loads(celery_ids_raw)
            for cid in celery_ids:
                celery_app.control.revoke(cid, terminate=False)
        except (json.JSONDecodeError, TypeError):
            logger.warning("解析 Celery 任务 ID 失败: task_id=%s", task_id)

    updates = {
        "status": TaskStatus.CANCELLED.value,
        "finished_at": _now_iso(),
    }
    await redis_client.hset(_task_key(task_id), mapping=updates)
    data.update(updates)

    # 取消是终态转换，发送通知（Phase 5 补齐）
    try:
        from app.services import task_tracker

        await task_tracker._send_notification(data)
    except Exception:
        logger.warning("任务取消通知发送失败: task_id=%s", task_id, exc_info=True)

    logger.info("任务已取消: task_id=%s, user=%s", task_id, user.username)

    resp = _task_to_response(data)
    return success(data=resp.model_dump(), message="任务已取消")


# ---------------------------------------------------------------------------
# 接口：查询非标任务的具体指标计算结果（P3-T5 新增）
# ---------------------------------------------------------------------------


@router.get("/{task_id}/results", response_model=ApiResponse[dict])
async def get_task_results(
    task_id: str,
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=200, description="每页条数（最多 200）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询非标（自定义）任务的具体指标计算结果.

    从 ``kpi_snapshot_custom`` 表查询指定 ``task_id`` 的全部回路 KPI 计算结果，
    包含综合评分、各指标率、可信度等级、数据血缘等信息。

    返回：
    - ``items``: 各回路的计算结果列表（含 loopTagName、score、各指标率）
    - ``total``: 总回路数
    - ``page`` / ``pageSize``: 分页信息
    - ``taskStatus``: 任务当前状态（从 Redis 读取）

    设计依据：DDS v4.1 §2.14, PRD §4.3.7B, FDS §5.3.11
    """
    # 查询任务状态（从 Redis）
    task_data = await _get_task(task_id)
    task_status = task_data.get("status", "UNKNOWN") if task_data else "NOT_FOUND"

    # 统计总数
    count_stmt = (
        select(func.count())
        .select_from(KpiSnapshotCustom)
        .where(KpiSnapshotCustom.task_id == task_id)
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    if total == 0:
        return success(
            data={
                "items": [],
                "total": 0,
                "page": page,
                "pageSize": pageSize,
                "taskStatus": task_status,
            }
        )

    # 分页查询，关联 loop_ledger 获取回路 tag_name
    stmt = (
        select(KpiSnapshotCustom, LoopLedger.tag_name.label("loop_tag_name"))
        .outerjoin(LoopLedger, KpiSnapshotCustom.loop_id == LoopLedger.id)
        .where(KpiSnapshotCustom.task_id == task_id)
        .order_by(KpiSnapshotCustom.ts_start.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
    )
    result = await db.execute(stmt)
    rows = result.all()

    items = [
        _build_task_result_item(snapshot, loop_tag_name)
        for snapshot, loop_tag_name in rows
    ]

    return success(
        data={
            "items": items,
            "total": total,
            "page": page,
            "pageSize": pageSize,
            "taskStatus": task_status,
        }
    )


def _build_task_result_item(snapshot: KpiSnapshotCustom, loop_tag_name: str | None) -> dict:
    """构建非标任务结果单项响应字典."""
    return {
        "resultId": str(snapshot.id),
        "taskId": str(snapshot.task_id),
        "loopId": str(snapshot.loop_id),
        "loopTagName": loop_tag_name,
        "tsStart": snapshot.ts_start.isoformat() if snapshot.ts_start else None,
        "tsEnd": snapshot.ts_end.isoformat() if snapshot.ts_end else None,
        "score": float(snapshot.score) if snapshot.score is not None else None,
        "accuracyRate": (
            float(snapshot.accuracy_rate) if snapshot.accuracy_rate is not None else None
        ),
        "fastRate": float(snapshot.fast_rate) if snapshot.fast_rate is not None else None,
        "steadyRate": (
            float(snapshot.steady_rate) if snapshot.steady_rate is not None else None
        ),
        "effectiveAutoRate": (
            float(snapshot.effective_auto_rate)
            if snapshot.effective_auto_rate is not None
            else None
        ),
        "goodValueRate": (
            float(snapshot.good_value_rate)
            if snapshot.good_value_rate is not None
            else None
        ),
        "oscillationRate": (
            float(snapshot.oscillation_rate)
            if snapshot.oscillation_rate is not None
            else None
        ),
        "saturationRate": (
            float(snapshot.saturation_rate)
            if snapshot.saturation_rate is not None
            else None
        ),
        "autoModeRate": (
            float(snapshot.auto_mode_rate) if snapshot.auto_mode_rate is not None else None
        ),
        "stictionIndex": (
            float(snapshot.stiction_index) if snapshot.stiction_index is not None else None
        ),
        "outputTripIndex": (
            float(snapshot.output_trip_index)
            if snapshot.output_trip_index is not None
            else None
        ),
        "settlingTime": (
            float(snapshot.settling_time) if snapshot.settling_time is not None else None
        ),
        "idealSettlingTime": (
            float(snapshot.ideal_settling_time)
            if snapshot.ideal_settling_time is not None
            else None
        ),
        "status": snapshot.status,
        "confidenceLevel": snapshot.confidence_level,
        "validRate": (
            float(snapshot.valid_rate) if snapshot.valid_rate is not None else None
        ),
        "algorithmVersion": snapshot.algorithm_version,
        "samplingFreq": snapshot.sampling_freq,
        "qualityPolicy": snapshot.quality_policy,
        "dataLineage": snapshot.data_lineage,
        "createdAt": (
            snapshot.created_at.isoformat() if snapshot.created_at else None
        ),
    }


__all__ = ["router"]
