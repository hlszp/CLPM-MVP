"""工厂模型 AAS 同步接口（工厂配置页）。

参考 AAS-erm ErmSync 模式：独立同步配置区（与链路配置的数据源配置分离），
支持连接测试、全量同步与同步日志查询。

路由清单（挂载于 /api/v1/configs/factory-sync）：
- GET  /settings — 读取同步配置（密码脱敏，仅 hasPassword 标记）
- PUT  /settings — 保存同步配置（password 空值=保留原密码；运行时生效）
- POST /test     — 连接测试（登录 AAS 验证账号）
- POST /sync     — 全量同步（AreaNode → plant_node，按 source_node_id upsert）
- GET  /logs     — 同步日志（倒序，默认 20 条）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.services import factory_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/factory-sync", tags=["factory-sync"])


class FactorySyncSettingUpdate(BaseModel):
    """PUT /configs/factory-sync/settings 请求体。"""

    baseUrl: str = Field(..., min_length=1, max_length=300)
    authApiPath: str = Field("/api/TokenAuth/Authenticate", max_length=300)
    nodesApiPath: str = Field("/api/services/v1/AreaNode/GetAllPagedAndSorted", max_length=300)
    userName: str = Field(..., min_length=1, max_length=100)
    password: str | None = Field(None, max_length=200, description="密码（空=保留原密码）")
    isEnabled: bool = Field(False, description="是否启用同步")
    pageBatchSize: int = Field(500, ge=1, le=2000)


@router.get("/settings", response_model=ApiResponse[dict])
async def get_factory_sync_settings(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """读取工厂模型同步配置（仅 ADMIN；密码脱敏）。"""
    setting = await factory_sync.get_sync_setting(db)
    return success(data=setting)


@router.put("/settings", response_model=ApiResponse[dict])
async def save_factory_sync_settings(
    body: FactorySyncSettingUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """保存工厂模型同步配置（仅 ADMIN；运行时生效，无需重启）。"""
    setting = await factory_sync.save_sync_setting(
        db,
        user.username,
        base_url=body.baseUrl,
        auth_api_path=body.authApiPath,
        nodes_api_path=body.nodesApiPath,
        user_name=body.userName,
        password=body.password,
        is_enabled=body.isEnabled,
        page_batch_size=body.pageBatchSize,
    )
    logger.info("工厂模型同步配置已保存: operator=%s", user.username)
    return success(data=setting, message="同步配置已保存（运行时生效）")


@router.post("/test", response_model=ApiResponse[dict])
async def test_factory_sync_connection(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """连接测试：用已保存配置登录 AAS 验证连通性（仅 ADMIN）。"""
    setting = await factory_sync.get_raw_sync_setting(db)
    result = await factory_sync.test_connection(setting)
    return success(data=result)


@router.post("/sync", response_model=ApiResponse[dict])
async def sync_factory_model_endpoint(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """全量同步 AAS 工厂模型（仅 ADMIN）。

    AreaNode → plant_node 按 source_node_id upsert（父先子后，
    本地独立节点不受影响），返回增改计数。
    """
    result = await factory_sync.sync_factory_model(db, user.username)
    return success(data=result, message=result.get("message", "同步完成"))


@router.get("/logs", response_model=ApiResponse[list])
async def get_factory_sync_logs(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询同步日志（倒序，仅 ADMIN）。"""
    logs = await factory_sync.get_sync_logs(db, limit=limit)
    return success(data=logs)


__all__ = ["router"]
