"""数据源配置 schemas — 对接外部历史数据 API + 实时 SignalR Hub.

对齐 docs/设计文档/05-IDS/HisDATA_API.md 与 RealDATA_API.md。
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


class DataSourceConfigInfo(CamelModel):
    """数据源配置信息。"""

    # 历史数据源
    dataSourceType: str = Field(..., description="历史数据源类型：tdengine / remote_api")
    historyApiUrl: str | None = Field(None, description="外部历史数据 API 地址（remote_api 模式）")
    historyApiToken: str | None = Field(None, description="外部历史数据 API 鉴权 Token")
    historyApiTimeout: float = Field(30.0, description="外部历史数据 API 超时（秒）")

    # 实时数据源
    signalrHubUrl: str | None = Field(None, description="实时数据 SignalR Hub URL")
    signalrEnabled: bool = Field(False, description="是否启用实时数据订阅")
    signalrReconnectInterval: int = Field(5, description="SignalR 断线重连间隔（秒）")

    # 运行态标记（启动时初始化的实际状态，UI 用于提示"需重启生效"）
    historyProviderActive: str = Field(..., description="当前生效的历史数据 Provider：tdengine / remote_api")
    signalrSubscriberRunning: bool = Field(..., description="实时订阅器是否在运行")


class DataSourceConfigUpdate(CamelModel):
    """PUT /api/v1/datasource/config 请求体。所有字段可选，仅更新传入字段。"""

    dataSourceType: str | None = Field(None, description="历史数据源类型：tdengine / remote_api")
    historyApiUrl: str | None = Field(None, description="外部历史数据 API 地址")
    historyApiToken: str | None = Field(None, description="外部历史数据 API 鉴权 Token")
    historyApiTimeout: float | None = Field(None, description="外部历史数据 API 超时（秒）")
    signalrHubUrl: str | None = Field(None, description="实时数据 SignalR Hub URL")
    signalrEnabled: bool | None = Field(None, description="是否启用实时数据订阅")
    signalrReconnectInterval: int | None = Field(None, description="SignalR 断线重连间隔（秒）")


class DataSourceTestResult(CamelModel):
    """数据源连通性测试结果。"""

    success: bool
    latencyMs: int | None = None
    message: str


__all__ = [
    "DataSourceConfigInfo",
    "DataSourceConfigUpdate",
    "DataSourceTestResult",
]
