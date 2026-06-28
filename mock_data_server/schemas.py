"""模拟远端数据服务 — 数据模型.

遵循 HisDATA_API.md / RealDATA_API.md 规范。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HistoryDataRequest(BaseModel):
    """POST /api/services/v1/HistoryData/Get 请求体."""

    tagCodes: list[str] = Field(..., description="标签编码数组")
    startTime: str = Field(..., description="查询开始时间")
    endTime: str = Field(..., description="查询结束时间")
    sampleInterval: int = Field(1, description="采样间隔（秒），默认 1")


class TagHistoryValueDto(BaseModel):
    """单个标签的历史值序列."""

    tagCode: str = Field(..., description="标签编码")
    values: list[str] = Field(default_factory=list, description="字符串值列表")
    qualities: list[int] = Field(default_factory=list, description="质量码列表: 0=未知, 1=Good, 2=Bad, 3=离线")


class HistoryDataDto(BaseModel):
    """历史数据响应体."""

    timestamps: list[str] = Field(default_factory=list, description="采样时间点列表")
    series: list[TagHistoryValueDto] = Field(default_factory=list, description="标签历史值序列")


class ApiResponse(BaseModel):
    """统一响应包装."""

    code: int = 200
    message: str = "Success"
    data: HistoryDataDto | None = None


class RealValueDto(BaseModel):
    """实时值数据传输对象."""

    id: int = Field(..., description="记录 ID")
    tagCode: str = Field(..., description="标签编码")
    value: str = Field(..., description="字符串值")
    quality: int = Field(0, description="质量码: 0=Good, 1=Bad")
    collectTime: str = Field(..., description="采集时间")


class SignalRResponse(BaseModel):
    """SignalR 方法响应."""

    code: int = 200
    message: str = "success"
    data: list[RealValueDto] | None = None
