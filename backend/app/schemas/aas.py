"""AAS-related schemas (IDS v3.2 §2.2.5~2.2.6, §3.2.1)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


class AasConfigInfo(CamelModel):
    """AAS 连接配置信息。"""

    endpoint: str = Field(..., description="OPC UA 端点 URL")
    syncIntervalSeconds: int = Field(..., description="同步周期（秒）")
    enabled: bool = Field(..., description="是否启用定时同步")
    mockMode: bool = Field(..., description="是否为 Mock 模式（无真实 AAS）")
    securityMode: str = Field("None", description="安全模式：None/Sign/SignAndEncrypt")


class AasConfigUpdate(CamelModel):
    """PUT /api/v1/aas/config 请求体。"""

    endpoint: str | None = Field(None, description="OPC UA 端点 URL")
    syncIntervalSeconds: int | None = Field(None, ge=30, le=86400, description="同步周期（秒）")
    enabled: bool | None = Field(None, description="是否启用定时同步")
    securityMode: str | None = Field(None, description="安全模式：None/Sign/SignAndEncrypt")


class AasConfigTestResult(CamelModel):
    """POST /api/v1/aas/config/test 响应。"""

    success: bool
    latencyMs: int | None = None
    message: str


class AasSyncTriggerResult(CamelModel):
    """POST /api/v1/aas/sync 响应。"""

    taskId: str
    status: str = "PROCESSING"
    checkUrl: str | None = None


class AasTagItem(CamelModel):
    """AAS Tag 列表项。"""

    tagId: str
    tagName: str
    description: str | None = None
    tagType: str | None = None
    currentValue: float | None = None
    quality: str | None = None
    lastSyncAt: str | None = None
    isLinked: bool = False
    associatedLoopId: str | None = None
    associatedLoopTagName: str | None = None


class AasTagListData(CamelModel):
    """AAS Tag 列表响应 data 块。"""

    items: list[AasTagItem]
    total: int
    page: int
    pageSize: int
    lastSyncAt: str | None = None
    syncStatus: str = "SUCCESS"


__all__ = [
    "AasConfigInfo",
    "AasConfigTestResult",
    "AasConfigUpdate",
    "AasSyncTriggerResult",
    "AasTagItem",
    "AasTagListData",
]
