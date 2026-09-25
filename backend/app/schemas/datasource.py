"""数据源配置 schemas — 对接外部历史数据 API + 实时 SignalR Hub.

对齐 docs/设计文档/05-IDS/HisDATA_API.md 与 RealDATA_API.md。
"""

from __future__ import annotations

from typing import Any, Literal

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
    # 实时数据断点续传（运行时经 sys_config 调整，即时生效无需重启）
    gapBackfillEnabled: bool = Field(
        False, description="实时数据断点续传总开关（SignalR 重连后自动补齐缺口）"
    )
    gapBackfillMinGapSeconds: int = Field(600, description="断点续传缺口阈值（秒，小于该缺口不补）")

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
    gapBackfillEnabled: bool | None = Field(None, description="实时数据断点续传总开关")
    gapBackfillMinGapSeconds: int | None = Field(
        None,
        description="断点续传缺口阈值（秒，下限 60，上限 86400=24h）",
        ge=60,
        le=86400,
    )


class DataSourceTestResult(CamelModel):
    """数据源连通性测试结果。"""

    success: bool
    latencyMs: int | None = None
    message: str


class SubscriptionRefreshResult(CamelModel):
    """实时订阅刷新结果（POST /datasource/refresh-subscription）。

    由订阅 Leader 进程写入 Redis 结果 key，API 轮询读取后透传。
    """

    requestId: str | None = Field(None, description="本次请求 ID（API 触发时用于匹配结果）")
    requestedAt: str | None = Field(None, description="请求时间 ISO 8601")
    finishedAt: str | None = Field(None, description="完成时间 ISO 8601")
    source: str | None = Field(
        None, description="触发来源（manual-api/tag-mapping/loop-import/...）"
    )
    total: int = Field(0, description="刷新后订阅的测点总数")
    added: list[str] = Field(default_factory=list, description="本次新增订阅的测点")
    removed: list[str] = Field(
        default_factory=list,
        description="本次移出订阅的测点（Hub 不支持可靠退订，仅不再使用其推送）",
    )
    invocationId: str | None = Field(None, description="重发 SubscribeAsync 的 invocationId")
    leaderPid: int | None = Field(None, description="执行刷新的 Leader 进程 PID")
    error: str | None = Field(None, description="失败原因（非 Leader/WS 未连接等），成功时为 null")


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
    # ---- 落库自检（2026-09-25 生产排查新增）----
    # 写入布局（sys_config: history.storage_mode）与读取路由
    # （history_layout_manifest）是两套独立开关，不一致时表现为
    # "实时数据在采、库里却没有 / 趋势全空"，此前无任何提示。
    storageMode: str | None = Field(
        None, description="历史写入布局：legacy 只写宽表 / shadow 双写 / point 只写点表"
    )
    readLayout: str | None = Field(
        None, description="趋势与评估的读取路由（无 manifest 段恒 legacy）"
    )
    layoutConsistent: bool | None = Field(
        None, description="写入布局与读取路由是否自洽；false 表示趋势会读到空表"
    )
    layoutSeverity: str | None = Field(None, description="自检级别：ok / warning / error")
    layoutDiagnosis: str | None = Field(None, description="自检结论与修复方向（可直接展示）")
    pointTableWritten: bool | None = Field(
        None, description="当前写入布局是否写点表 st_point_data_v1"
    )


class StorageModeInfo(CamelModel):
    """历史写入布局 + 读写一致性自检（2026-09-25）。"""

    writeMode: str = Field(
        "point", description="sys_config 历史值（宽表退役后仅作展示/审计，恒按 point 执行）"
    )
    readLayout: str | None = Field("point", description="读取路由（恒 point：测点点表）")
    writesPointTable: bool = Field(True, description="唯一落库形态：测点点表 st_point_data_v1")
    consistent: bool | None = Field(True, description="写入与读取形态是否自洽（恒 True）")
    severity: str = Field("ok", description="自检级别：ok / warning / error")
    diagnosis: str = Field("", description="自检结论与修复方向")
    manifest: dict[str, Any] | None = Field(None, description="当前生效的最新布局段")
    changed: bool | None = Field(None, description="本次调用是否真的改变了布局")
    previousMode: str | None = Field(None, description="变更前的写入布局")


class StorageModeUpdate(CamelModel):
    """修改历史写入布局的请求体（仅 ADMIN）。"""

    mode: str = Field(..., description="目标布局：宽表已退役，仅接受 point")


__all__ = [
    "DataSourceConfigInfo",
    "DataSourceConfigUpdate",
    "DataSourceHealthInfo",
    "DataSourceTestResult",
    "StorageModeInfo",
    "StorageModeUpdate",
    "SubscriptionRefreshResult",
]
