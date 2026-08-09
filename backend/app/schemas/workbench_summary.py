"""工作台摘要（BFF）API schemas——整改方案 §8.2。

``GET /api/v1/monitor/loops/{loopId}/summary`` 一次返回工作台首屏所需的全部摘要：
回路基本信息、运行态（PV/SP/OP/MODE + readAt + 质量码 + dataFreshness）、
数据健康度、评分趋势、当前回路活跃关注项汇总、最新评估/诊断/整定摘要、
最新开放 Tracker/实施/验证状态、五阶段生命周期、推荐下一步 ``nextAction``。

设计约束（MW-P3-01）：
- 摘要禁止返回趋势数组、FFT 点、仿真曲线等大数据；详细按既有 API 延迟加载。
- 所有摘要包含 ``resultAt``/``timeWindow``/``confidence``/``status``。
- 运行态 ``dataFreshness`` 由服务端计算，复用实时链路停滞配置，前端不复制常量。
- 单个来源失败时返回 ``partial=true`` 和 ``unavailableSections``，不让整页 500。

所有 schema 继承 CamelModel（snake_case 字段 → camelCase JSON）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

#: 生命周期阶段
LifecycleStage = Literal["MONITOR", "ASSESS", "DIAGNOSE", "TUNE", "VERIFY"]

#: 生命周期统一状态（方案 §7.2）
LifecycleStatus = Literal[
    "NOT_STARTED",
    "READY",
    "RUNNING",
    "COMPLETED",
    "INCONCLUSIVE",
    "BLOCKED",
    "OVERDUE",
    "NOT_REQUIRED",
]

#: 数据新鲜度状态
DataFreshnessStatus = Literal["FRESH", "DELAYED", "UNKNOWN"]

#: nextAction 动作类型（与 AttentionActionType 对齐 + 工作台专用动作）
NextActionType = Literal[
    "OPEN_WORKBENCH",
    "RUN_ASSESSMENT",
    "RUN_DIAGNOSIS",
    "CREATE_TRACKER",
    "RUN_TUNING",
    "RECORD_IMPLEMENTATION",
    "VERIFY_EFFECT",
    "IMPORT_DATA",
    "FIX_TAG_CONFIG",
    "CONTINUE_MONITORING",
    "VIEW_DETAIL",
]


# ---------------------------------------------------------------------------
# 运行态
# ---------------------------------------------------------------------------


class RuntimeState(CamelModel):
    """回路运行态（来自 Redis 实时缓存 + Tag 注册表）。"""

    pv: float | None = None
    sp: float | None = None
    op: float | None = None
    mode: float | None = None
    mode_label: str | None = Field(None, description="控制模式标签：Auto/Cascade/Manual/Unknown")
    pv_quality: str | None = Field(None, description="PV 质量码标签：GOOD/BAD/UNCERTAIN")
    pv_unit: str | None = None
    pv_range: dict[str, float | None] | None = None
    op_range: dict[str, float | None] | None = None
    read_at: str | None = Field(None, description="最近一次采样时间 ISO8601")
    control_mode: str | None = Field(None, description="= modeLabel，兼容前端字段")


class DataFreshness(CamelModel):
    """数据新鲜度（服务端计算，复用实时链路停滞配置）。

    ``thresholdSeconds`` 复用 ``SIGNALR_STALL_TIMEOUT_SECONDS``（实时链路停滞阈值），
    前端不复制常量；``status`` 由 readAt 与阈值比较得出。
    """

    status: DataFreshnessStatus
    threshold_seconds: int = Field(..., description="停滞阈值（秒），复用实时链路配置")
    reason: str | None = Field(None, description="可读原因（如已停滞 320 秒）")


# ---------------------------------------------------------------------------
# 数据健康度 & 评分趋势
# ---------------------------------------------------------------------------


class DataHealth(CamelModel):
    """数据健康度（预处理 validRate + 可信度 + 完整度）。"""

    valid_rate: float | None = None
    confidence_level: str | None = Field(None, description="A/B/C/D/E")
    pv_completeness: float | None = None
    overall_completeness: float | None = None
    integrity_status: str | None = Field(None, description="OK/WARNING/CRITICAL/DATA_UNAVAILABLE")


class ScoreTrend(CamelModel):
    """评分趋势（最新评分 + 较昨日）。"""

    score: float | None = None
    score_delta: float | None = Field(None, description="较昨日增量")
    day_trend: str | None = Field(None, description="NEW/WORSENED/IMPROVED/FLAT")
    result_at: str | None = Field(None, description="最新快照时间 ISO8601")
    confidence_level: str | None = None
    status: str | None = Field(None, description="SUCCESS/INCONCLUSIVE/PARTIAL")


# ---------------------------------------------------------------------------
# 活跃关注项汇总（当前回路）
# ---------------------------------------------------------------------------


class ActiveAttentionSummary(CamelModel):
    """当前回路活跃关注项汇总（方案 §6 单回路上下文层）。"""

    total: int = Field(0, description="当前回路开放关注项总数")
    highest_priority: str | None = Field(None, description="最高优先级 URGENT/HIGH/MEDIUM/LOW")
    items: list[dict] = Field(
        default_factory=list,
        description="最多 3 条明细（结构与 AttentionItem 一致，截断）",
    )


# ---------------------------------------------------------------------------
# 评估/诊断/整定 摘要
# ---------------------------------------------------------------------------


class AssessmentSummary(CamelModel):
    """最新评估摘要（不含趋势数组）。"""

    score: float | None = None
    confidence_level: str | None = None
    status: str | None = Field(None, description="SUCCESS/INCONCLUSIVE/PARTIAL")
    result_at: str | None = Field(None, description="最新快照 ts_end")
    time_window: str | None = Field(None, description="评估时间窗描述")
    summary: str | None = Field(None, description="一句话结论")


class DiagnosisSummary(CamelModel):
    """最新诊断摘要（不含证据链大对象）。"""

    diag_label: str | None = Field(None, description="主诊断标签")
    confidence: float | None = Field(None, description="融合可信度 0-100")
    status: str | None = Field(
        None, description="任务状态 PENDING/RUNNING/SUCCESS/FAILED/CANCELLED"
    )
    result_at: str | None = Field(None, description="diagnosed_at")
    task_id: str | None = None
    labels: list[str] = Field(default_factory=list, description="诊断标签代码列表")
    summary: str | None = Field(None, description="一句话结论")


class TuningSummary(CamelModel):
    """最新整定摘要（不含仿真曲线点）。"""

    status: str | None = Field(
        None,
        description="DRAFT/RUNNING/IDENTIFIED/SIMULATED/COMPLETED/INCONCLUSIVE/ROLLED_BACK",
    )
    model_type: str | None = Field(None, description="FOPDT/SOPDT/IPDT")
    algorithm: str | None = None
    confidence_level: str | None = None
    result_at: str | None = Field(None, description="created_at 或 completed_at")
    current_pid: dict | None = Field(None, description="当前 PID 快照")
    recommended_pid: dict | None = Field(None, description="推荐 PID")
    fitting_score: float | None = None
    risk_level: str | None = Field(None, description="风险评估等级")
    summary: str | None = Field(None, description="一句话结论")


# ---------------------------------------------------------------------------
# Tracker / 实施 / 验证 时间线
# ---------------------------------------------------------------------------


class TrackerTimeline(CamelModel):
    """最新开放 Tracker 及其实施/验证状态（方案 §7.1 闭环时间线）。"""

    tracker_id: str | None = None
    diagnosis_label: str | None = None
    action_status: str | None = Field(
        None,
        description="PENDING/IN_PROGRESS/VERIFYING/CLOSED/REOPENED",
    )
    severity: str | None = None
    trigger_type: str | None = Field(None, description="auto/manual")
    assignee: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    implemented_at: str | None = None
    implemented_by: str | None = None
    new_pid: dict | None = Field(None, description="实施后 PID {p,i,d}")
    moc_ref: str | None = Field(None, description="MOC 变更管理关联")
    moc_not_applicable: bool | None = None
    planned_at: str | None = None
    closed_at: str | None = None
    effect_verified: bool | None = Field(
        None, description="整改效果验证 True=改善/False=恶化/None=未验证"
    )
    effect_verified_at: str | None = None
    ab_compare_summary: dict | None = Field(None, description="A/B 对比结果快照")
    reopen_reason: str | None = None
    is_overdue: bool = Field(False, description="VERIFYING 是否超期")
    overdue_hours: float | None = Field(None, description="超期小时数（is_overdue=True 时有值）")


# ---------------------------------------------------------------------------
# 生命周期 & nextAction
# ---------------------------------------------------------------------------


class LifecycleStageState(CamelModel):
    """单阶段状态。"""

    stage: LifecycleStage
    status: LifecycleStatus
    result_at: str | None = Field(None, description="该阶段最新结果时间")
    reason: str | None = Field(None, description="状态原因（可读解释）")


class Lifecycle(CamelModel):
    """五阶段生命周期（MONITOR/ASSESS/DIAGNOSE/TUNE/VERIFY）。"""

    stages: list[LifecycleStageState]
    current_stage: LifecycleStage | None = Field(None, description="当前推荐关注阶段")


class NextAction(CamelModel):
    """推荐下一步（方案 §7.3，服务端按角色返回唯一主动作）。"""

    action_type: NextActionType
    label: str = Field(..., description="动作按钮文案")
    reason: str = Field(..., description="推荐原因")
    enabled: bool = True
    disabled_reason: str | None = Field(None, description="禁用原因（enabled=false 时必填）")
    target: dict | None = Field(
        None,
        description="跳转目标 {route, query}，OPEN_WORKBENCH 等动作必填",
    )


# ---------------------------------------------------------------------------
# 工作台摘要响应
# ---------------------------------------------------------------------------


class WorkbenchSummary(CamelModel):
    """工作台首屏摘要（BFF 聚合）。

    单个来源失败时 ``partial=true`` 且该来源在 ``unavailableSections`` 中列出，
    其他来源正常返回，不让整页 500。
    """

    loop_id: str
    tag_name: str
    description: str | None = None
    unit_name: str | None = None
    loop_type: str | None = None
    control_type: str | None = None
    loop_status: str | None = Field(None, description="READY/PARTIAL/INACTIVE")
    is_active: bool | None = None
    importance_level: int | None = None

    runtime: RuntimeState
    data_freshness: DataFreshness
    data_health: DataHealth
    score_trend: ScoreTrend

    active_attention: ActiveAttentionSummary

    assessment: AssessmentSummary | None = None
    diagnosis: DiagnosisSummary | None = None
    tuning: TuningSummary | None = None
    tracker_timeline: TrackerTimeline | None = None

    lifecycle: Lifecycle
    next_action: NextAction

    partial: bool = Field(False, description="是否有来源失败")
    unavailable_sections: list[str] = Field(default_factory=list, description="失败来源列表")
