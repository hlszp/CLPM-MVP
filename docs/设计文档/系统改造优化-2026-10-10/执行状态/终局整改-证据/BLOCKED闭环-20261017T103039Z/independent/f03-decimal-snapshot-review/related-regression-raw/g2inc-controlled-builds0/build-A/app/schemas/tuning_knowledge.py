"""整定知识库 schemas（P3-01）.

知识库条目响应模型 + 列表分页 + 相似案例推荐。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.base import CamelModel


class TuningKnowledgeEntryItem(CamelModel):
    """知识库条目（列表项 / 详情）。"""

    id: str
    trackerId: str
    tuningRecordId: str | None = None
    loopId: str
    loopType: str | None = None
    controlType: str | None = None
    tagName: str
    diagnosisLabel: str | None = None
    severity: str | None = None

    # 整定元数据（可空，match_source=none 时全为 None）
    modelType: str | None = None
    algorithm: str | None = None
    identifyMethod: str | None = None
    confidenceLevel: str | None = None

    # PID 变化
    pidBefore: dict[str, Any] | None = None
    pidAfter: dict[str, Any] | None = None

    # 改善幅度
    kpiSummary: dict[str, Any] | None = None
    effectVerified: bool | None = None
    improvedCount: int | None = None
    deterioratedCount: int | None = None

    # 关联匹配方式
    matchSource: str = "none"

    implementedAt: str | None = None
    verifiedAt: str | None = None
    createdAt: str | None = None


class TuningKnowledgeListStats(CamelModel):
    """知识库全局统计（当前筛选条件下，非当前页）。

    IA 整改 C-2/T-3：改善/恶化案例数之前基于当前页 recordList 计算，
    翻页时 KPI 跳变；现由后端带相同筛选条件一次性聚合，前端直接取用。
    所有计数均基于筛选后的全局范围（WHERE 条件一致），不是当前页 items 的长度。
    """

    total: int = 0
    """总条目数（与 TuningKnowledgeListData.total 数值一致，冗余便于前端统一取用）。"""
    improvedCount: int = 0
    """改善案例数（effect_verified = True）。"""
    deterioratedCount: int = 0
    """恶化案例数（effect_verified = False）。"""
    unverifiedCount: int = 0
    """未验证案例数（effect_verified IS NULL）。"""
    avgImprovedMetrics: float | None = None
    """平均改善指标数（improved_count 字段平均值，未验证/缺失不计入分母，保留 2 位小数）。"""


class TuningKnowledgeListData(CamelModel):
    """知识库列表响应 data 块。"""

    items: list[TuningKnowledgeEntryItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 20
    stats: TuningKnowledgeListStats | None = None
    """当前筛选条件下的全局统计（新增字段，前端需 ?. 兜底兼容旧后端）。"""


class TuningKnowledgeSimilarData(CamelModel):
    """相似案例推荐响应 data 块。"""

    items: list[TuningKnowledgeEntryItem] = Field(default_factory=list)
    total: int = 0
