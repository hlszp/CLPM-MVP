"""Loop type weight endpoints (重构方案 v1.2 — 回路类型权重 CRUD).

对齐 GB/T 44693.2-2024 附表1，用于回路级综合评分公式：
    P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R

路由清单：
- GET /api/v1/config/loop-type-weights            — 获取全部回路类型权重（所有认证用户）
- PUT /api/v1/config/loop-type-weights/{loopType} — 更新指定类型权重（仅 ADMIN）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop_config import LoopTypeWeightItem, LoopTypeWeightUpdate
from app.services.loop_config import list_loop_type_weights, update_loop_type_weight

router = APIRouter(prefix="/config/loop-type-weights", tags=["loop-type-weight"])


@router.get("", response_model=ApiResponse[list[LoopTypeWeightItem]])
async def list_loop_type_weights_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取全部回路类型权重配置（所有认证用户可读）。"""
    data = await list_loop_type_weights(db)
    return success(data=data)


@router.put("/{loop_type}", response_model=ApiResponse[LoopTypeWeightItem])
async def update_loop_type_weight_endpoint(
    loop_type: str,
    body: LoopTypeWeightUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新指定回路类型权重（仅 ADMIN）。

    校验：
    - 类型必须存在（ERR_LOOP_TYPE_NOT_FOUND）
    - weightA + weightF + weightS 应为 1.0（±0.01 误差，ERR_WEIGHT_SUM_INVALID）
    """
    data = await update_loop_type_weight(
        db=db,
        loop_type=loop_type,
        operator=user.username,
        type_name=body.typeName,
        weight_a=body.weightA,
        weight_f=body.weightF,
        weight_s=body.weightS,
        description=body.description,
    )
    return success(data=data, message="更新成功")


__all__ = ["router"]
