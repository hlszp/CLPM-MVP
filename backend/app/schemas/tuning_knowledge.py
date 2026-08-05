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


class TuningKnowledgeListData(CamelModel):
    """知识库列表响应 data 块。"""

    items: list[TuningKnowledgeEntryItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 20


class TuningKnowledgeSimilarData(CamelModel):
    """相似案例推荐响应 data 块。"""

    items: list[TuningKnowledgeEntryItem] = Field(default_factory=list)
    total: int = 0
