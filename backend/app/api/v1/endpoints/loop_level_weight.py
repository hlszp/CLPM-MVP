"""Loop level weight endpoints (重构方案 v1.2 — 回路级别权重 CRUD).

对齐 GB/T 44693.2-2024 附表2，用于装置级聚合公式：
    装置平均性能评分 = Σ(w_i * P_i) / Σw_i

路由清单：
- GET /api/v1/configs/loop-level-weights         — 获取全部回路级别权重（所有认证用户）
- PUT /api/v1/configs/loop-level-weights/{level} — 更新指定级别权重（仅 ADMIN）

P2 #30 B7: 前缀从 `/config/loop-level-weights` 统一为 `/configs/loop-level-weights`，
与 `/configs/metrics` / `/configs/diagnosis` / `/configs/loop-type-weights` 保持一致。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop_config import LoopLevelWeightItem, LoopLevelWeightUpdate
from app.services.loop_config import list_loop_level_weights, update_loop_level_weight

router = APIRouter(prefix="/configs/loop-level-weights", tags=["loop-level-weight"])


@router.get("", response_model=ApiResponse[list[LoopLevelWeightItem]])
async def list_loop_level_weights_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取全部回路级别权重配置（所有认证用户可读）。"""
    data = await list_loop_level_weights(db)
    return success(data=data)


@router.put("/{level}", response_model=ApiResponse[LoopLevelWeightItem])
async def update_loop_level_weight_endpoint(
    level: int,
    body: LoopLevelWeightUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新指定回路级别权重（仅 ADMIN）。

    校验：
    - 级别必须存在（ERR_LOOP_LEVEL_NOT_FOUND）
    - weight 必须 > 0（ERR_WEIGHT_INVALID）
    """
    data = await update_loop_level_weight(
        db=db,
        level=level,
        operator=user.username,
        level_name=body.levelName,
        weight=body.weight,
        description=body.description,
    )
    return success(data=data, message="更新成功")


__all__ = ["router"]
