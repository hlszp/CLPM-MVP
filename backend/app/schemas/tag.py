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


class TagBatchDeleteRequest(CamelModel):
    """批量删除测点请求体。"""

    tagIds: list[str] = Field(..., min_length=1, max_length=500)


class TagBatchDeleteFailure(CamelModel):
    """批量删除中单个失败项。"""

    tagId: str
    tagName: str | None = None
    reason: str


class TagBatchDeleteResult(CamelModel):
    """批量删除测点响应。"""

    deleted: int
    failed: int
    failures: list[TagBatchDeleteFailure] = []


class TagImportError(CamelModel):
    """测点导入单行错误。"""

    row: int
    tagName: str | None = None
    message: str


class TagImportResult(CamelModel):
    """POST /api/v1/tags/import 响应."""

    total: int
    inserted: int
    updated: int
    failed: int
    errors: list[TagImportError] = []


# ---------------------------------------------------------------------------
# v4.0 波形数据 schema（IDS §2.4.5 — 扩展 valid_mask + tagGroup 筛选）
# 设计依据：IDS §2.4.5, 算法说明 §3.4（KEEP_ALL_WITH_VALIDITY）/§3.7.1（数据血缘）
# ---------------------------------------------------------------------------


class WaveformTimeRange(CamelModel):
    """波形时间范围."""

    startTime: str
    endTime: str


class WaveformPoint(CamelModel):
    """波形数据点（含 valid_mask 标记）.

    设计依据：IDS §2.4.5, 算法说明 §3.4（KEEP_ALL_WITH_VALIDITY 策略）

    Attributes:
        timestamp: ISO 8601 时间戳
        pv: 过程值
        sp: 设定值
        op: 操作输出
        mode: 控制模式（0=手动, 1=自动）
        pvQuality: PV 质量码（1=Good, 0=Bad）
        valid: valid_mask 标记（True=有效, False=无效/异常）
        outlierReason: 异常原因码（如 FROZEN/JUMP/SPIKE/OUT_OF_RANGE 等，多个以逗号分隔）
    """

    timestamp: str
    pv: float | None = None
    sp: float | None = None
    op: float | None = None
    mode: int | None = None
    pvQuality: int | None = None
    valid: bool = True
    outlierReason: str | None = None


class WaveformResponse(CamelModel):
    """波形响应（含 valid_mask + 数据血缘）.

    设计依据：IDS §2.4.5, 算法说明 §3.7.1

    Attributes:
        loopId: 回路 ID
        tagName: 回路位号
        timeRange: 时间范围
        points: 波形数据点列表（含 valid_mask）
        samplingFreq: 采样频率（如 ``1s`` / ``5s``）
        qualityPolicy: 质量策略（``KEEP_ALL_WITH_VALIDITY`` / ``KEEP_ALL``）
        validRate: 有效数据率（0~1）
        downsampled: 是否经过 LTTB 降采样
        pointCount: 返回的数据点数
    """

    loopId: str
    tagName: str | None = None
    timeRange: WaveformTimeRange
    points: list[WaveformPoint] = Field(default_factory=list)
    samplingFreq: str = "1s"
    qualityPolicy: str = "KEEP_ALL_WITH_VALIDITY"
    validRate: float = 1.0
    downsampled: bool = False
    pointCount: int = 0


class BatchWaveformRequest(CamelModel):
    """POST /api/v1/timeseries/batch/waveform 请求体.

    批量查询多个回路的波形数据，使用 ``asyncio.gather`` 并行获取。

    Attributes:
        loopIds: 回路 ID 列表（1~50 个）
        startTime: 开始时间（ISO 8601）
        endTime: 结束时间（ISO 8601）
        tagGroup: 按标签组筛选（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF），默认 BASE
        includeValidMask: 是否返回 valid_mask（默认 True）
        maxPoints: 每个回路最大数据点数（100~50000，默认 5000）
    """

    loopIds: list[str] = Field(..., min_length=1, max_length=50)
    startTime: str
    endTime: str
    tagGroup: str | None = Field(
        None, description="按标签组筛选: BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF"
    )
    includeValidMask: bool = True
    maxPoints: int = Field(5000, ge=100, le=50000)


class BatchWaveformFailure(CamelModel):
    """批量查询中失败的回路信息."""

    loopId: str
    error: str


class BatchWaveformResponse(CamelModel):
    """批量波形查询响应.

    Attributes:
        items: 成功获取的波形数据列表
        failed: 失败的回路列表（含错误信息）
        total: 成功回路数
    """

    items: list[WaveformResponse] = Field(default_factory=list)
    failed: list[BatchWaveformFailure] = Field(default_factory=list)
    total: int = 0


__all__ = [
    "BatchWaveformFailure",
    "BatchWaveformRequest",
    "BatchWaveformResponse",
    "TagDeleteResult",
    "TagDetail",
    "TagImportError",
    "TagImportResult",
    "TagListData",
    "TagListItem",
    "TagLoopInfo",
    "TagUpdate",
    "WaveformPoint",
    "WaveformResponse",
    "WaveformTimeRange",
]
