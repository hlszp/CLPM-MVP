"""Report configuration schemas (S5-SYS-003)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.base import CamelModel


class ReportConfigCreateRequest(CamelModel):
    """POST /api/v1/reports/configs request body."""

    name: str = Field(..., min_length=1, max_length=100, description="配置名称")
    reportPeriod: str = Field(..., description="报表周期：SHIFT/DAILY/WEEKLY/MONTHLY")
    recipients: list[str] = Field(..., min_length=1, description="接收人用户 ID 列表")
    contentTemplate: dict[str, Any] | None = Field(None, description="内容模板")
    isEnabled: bool = Field(True, description="是否启用")

    @field_validator("reportPeriod")
    @classmethod
    def validate_period(cls, v: str) -> str:
        allowed = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
        if v not in allowed:
            raise ValueError(f"报表周期必须是 {allowed} 之一")
        return v


class ReportConfigUpdateRequest(CamelModel):
    """PUT /api/v1/reports/configs/{id} request body (partial update)."""

    name: str | None = Field(None, min_length=1, max_length=100)
    reportPeriod: str | None = None
    recipients: list[str] | None = None
    contentTemplate: dict[str, Any] | None = None
    isEnabled: bool | None = None

    @field_validator("reportPeriod")
    @classmethod
    def validate_period(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
        if v not in allowed:
            raise ValueError(f"报表周期必须是 {allowed} 之一")
        return v


class ReportConfigItem(CamelModel):
    """Report config item in list / detail responses."""

    id: str
    name: str
    reportPeriod: str
    recipients: list[str]
    contentTemplate: dict[str, Any] | None = None
    isEnabled: bool = True
    createdBy: str | None = None
    updatedBy: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class ReportGenerateRequest(CamelModel):
    """POST /api/v1/reports/generate request body."""

    configId: str | None = Field(None, description="报表配置 ID（可选）")
    reportPeriod: str | None = Field(None, description="报表周期（可选，默认 DAILY）")

    @field_validator("reportPeriod")
    @classmethod
    def validate_period(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
        if v not in allowed:
            raise ValueError(f"报表周期必须是 {allowed} 之一")
        return v


class ReportGenerateData(CamelModel):
    """Report generation trigger response data."""

    taskId: str
    taskType: str = "REPORT_GENERATE"
    status: str = "PROCESSING"
    checkUrl: str | None = None
    estimatedSeconds: int = 30


__all__ = [
    "ReportConfigCreateRequest",
    "ReportConfigItem",
    "ReportConfigUpdateRequest",
    "ReportGenerateData",
    "ReportGenerateRequest",
]
