"""Loop ledger schemas (IDS v3.2 §2.2.7~2.2.11)."""

from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.base import CamelModel


class ScoreWeights(CamelModel):
    """回路评分权重（6 大 KPI 权重，总和须为 100）。

    对齐 GB/T 44693.2-2024：
    - 好值率仅作为显示指标，不参与综合评分加权
    - 新增快速率（fast_rate）参与加权
    - 有效自控率作为乘数因子（单独显示）
    - 向后兼容：读取时忽略已有的 good_value_rate 字段
    """

    auto_mode_rate: int = Field(10, ge=0, le=100)
    steady_rate: int = Field(30, ge=0, le=100)
    accuracy_rate: int = Field(15, ge=0, le=100)
    fast_rate: int = Field(10, ge=0, le=100)
    oscillation_rate: int = Field(20, ge=0, le=100)
    saturation_rate: int = Field(15, ge=0, le=100)

    @model_validator(mode="after")
    def check_sum(self) -> ScoreWeights:
        total = (
            self.auto_mode_rate
            + self.steady_rate
            + self.accuracy_rate
            + self.fast_rate
            + self.oscillation_rate
            + self.saturation_rate
        )
        if total != 100:
            raise ValueError(f"评分权重总和必须为 100，当前为 {total}")
        return self


class LoopCreate(CamelModel):
    """POST /api/v1/loops 请求体。"""

    tagName: str = Field(..., min_length=1, max_length=100, description="回路位号（唯一）")
    description: str | None = Field(None, max_length=255, description="回路描述")
    unitId: str | None = Field(None, description="所属工艺单元 ID")
    scoreWeights: ScoreWeights | None = Field(None, description="评分权重")
    isActive: bool = Field(True, description="是否启用")
    remark: str | None = Field(None, max_length=500, description="备注")
    loopType: str | None = Field(None, description="回路类型（业务类型 TEMPERATURE/PRESSURE/...）")
    # P2 #24/#25: 补全前端已声明但被忽略的字段
    controlType: str | None = Field(
        None,
        description="控制类型 STABLE/SLOW/FAST/LOGIC（用于评分权重分类）",
    )
    # v5.3 对齐 DDS v4.1：level → importance_level（CamelModel 自动映射为 importanceLevel）
    importance_level: int | None = Field(None, ge=1, le=3, description="回路重要等级 1/2/3")
    # v5.3 新增：是否参与评估（对齐 FDS §5.2.3 / DDS v4.1）
    include_in_evaluation: bool | None = Field(
        None, description="是否参与评估：true=参与综合评分与装置级聚合"
    )
    modeattrTagId: str | None = Field(
        None,
        description=(
            "APC 识别位号 ID（保留字段，未参与 KPI 计算；实际自控率判定使用 MODE 信号值）"
        ),
    )
    dataRetentionDays: int | None = Field(None, ge=1, description="数据保存周期（天）")
    # v6.1 新增：OP 输出限位（用于饱和率算法）
    # 设计依据：docs/设计文档/00-BASELINE/loop-range-and-output-limits-design-v1.0.md §6.3
    # 应用层校验：op_output_lower_limit < op_output_upper_limit
    # 且范围在 OP Tag range_min ~ range_max 之间
    opOutputLowerLimit: float | None = Field(
        None,
        description="OP 输出下限位（NULL 时取 OP Tag range_min，再 NULL 时取默认值 0.0）",
    )
    opOutputUpperLimit: float | None = Field(
        None,
        description="OP 输出上限位（NULL 时取 OP Tag range_max，再 NULL 时取默认值 100.0）",
    )


