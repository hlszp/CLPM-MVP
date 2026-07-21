"""数据源配置 endpoints — 对接外部历史数据 API + 实时 SignalR Hub.

- GET   /api/v1/datasource/config           — 获取数据源配置（ADMIN）
- PUT   /api/v1/datasource/config           — 更新数据源配置（ADMIN）
- POST  /api/v1/datasource/test-history-api — 测试历史数据 API 连通性（ADMIN）
- POST  /api/v1/datasource/test-signalr     — 测试 SignalR Hub 连通性（ADMIN）

对接文档：docs/设计文档/05-IDS/HisDATA_API.md、RealDATA_API.md
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.datasource import (
    DataSourceConfigInfo,
    DataSourceConfigUpdate,
    DataSourceTestResult,
)
from app.services.datasource_config import (
    get_datasource_config,
    test_history_api_connection,
    test_signalr_hub_connection,
    update_datasource_config,
)

router = APIRouter(prefix="/datasource", tags=["datasource"])


@router.get("/config", response_model=ApiResponse[DataSourceConfigInfo])
async def get_datasource_config_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """获取数据源配置（仅 ADMIN）。"""
    data = await get_datasource_config(db)
    return success(data=data)


@router.put("/config", response_model=ApiResponse[DataSourceConfigInfo])
async def update_datasource_config_endpoint(
    body: DataSourceConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新数据源配置（仅 ADMIN）。

    即时生效：networkMode（触发 Tailscale 切换）/ historyApiUrl / historyApiToken
    / historyApiTimeout / signalrHubUrl / signalrReconnectInterval
    重启生效：dataSourceType（Provider 单例）/ signalrEnabled（订阅器后台任务）

    注意：dataSourceType 固定为 remote_api（UI 已删除选择），忽略前端传入值。
    """
    data = await update_datasource_config(
        db=db,
        operator=user.username,
        # 固定 dataSourceType=remote_api，忽略前端传入值（兼容旧前端）
        dataSourceType="remote_api",
        networkMode=body.networkMode,
        historyApiUrl=body.historyApiUrl,
        historyApiToken=body.historyApiToken,
        historyApiTimeout=body.historyApiTimeout,
        signalrHubUrl=body.signalrHubUrl,
        signalrEnabled=body.signalrEnabled,
        signalrReconnectInterval=body.signalrReconnectInterval,
        realtimeWritebackEnabled=body.realtimeWritebackEnabled,
    )

    # 根据 tailscale 切换结果构造响应消息
    msg = "配置更新成功"
    tailscale_switch = data.get("tailscaleSwitch")
    if tailscale_switch:
        status = tailscale_switch["status"]
        if status == "success":
            msg = f"配置更新成功；{tailscale_switch['message']}"
        elif status == "skipped":
            msg = f"配置更新成功；Tailscale 跳过：{tailscale_switch['message']}"
        else:
            msg = f"配置已保存，但 Tailscale 切换失败：{tailscale_switch['message']}"

    return success(data=data, message=msg)


@router.post("/test-history-api", response_model=ApiResponse[DataSourceTestResult])
async def test_history_api_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """测试历史数据 API 连通性（仅 ADMIN，使用当前已保存配置，不写入数据库）。

    使用已关联的真实 Tag 位号验证数据查询链路，而非仅测 HTTP 连通性。
    """
    from sqlalchemy import select

    from app.models.tag import TagRegistry

    # 连通性测试需要原始 Token（mask_token=False），GET 响应中的打码值不可用
    config = await get_datasource_config(db, mask_token=False)

    # 查询第一个已关联的 Tag 位号，用于真实数据查询测试
    tag_result = await db.execute(
        select(TagRegistry.tag_name).where(TagRegistry.is_linked.is_(True)).limit(1)
    )
    real_tag = tag_result.scalar_one_or_none()

    result = await test_history_api_connection(
        url=config["historyApiUrl"],
        token=config["historyApiToken"],
        timeout=config["historyApiTimeout"],
        tag_code=real_tag,
    )
    return success(data=result)


@router.post("/test-signalr", response_model=ApiResponse[DataSourceTestResult])
async def test_signalr_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """测试 SignalR Hub 连通性（仅 ADMIN）。

    使用当前内存中生效的配置（settings），而非数据库配置。
    数据库配置需重启后端才生效，内存配置才是当前实际运行的配置。
    """
    from app.core.config import settings

    result = await test_signalr_hub_connection(hub_url=settings.SIGNALR_HUB_URL)
    return success(data=result)


__all__ = ["router"]
