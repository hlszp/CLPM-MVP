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


class DiagnosisConfigCreateItem(CamelModel):
    """诊断配置创建项（2026-08-19 诊断配置页 CRUD 扩展）.

    Attributes:
        diagKey: 诊断代码（唯一，如 OSCILLATION）
        diagName: 诊断中文名
        algorithmType: 算法类型
        calcMethod: 计算方法
        params: 算法参数
        threshold: 阈值对象
        isEnabled: 是否启用
    """

    diagKey: str = Field(min_length=1, max_length=50)
    diagName: str = Field(min_length=1, max_length=100)
    algorithmType: str = Field(min_length=1, max_length=50)
    calcMethod: str | None = Field(None, max_length=50)
    params: dict[str, Any] | None = None
    threshold: dict[str, Any] | None = None
    isEnabled: bool = True


class DiagnosisConfigCreateRequest(CamelModel):
    """诊断配置创建请求（单条创建）."""

    item: DiagnosisConfigCreateItem


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


# ---------------------------------------------------------------------------
# 8 类异常值检测参数配置（sys_config outlier_params.current）
# 设计依据：算法说明 §3.4.3-3.4.4, PRD §5.5.2-5.5.3
# ---------------------------------------------------------------------------

# 回路物理控制类型枚举（流量/压力/温度/液位/成分，对齐 contracts.ControlType 值）
OutlierControlType = Literal["FC", "PC", "TC", "LC", "CC"]

# 8 类异常值检测开关键（对齐 thresholds.DETECTOR_KEYS）
DetectorKey = Literal[
    "nan",
    "out_of_range",
    "frozen",
    "jump",
    "spike",
    "ts_anomaly",
    "qc_bad",
    "hf_noise",
]


class OutlierThresholdParams(CamelModel):
    """单控制类型的异常值检测参数（全部可选，None 表示未覆盖→回落算法默认）.

    Attributes:
        base_sampling_freq: 基础采样率（秒）
        frozen_window_points: 冻结检测窗口点数（≥2）
        frozen_std_pct: 冻结标准差阈值（占量程百分比，0~1）
        jump_threshold_pct: 跳变阈值（占量程百分比，0~1）
        spike_threshold_pct: 尖峰阈值（占量程百分比，0~1）
        noise_cutoff_hz: 噪声截止频率（Hz，>0）
        min_consecutive_points: 连续有效最短段点数（≥2）
    """

    base_sampling_freq: int | None = Field(None, ge=1, le=3600)
    frozen_window_points: int | None = Field(None, ge=2, le=10000)
    frozen_std_pct: float | None = Field(None, ge=0, le=1)
    jump_threshold_pct: float | None = Field(None, ge=0, le=1)
    spike_threshold_pct: float | None = Field(None, ge=0, le=1)
    noise_cutoff_hz: float | None = Field(None, gt=0, le=1000)
    min_consecutive_points: int | None = Field(None, ge=2, le=100000)


class OutlierThresholdViewItem(CamelModel):
    """单控制类型阈值合并视图（GET 响应）.

    Attributes:
        control_type: 控制类型（FC/PC/TC/LC/CC）
        params: 合并后的生效参数（默认值叠加覆盖项，全部非空）
        overridden: 各参数是否被覆盖（camelCase 参数名 → true=sys_config 覆盖，false=算法默认）
    """

    control_type: OutlierControlType
    params: OutlierThresholdParams
    overridden: dict[str, bool] = Field(default_factory=dict)


class OutlierParamsSchema(CamelModel):
    """8 类异常值检测参数配置合并视图（GET 响应）.

    Attributes:
        thresholds: 5 个控制类型的合并视图
        switches: 8 类检测开关生效值（key=检测键，value=是否启用）
        updated_at: 最近更新时间（ISO 8601）
        updated_by: 最近更新人
    """

    thresholds: list[OutlierThresholdViewItem] = Field(default_factory=list)
    switches: dict[str, bool] = Field(default_factory=dict)
    updated_at: str | None = None
    updated_by: str | None = None


