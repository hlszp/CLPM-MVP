"""Loop ledger schemas (IDS v3.2 §2.2.7~2.2.11)."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ScoreWeights(BaseModel):
    """回路评分权重（6 大 KPI 权重，总和须为 100）。"""

    good_value_rate: int = Field(0, ge=0, le=100)
    auto_mode_rate: int = Field(0, ge=0, le=100)
    steady_rate: int = Field(0, ge=0, le=100)
    accuracy_rate: int = Field(0, ge=0, le=100)
    oscillation_rate: int = Field(0, ge=0, le=100)
    saturation_rate: int = Field(0, ge=0, le=100)

    @model_validator(mode="after")
    def check_sum(self) -> ScoreWeights:
        total = (
            self.good_value_rate
            + self.auto_mode_rate
            + self.steady_rate
            + self.accuracy_rate
            + self.oscillation_rate
            + self.saturation_rate
        )
        if total != 100:
            raise ValueError(f"评分权重总和必须为 100，当前为 {total}")
        return self


class LoopCreate(BaseModel):
    """POST /api/v1/loops 请求体。"""

    tagName: str = Field(..., min_length=1, max_length=100, description="回路位号（唯一）")
    description: str | None = Field(None, max_length=255, description="回路描述")
    unitId: str | None = Field(None, description="所属工艺单元 ID")
    scoreWeights: ScoreWeights | None = Field(None, description="评分权重")
    isActive: bool = Field(True, description="是否启用")
    remark: str | None = Field(None, max_length=500, description="备注")


class LoopUpdate(BaseModel):
    """PUT /api/v1/loops/{id} 请求体。"""

    description: str | None = Field(None, max_length=255)
    scoreWeights: ScoreWeights | None = None
    isActive: bool | None = None
    remark: str | None = Field(None, max_length=500)


class TagMappingSlot(BaseModel):
    """回路详情中单个 Tag 槽位状态。"""

    tagId: str | None = None
    tagName: str | None = None
    required: bool
    associated: bool


class TagMappingStatus(BaseModel):
    """回路列表中 7 Tag 关联状态摘要。"""

    pv: bool = False
    sp: bool = False
    op: bool = False
    mode: bool = False
    pid_p: bool = False
    pid_i: bool = False
    pid_d: bool = False


class LoopListItem(BaseModel):
    """回路列表项。"""

    loopId: str
    tagName: str
    description: str | None = None
    unitId: str | None = None
    unitName: str | None = None
    controlMode: str | None = None
    isActive: bool = True
    status: str = "PARTIAL"
    score: float | None = None
    lastScoreAt: str | None = None
    tagMappingStatus: TagMappingStatus


class LoopListData(BaseModel):
    """回路列表响应 data 块。"""

    items: list[LoopListItem]
    total: int
    page: int
    pageSize: int


class LoopBasicInfo(BaseModel):
    """回路详情 basicInfo 块。"""

    loopId: str
    tagName: str
    description: str | None = None
    unitId: str | None = None
    unitName: str | None = None
    isActive: bool = True
    status: str = "PARTIAL"
    scoreWeights: dict | None = None
    remark: str | None = None
    createdAt: str | None = None
    createdBy: str | None = None
    updatedAt: str | None = None
    updatedBy: str | None = None


class LoopTagMappingDetail(BaseModel):
    """回路详情 tagMapping 中单个角色。"""

    tagId: str | None = None
    tagName: str | None = None
    required: bool
    associated: bool


class LoopTagMappingBlock(BaseModel):
    """回路详情 tagMapping 块（7 个角色）。"""

    pv: LoopTagMappingDetail
    sp: LoopTagMappingDetail
    op: LoopTagMappingDetail
    mode: LoopTagMappingDetail
    pid_p: LoopTagMappingDetail
    pid_i: LoopTagMappingDetail
    pid_d: LoopTagMappingDetail


class LoopRuntimeParams(BaseModel):
    """回路详情 runtimeParams 块。"""

    controlMode: str | None = None
    pidP: float | None = None
    pidI: float | None = None
    pidD: float | None = None
    readAt: str | None = None


class LoopAasSyncStatus(BaseModel):
    """回路详情 aasSyncStatus 块。"""

    lastSyncAt: str | None = None
    associatedTagCount: int = 0


class LoopDetailData(BaseModel):
    """回路详情响应 data 块。"""

    basicInfo: LoopBasicInfo
    tagMapping: LoopTagMappingBlock
    runtimeParams: LoopRuntimeParams
    aasSyncStatus: LoopAasSyncStatus


class LoopUpdateResult(BaseModel):
    """回路更新响应。"""

    loopId: str
    description: str | None = None
    scoreWeights: dict | None = None
    isActive: bool | None = None
    remark: str | None = None
    updatedAt: str | None = None
    updatedBy: str | None = None


class LoopDeleteResult(BaseModel):
    """回路删除响应。"""

    loopId: str
    deleted: bool = True
    deletedAt: str


# ---------------------------------------------------------------------------
# Tag 关联管理 schemas (S2-LOOP-005)
# ---------------------------------------------------------------------------


class LoopTagMappingUpdate(BaseModel):
    """PUT /api/v1/loops/{id}/tags 请求体。

    7 个 Tag 角色与 tag_registry 中 tagId 的映射，未关联的角色传 null。
    """

    pv: str | None = None
    sp: str | None = None
    op: str | None = None
    mode: str | None = None
    pid_p: str | None = None
    pid_i: str | None = None
    pid_d: str | None = None


class LoopTagSlotInfo(BaseModel):
    """回路 Tag 关联详情中单个槽位。"""

    role: str
    tagId: str | None = None
    tagName: str | None = None
    description: str | None = None
    required: bool
    associated: bool
    currentValue: float | None = None
    quality: str | None = None
    lastSyncAt: str | None = None


class LoopTagMappingResponse(BaseModel):
    """GET /api/v1/loops/{id}/tags 响应。"""

    loopId: str
    tagName: str
    status: str
    tags: list[LoopTagSlotInfo]


class LoopTagMappingUpdateResponse(BaseModel):
    """PUT /api/v1/loops/{id}/tags 响应。"""

    loopId: str
    status: str
    tags: list[dict]
    updatedAt: str | None = None
    updatedBy: str | None = None


__all__ = [
    "LoopAasSyncStatus",
    "LoopBasicInfo",
    "LoopCreate",
    "LoopDeleteResult",
    "LoopDetailData",
    "LoopListItem",
    "LoopListData",
    "LoopRuntimeParams",
    "LoopTagMappingBlock",
    "LoopTagMappingDetail",
    "LoopTagMappingResponse",
    "LoopTagMappingUpdate",
    "LoopTagMappingUpdateResponse",
    "LoopTagSlotInfo",
    "LoopUpdate",
    "LoopUpdateResult",
    "ScoreWeights",
    "TagMappingSlot",
    "TagMappingStatus",
]
