"""DataPlanner 内部接口 Schema (IDS v3.2 §2.7.5).

定义 DataPlanner 管理接口的请求/响应模型，用于系统管理和调试。
所有响应仅返回摘要信息，不包含完整时序数据（数据量过大）。

设计依据：IDS §2.7.5, PRD §8.1-8.3
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


# ---------------------------------------------------------------------------
# 查询计划
# ---------------------------------------------------------------------------


class QueryTaskSchema(CamelModel):
    """单个 tagGroup 的查询任务（合并后）.

    Attributes:
        tagGroup: 目标 tagGroup（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）
        metrics: 依赖此 tagGroup 的指标列表
        tagRoles: 需要查询的 tag 角色并集（如 ["pv", "sp"]）
        intervalS: 采样间隔（秒）
        reusedFrom: 若复用 BASE，则为 BASE；否则 None
    """

    tagGroup: str
    metrics: list[str]
    tagRoles: list[str]
    intervalS: int
    reusedFrom: str | None = None


class PlanRequest(CamelModel):
    """查询计划请求.

    Attributes:
        loopId: 回路 ID
        metrics: 指标代码列表，如 ``["accuracy_rate", "stability_rate"]``
        start: 起始时间（ISO 8601）
        end: 结束时间（ISO 8601）
        controlType: 控制类型 FC/PC/TC/LC/CC
    """

    loopId: str = Field(..., description="回路 ID")
    metrics: list[str] = Field(
        ..., description="指标代码列表，如 ['accuracy_rate', 'stability_rate']"
    )
    start: str = Field(..., description="起始时间（ISO 8601）")
    end: str = Field(..., description="结束时间（ISO 8601）")
    controlType: str = Field(..., description="控制类型：FC/PC/TC/LC/CC")


class QueryPlanResponse(CamelModel):
    """查询计划响应.

    Attributes:
        loopId: 回路 ID
        queryTasks: 合并后的查询任务列表
        totalTagGroups: tagGroup 总数
    """

    loopId: str
    queryTasks: list[QueryTaskSchema]
    totalTagGroups: int


# ---------------------------------------------------------------------------
# Bundle 获取
# ---------------------------------------------------------------------------


class BundleRequest(CamelModel):
    """Bundle 请求.

    Attributes:
        loopId: 回路 ID
        metrics: 指标代码列表
        start: 起始时间（ISO 8601）
        end: 结束时间（ISO 8601）
        controlType: 控制类型 FC/PC/TC/LC/CC
    """

    loopId: str
    metrics: list[str]
    start: str
    end: str
    controlType: str


class BundleSummary(CamelModel):
    """Bundle 摘要（不包含完整时序数据）.

    Attributes:
        metricCode: 指标代码
        tagGroup: 数据来源 tagGroup
        samplingFreq: 实际采样频率，如 ``"1s"`` / ``"5s"``
        pointCount: 数据点数
        validRate: 有效数据率 0~1
        dataBlockId: DataBlock 唯一标识
    """

    metricCode: str
    tagGroup: str
    samplingFreq: str
    pointCount: int
    validRate: float
    dataBlockId: str


class BundleResponse(CamelModel):
    """Bundle 响应（简化版，不返回完整时序数据）.

    Attributes:
        loopId: 回路 ID
        bundles: Bundle 摘要列表
        validRate: 平均有效数据率 0~1
        confidenceLevel: 可信度等级 A/B/C/D/E
    """

    loopId: str
    bundles: list[BundleSummary]
    validRate: float
    confidenceLevel: str


# ---------------------------------------------------------------------------
# 缓存管理
# ---------------------------------------------------------------------------


class CacheStatsResponse(CamelModel):
    """缓存统计.

    Attributes:
        totalKeys: L1 DataBlock 缓存总键数
        hitRate: 缓存命中率 0~1
        memoryUsageMb: Redis 内存占用（MB）
        byTagGroup: 按 tagGroup 分组的键数统计
    """

    totalKeys: int
    hitRate: float
    memoryUsageMb: float
    byTagGroup: dict[str, int]


__all__ = [
    "BundleRequest",
    "BundleResponse",
    "BundleSummary",
    "CacheStatsResponse",
    "PlanRequest",
    "QueryPlanResponse",
    "QueryTaskSchema",
]
