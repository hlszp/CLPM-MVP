"""回路数据管理 API — 历史数据导入 + 数据完整性检查（Phase 3）.

路由前缀: /api/v1/loops/data-import
权限: ADMIN / IC_ENGINEER / PE_ENGINEER

设计依据：data-architecture-optimization-spec §5.3.1
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import require_roles
from app.core.exceptions import BizError
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop_data import (
    ImportRequest,
    ImportTaskListResponse,
    ImportTaskResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/loops/data-import", tags=["loop-data"])

# 导入时间窗最大范围（30 天，防误操作）
_MAX_IMPORT_WINDOW_DAYS = 30

# 允许操作的角色
_IMPORT_ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER")


@router.post("/start", response_model=ApiResponse[dict])
async def start_import(
    body: ImportRequest,
    user: SysUser = Depends(require_roles(*_IMPORT_ROLES)),
) -> dict:
    """开始历史数据导入.

    创建 Celery 任务，从远端 HTTP API 拉取历史数据写入本地 TDengine 宽表。
    """
    # 1. 校验时间窗
    try:
        start_dt = datetime.fromisoformat(body.tsStart.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(body.tsEnd.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BizError(
            code="ERR_INVALID_TIME_FORMAT",
            message=f"时间格式无效: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    if start_dt >= end_dt:
        raise BizError(
            code="ERR_INVALID_TIME_RANGE",
            message="tsStart 必须早于 tsEnd",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if (end_dt - start_dt) > timedelta(days=_MAX_IMPORT_WINDOW_DAYS):
        raise BizError(
            code="ERR_IMPORT_WINDOW_TOO_LARGE",
            message=(
                f"时间窗不能超过 {_MAX_IMPORT_WINDOW_DAYS} 天"
                f"（当前: {(end_dt - start_dt).days} 天）"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not body.loopIds:
        raise BizError(
            code="ERR_INVALID_REQUEST",
            message="目标回路列表不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2. 校验远端 API 配置
    from app.core.config import settings

    if not settings.HISTORY_DATA_API_URL:
        raise BizError(
            code="ERR_REMOTE_API_NOT_CONFIGURED",
            message="HISTORY_DATA_API_URL 未配置，无法拉取远端历史数据",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 3. 触发 Celery 任务
    from app.services.data_import import create_import_task

    # 先创建一个占位 Celery task id
    from app.tasks.kpi_calc import import_history_data as import_task

    celery_result = import_task.delay(
        loop_ids=body.loopIds,
        ts_start=body.tsStart,
        ts_end=body.tsEnd,
        interval=body.interval,
        conflict_strategy=body.conflictStrategy.value,
        trigger_backfill=body.triggerBackfill,
    )

    # 创建导入任务记录
    task_id = await create_import_task(
        loop_ids=body.loopIds,
        ts_start=body.tsStart,
        ts_end=body.tsEnd,
        conflict_strategy=body.conflictStrategy.value,
        trigger_backfill=body.triggerBackfill,
        created_by=user.username,
        celery_task_id=celery_result.id,
    )

    logger.info(
        "历史数据导入任务已触发: task_id=%s, celery_id=%s, loops=%d, user=%s",
        task_id,
        celery_result.id,
        len(body.loopIds),
        user.username,
    )

    return success(
        data={"taskId": task_id, "celeryTaskId": celery_result.id},
        message=f"导入任务已启动，共 {len(body.loopIds)} 个回路",
    )


@router.get("/tasks", response_model=ApiResponse[ImportTaskListResponse])
async def list_import_tasks(
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    pageSize: int = Query(20, ge=1, le=200, description="每页条数"),
    _: SysUser = Depends(require_roles(*_IMPORT_ROLES)),
) -> dict:
    """查询导入任务列表（按创建时间倒序）."""
    from app.services.data_import import list_import_tasks as _list

    result = await _list(page=page, page_size=pageSize)
    return success(data=result)


@router.get("/{task_id}/status", response_model=ApiResponse[ImportTaskResponse])
async def get_import_status(
    task_id: str,
    _: SysUser = Depends(require_roles(*_IMPORT_ROLES)),
) -> dict:
    """查询单个导入任务状态."""
    from app.services.data_import import get_import_task

    data = await get_import_task(task_id)
    if data is None:
        raise BizError(
            code="ERR_TASK_NOT_FOUND",
            message=f"导入任务不存在: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return success(data=data)


@router.post("/{task_id}/cancel", response_model=ApiResponse[dict])
async def cancel_import(
    task_id: str,
    _: SysUser = Depends(require_roles(*_IMPORT_ROLES)),
) -> dict:
    """取消导入任务."""
    from app.services.data_import import cancel_import_task

    data = await cancel_import_task(task_id)
    if data is None:
        raise BizError(
            code="ERR_TASK_NOT_FOUND",
            message=f"导入任务不存在: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return success(data=data, message="导入任务已取消")


@router.post("/{task_id}/backfill-kpi", response_model=ApiResponse[dict])
async def trigger_backfill(
    task_id: str,
    _: SysUser = Depends(require_roles(*_IMPORT_ROLES)),
) -> dict:
    """导入完成后触发 KPI 回算.

    使用导入任务的时间范围和回路列表，触发 backfill_kpi_range Celery 任务。
    """
    from app.services.data_import import get_import_task

    data = await get_import_task(task_id)
    if data is None:
        raise BizError(
            code="ERR_TASK_NOT_FOUND",
            message=f"导入任务不存在: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    import json

    from app.core.redis import redis_client

    loop_ids_raw = await redis_client.hget(f"import_task:{task_id}", "loop_ids")
    try:
        loop_ids = json.loads(loop_ids_raw) if loop_ids_raw else []
    except (json.JSONDecodeError, TypeError):
        loop_ids = []

    ts_start = data.get("tsStart", "")
    ts_end = data.get("tsEnd", "")

    if not loop_ids or not ts_start or not ts_end:
        raise BizError(
            code="ERR_INVALID_TASK_DATA",
            message="导入任务数据不完整，无法触发回算",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    from app.tasks.kpi_calc import backfill_kpi_range

    celery_result = backfill_kpi_range.delay(ts_start, ts_end, loop_ids=loop_ids)

    logger.info(
        "KPI 回算已触发: import_task=%s, celery_id=%s, loops=%d",
        task_id,
        celery_result.id,
        len(loop_ids),
    )

    return success(
        data={"celeryTaskId": celery_result.id, "loopCount": len(loop_ids)},
        message=f"KPI 回算已触发，共 {len(loop_ids)} 个回路",
    )


__all__ = ["router"]