class OutlierParamsSaveRequest(CamelModel):
    """8 类异常值检测参数配置保存请求（PUT，部分覆盖）.

    Attributes:
        thresholds: 按控制类型的参数覆盖（全部可选，未覆盖的参数回落默认）
        switches: 检测开关覆盖（未列出的检测键保持默认 true）
    """

    thresholds: dict[OutlierControlType, OutlierThresholdParams] = Field(default_factory=dict)
    switches: dict[DetectorKey, bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 算法参数配置（P0-B 配置化基础设施）
# ---------------------------------------------------------------------------


class AlgorithmParamsControlItem(CamelModel):
    """单个 metric_code × control_type 的算法参数项.

    Attributes:
        controlType: 控制类型（STABLE/SLOW/FAST/LOGIC）
        params: 当前生效参数（默认值 + 覆盖合并后）
        defaults: 算法默认参数（无覆盖时的回退值）
        overridden: 是否被覆盖（params != defaults）
    """

    controlType: ControlType
    params: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    overridden: bool = False


class AlgorithmParamsMetricGroup(CamelModel):
    """单个指标的所有控制类型参数组.

    Attributes:
        metricCode: 指标代码
        metricName: 指标中文名
        items: 4 个控制类型的参数项列表
        paramMeta: 参数元数据注册表（整改 F6：min/max/unit/description/category 单源下发）
    """

    metricCode: str
    metricName: str
    items: list[AlgorithmParamsControlItem] = Field(default_factory=list)
    paramMeta: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AlgorithmParamsSchema(CamelModel):
    """算法参数配置合并视图（全部指标 × 全部控制类型）.

    Attributes:
        metrics: 按指标分组的参数列表
        updatedAt: 最近更新时间
        updatedBy: 最近更新人
    """

    metrics: list[AlgorithmParamsMetricGroup] = Field(default_factory=list)
    updatedAt: str | None = None
    updatedBy: str | None = None


class AlgorithmParamsSaveItem(CamelModel):
    """单个控制类型的参数保存项.

    Attributes:
        controlType: 控制类型
        params: 要覆盖的参数键值对（部分覆盖，未列出的参数保持原值）
    """

    controlType: ControlType
    params: dict[str, Any] = Field(default_factory=dict)


class AlgorithmParamsSaveRequest(CamelModel):
    """算法参数保存请求（按指标 code，含 4 控制类型的部分覆盖）.

    Attributes:
        items: 4 控制类型的参数覆盖项（可只传部分控制类型）
        resetControlTypes: 需重置为算法默认的控制类型（整改 F6 重置默认；与 items 合并生效）
    """

    items: list[AlgorithmParamsSaveItem] = Field(default_factory=list)
    resetControlTypes: list[ControlType] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# 诊断触发条件配置（整改计划 C6 — 触发条件可配）
# ---------------------------------------------------------------------------


class DiagnosisTriggerSchema(CamelModel):
    """诊断触发条件配置（sys_config 存储，运行时缓存）.

    存储 key: ``diagnosis_trigger.current``，结构见
    ``app.services.diagnosis_trigger_config``。

    保存后立即刷新进程内缓存，热路径（diagnosis_engine）通过
    ``get_trigger_config()`` 读取，不查库。
    """

    score_threshold: float = Field(
        default=60.0, ge=0, le=100, description="评分阈值：跌破此值触发诊断"
    )
    concurrency: int = Field(default=5, ge=1, le=50, description="并发 worker 数")
    min_data_points: int = Field(default=32, ge=8, description="数据最少点数（低于此值跳过诊断）")
    checkup_enabled: bool = Field(default=True, description="体检轨是否启用（每 8h 全回路体检）")
    updated_at: str | None = None
    updated_by: str | None = None


class DiagnosisTriggerSaveRequest(CamelModel):
    """诊断触发条件配置保存请求."""

    score_threshold: float = Field(ge=0, le=100)
    concurrency: int = Field(ge=1, le=50)
    min_data_points: int = Field(ge=8)
    checkup_enabled: bool


# ---------------------------------------------------------------------------
# P3-04: LLM 配置（自然语言诊断解读）
# ---------------------------------------------------------------------------


class LlmConfigSchema(CamelModel):
    """LLM 配置响应（GET /configs/llm）。

    API Key 脱敏返回（仅尾 4 位），明文不出口。
    """

    enabled: bool = Field(False, description="是否启用 LLM 解读")
    endpoint: str | None = Field(None, description="BaseURL（API 根地址，不含 /v1）")
    apiKey: str | None = Field(None, description="API Key（脱敏，形如 sk-***xxxx）")
    apiKeyConfigured: bool = Field(
        False, description="API Key 是否已配置（前端据此区分空值与未配置）"
    )
    model: str | None = Field(None, description="模型名")
    timeout: int = Field(30, description="超时秒数")
    maxTokens: int = Field(4096, description="最大输出 token 数（推理模型建议 ≥4096）")
    updatedAt: str | None = Field(None, description="最近更新时间 ISO 8601")
    updatedBy: str | None = Field(None, description="最近更新人")


class LlmConfigSaveRequest(CamelModel):
    """LLM 配置保存请求（POST /configs/llm）。

    apiKey 为空字符串时保留原值（不修改），非空时更新。
    """

    enabled: bool = Field(..., description="是否启用 LLM")
    endpoint: str | None = Field(None, max_length=500, description="BaseURL")
    apiKey: str | None = Field(None, max_length=500, description="API Key（空=保留原值）")
    model: str | None = Field(None, max_length=100, description="模型名")
    timeout: int = Field(30, ge=5, le=300, description="超时秒数")
    maxTokens: int = Field(4096, ge=256, le=32768, description="最大输出 token 数")


class LlmTestResult(CamelModel):
    """LLM 连接测试结果（POST /configs/llm/test）."""

    success: bool = Field(..., description="是否连接成功")
    latencyMs: int | None = Field(None, description="往返延迟毫秒（成功时）")
    model: str | None = Field(None, description="实际使用的模型名")
    message: str = Field(..., description="结果说明（成功/失败原因）")


__all__ = [
    "AlgorithmParamsControlItem",
    "AlgorithmParamsMetricGroup",
    "AlgorithmParamsSaveItem",
    "AlgorithmParamsSaveRequest",
    "AlgorithmParamsSchema",
    "ConfidenceThresholdItem",
    "ConfidenceThresholdSaveRequest",
    "ConfidenceThresholdSchema",
    "ControlType",
    "DetectorKey",
    "DiagnosisConfigBatchResponse",
    "DiagnosisConfigBatchUpdateRequest",
    "DiagnosisConfigItem",
    "DiagnosisConfigUpdateItem",
    "DiagnosisLabel",
    "DiagnosisTriggerSaveRequest",
    "DiagnosisTriggerSchema",
    "GradingThresholdItem",
    "GradingThresholdSaveRequest",
    "GradingThresholdSchema",
    "LlmConfigSaveRequest",
    "LlmConfigSchema",
    "LlmTestResult",
    "MetricCategory",
    "MetricConfigBatchResponse",
    "MetricConfigBatchUpdateRequest",
    "MetricConfigItem",
    "MetricConfigUpdateItem",
    "MetricThresholdSchema",
    "OutlierControlType",
    "OutlierParamsSaveRequest",
    "OutlierParamsSchema",
    "OutlierThresholdParams",
    "OutlierThresholdViewItem",
    "VersionHistoryItem",
    "VersionHistorySchema",
    "WeightTemplateItem",
    "WeightTemplateSaveRequest",
    "WeightTemplateSchema",
]
