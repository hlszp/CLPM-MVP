"""User management endpoints (S5-SYS-001).

Routes:
- GET    /api/v1/users                — Paginated user list (ADMIN only)
- POST   /api/v1/users                — Create user (ADMIN only)
- PUT    /api/v1/users/{id}           — Update user (ADMIN only)
- DELETE /api/v1/users/{id}           — Disable user (ADMIN only, soft delete)
- PUT    /api/v1/users/{id}/reset-password — Reset password (ADMIN only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.user import (
    ResetPasswordRequest,
    UserCreateRequest,
    UserItem,
    UserListData,
    UserUpdateRequest,
)
from app.services.user import (
    create_user,
    disable_user,
    list_users,
    reset_password,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=ApiResponse[UserListData])
async def list_users_endpoint(
    keyword: str | None = Query(None, description="按用户名/姓名模糊查询"),
    role: str | None = Query(None, description="按角色筛选"),
    isActive: bool | None = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """分页查询用户列表（仅 ADMIN）。"""
    data = await list_users(
        db=db,
        keyword=keyword,
        role=role,
        is_active=isActive,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.post("", status_code=201, response_model=ApiResponse[UserItem])
async def create_user_endpoint(
    body: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建用户（仅 ADMIN）。用户名重复返回 ERR_USER_DUPLICATE。"""
    data = await create_user(
        db=db,
        operator=user.username,
        username=body.username,
        password=body.password,
        display_name=body.displayName,
        email=body.email,
        role=body.role,
    )
    return success(data=data, message="用户创建成功")


@router.put("/{user_id}", response_model=ApiResponse[UserItem])
async def update_user_endpoint(
    user_id: str,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新用户信息（仅 ADMIN）。"""
    data = await update_user(
        db=db,
        operator=user.username,
        user_id=user_id,
        display_name=body.displayName,
        email=body.email,
        role=body.role,
        is_active=body.isActive,
    )
    return success(data=data, message="用户更新成功")


@router.delete("/{user_id}", response_model=ApiResponse[UserItem])
async def disable_user_endpoint(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """禁用用户（仅 ADMIN，软删除 is_active=FALSE）。"""
    data = await disable_user(
        db=db,
        operator=user.username,
        user_id=user_id,
    )
    return success(data=data, message="用户已禁用")


@router.put("/{user_id}/reset-password", response_model=ApiResponse[dict])
async def reset_password_endpoint(
    user_id: str,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """重置用户密码（仅 ADMIN）。"""
    data = await reset_password(
        db=db,
        operator=user.username,
        user_id=user_id,
        new_password=body.newPassword,
    )
    return success(data=data, message="密码重置成功")


__all__ = ["router"]
