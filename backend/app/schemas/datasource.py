"""数据源配置 schemas — 对接外部历史数据 API + 实时 SignalR Hub.

对齐 docs/设计文档/05-IDS/HisDATA_API.md 与 RealDATA_API.md。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel


class DataSourceConfigInfo(CamelModel):
    """数据源配置信息。"""

    # 历史数据源（保留字段，固定 remote_api，UI 不暴露选择）
    dataSourceType: str = Field(..., description="历史数据源类型（保留字段，固定 remote_api）")
    # 网络模式（局域网/公网切换，控制 Tailscale 子网路由）
    networkMode: Literal["lan", "wan"] = Field(
        "lan", description="网络模式：lan 局域网直连 / wan 公网走 Tailscale"
    )
    historyApiUrl: str | None = Field(None, description="外部历史数据 API 地址")
    historyApiToken: str | None = Field(
        None, description="外部历史数据 API 鉴权 Token（打码返回，保留前后各 4 位）"
    )
    historyApiTimeout: float = Field(30.0, description="外部历史数据 API 超时（秒）")

    # 实时数据源
    signalrHubUrl: str | None = Field(None, description="实时数据 SignalR Hub URL")
    signalrEnabled: bool = Field(False, description="是否启用实时数据订阅")
    signalrReconnectInterval: int = Field(5, description="SignalR 断线重连间隔（秒）")
    realtimeWritebackEnabled: bool = Field(
        False, description="是否将实时数据写回本地 TDengine 宽表（仅 tdengine 模式生效）"
    )

    # 运行态标记（启动时初始化的实际状态，UI 用于提示"需重启生效"）
    historyProviderActive: str = Field(
        ..., description="当前生效的历史数据 Provider：tdengine / remote_api"
    )
    signalrSubscriberRunning: bool = Field(
        ..., description="实时订阅器真实运行状态（非配置镜像，启停变更需重启后端生效）"
    )

    # tailscale 客户端可用性预检（容器内为 False）
    tailscaleAvailable: bool = Field(
        False, description="tailscale 客户端是否可用（容器内为 False）"
    )
    # Tailscale 切换结果（仅 networkMode 变化时返回，GET 时为 null）
    tailscaleSwitch: dict | None = Field(
        None, description="Tailscale 切换结果（仅 networkMode 变化时返回）"
    )


class DataSourceConfigUpdate(CamelModel):
    """PUT /api/v1/datasource/config 请求体。

    更新语义：字段不传（None）＝保持不变；字符串字段传空串 "" ＝显式清空。
    """

    # dataSourceType 已废弃：保留字段兼容旧前端，后端固定 remote_api
    dataSourceType: str | None = Field(
        None, description="（已废弃，保留兼容，固定 remote_api）", deprecated=True
    )
    networkMode: Literal["lan", "wan"] | None = Field(
        None, description="网络模式：lan 局域网直连 / wan 公网走 Tailscale"
    )
    historyApiUrl: str | None = Field(
        None, description="外部历史数据 API 地址（不传=不变，空串=清空）"
    )
    historyApiToken: str | None = Field(
        None,
        description="外部历史数据 API 鉴权 Token（不传=不变，空串=清空，打码值回传=忽略）",
    )
    historyApiTimeout: float | None = Field(None, description="外部历史数据 API 超时（秒）")
    signalrHubUrl: str | None = Field(None, description="实时数据 SignalR Hub URL")
    signalrEnabled: bool | None = Field(None, description="是否启用实时数据订阅")
    signalrReconnectInterval: int | None = Field(None, description="SignalR 断线重连间隔（秒）")
    realtimeWritebackEnabled: bool | None = Field(
        None, description="是否将实时数据写回本地 TDengine 宽表"
    )


class DataSourceTestResult(CamelModel):
    """数据源连通性测试结果。"""

    success: bool
    latencyMs: int | None = None
    message: str


class DataSourceHealthInfo(CamelModel):
    """数据链路健康状态（P1-05：工作台常驻卡片，IC_ENGINEER+ 可查看）。

    不含敏感字段（historyApiToken），仅供工作台首屏展示链路连通性。
    """

    networkMode: Literal["lan", "wan"] = Field(
        "lan", description="网络模式：lan 局域网直连 / wan 公网走 Tailscale"
    )
    signalrEnabled: bool = Field(False, description="是否启用实时数据订阅（配置态）")
    signalrSubscriberRunning: bool = Field(
        False, description="实时订阅器真实运行状态（运行态，需重启后端生效变更）"
    )
    signalrHubUrl: str | None = Field(None, description="实时数据 SignalR Hub URL")
    historyApiUrl: str | None = Field(None, description="外部历史数据 API 地址")
    tailscaleAvailable: bool = Field(
        False, description="tailscale 客户端是否可用（容器内为 False）"
    )
    lastSyncAt: str | None = Field(None, description="AAS Tag 最近同步时间 ISO 8601")


__all__ = [
    "DataSourceConfigInfo",
    "DataSourceConfigUpdate",
    "DataSourceHealthInfo",
    "DataSourceTestResult",
]
