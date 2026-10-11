"""分层配置发布接口（P2-02：统一作用域 + expectedRevision + 跨进程一致）.

统一作用域链（低→高）：DEFAULT < TEMPLATE < NODE < LOOP < TASK。

路由清单：
- GET  /api/v1/configs/publish/revision                    — 当前统一配置 revision
- GET  /api/v1/configs/publish/effective                   — 有效参数解析（来源/遮盖关系）
- GET  /api/v1/configs/publish/overrides                   — 分层覆盖列表
- POST /api/v1/configs/publish/overrides                   — 发布一层覆盖（仅 ADMIN）
- POST /api/v1/configs/publish/overrides/{id}/reset        — 重置一层覆盖（仅 ADMIN）
- GET  /api/v1/configs/publish/publications                — 发布账本历史
- POST /api/v1/configs/publish/publications/{rev}/rollback — 回退指定发布（仅 ADMIN）

权限（DEC-03，2026-10 已裁决）：**配置发布为 ADMIN 职责**——发布/重置/回退
端点一律 ``require_roles("ADMIN")``；IC/PE 可维护草稿与回路工程属性，本批
未开放草稿工作流端点（后续新增时按 ADMIN=发布 / IC+PE=草稿 标注）；查询
端点对 ADMIN/IC/PE 只读开放。

冲突语义：发布/重置/回退必带 ``expectedRevision``，与持久 revision 不一致
（并发抢先提交）返回 409 并携带最新版本（``data.currentRevision``）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.config_publish import ConfigOverride, ConfigPublication
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    EffectiveParamsResponse,
    OverrideItem,
    OverridePublishRequest,
    OverrideResetRequest,
    PublicationItem,
    PublishResultResponse,
    RevisionInfoResponse,
    RollbackRequest,
    RollbackResultResponse,
)
from app.services import config_publish as cp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/publish", tags=["config-publish"])

#: 只读角色（查询端点）；写端点一律 ADMIN（DEC-03）
_READ_ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER")


def _override_to_item(row: ConfigOverride) -> OverrideItem:
    return OverrideItem(
        id=row.id,
        layer=row.layer,
        scopeId=row.scope_id,
        metricCode=row.metric_code,
        params=dict(row.params or {}),
        isEnabled=bool(row.is_enabled),
        publishedRevision=int(row.published_revision),
        updatedBy=row.updated_by,
        updatedAt=row.updated_at.isoformat() if row.updated_at else None,
        version=int(row.version),
    )


def _publication_to_item(row: ConfigPublication) -> PublicationItem:
    return PublicationItem(
        revision=int(row.revision),
        operation=row.operation,
        layer=row.layer,
        scope=dict(row.scope or {}),
        reason=row.reason,
        operator=row.operator,
        beforeValue=row.before_value,
        afterValue=row.after_value,
        affectedLoops=row.affected_loops,
        rollbackRevision=row.rollback_revision,
        createdAt=row.created_at.isoformat() if row.created_at else None,
    )


# ---------------------------------------------------------------------------
# GET /configs/publish/revision — 当前统一配置 revision
# ---------------------------------------------------------------------------


@router.get("/revision", response_model=ApiResponse[RevisionInfoResponse])
async def get_revision(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """当前统一配置 revision（持久值，DB 真相源）+ 最近一次发布摘要."""
    revision = await cp.get_current_revision(db)
    last = await cp.get_last_publication(db)
    data: dict[str, Any] = {"revision": revision, "lastPublication": None}
    if last is not None:
        data["lastPublication"] = _publication_to_item(last).model_dump(by_alias=True)
    return success(data=data)


# ---------------------------------------------------------------------------
# GET /configs/publish/effective — 有效参数解析（有效值/来源/遮盖关系）
# ---------------------------------------------------------------------------


@router.get("/effective", response_model=ApiResponse[EffectiveParamsResponse])
async def get_effective_params(
    metricCode: str = Query(..., min_length=1, max_length=50),
    controlType: str | None = Query(default=None),
    templateKey: str | None = Query(default=None, max_length=64),
    nodeId: str | None = Query(default=None, max_length=64),
    loopId: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """解析统一作用域链的有效参数（逐参数 source/sourceId/sourceRevision + 遮盖关系）.

    TASK 层为任务内存覆盖不落库，查询接口不涉及（任务内经
    ``pin_config_snapshot`` 固定）。
    """
    result = await cp.resolve_effective_params(
        db,
        metricCode,
        controlType,
        template_key=templateKey,
        node_id=nodeId,
        loop_id=loopId,
    )
    response = EffectiveParamsResponse(
        metricCode=result["metricCode"],
        controlType=result["controlType"],
        revision=result["revision"],
        params=result["params"],
        effective=result["effective"],
        shadowed=result["shadowed"],
    )
    return success(data=response.model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# GET /configs/publish/overrides — 分层覆盖列表
# ---------------------------------------------------------------------------


@router.get("/overrides", response_model=ApiResponse[dict])
async def list_overrides(
    layer: str | None = Query(default=None),
    metricCode: str | None = Query(default=None, max_length=50),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """列出 TEMPLATE/NODE/LOOP 分层覆盖（DEFAULT 层经既有 algorithm-params 接口查看）."""
    query = select(ConfigOverride).order_by(
        ConfigOverride.layer.asc(), ConfigOverride.metric_code.asc()
    )
    if layer:
        query = query.where(ConfigOverride.layer == layer)
    if metricCode:
        query = query.where(ConfigOverride.metric_code == metricCode)
    rows = (await db.execute(query)).scalars().all()
    items = [_override_to_item(row).model_dump(by_alias=True) for row in rows]
    return success(data={"items": items, "total": len(items)})


# ---------------------------------------------------------------------------
# POST /configs/publish/overrides — 发布一层覆盖（ADMIN，DEC-03）
# ---------------------------------------------------------------------------


@router.post("/overrides", response_model=ApiResponse[PublishResultResponse])
async def publish_override_endpoint(
    body: OverridePublishRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """发布一层覆盖（整组替换语义；expectedRevision 乐观锁，冲突 409）.

    DEC-03：发布动作 ADMIN。IC/PE 草稿维护路径本批未开放（后续按
    ADMIN=发布 / IC+PE=草稿 标注）。
    """
    result = await cp.publish_override(
        db,
        layer=body.layer,
        scope_id=body.scopeId,
        metric_code=body.metricCode,
        params=body.params,
        expected_revision=body.expectedRevision,
        reason=body.reason,
        operator=user.username,
        control_type=body.controlType,
    )
    response = PublishResultResponse(
        revision=result["revision"],
        rollbackRevision=result["rollbackRevision"],
        affectedLoops=result["affectedLoops"],
        note=result["note"],
        before=result["before"],
        after=result["after"],
        layer=result["layer"],
        scopeId=result["scopeId"],
        metricCode=result["metricCode"],
    )
    return success(data=response.model_dump(by_alias=True), message="配置已发布")


# ---------------------------------------------------------------------------
# POST /configs/publish/overrides/{id}/reset — 重置一层覆盖（ADMIN）
# ---------------------------------------------------------------------------


@router.post("/overrides/{override_id}/reset", response_model=ApiResponse[PublishResultResponse])
async def reset_override_endpoint(
    override_id: str,
    body: OverrideResetRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """重置一层覆盖（P2-01 遗留收口：重置低层后高层仍生效且可解释）.

    响应 resetExplanation 逐键说明被重置层的键中哪些仍被更高层覆盖
    （值/来源层/来源作用域/发布版本）。
    """
    result = await cp.reset_override(
        db,
        override_id=override_id,
        expected_revision=body.expectedRevision,
        reason=body.reason,
        operator=user.username,
    )
    before_params = result.get("before") or {}
    explanation: list[dict[str, Any]] = []
    if before_params:
        explanation = await cp.explain_reset(
            db,
            layer=result["layer"],
            scope_id=result["scopeId"],
            metric_code=result["metricCode"],
            control_type=None,
            keys=list(before_params.keys()),
            template_key=body.templateKey,
            node_id=body.nodeId,
            loop_id=body.loopId,
        )
    response = PublishResultResponse(
        revision=result["revision"],
        rollbackRevision=result["rollbackRevision"],
        affectedLoops=result["affectedLoops"],
        note=result["note"],
        before=result["before"],
        after=result["after"],
        layer=result["layer"],
        scopeId=result["scopeId"],
        metricCode=result["metricCode"],
        resetExplanation=explanation,
    )
    return success(data=response.model_dump(by_alias=True), message="配置层已重置")


# ---------------------------------------------------------------------------
# GET /configs/publish/publications — 发布账本历史
# ---------------------------------------------------------------------------


@router.get("/publications", response_model=ApiResponse[dict])
async def list_publications(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """发布账本（追加式，按 revision 倒序）."""
    rows = (
        (
            await db.execute(
                select(ConfigPublication).order_by(ConfigPublication.revision.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    items = [_publication_to_item(row).model_dump(by_alias=True) for row in rows]
    return success(data={"items": items, "total": len(items)})


# ---------------------------------------------------------------------------
# POST /configs/publish/publications/{revision}/rollback — 回退（ADMIN）
# ---------------------------------------------------------------------------


@router.post(
    "/publications/{revision}/rollback", response_model=ApiResponse[RollbackResultResponse]
)
async def rollback_publication_endpoint(
    revision: int,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回退指定发布：将其 before 状态重新应用为新 revision（不删历史）.

    DEC-03：回退动作 ADMIN。方案 §6"配置回退生成新 revision"——rollback(R)
    是 R 的逆操作重放，R 之后其他作用域的发布不受影响。
    """
    if revision < 1:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message=f"回退目标 revision 必须 ≥1，收到 {revision}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    result = await cp.rollback_publication(
        db,
        revision=revision,
        expected_revision=body.expectedRevision,
        reason=body.reason,
        operator=user.username,
    )
    response = RollbackResultResponse(
        revision=result["revision"],
        rollbackRevision=result["rollbackRevision"],
        rolledBackRevision=result["rolledBackRevision"],
        description=result["description"],
    )
    return success(data=response.model_dump(by_alias=True), message="配置已回退")


__all__ = ["router"]
