"""个人关系分片端点（P6-05，契约 01a §3）.

路由清单（均在 /api/v1/me 下，登录主体只来自 ``get_current_user``，
无自由 userId 参数）：

- GET    /me/loop-follows                     — 本人已关注且可访问回路（分页/搜索）
- PUT    /me/loop-follows/{loopId}            — 设定关注状态（isFollowed + expectedRevision
                                                + requestId，非 toggle）
- GET    /me/workspace-preferences            — 本人客户端可写偏好（保留键不返回）
- PUT    /me/workspace-preferences/{key}      — 按单 key 设定偏好（CAS+幂等）
- GET    /me/work-resume                      — 本人最近继续（服务端时间排序）
- PUT    /me/work-resume/{kind}/{resourceId}  — 记录最近继续（验证对象后写入）
- DELETE /me/work-resume/{kind}/{resourceId}  — 删除单条（保留版本墓碑）
- DELETE /me/work-resume                      — 清空（先原子增代际再清旧代）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_scope_ctx
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.personal_workspace import (
    LoopFollowListData,
    LoopFollowSetRequest,
    LoopFollowSetResult,
    PreferenceListData,
    PreferencePutRequest,
    PreferencePutResult,
    WorkResumeClearResult,
    WorkResumeDeleteResult,
    WorkResumeListData,
    WorkResumePutRequest,
    WorkResumePutResult,
)
from app.services.authz_scope import ScopeContext
from app.services.personal_workspace import (
    clear_work_resume,
    delete_work_resume,
    list_loop_follows,
    list_preferences,
    list_work_resume,
    put_preference,
    put_work_resume,
    set_loop_follow,
)

router = APIRouter(prefix="/me", tags=["personal-workspace"])


@router.get("/loop-follows", response_model=ApiResponse[LoopFollowListData])
async def list_loop_follows_endpoint(
    search: str | None = Query(None, max_length=100, description="按回路位号/描述搜索"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """本人已关注且当前可访问的回路列表.

    撤权对象不返回名称/描述，仅 inaccessibleCount + readStatus=PARTIAL 提示。
    """
    data = await list_loop_follows(
        db,
        user_id=str(user.id),
        ctx=ctx,
        page=page,
        page_size=pageSize,
        search=search,
    )
    return success(data=data)


@router.put("/loop-follows/{loopId}", response_model=ApiResponse[LoopFollowSetResult])
async def set_loop_follow_endpoint(
    loopId: str,
    body: LoopFollowSetRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """设定关注状态（设定语义非 toggle）.

    - 首次创建 expectedRevision=0；过期版本 409 并携当前有效版本；
    - 同 requestId 重试幂等返回已有结果；
    - 取消关注保留版本墓碑，取消后迟到的旧关注请求不得复活。
    """
    data = await set_loop_follow(
        db,
        user_id=str(user.id),
        ctx=ctx,
        loop_id=loopId,
        is_followed=body.is_followed,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
    )
    return success(data=data, message="保存成功")


@router.get("/workspace-preferences", response_model=ApiResponse[PreferenceListData])
async def list_preferences_endpoint(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """本人客户端可写偏好（按 key 索引；服务端保留键不对外返回）."""
    data = await list_preferences(db, user_id=str(user.id))
    return success(data=data)


@router.put("/workspace-preferences/{key}", response_model=ApiResponse[PreferencePutResult])
async def put_preference_endpoint(
    key: str,
    body: PreferencePutRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """按单 key 设定偏好（白名单校验；各 key 独立版本，不整包覆盖）."""
    data = await put_preference(
        db,
        user_id=str(user.id),
        key=key,
        payload=body.payload,
        expected_revision=body.expected_revision,
        request_id=body.request_id,
    )
    return success(data=data, message="保存成功")


@router.get("/work-resume", response_model=ApiResponse[WorkResumeListData])
async def list_work_resume_endpoint(
    kind: str | None = Query(None, description="按资源类型筛选（白名单内）"),
    limit: int = Query(20, ge=1, le=100, description="返回条数上限"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """本人最近继续列表（currentGeneration 供后续写/删携带）."""
    data = await list_work_resume(
        db,
        user_id=str(user.id),
        ctx=ctx,
        kind=kind,
        limit=limit,
    )
    return success(data=data)


@router.put("/work-resume/{kind}/{resourceId}", response_model=ApiResponse[WorkResumePutResult])
async def put_work_resume_endpoint(
    kind: str,
    resourceId: str,
    body: WorkResumePutRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """记录最近继续（成功验证业务对象后才写入，失败打开不制造条目）.

    generation 必须与本人当前代际一致；旧代迟到请求 409 拒绝。
    更新顺序以服务端接收时间为准，不以客户端时钟排序。
    """
    data = await put_work_resume(
        db,
        user_id=str(user.id),
        ctx=ctx,
        kind=kind,
        resource_id=resourceId,
        context=body.context,
        expected_revision=body.expected_revision,
        generation=body.generation,
        request_id=body.request_id,
    )
    return success(data=data, message="保存成功")


@router.delete(
    "/work-resume/{kind}/{resourceId}",
    response_model=ApiResponse[WorkResumeDeleteResult],
)
async def delete_work_resume_endpoint(
    kind: str,
    resourceId: str,
    generation: int = Query(..., ge=1, description="本人当前代际（旧代拒绝）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """删除单条最近继续（保留版本墓碑；重复删除幂等）."""
    data = await delete_work_resume(
        db,
        user_id=str(user.id),
        kind=kind,
        resource_id=resourceId,
        generation=generation,
    )
    return success(data=data, message="删除成功")


@router.delete("/work-resume", response_model=ApiResponse[WorkResumeClearResult])
async def clear_work_resume_endpoint(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """清空本人最近继续（先原子递增代际，再清理旧代记录）."""
    data = await clear_work_resume(db, user_id=str(user.id))
    return success(data=data, message="清空成功")


__all__ = ["router"]
