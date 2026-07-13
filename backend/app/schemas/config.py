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
    # v5.3 P3-T8：formula 字段标记为废弃（算法已固化在代码中，不再支持自定义公式）
    formula: str | None = Field(
        None,
        deprecated=True,
        description="（已废弃）计算公式——算法已固化在 metric_calculator 代码中，不再支持自定义公式",
    )
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


# ---------------------------------------------------------------------------
# v5.3 P3-T8：权重模板 / 定级阈值 / 版本历史 schema
# 设计依据：FDS v5.1 §5.2.2 权重配置 / §5.2.4 定级阈值 / DDS v4.1
# ---------------------------------------------------------------------------


class WeightTemplateItem(CamelModel):
    """权重模板单项（单个控制类型的 6 指标权重）.

    Attributes:
        controlType: 控制类型 STABLE/SLOW/FAST/LOGIC
        autoModeRate: 自控率权重（0-100）
        steadyRate: 稳定率权重（0-100）
        accuracyRate: 准确率权重（0-100）
        fastRate: 快速率权重（0-100）
        oscillationRate: 振荡率权重（0-100）
        saturationRate: 饱和率权重（0-100）
    """

    controlType: ControlType
    autoModeRate: int = Field(0, ge=0, le=100)
    steadyRate: int = Field(0, ge=0, le=100)
    accuracyRate: int = Field(0, ge=0, le=100)
    fastRate: int = Field(0, ge=0, le=100)
    oscillationRate: int = Field(0, ge=0, le=100)
    saturationRate: int = Field(0, ge=0, le=100)


class WeightTemplateSchema(CamelModel):
    """权重模板（4 类控制类型的权重集合）.

    对齐 GB/T 44693.2-2024 国标默认权重：
    - STABLE: 稳定型（快速率权重低，稳定率权重高）
    - SLOW: 慢速型
    - FAST: 快速型（快速率权重高）
    - LOGIC: 逻辑型

    有效自控率（effective_auto_rate）为折扣因子 R，不参与权重和校验。
    """

    version: int = 1
    templates: list[WeightTemplateItem] = Field(default_factory=list)
    updatedAt: str | None = None
    updatedBy: str | None = None


class WeightTemplateSaveRequest(CamelModel):
    """权重模板保存请求（保存为新版本）."""

    templates: list[WeightTemplateItem] = Field(..., min_length=1, max_length=4)
    remark: str | None = Field(None, max_length=500)


class GradingThresholdItem(CamelModel):
    """定级阈值单项.

    Attributes:
        level: 等级编号 1-5
        name: 等级名称（EXCELLENT/GOOD/FAIR/WARNING/POOR）
        label: 中文显示名称（如"优秀"/"良好"/"合格"/"警告"/"不合格"），可配置
        minScore: 最低分（含）
        maxScore: 最高分（不含，最后一档为含）
        color: 显示颜色
    """

    level: int = Field(..., ge=1, le=5)
    name: str
    label: str | None = Field(None, max_length=20, description="中文显示名称，可配置")
    minScore: float = Field(..., ge=0, le=100)
    maxScore: float = Field(..., ge=0, le=100)
    color: str | None = None


class GradingThresholdSchema(CamelModel):
    """定级阈值配置（5 级）.

    对齐 FDS v5.1 §5.2.4：
    - 1 级 EXCELLENT (≥90) 绿色
    - 2 级 GOOD (80-90) 蓝色
    - 3 级 FAIR (60-80) 黄色
    - 4 级 WARNING (40-60) 橙色
    - 5 级 POOR (<40) 红色
    """

    thresholds: list[GradingThresholdItem] = Field(default_factory=list)
    updatedAt: str | None = None
    updatedBy: str | None = None


class GradingThresholdSaveRequest(CamelModel):
    """定级阈值更新请求."""

    thresholds: list[GradingThresholdItem] = Field(..., min_length=5, max_length=5)


class ConfidenceThresholdItem(CamelModel):
    """可信度阈值单项.

    Attributes:
        level: 等级编号 1-5（A/B/C/D/E）
        name: 等级名称（A/B/C/D/E）
        minRate: 最低有效数据率（含，0~1）
        description: 等级描述
        color: 显示颜色
    """

    level: int = Field(..., ge=1, le=5)
    name: str
    minRate: float = Field(..., ge=0, le=1)
    description: str | None = None
    color: str | None = None


class ConfidenceThresholdSchema(CamelModel):
    """可信度阈值配置（5 级 A/B/C/D/E）.

    对齐算法说明 §3.7.2：
    - A 级: valid_rate >= 0.95（数据充分）
    - B 级: 0.80 <= valid_rate < 0.95（数据较充分）
    - C 级: 0.60 <= valid_rate < 0.80（数据一般）
    - D 级: 0.20 <= valid_rate < 0.60（数据不足）
    - E 级: valid_rate < 0.20 → INCONCLUSIVE（可信度不足）
    """

    thresholds: list[ConfidenceThresholdItem] = Field(default_factory=list)
    updatedAt: str | None = None
    updatedBy: str | None = None


class ConfidenceThresholdSaveRequest(CamelModel):
    """可信度阈值更新请求."""

    thresholds: list[ConfidenceThresholdItem] = Field(..., min_length=5, max_length=5)


class VersionHistoryItem(CamelModel):
    """版本历史单项.

    Attributes:
        version: 版本号
        updatedAt: 更新时间
        updatedBy: 更新人
        remark: 备注
        isCurrent: 是否为当前版本
    """

    version: int
    updatedAt: str | None = None
    updatedBy: str | None = None
    remark: str | None = None
    isCurrent: bool = False


class VersionHistorySchema(CamelModel):
    """版本历史列表."""

    items: list[VersionHistoryItem] = Field(default_factory=list)


__all__ = [
    "ConfidenceThresholdItem",
    "ConfidenceThresholdSaveRequest",
    "ConfidenceThresholdSchema",
    "ControlType",
    "DiagnosisConfigBatchResponse",
    "DiagnosisConfigBatchUpdateRequest",
    "DiagnosisConfigItem",
    "DiagnosisConfigUpdateItem",
    "DiagnosisLabel",
    "GradingThresholdItem",
    "GradingThresholdSaveRequest",
    "GradingThresholdSchema",
    "MetricCategory",
    "MetricConfigBatchResponse",
    "MetricConfigBatchUpdateRequest",
    "MetricConfigItem",
    "MetricConfigUpdateItem",
    "MetricThresholdSchema",
    "VersionHistoryItem",
    "VersionHistorySchema",
    "WeightTemplateItem",
    "WeightTemplateSaveRequest",
    "WeightTemplateSchema",
]
