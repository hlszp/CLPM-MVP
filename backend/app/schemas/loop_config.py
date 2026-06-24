"""Loop configuration schemas (重构方案 v1.2).

对齐 GB/T 44693.2-2024 的 3 项配置 CRUD：
- 投用定义（LoopModeMapping）
- 回路类型权重（LoopTypeWeight，附表1）
- 回路级别权重（LoopLevelWeight，附表2）
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 投用定义（LoopModeMapping）
# ---------------------------------------------------------------------------


class ModeMappingItem(CamelModel):
    """投用定义项（响应）。"""

    id: str
    loopId: str
    modeValue: int
    modeLabel: str
    isAuto: bool = False
    isEffective: bool = False
    createdAt: str | None = None


class ModeMappingInput(CamelModel):
    """投用定义单条输入（请求体内元素）。"""

    modeValue: int = Field(..., ge=0, description="DCS 返回的 MODE 值（非负整数）")
    modeLabel: str = Field(
        ..., pattern="^(AUTO|CAS|REMOTE|APC|MANUAL)$", description="控制模式"
    )
    isAuto: bool = Field(False, description="是否算自动控制")
    isEffective: bool = Field(False, description="是否算有效自动")


class ModeMappingReplaceRequest(CamelModel):
    """PUT /loops/{loopId}/mode-mapping 请求体。"""

    mappings: list[ModeMappingInput] = Field(
        ..., description="全量替换的投用定义列表"
    )


# ---------------------------------------------------------------------------
# 回路类型权重（LoopTypeWeight）
# ---------------------------------------------------------------------------


class LoopTypeWeightItem(CamelModel):
    """回路类型权重项（响应）。"""

    id: str
    loopType: str
    typeName: str
    weightA: float
    weightF: float
    weightS: float
    description: str | None = None
    updatedBy: str | None = None
    updatedAt: str | None = None


class LoopTypeWeightUpdate(CamelModel):
    """PUT /config/loop-type-weights/{loopType} 请求体。"""

    typeName: str | None = Field(None, max_length=50)
    weightA: Decimal | None = Field(None, ge=0, le=1)
    weightF: Decimal | None = Field(None, ge=0, le=1)
    weightS: Decimal | None = Field(None, ge=0, le=1)
    description: str | None = None


# ---------------------------------------------------------------------------
# 回路级别权重（LoopLevelWeight）
# ---------------------------------------------------------------------------


class LoopLevelWeightItem(CamelModel):
    """回路级别权重项（响应）。"""

    id: str
    level: int
    levelName: str
    weight: float
    description: str | None = None
    updatedBy: str | None = None
    updatedAt: str | None = None


class LoopLevelWeightUpdate(CamelModel):
    """PUT /config/loop-level-weights/{level} 请求体。"""

    levelName: str | None = Field(None, max_length=50)
    weight: Decimal | None = Field(None, gt=0)
    description: str | None = None


__all__ = [
    "LoopLevelWeightItem",
    "LoopLevelWeightUpdate",
    "LoopTypeWeightItem",
    "LoopTypeWeightUpdate",
    "ModeMappingInput",
    "ModeMappingItem",
    "ModeMappingReplaceRequest",
]
