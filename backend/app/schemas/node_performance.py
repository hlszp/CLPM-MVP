"""Node-level performance evaluation schemas (GB/T 44693.2-2024 §6.4)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


class NodeSnapshotItem(CamelModel):
    """节点级快照项。"""

    plantNodeId: str
    plantNodeName: str | None = None
    tsStart: str | None = None
    tsEnd: str | None = None
    score: float | None = None
    goodValueRate: float | None = None
    autoModeRate: float | None = None
    effectiveAutoRate: float | None = None
    steadyRate: float | None = None
    accuracyRate: float | None = None
    fastResponseRate: float | None = None
    oscillationRate: float | None = None
    saturationRate: float | None = None
    autoLoopRatio: float | None = None
    realtimeAutoRate: float | None = None
    loopCount: int = 0
    status: str
    algorithmVersion: str | None = None


class NodeRankingItem(CamelModel):
    """节点间排名项。"""

    rank: int
    plantNodeId: str
    plantNodeName: str | None = None
    plantNodeType: str | None = None
    tsStart: str | None = None
    score: float | None = None
    goodValueRate: float | None = None
    autoModeRate: float | None = None
    effectiveAutoRate: float | None = None
    steadyRate: float | None = None
    accuracyRate: float | None = None
    fastResponseRate: float | None = None
    oscillationRate: float | None = None
    saturationRate: float | None = None
    autoLoopRatio: float | None = None
    realtimeAutoRate: float | None = None
    loopCount: int = 0
    status: str
    algorithmVersion: str | None = None


class NodeTrendSeries(CamelModel):
    """趋势单条序列。"""

    metricKey: str
    metricName: str
    values: list[float | None] = []


class NodeTrendData(CamelModel):
    """节点历史趋势。"""

    plantNodeId: str
    plantNodeName: str | None = None
    timestamps: list[str] = []
    series: list[NodeTrendSeries] = []


class NodeOverviewItem(CamelModel):
    """全厂总览单节点项。"""

    plantNodeId: str
    plantNodeName: str | None = None
    plantNodeType: str | None = None
    score: float | None = None
    autoLoopRatio: float | None = None
    realtimeAutoRate: float | None = None
    steadyRate: float | None = None
    effectiveAutoRate: float | None = None
    loopCount: int = 0
    status: str
    tsStart: str | None = None


class NodeOverviewData(CamelModel):
    """全厂总览。"""

    totalNodes: int = 0
    nodesWithSnapshot: int = 0
    nodes: list[NodeOverviewItem] = []
    statusDistribution: dict[str, int] = {}


class NodeCalculateRequest(CamelModel):
    """手动触发节点级计算请求。"""

    tsStart: str | None = Field(None, description="起始时间（ISO 8601），None 表示上一小时")
    tsEnd: str | None = Field(None, description="结束时间（ISO 8601），None 表示 tsStart + 1h")


class NodeCalculateResult(CamelModel):
    """手动触发计算结果。"""

    plantNodeId: str
    status: str
    snapshot: dict | None = None
    reason: str | None = None


__all__ = [
    "NodeCalculateRequest",
    "NodeCalculateResult",
    "NodeOverviewData",
    "NodeOverviewItem",
    "NodeRankingItem",
    "NodeSnapshotItem",
    "NodeTrendData",
    "NodeTrendSeries",
]
