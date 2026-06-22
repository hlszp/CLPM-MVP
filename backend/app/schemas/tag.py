"""Tag registry schemas (IDS v3.2 §2.2.5)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TagRegistryInfo(BaseModel):
    """Tag registry info."""

    tagId: str
    tagName: str
    description: str | None = None
    tagType: str | None = None
    currentValue: float | None = None
    quality: str | None = None
    lastSyncAt: str | None = None
    isLinked: bool = False


class TagSyncStats(BaseModel):
    """AAS 同步统计结果。"""

    total: int = Field(0, description="AAS 读取的 Tag 总数")
    inserted: int = Field(0, description="新增 Tag 数")
    updated: int = Field(0, description="更新 Tag 数")
    unchanged: int = Field(0, description="未变化 Tag 数")
    duration_ms: int = Field(0, description="同步耗时（毫秒）")


__all__ = ["TagRegistryInfo", "TagSyncStats"]
