"""Plant node schemas (IDS v3.2 §2.2.1~2.2.4)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel


class PlantNodeBase(CamelModel):
    """Plant node base fields."""

    name: str = Field(..., min_length=1, max_length=100, description="节点名称")
    type: str = Field(..., description="节点类型：FACTORY/AREA/UNIT")
    parentId: str | None = Field(None, description="父节点 ID（顶层节点为 null）")


class PlantNodeCreate(PlantNodeBase):
    """POST /api/v1/plant-nodes request body."""

    type: str = Field(..., description="节点类型：FACTORY/AREA/UNIT")


class PlantNodeUpdate(CamelModel):
    """PUT /api/v1/plant-nodes/{id} request body."""

    name: str = Field(..., min_length=1, max_length=100, description="节点名称")
    isKpiEnabled: bool | None = Field(None, description="是否纳入性能评估")


class PlantNodeInfo(CamelModel):
    """Plant node info (flat)."""

    id: str
    name: str
    type: str
    parentId: str | None = None
    isKpiEnabled: bool | None = None


class PlantNodeTree(PlantNodeInfo):
    """Plant node with children (recursive tree)."""

    children: list[PlantNodeTree] = Field(default_factory=list)


PlantNodeTree.model_rebuild()


# ---------------------------------------------------------------------------
# 批量导入导出 schemas
# ---------------------------------------------------------------------------


class PlantNodeImportError(CamelModel):
    """工厂节点导入单行错误。"""

    row: int
    name: str | None = None
    message: str


class PlantNodeImportResult(CamelModel):
    """POST /api/v1/plant-nodes/import 响应。"""

    total: int
    inserted: int
    updated: int
    failed: int
    errors: list[PlantNodeImportError] = []


__all__ = [
    "PlantNodeBase",
    "PlantNodeCreate",
    "PlantNodeImportError",
    "PlantNodeImportResult",
    "PlantNodeInfo",
    "PlantNodeTree",
    "PlantNodeUpdate",
]
