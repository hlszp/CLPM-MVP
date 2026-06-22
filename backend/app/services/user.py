"""User management service (S5-SYS-001).

Business logic:
- Paginated user list with filters (keyword / role / isActive)
- Create user (username uniqueness check, password hashing, audit log)
- Update user (partial update, audit log)
- Disable user (soft delete: is_active=False, audit log)
- Reset password (hash new password, audit log)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.security import hash_password
from app.models.audit import SysAuditLog
from app.models.sys_user import SysUser

# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """Write an audit log entry."""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


async def list_users(
    db: AsyncSession,
    *,
    keyword: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Paginated user list with optional filters.

    Returns ``{"items": [...], "total": N, "page": P, "pageSize": S}``.
    """
    stmt = select(SysUser)

    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                SysUser.username.ilike(pattern),
                SysUser.display_name.ilike(pattern),
            )
        )
    if role:
        stmt = stmt.where(SysUser.role == role)
    if is_active is not None:
        stmt = stmt.where(SysUser.is_active.is_(is_active))

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.order_by(SysUser.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    users = result.scalars().all()

    return {
        "items": [_user_to_dict(u) for u in users],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def create_user(
    db: AsyncSession,
    *,
    operator: str,
    username: str,
    password: str,
    display_name: str,
    email: str | None = None,
    role: str = "IC_ENGINEER",
) -> dict:
    """Create a new user.

    Raises ``BizError(ERR_USER_DUPLICATE)`` if username already exists.
    """
    # Check username uniqueness
    existing = await db.execute(select(SysUser).where(SysUser.username == username))
    if existing.scalar_one_or_none() is not None:
        raise BizError(
            code="ERR_USER_DUPLICATE",
            message=f"用户名已存在: {username}",
            status_code=409,
        )

    user_id = str(uuid4())
    user = SysUser(
        id=user_id,
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        email=email,
        role=role,
        is_active=True,
    )
    db.add(user)

    after = {
        "id": user_id,
        "username": username,
        "displayName": display_name,
        "email": email,
        "role": role,
        "isActive": True,
    }
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="USER_CREATE",
        target_type="sys_user",
        target_id=user_id,
        before_value=None,
        after_value=json.dumps(after, ensure_ascii=False, default=str),
    )
    await db.commit()

    return after


async def update_user(
    db: AsyncSession,
    *,
    operator: str,
    user_id: str,
    display_name: str | None = None,
    email: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """Update user info (partial update).

    Raises ``BizError(ERR_USER_NOT_FOUND)`` if user does not exist.
    """
    result = await db.execute(select(SysUser).where(SysUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise BizError(
            code="ERR_USER_NOT_FOUND",
            message="用户不存在",
            status_code=404,
        )

    before = _user_to_dict(user)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if display_name is not None:
        user.display_name = display_name
    if email is not None:
        user.email = email
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    user.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _user_to_dict(user)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="USER_UPDATE",
        target_type="sys_user",
        target_id=user_id,
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


async def disable_user(
    db: AsyncSession,
    *,
    operator: str,
    user_id: str,
) -> dict:
    """Soft-delete a user by setting ``is_active=False``.

    Raises ``BizError(ERR_USER_NOT_FOUND)`` if user does not exist.
    """
    result = await db.execute(select(SysUser).where(SysUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise BizError(
            code="ERR_USER_NOT_FOUND",
            message="用户不存在",
            status_code=404,
        )

    before = _user_to_dict(user)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    user.is_active = False
    user.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _user_to_dict(user)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="USER_DISABLE",
        target_type="sys_user",
        target_id=user_id,
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


async def reset_password(
    db: AsyncSession,
    *,
    operator: str,
    user_id: str,
    new_password: str,
) -> dict:
    """Reset a user's password.

    Raises ``BizError(ERR_USER_NOT_FOUND)`` if user does not exist.
    """
    result = await db.execute(select(SysUser).where(SysUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise BizError(
            code="ERR_USER_NOT_FOUND",
            message="用户不存在",
            status_code=404,
        )

    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now(UTC).replace(tzinfo=None)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="USER_RESET_PASSWORD",
        target_type="sys_user",
        target_id=user_id,
        before_value=None,
        after_value=json.dumps({"passwordChanged": True}, ensure_ascii=False),
    )
    await db.commit()

    return {"id": user_id, "passwordChanged": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_to_dict(u: SysUser) -> dict:
    return {
        "id": str(u.id),
        "username": u.username,
        "displayName": u.display_name,
        "email": u.email,
        "role": u.role,
        "isActive": bool(u.is_active) if u.is_active is not None else True,
        "lastLoginAt": u.last_login_at.isoformat() if u.last_login_at else None,
        "createdAt": u.created_at.isoformat() if u.created_at else None,
        "updatedAt": u.updated_at.isoformat() if u.updated_at else None,
    }


__all__ = [
    "create_user",
    "disable_user",
    "list_users",
    "reset_password",
    "update_user",
]