class LoopUpdate(CamelModel):
    """PUT /api/v1/loops/{id} 请求体。"""

    description: str | None = Field(None, max_length=255)
    scoreWeights: ScoreWeights | None = None
    isActive: bool | None = None
    remark: str | None = Field(None, max_length=500)
    loopType: str | None = Field(None, description="回路类型")
    # P2 #24/#25: 补全前端已声明但被忽略的字段
    controlType: str | None = Field(
        None,
        description="控制类型 STABLE/SLOW/FAST/LOGIC",
    )
    # v5.3 对齐 DDS v4.1：level → importance_level
    importance_level: int | None = Field(None, ge=1, le=3, description="回路重要等级 1/2/3")
    # v5.3 新增：是否参与评估
    include_in_evaluation: bool | None = Field(
        None, description="是否参与评估：true=参与综合评分与装置级聚合"
    )
    modeattrTagId: str | None = Field(
        None,
        description=(
            "APC 识别位号 ID（保留字段，未参与 KPI 计算；实际自控率判定使用 MODE 信号值）"
        ),
    )
    dataRetentionDays: int | None = Field(None, ge=1, description="数据保存周期（天）")
    # v6.1 新增：OP 输出限位（同 LoopCreate）
    opOutputLowerLimit: float | None = Field(
        None,
        description="OP 输出下限位（NULL 时取 OP Tag range_min，再 NULL 时取默认值 0.0）",
    )
    opOutputUpperLimit: float | None = Field(
        None,
        description="OP 输出上限位（NULL 时取 OP Tag range_max，再 NULL 时取默认值 100.0）",
    )


class TagMappingSlot(CamelModel):
    """回路详情中单个 Tag 槽位状态。"""

    tagId: str | None = None
    tagName: str | None = None
    required: bool
    associated: bool


class TagMappingStatus(CamelModel):
    """回路列表中 7 Tag 关联状态摘要。"""

    pv: bool = False
    sp: bool = False
    op: bool = False
    mode: bool = False
    pid_p: bool = False
    pid_i: bool = False
    pid_d: bool = False


class RangeInfo(CamelModel):
    """量程信息（从关联 Tag 引用，不冗余存储）。"""

    min: float | None = None
    max: float | None = None


class LoopListItem(CamelModel):
    """回路列表项。"""

    loopId: str
    tagName: str
    description: str | None = None
    unitId: str | None = None
    unitName: str | None = None
    controlMode: str | None = None
    isActive: bool = True
    status: str = "PARTIAL"
    loopType: str | None = None
    controlType: str | None = None
    # v5.3 对齐 DDS v4.1：level → importance_level
    importance_level: int | None = None
    include_in_evaluation: bool | None = None
    score: float | None = None
    lastScoreAt: str | None = None
    tagMappingStatus: TagMappingStatus
    # v6.1 新增：量程与限位（设计依据：loop-range-and-output-limits-design-v1.0.md §6.1）
    pvRange: RangeInfo | None = Field(None, description="PV 量程（从 PV Tag 引用）")
    pvUnit: str | None = Field(None, description="PV 工程单位")
    opRange: RangeInfo | None = Field(None, description="OP 量程（从 OP Tag 引用）")
    opUnit: str | None = Field(None, description="OP 工程单位")
    opOutputLowerLimit: float | None = Field(None, description="OP 输出下限位")
    opOutputUpperLimit: float | None = Field(None, description="OP 输出上限位")


class LoopListData(CamelModel):
    """回路列表响应 data 块。"""

    items: list[LoopListItem]
    total: int
    page: int
    pageSize: int


class LoopBasicInfo(CamelModel):
    """回路详情 basicInfo 块。"""

    loopId: str
    tagName: str
    description: str | None = None
    unitId: str | None = None
    unitName: str | None = None
    isActive: bool = True
    status: str = "PARTIAL"
    loopType: str | None = None
    controlType: str | None = None
    # v5.3 对齐 DDS v4.1：level → importance_level
    importance_level: int | None = None
    include_in_evaluation: bool | None = None
    # modeattrTagId 保留字段，未参与 KPI 计算链路（详见 models/loop.py 注释）
    modeattrTagId: str | None = None
    dataRetentionDays: int | None = None
    scoreWeights: dict | None = None
    remark: str | None = None
    # v6.1 新增：量程与限位（设计依据：loop-range-and-output-limits-design-v1.0.md §6.5）
    pvRange: RangeInfo | None = Field(None, description="PV 量程（从 PV Tag 引用）")
    pvUnit: str | None = Field(None, description="PV 工程单位")
    opRange: RangeInfo | None = Field(None, description="OP 量程（从 OP Tag 引用）")
    opUnit: str | None = Field(None, description="OP 工程单位")
    opOutputLowerLimit: float | None = Field(None, description="OP 输出下限位")
    opOutputUpperLimit: float | None = Field(None, description="OP 输出上限位")
    createdAt: str | None = None
    createdBy: str | None = None
    updatedAt: str | None = None
    updatedBy: str | None = None


