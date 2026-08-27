"""``diagnosis_config``、``diagnosis_result`` 与 ``diagnosis_tag`` models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ---------- Workbench v2.0 新增 disposition / SLA 枚举 ----------
DISPOSITION_STATES = ("UNADDRESSED", "CONVERTED", "ACK_REVIEWED", "IGNORED")
SLA_STAGES = ("NONE", "WARN", "BREACH")


class DiagnosisConfig(Base):
    """Diagnosis metric configuration (DDL §7)."""

    __tablename__ = "diagnosis_config"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    diag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    diag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    algorithm_type: Mapped[str] = mapped_column(String(50), nullable=False)
    calc_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    threshold: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_enabled: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)

    __table_args__ = (UniqueConstraint("diag_code", name="uk_diagnosis_config_code"),)


class DiagnosisResult(Base):
    """Diagnosis engine result for a loop (DDL §11)."""

    __tablename__ = "diagnosis_result"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=True,
    )
    diag_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    feature_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_chain: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # C4: 阈值版本号（记录诊断时使用的配置版本，可追溯当时阈值）
    threshold_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnosed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Workbench v2.0: 推荐处置类别（与 DiagnosisTag.category 对齐，用于 CONCL 因果链）
    recommended_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Workbench v2.0: 证据摘要文本（首屏 CONCL 时间线用，避免再读 evidence_chain）
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联诊断任务（可选，向后兼容：旧记录 task_id 为 NULL）
    task_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("diagnosis_task.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="ck_diagnosis_result_conf",
        ),
        Index("idx_diagnosis_result_loop_id", "loop_id"),
        Index("idx_diagnosis_result_diagnosed", "diagnosed_at"),
        Index("idx_diagnosis_result_task_id", "task_id"),
    )


class DiagnosisTask(Base):
    """诊断任务 — 每回路每批次一条任务记录（DDL §11.1）。

    承载用户手动触发或系统自动触发的回路诊断任务全生命周期记录：
    - 状态机：PENDING → RUNNING → SUCCESS / FAILED / CANCELLED
    - 触发方式：manual（用户手动） / auto（系统自动）
    - 完成后可归档（is_archived=true），归档后从任务列表移入诊断记录
    - 与 DiagnosisResult 一对多：一个任务可产生多条诊断结果记录

    设计依据：PRD §5.6 诊断中心 / IDS §2.4 诊断任务管理
    """

    __tablename__ = "diagnosis_task"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 触发方式：manual（手动）/ auto（自动）
    trigger_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # 触发人：用户名或 'system'
    triggered_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 任务状态：PENDING / RUNNING / SUCCESS / FAILED / CANCELLED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    # 诊断时间窗（NULL 表示使用默认 1 小时）
    time_range_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_range_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 失败时的错误信息
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 归档相关字段
    is_archived: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        CheckConstraint("trigger_type IN ('manual', 'auto')", name="ck_diag_task_trigger_type"),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_diag_task_status",
        ),
        Index("idx_diagnosis_task_loop_id", "loop_id"),
        Index("idx_diagnosis_task_status", "status"),
        Index("idx_diagnosis_task_archived", "is_archived"),
    )


class DiagnosisTag(Base):
    """Diagnosis tag — loop-level fault tag (DDS §2.16).

    承载回路级的诊断标签记录，用于故障定位和告警，包括振荡、阀门粘滞、
    输出饱和、PV 质量异常等标签。与 ``DiagnosisResult`` 互补：
    ``DiagnosisResult`` 存储完整诊断证据链，``DiagnosisTag`` 存储可枚举、
    可查询、可状态流转的标签实例，支撑告警面板与标签筛选。

    Workbench v2.0 扩展：
    - disposition_state 四态：诊断结论在"处置-采纳链路"中的当前位置
    - sla_deadline_at / sla_stage：关键 Tag 自身的 SLA 闭环（超期红 dot）

    设计依据：DDS §2.16, PRD §5.6, IDS §2.4.10-2.4.12
    """

    __tablename__ = "diagnosis_tag"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 标签代码（如 OSCILLATION / VALVE_STICTION / OUTPUT_SATURATION / QUALITY_ABNORMAL）
    tag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    tag_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 严重等级：INFO / WARN / ERROR / CRITICAL
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # 触发该标签的来源指标代码（如 oscillation_rate）
    source_metric: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 触发条件，如 {"threshold": 0.4, "window_minutes": 60}
    trigger_condition: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 触发阈值数值
    trigger_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 标签状态：ACTIVE（生效中）/ RESOLVED（已解除）/ SUPPRESSED（已抑制）
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"), nullable=False)
    # --- Workbench v2.0 新增：disposition 三态（+ IGNORED）  ---
    disposition_state: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'UNADDRESSED'"),
        default="UNADDRESSED",
        nullable=False,
    )
    # --- Workbench v2.0 新增：Tag 自身 SLA（关键异常气泡） ---
    sla_deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sla_stage: Mapped[str] = mapped_column(
        String(8),
        server_default=text("'NONE'"),
        default="NONE",
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')",
            name="ck_diag_tag_severity",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')",
            name="ck_diag_tag_status",
        ),
        CheckConstraint(
            "disposition_state IN ('UNADDRESSED','CONVERTED','ACK_REVIEWED','IGNORED')",
            name="ck_diag_tag_disposition",
        ),
        CheckConstraint(
            "sla_stage IN ('NONE','WARN','BREACH')",
            name="ck_diag_tag_sla_stage",
        ),
        Index("ix_diagnosis_tag_loop_status", "loop_id", "status"),
        Index("ix_diagnosis_tag_severity", "severity", "triggered_at"),
        Index("idx_diag_tag_disposition", "disposition_state"),
        Index(
            "idx_diag_tag_active_sla",
            "status",
            "sla_stage",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"comment": "诊断标签表：用于故障定位和告警（振荡/阀门粘滞/输出饱和/PV质量异常等）"},
    )


class DiagnosisRule(Base):
    """专家规则配置（C2 规则引擎化，DDL §7.2）。

    将硬编码的 R01-R08 专家规则迁入数据库表，支持 UI 新增/停用/修改，
    运行时用 simpleeval 安全沙箱求值条件表达式。

    设计依据：FDS §5.4.6 / 整改计划 C2
    """

    __tablename__ = "diagnosis_rule"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 规则代码（R01-R08，唯一）
    rule_code: Mapped[str] = mapped_column(String(20), nullable=False)
    # 规则名称（中文显示）
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 执行优先级（数值越小越先执行）
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    # 条件表达式（simpleeval 安全沙箱求值）
    condition_expr: Mapped[str] = mapped_column(Text, nullable=False)
    # 动作类型：REMOVE_LABEL / ADD_LABEL / KEEP_HIGHEST / FILTER_ONLY / SORT_PRIORITY
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 动作参数（JSON：label / labels / keep / confidence / priority_map 等）
    action_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 是否启用
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), default=True, nullable=False
    )
    # 版本号（C4 版本回滚依赖）
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('REMOVE_LABEL', 'ADD_LABEL', 'KEEP_HIGHEST', "
            "'FILTER_ONLY', 'SORT_PRIORITY')",
            name="ck_diag_rule_action_type",
        ),
        Index("uk_diagnosis_rule_code", "rule_code", unique=True),
        Index("idx_diagnosis_rule_priority", "priority"),
    )


class DiagnosisThresholdOverride(Base):
    """诊断阈值差异化覆盖（C3 差异化阈值，FDS §5.4.1）。

    支持"全局默认 → 回路类型模板 → 装置级 → 回路级"四级阈值覆盖。
    全局默认存储在 ``DiagnosisConfig.threshold``；本表存储各级覆盖。

    优先级（高→低）：
    1. loop: 回路级覆盖（scope_id = loop_id）
    2. plant: 装置级覆盖（scope_id = plant_node_id）
    3. loop_type: 回路类型模板（scope_id = FLOW/TEMPERATURE/LEVEL/PRESSURE/…）
    4. 全局默认（DiagnosisConfig）

    设计依据：整改计划 C3 / FDS §5.4.1
    """

    __tablename__ = "diagnosis_threshold_override"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 诊断算法代码（关联 diagnosis_config.diag_code）
    diag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    # 覆盖范围：loop_type（回路类型模板）/ plant（装置级）/ loop（回路级）
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 范围标识：loop_type 时为 FLOW/TEMPERATURE/…；plant 时为 plant_node_id；loop 时为 loop_id
    scope_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # 覆盖的阈值 JSON（与 DiagnosisConfig.threshold 同结构）
    threshold: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('loop_type', 'plant', 'loop')",
            name="ck_diag_threshold_override_scope",
        ),
        Index(
            "uk_diag_threshold_override",
            "diag_code",
            "scope_type",
            "scope_id",
            unique=True,
        ),
        Index("idx_diag_threshold_override_scope", "scope_type", "scope_id"),
    )


class DiagnosisConfigChange(Base):
    """关键配置变更审批（C5 审批流，ADS §1）。

    危化企业关键诊断配置变更（触发阈值、规则启停等）须经第二人审批后方可生效。
    "双人确认"：审批人不能与申请人相同。

    状态机：PENDING → APPROVED（已审批，自动应用）/ REJECTED（已拒绝）

    设计依据：整改计划 C5 / ADS §1
    """

    __tablename__ = "diagnosis_config_change"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 变更目标类型：config（诊断指标配置）/ rule（专家规则）/ trigger（触发条件）
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 目标 ID（diagnosis_config.id / diagnosis_rule.id / 'trigger'）
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # 变更类型：update / enable / disable
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 变更前值（JSON）
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 变更后值（JSON）
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 审批状态：PENDING / APPROVED / REJECTED
    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'PENDING'"), nullable=False
    )
    # 申请人
    requested_by: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )
    # 审批人
    reviewed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 审批意见
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生效时间（审批通过后立即生效）
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "target_type IN ('config', 'rule', 'trigger')",
            name="ck_diag_config_change_target",
        ),
        CheckConstraint(
            "change_type IN ('update', 'enable', 'disable')",
            name="ck_diag_config_change_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_diag_config_change_status",
        ),
        Index("idx_diag_config_change_status", "status"),
        Index("idx_diag_config_change_target", "target_type", "target_id"),
    )
