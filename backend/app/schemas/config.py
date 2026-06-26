"""批量配置接口 Schema (IDS v3.2 §2.8/§2.9).

提供指标配置与诊断配置的批量读写能力。指标配置采用 v4.0 3+1+8 三段式
结构（3 核心 + 1 投用 + 8 辅助诊断），诊断配置采用 8 类标签 items 数组结构。

设计依据：IDS §2.8.1/§2.8.2/§2.9.1/§2.9.2
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import CamelModel

# 指标类别枚举
MetricCategory = Literal["CORE", "COMMISSIONING", "AUXILIARY_DIAGNOSTIC"]

# 控制类型枚举
ControlType = Literal["STABLE", "SLOW", "FAST", "LOGIC"]

# 诊断标签枚举（8 类）
DiagnosisLabel = Literal[
    "OSCILLATION",
    "VALVE_STICTION",
    "OVERAGGRESSIVE",
    "OVERCONSERVATIVE",
    "EXTERNAL_DISTURBANCE",
    "QUALITY_ABNORMAL",
    "OUTPUT_SATURATION",
    "MANUAL_REVIEW",
]


# ---------------------------------------------------------------------------
# §2.8 指标配置批量接口
# ---------------------------------------------------------------------------


class MetricThresholdSchema(CamelModel):
    """指标阈值配置（JSONB）.

    Attributes:
        min: 最小值
        max: 最大值
        alert: 告警阈值
    """

    min: float | None = None
    max: float | None = None
    alert: float | None = None


class MetricConfigItem(CamelModel):
    """指标配置项（响应/批量获取）.

    Attributes:
        metricId: 指标 ID
        metricKey: 指标代码
        metricName: 指标中文名
        category: 类别（CORE/COMMISSIONING/AUXILIARY_DIAGNOSTIC）
        isDiscountFactor: 是否为折扣因子（仅投用指标为 true）
        formula: 计算公式
        weight: 权重（核心指标为 0-100，其他为 null）
        threshold: 阈值对象
        controlType: 控制类型
        isEnabled: 是否启用
        description: 描述
        algorithmVersion: 算法版本号
        updatedAt: 更新时间
        updatedBy: 更新人
    """

    metricId: str
    metricKey: str
    metricName: str | None = None
    category: MetricCategory | None = None
    isDiscountFactor: bool | None = None
    formula: str | None = None
    weight: float | None = None
    threshold: dict[str, Any] | None = None
    controlType: ControlType | None = None
    isEnabled: bool = True
    description: str | None = None
    algorithmVersion: str | None = None
    updatedAt: str | None = None
    updatedBy: str | None = None


class MetricConfigUpdateItem(CamelModel):
    """指标配置更新项（批量更新请求）.

    Attributes:
        metricId: 指标 ID
        formula: 计算公式
        weight: 权重（仅核心指标生效）
        threshold: 阈值对象
        controlType: 控制类型
        isEnabled: 是否启用
        description: 描述
    """

    metricId: str
    formula: str | None = None
    weight: float | None = Field(None, ge=0, le=100)
    threshold: dict[str, Any] | None = None
    controlType: ControlType | None = None
    isEnabled: bool | None = None
    description: str | None = None


class MetricConfigBatchUpdateRequest(CamelModel):
    """批量更新指标配置请求（3+1+8 三段式）.

    Attributes:
        coreMetrics: 核心指标更新列表（3 项，参与权重校验）
        commissioningMetric: 投用指标更新（1 项，折扣因子）
        auxiliaryDiagnosticMetrics: 辅助诊断指标更新列表（8 项）
    """

    coreMetrics: list[MetricConfigUpdateItem] = Field(default_factory=list)
    commissioningMetric: MetricConfigUpdateItem | None = None
    auxiliaryDiagnosticMetrics: list[MetricConfigUpdateItem] = Field(default_factory=list)


class MetricConfigBatchResponse(CamelModel):
    """批量获取/更新指标配置响应（3+1+8 三段式）.

    Attributes:
        coreMetrics: 核心指标列表（3 项）
        commissioningMetric: 投用指标（1 项）
        auxiliaryDiagnosticMetrics: 辅助诊断指标列表（8 项）
        coreTotalWeight: 核心指标权重总和
        coreWeightValid: 核心指标权重是否合法（=100）
        structureVersion: 结构版本（3+1+8）
        updatedCount: 更新条数（仅批量更新响应返回）
    """

    coreMetrics: list[MetricConfigItem] = Field(default_factory=list)
    commissioningMetric: MetricConfigItem | None = None
    auxiliaryDiagnosticMetrics: list[MetricConfigItem] = Field(default_factory=list)
    coreTotalWeight: float = 0.0
    coreWeightValid: bool = True
    structureVersion: str = "3+1+8"
    updatedCount: int | None = None


# ---------------------------------------------------------------------------
# §2.9 诊断配置批量接口
# ---------------------------------------------------------------------------


class DiagnosisConfigItem(CamelModel):
    """诊断配置项（响应/批量获取）.

    Attributes:
        diagId: 诊断配置 ID
        diagKey: 诊断代码
        diagName: 诊断中文名
        label: 诊断标签枚举（8 类）
        algorithmType: 算法类型
        calcMethod: 计算方法
        params: 算法参数
        threshold: 阈值对象
        isEnabled: 是否启用
        algorithmVersion: 算法版本号
        updatedAt: 更新时间
        updatedBy: 更新人
    """

    diagId: str
    diagKey: str | None = None
    diagName: str | None = None
    label: DiagnosisLabel | None = None
    algorithmType: str | None = None
    calcMethod: str | None = None
    params: dict[str, Any] | None = None
    threshold: dict[str, Any] | None = None
    isEnabled: bool = True
    algorithmVersion: str | None = None
    updatedAt: str | None = None
    updatedBy: str | None = None


class DiagnosisConfigUpdateItem(CamelModel):
    """诊断配置更新项（批量更新请求）.

    Attributes:
        diagId: 诊断配置 ID
        label: 诊断标签枚举（用于校验）
        algorithmType: 算法类型
        calcMethod: 计算方法
        params: 算法参数
        threshold: 阈值对象
        isEnabled: 是否启用
    """

    diagId: str
    label: DiagnosisLabel | None = None
    algorithmType: str | None = None
    calcMethod: str | None = None
    params: dict[str, Any] | None = None
    threshold: dict[str, Any] | None = None
    isEnabled: bool | None = None


class DiagnosisConfigBatchUpdateRequest(CamelModel):
    """批量更新诊断配置请求.

    Attributes:
        items: 诊断配置更新列表
    """

    items: list[DiagnosisConfigUpdateItem] = Field(default_factory=list)


class DiagnosisConfigBatchResponse(CamelModel):
    """批量获取/更新诊断配置响应.

    Attributes:
        items: 诊断配置列表
        updatedCount: 更新条数（仅批量更新响应返回）
    """

    items: list[DiagnosisConfigItem] = Field(default_factory=list)
    updatedCount: int | None = None


__all__ = [
    "ControlType",
    "DiagnosisConfigBatchResponse",
    "DiagnosisConfigBatchUpdateRequest",
    "DiagnosisConfigItem",
    "DiagnosisConfigUpdateItem",
    "DiagnosisLabel",
    "MetricCategory",
    "MetricConfigBatchResponse",
    "MetricConfigBatchUpdateRequest",
    "MetricConfigItem",
    "MetricConfigUpdateItem",
    "MetricThresholdSchema",
]