class LoopTagMappingDetail(CamelModel):
    """回路详情 tagMapping 中单个角色。"""

    tagId: str | None = None
    tagName: str | None = None
    required: bool
    associated: bool


class LoopTagMappingBlock(CamelModel):
    """回路详情 tagMapping 块（7 个角色）。"""

    pv: LoopTagMappingDetail
    sp: LoopTagMappingDetail
    op: LoopTagMappingDetail
    mode: LoopTagMappingDetail
    pid_p: LoopTagMappingDetail
    pid_i: LoopTagMappingDetail
    pid_d: LoopTagMappingDetail


class LoopRuntimeParams(CamelModel):
    """回路详情 runtimeParams 块。"""

    controlMode: str | None = None
    pidP: float | None = None
    pidI: float | None = None
    pidD: float | None = None
    readAt: str | None = None


class LoopAasSyncStatus(CamelModel):
    """回路详情 aasSyncStatus 块。"""

    lastSyncAt: str | None = None
    associatedTagCount: int = 0


class LoopDetailData(CamelModel):
    """回路详情响应 data 块。"""

    basicInfo: LoopBasicInfo
    tagMapping: LoopTagMappingBlock
    runtimeParams: LoopRuntimeParams
    aasSyncStatus: LoopAasSyncStatus


class LoopUpdateResult(CamelModel):
    """回路更新响应。"""

    loopId: str
    description: str | None = None
    scoreWeights: dict | None = None
    isActive: bool | None = None
    remark: str | None = None
    loopType: str | None = None
    controlType: str | None = None
    # v5.3 对齐 DDS v4.1：level → importance_level
    importance_level: int | None = None
    include_in_evaluation: bool | None = None
    # modeattrTagId 保留字段，未参与 KPI 计算链路（详见 models/loop.py 注释）
    modeattrTagId: str | None = None
    dataRetentionDays: int | None = None
    updatedAt: str | None = None
    updatedBy: str | None = None


class LoopDeleteResult(CamelModel):
    """回路删除响应。"""

    loopId: str
    deleted: bool = True
    deletedAt: str


# ---------------------------------------------------------------------------
# Tag 关联管理 schemas (S2-LOOP-005)
# ---------------------------------------------------------------------------


class LoopTagMappingUpdate(CamelModel):
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


class LoopTagSlotInfo(CamelModel):
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


class LoopTagMappingResponse(CamelModel):
    """GET /api/v1/loops/{id}/tags 响应。"""

    loopId: str
    tagName: str
    status: str
    tags: list[LoopTagSlotInfo]


class LoopTagMappingUpdateResponse(CamelModel):
    """PUT /api/v1/loops/{id}/tags 响应。"""

    loopId: str
    status: str
    tags: list[dict]
    updatedAt: str | None = None
    updatedBy: str | None = None


# ---------------------------------------------------------------------------
# 批量导入导出 schemas
# ---------------------------------------------------------------------------


class LoopImportError(CamelModel):
    """回路导入单行错误。"""

    row: int
    tagName: str | None = None
    message: str


class LoopImportResult(CamelModel):
    """POST /api/v1/loops/import 响应。"""

    total: int
    inserted: int
    updated: int
    failed: int
    errors: list[LoopImportError] = []


__all__ = [
    "LoopAasSyncStatus",
    "LoopBasicInfo",
    "LoopCreate",
    "LoopDeleteResult",
    "LoopDetailData",
    "LoopImportError",
    "LoopImportResult",
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
    "RangeInfo",
    "ScoreWeights",
    "TagMappingSlot",
    "TagMappingStatus",
]
