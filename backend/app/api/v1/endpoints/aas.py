"""AAS endpoints (IDS v3.2 §2.2.5~2.2.6, §3.2.1).

- GET   /api/v1/aas/config       — 获取 AAS 连接配置（ADMIN）
- PUT   /api/v1/aas/config       — 更新 AAS 连接配置（ADMIN）
- POST  /api/v1/aas/config/test  — 测试 AAS 连接（ADMIN）
- POST  /api/v1/aas/sync         — 手动触发 AAS Tag 同步
- GET   /api/v1/aas/tags         — 分页查询 AAS Tag 列表
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.sys_user import SysUser
from app.models.tag import TagRegistry
from app.schemas.aas import AasConfigUpdate
from app.schemas.common import success
from app.services.aas_config import get_aas_config, update_aas_config
from app.services.aas_sync import test_aas_connection

router = APIRouter(prefix="/aas", tags=["aas"])


# ---------------------------------------------------------------------------
# AAS Config
# ---------------------------------------------------------------------------


@router.get("/config")
async def get_aas_config_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """获取 AAS 连接配置（仅 ADMIN）。"""
    data = await get_aas_config(db)
    return success(data=data)


@router.put("/config")
async def update_aas_config_endpoint(
    body: AasConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新 AAS 连接配置（仅 ADMIN，即时生效）。"""
    data = await update_aas_config(
        db=db,
        operator=user.username,
        endpoint=body.endpoint,
        sync_interval_seconds=body.syncIntervalSeconds,
        enabled=body.enabled,
        security_mode=body.securityMode,
    )
    return success(data=data, message="配置更新成功")


@router.post("/config/test")
async def test_aas_connection_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """测试 AAS 连接（仅 ADMIN，不写入数据库）。"""
    config = await get_aas_config(db)
    result = await test_aas_connection(endpoint=config["endpoint"])
    return success(data=result)


# ---------------------------------------------------------------------------
# AAS Sync
# ---------------------------------------------------------------------------


@router.post("/sync")
async def trigger_aas_sync(
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """手动触发 AAS Tag 同步（返回 task_id）。

    异步任务由 Celery 执行，前端可通过 task_id 查询状态。
    """
    from app.tasks.aas_sync import trigger_sync

    task = trigger_sync.delay()
    return success(
        data={
            "taskId": task.id,
            "status": "PROCESSING",
            "checkUrl": f"/api/v1/tasks/{task.id}",
        }
    )


# ---------------------------------------------------------------------------
# AAS Tags
# ---------------------------------------------------------------------------


@router.get("/tags")
async def list_aas_tags(
    keyword: str | None = Query(None, description="按 tag 名/描述模糊查询"),
    quality: str | None = Query(None, description="按质量码筛选：GOOD/BAD/UNCERTAIN"),
    associated: bool | None = Query(None, description="是否已关联回路"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """分页查询 AAS Tag 列表。"""
    # 构建基础查询
    conditions = []
    if keyword:
        kw = f"%{keyword}%"
        conditions.append(
            or_(
                TagRegistry.tag_name.ilike(kw),
                TagRegistry.tag_description.ilike(kw),
            )
        )
    if quality:
        # 质量码大小写不敏感
        conditions.append(func.upper(TagRegistry.quality) == quality.upper())
    if associated is not None:
        conditions.append(TagRegistry.is_linked.is_(associated))

    # 统计总数
    count_stmt = select(func.count()).select_from(TagRegistry)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # 分页查询
    stmt = select(TagRegistry).order_by(TagRegistry.tag_name)
    for cond in conditions:
        stmt = stmt.where(cond)
    stmt = stmt.offset((page - 1) * pageSize).limit(pageSize)
    result = await db.execute(stmt)
    tags = result.scalars().all()

    # 查询关联的回路信息（批量）
    tag_ids = [str(t.id) for t in tags]
    loop_mapping: dict[str, dict] = {}
    if tag_ids:
        mapping_stmt = (
            select(LoopTagMapping, LoopLedger)
            .join(LoopLedger, LoopTagMapping.loop_id == LoopLedger.id)
            .where(LoopTagMapping.tag_id.in_(tag_ids))
        )
        mapping_result = await db.execute(mapping_stmt)
        for mapping, loop in mapping_result:
            loop_mapping[str(mapping.tag_id)] = {
                "loopId": str(loop.id),
                "loopTagName": loop.tag_name,
            }

    # 获取最近同步时间
    last_sync_at = None
    sync_status = "SUCCESS"
    if tags:
        last_sync_at = max((t.last_sync_at for t in tags if t.last_sync_at), default=None)
        if last_sync_at:
            last_sync_at = (
                last_sync_at.isoformat()
                if hasattr(last_sync_at, "isoformat")
                else str(last_sync_at)
            )

    items = []
    for t in tags:
        mapping_info = loop_mapping.get(str(t.id), {})
        items.append(
            {
                "tagId": str(t.id),
                "tagName": t.tag_name,
                "description": t.tag_description,
                "tagType": t.tag_type,
                "currentValue": t.current_value,
                "quality": t.quality,
                "lastSyncAt": (
                    t.last_sync_at.isoformat()
                    if t.last_sync_at and hasattr(t.last_sync_at, "isoformat")
                    else (str(t.last_sync_at) if t.last_sync_at else None)
                ),
                "isLinked": bool(t.is_linked),
                "associatedLoopId": mapping_info.get("loopId"),
                "associatedLoopTagName": mapping_info.get("loopTagName"),
            }
        )

    return success(
        data={
            "items": items,
            "total": total,
            "page": page,
            "pageSize": pageSize,
            "lastSyncAt": last_sync_at,
            "syncStatus": sync_status,
        }
    )


__all__ = ["router"]
