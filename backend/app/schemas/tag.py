"""Tag registry schemas — 测点清单 (IDS §测点管理)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


class TagLoopInfo(CamelModel):
    """测点关联的回路信息（通过 loop_tag_mapping 间接关联）。"""

    loopId: str
    loopTagName: str
    loopDescription: str | None = None


class TagListItem(CamelModel):
    """测点列表项。"""

    id: str
    tagName: str
    tagDescription: str | None = None
    tagType: str
    currentValue: float | None = None
    quality: str | None = None
    lastSyncAt: str | None = None
    isLinked: bool | None = None
    rangeMin: float | None = None
    rangeMax: float | None = None
    unit: str | None = None
    measureType: str | None = None
    tdengineTagId: str | None = None
    loop: TagLoopInfo | None = None


class TagListData(CamelModel):
    """测点列表响应 data 块。"""

    items: list[TagListItem]
    total: int
    page: int
    pageSize: int


class TagDetail(CamelModel):
    """测点详情。"""

    id: str
    tagName: str
    tagDescription: str | None = None
    tagType: str
    currentValue: float | None = None
    quality: str | None = None
    lastSyncAt: str | None = None
    isLinked: bool | None = None
    rangeMin: float | None = None
    rangeMax: float | None = None
    unit: str | None = None
    measureType: str | None = None
    tdengineTagId: str | None = None
    loop: TagLoopInfo | None = None


class TagUpdate(CamelModel):
    """PUT /api/v1/tags/{id} 请求体。"""

    tagDescription: str | None = Field(None, max_length=255)
    rangeMin: float | None = None
    rangeMax: float | None = None
    unit: str | None = Field(None, max_length=20)
    measureType: str | None = None
    tdengineTagId: str | None = Field(None, max_length=100)


class TagDeleteResult(CamelModel):
    """测点删除响应。"""

    id: str
    deleted: bool = True
    deletedAt: str


class TagImportError(CamelModel):
    """测点导入单行错误。"""

    row: int
    tagName: str | None = None
    message: str


class TagImportResult(CamelModel):
    """POST /api/v1/tags/import 响应。"""

    total: int
    inserted: int
    updated: int
    failed: int
    errors: list[TagImportError] = []


__all__ = [
    "TagDeleteResult",
    "TagDetail",
    "TagImportError",
    "TagImportResult",
    "TagListData",
    "TagListItem",
    "TagLoopInfo",
    "TagUpdate",
]
