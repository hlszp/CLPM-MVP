"""模块管理端点 — GET/PUT /api/v1/system/modules（ADMIN）。

- GET：列出全部模块及启用状态
- PUT：更新模块启用状态（依赖校验：禁 diagnosis 时若 handling 启用则拒绝；
  启 handling 联动启 diagnosis）
- 启用/禁用需重启后端生效（Celery beat 调度变更需要重启）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.modules import (
    MODULES,
    get_enabled_modules,
    list_modules,
    save_enabled_modules,
    validate_dependencies,
)
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success

router = APIRouter(prefix="/system/modules", tags=["system"])


class ModuleItem(BaseModel):
    key: str
    name: str
    order: int
    base: bool
    deps: list[str] = Field(default_factory=list)
    enabled: bool


class ModulesResponse(BaseModel):
    modules: list[ModuleItem]
    enabledKeys: list[str]
    restartRequired: bool = True


class UpdateModulesRequest(BaseModel):
    enabledKeys: list[str] = Field(..., description="期望启用的模块 key 列表")


@router.get("", response_model=ApiResponse[ModulesResponse])
async def get_modules(
    _: SysUser = Depends(get_current_user),
) -> dict:
    """列出全部模块及当前启用状态。"""
    modules = list_modules()
    enabled = sorted(get_enabled_modules())
    return success(
        ModulesResponse(
            modules=[ModuleItem(**m) for m in modules],
            enabledKeys=enabled,
            restartRequired=True,
        ).model_dump()
    )


@router.put("", response_model=ApiResponse[ModulesResponse])
async def update_modules(
    body: UpdateModulesRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新模块启用状态（ADMIN）。

    依赖规则：
    - 禁用 diagnosis 时若 handling 仍启用则拒绝
    - 启用 handling 时自动联动启用 diagnosis
    - 基础模块不可禁用
    """
    requested = set(body.enabledKeys)
    # 非法 key 拒绝
    unknown = requested - set(MODULES.keys())
    if unknown:
        raise BizError(
            code="ERR_PARAM",
            message=f"未知模块 key: {', '.join(sorted(unknown))}",
            status_code=400,
        )

    current = get_enabled_modules()
    # 基础模块强制启用
    for key, meta in MODULES.items():
        if meta.get("base"):
            requested.add(key)

    # 自动补全依赖（启用 handling → 启用 diagnosis）
    for key in list(requested):
        for dep in MODULES[key].get("deps", []):
            requested.add(dep)

    # 校验禁用冲突（禁 diagnosis 时 handling 不能启用）
    for key in list(current - requested):
        conflicts = validate_dependencies(requested, stopping_on=key)
        if conflicts:
            raise BizError(
                code="ERR_MODULE_DEPENDENCY",
                message="; ".join(conflicts),
                status_code=400,
            )

    try:
        saved = await save_enabled_modules(db, requested, operator=user.username)
    except ValueError as exc:
        raise BizError(code="ERR_MODULE_DEPENDENCY", message=str(exc), status_code=400) from exc

    modules = list_modules()
    return success(
        ModulesResponse(
            modules=[ModuleItem(**m) for m in modules],
            enabledKeys=sorted(saved),
            restartRequired=True,
        ).model_dump()
    )
