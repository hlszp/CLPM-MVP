"""SysDictItem schemas — 通用字典项."""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


class DictItemCreate(CamelModel):
    """POST /api/v1/dicts/items 请求体."""

    dictType: str = Field(..., max_length=50, description="字典类型编码（如 MEASURE_TYPE）")
    itemCode: str = Field(..., min_length=1, max_length=50, description="项编码（落库值）")
    itemLabel: str = Field(..., min_length=1, max_length=100, description="项显示名")
    sortOrder: int = Field(0, ge=0, le=999_999)
    isEnabled: bool = True


class DictItemUpdate(CamelModel):
    """PUT /api/v1/dicts/items/{id} 请求体（code 与 dictType 不可改）."""

    itemLabel: str | None = Field(None, min_length=1, max_length=100)
    sortOrder: int | None = Field(None, ge=0, le=999_999)
    isEnabled: bool | None = None


class DictItemInfo(CamelModel):
    """字典项响应."""

    id: str
    dictType: str
    itemCode: str
    itemLabel: str
    sortOrder: int = 0
    isEnabled: bool = True
    updatedBy: str | None = None
    updatedAt: str | None = None
