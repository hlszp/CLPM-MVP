"""回路核查最小实体端点（P6-05 后续分片一 E1，契约 01a §2.1）.

路由清单（均在 /api/v1 下，登录主体只来自 ``get_current_user``，无自由
userId 参数；每次查询/操作经 ScopeContext 重新校验数据范围）：

- POST /loops/{loopId}/investigations        — 登记核查（来源+问题身份去重）
- GET  /loops/{loopId}/investigations        — 回路核查列表（stage 过滤/分页）
- GET  /investigations/{investigationId}     — 核查详情
- POST /investigations/{investigationId}/claim   — 认领（expectedRevision CAS）
- POST /investigations/{investigationId}/wait    — 进入等待（三要素必填）
- POST /investigations/{investigationId}/resume  — 恢复核查（清空三要素）
- POST /investigations/{investigationId}/close   — 结束（结果+目标回链；
                                                HANDED_OFF 拒绝，须走 /handoff）
- POST /investigations/{investigationId}/handoff — 移交（双记录原子）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_scope_ctx
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop_investigation import (
    InvestigationClaimRequest,
    InvestigationCloseRequest,
    InvestigationCreateRequest,
    InvestigationCreateResult,
    InvestigationHandoffRequest,
    InvestigationHandoffResult,
    InvestigationListData,
    InvestigationResult,
    InvestigationResumeRequest,
    InvestigationView,
    InvestigationWaitRequest,
)
from app.services.authz_scope import ScopeContext
from app.services.loop_investigation import (
    claim_investigation,
    close_investigation,
    create_investigation,
    get_investigation,
    handoff_investigation,
    list_loop_investigations,
    resume_investigation,
    wait_investigation,
)

loops_router = APIRouter(prefix="/loops", tags=["loop-investigation"])
investigations_router = APIRouter(prefix="/investigations", tags=["loop-investigation"])


@loops_router.post(
    "/{loopId}/investigations",
    response_model=ApiResponse[InvestigationCreateResult],
)
async def create_investigation_endpoint(
    loopId: str,
    body: InvestigationCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """登记核查（TODO/未认领）.

    同一回路同一来源同一问题已存在未结束核查 → 去重返回既有记录
    （deduped=true）；并发首建由部分唯一索引裁决。
    """
    data = await create_investigation(
        db,
        user_id=str(user.id),
        ctx=ctx,
        loop_id=loopId,
        source_kind=body.source_kind,
        source_id=body.source_id,
        problem_key=body.problem_key,
        title=body.title,
        detail=body.detail,
        request_id=body.request_id,
    )
    return success(data=data, message="登记成功")


@loops_router.get(
    "/{loopId}/investigations",
    response_model=ApiResponse[InvestigationListData],
)
async def list_investigations_endpoint(
    loopId: str,
    stage: str | None = Query(None, description="按阶段过滤（TODO/IN_PROGRESS/WAITING/CLOSED）"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """回路核查列表（稳定排序：创建时间倒序 + id 升序）."""
    data = await list_loop_investigations(
        db,
        ctx=ctx,
        loop_id=loopId,
        stage=stage,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@investigations_router.get(
    "/{investigationId}",
    response_model=ApiResponse[InvestigationView],
)
async def get_investigation_endpoint(
    investigationId: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """核查详情（每次打开重新校验数据范围，越权 403 通用拒绝）."""
    data = await get_investigation(db, ctx=ctx, investigation_id=investigationId)
    return success(data=data)


@investigations_router.post(
    "/{investigationId}/claim",
    response_model=ApiResponse[InvestigationResult],
)
async def claim_investigation_endpoint(
    investigationId: str,
    body: InvestigationClaimRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """认领核查（TODO → IN_PROGRESS）.

    并发认领由行锁 + expectedRevision CAS 服务端裁决，败方 409 携当前
    版本/阶段/负责人；移交预留记录仅接手人本人可认领。
    """
    data = await claim_investigation(
        db,
        user_id=str(user.id),
        role=str(user.role),
        ctx=ctx,
        investigation_id=investigationId,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
    )
    return success(data=data, message="认领成功")


@investigations_router.post(
    "/{investigationId}/wait",
    response_model=ApiResponse[InvestigationResult],
)
async def wait_investigation_endpoint(
    investigationId: str,
    body: InvestigationWaitRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """进入等待（IN_PROGRESS → WAITING；等待原因/复查条件/责任必填）."""
    data = await wait_investigation(
        db,
        user_id=str(user.id),
        role=str(user.role),
        ctx=ctx,
        investigation_id=investigationId,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
        wait_reason=body.wait_reason,
        wait_review_condition=body.wait_review_condition,
        wait_responsible=body.wait_responsible,
    )
    return success(data=data, message="已转入等待")


@investigations_router.post(
    "/{investigationId}/resume",
    response_model=ApiResponse[InvestigationResult],
)
async def resume_investigation_endpoint(
    investigationId: str,
    body: InvestigationResumeRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """恢复核查（WAITING → IN_PROGRESS；清空等待三要素）."""
    data = await resume_investigation(
        db,
        user_id=str(user.id),
        role=str(user.role),
        ctx=ctx,
        investigation_id=investigationId,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
    )
    return success(data=data, message="已恢复核查")


@investigations_router.post(
    "/{investigationId}/close",
    response_model=ApiResponse[InvestigationResult],
)
async def close_investigation_endpoint(
    investigationId: str,
    body: InvestigationCloseRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """结束核查（NO_FURTHER_ACTION/ANSWERED/CANCELLED + 可选目标回链）.

    取消必须填理由——取消 ≠ 回路正常，不改任何回路/工单状态；目标回链
    只存引用，不复制目标执行/验证状态。
    """
    data = await close_investigation(
        db,
        user_id=str(user.id),
        role=str(user.role),
        ctx=ctx,
        investigation_id=investigationId,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
        outcome=body.outcome,
        note=body.note,
        close_target_kind=body.close_target_kind,
        close_target_id=body.close_target_id,
    )
    return success(data=data, message="核查已结束")


@investigations_router.post(
    "/{investigationId}/handoff",
    response_model=ApiResponse[InvestigationHandoffResult],
)
async def handoff_investigation_endpoint(
    investigationId: str,
    body: InvestigationHandoffRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """移交核查（双记录原子：源 CLOSED/HANDED_OFF + 目标 TODO）.

    接手人只接受稳定 sys_user.id（拒绝按姓名移交）；接手人不存在/停用/
    为自己 → 400，整体回滚无半成品；目标记录复制来源身份与问题身份，
    不复制任何执行/验证状态。
    """
    data = await handoff_investigation(
        db,
        user_id=str(user.id),
        role=str(user.role),
        ctx=ctx,
        investigation_id=investigationId,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
        new_owner_id=body.new_owner_id,
        note=body.note,
    )
    return success(data=data, message="移交成功")


__all__ = ["investigations_router", "loops_router"]
