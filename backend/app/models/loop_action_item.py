"""回路处置建议模型（处置闭环：建议-处置-验证-关闭）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §3 数据模型 / §4 状态机
一条处置建议 = 诊断详情弹窗"处置建议" Tab 的一行：
- 来源 SYSTEM：系统根据诊断结论/人工复核结论自动带出的标准处置建议
- 来源 MANUAL：工程师在诊断详情中手工增加的处置措施
处置模块 Phase 1（2026-08-18）：扩展为全生命周期管理
（PENDING → HANDLING → VERIFYING → CLOSED/REOPENED，PENDING → IGNORED 终态）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: 处置类型枚举（§5.1，8 类）
ACTION_TYPES = (
    "TUNING",
    "VALVE",
    "INSTRUMENT",
    "LINK",
    "PROCESS",
    "UTILIZATION",
    "RECONFIG",
    "OTHER",
)

#: 状态机枚举（§4.1，5 态 + 忽略终态）
ACTION_STATUSES = ("PENDING", "HANDLING", "VERIFYING", "CLOSED", "REOPENED", "IGNORED")


class LoopActionItem(Base, TimestampMixin):
    """回路处置建议（一次诊断 N 条；同一 run 重复拉取不重复生成）。"""

    __tablename__ = "loop_action_item"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    #: 关联诊断记录（一次诊断的建议集合）
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("diagnosis_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 冗余回路 ID（处置模块按回路聚合查询）
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 来源：SYSTEM 系统带出 / MANUAL 人工新增
    source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="SYSTEM")
    #: 问题分类（与诊断 8 类同域；MANUAL 可空）
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 处置措施内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: 依据（如"诊断结论：参数问题（置信度 85%）"或"人工复核：..."）
    basis: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: 优先级（1 最高；MANUAL 为空）
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 生命周期：PENDING 待处置 / HANDLING 处置中 / VERIFYING 验证中 /
    #: CLOSED 已闭环（终态）/ REOPENED 重开 / IGNORED 已忽略（终态）
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    #: 建议人（SYSTEM="系统"；MANUAL=登录用户名）
    suggested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    suggested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # ---- 处置模块 Phase 1 扩展（§3.2，2026-08-18）----
    #: 处置类型（开始处置时必填，8 类）
    action_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 结构化处置详情（schema 按 action_type，§5.2）
    action_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 处置人（手工填写，缺省当前登录用户）/ 开始处置时间
    handled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 提交验证时间（处置完成时点，KPI 前后窗口分界）
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 验证依据的复诊记录（可选，复诊删除后置空）
    verify_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("diagnosis_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: 验证结论：EFFECTIVE 有效 / INEFFECTIVE 无效
    verify_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verify_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 验证提交时固化的 KPI 前后快照摘要（防快照滚动导致对比漂移）
    kpi_before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    kpi_after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 预留：关联整定记录（整定模块开放后回填）
    tuning_record_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    #: 忽略原因（PENDING → IGNORED 时必填）
    ignore_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "source IN ('SYSTEM', 'MANUAL')",
            name="ck_loop_action_item_source",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'HANDLING', 'VERIFYING', 'CLOSED', 'REOPENED', 'IGNORED')",
            name="ck_loop_action_item_status",
        ),
        CheckConstraint(
            "category IS NULL OR category IN "
            "('TUNING', 'VALVE', 'INSTRUMENT', 'COMMUNICATION', 'PROCESS', "
            "'UTILIZATION', 'DESIGN', 'DATA_INSUFFICIENT')",
            name="ck_loop_action_item_category",
        ),
        CheckConstraint(
            "action_type IS NULL OR action_type IN "
            "('TUNING', 'VALVE', 'INSTRUMENT', 'LINK', 'PROCESS', "
            "'UTILIZATION', 'RECONFIG', 'OTHER')",
            name="ck_loop_action_item_action_type",
        ),
        CheckConstraint(
            "verify_result IS NULL OR verify_result IN ('EFFECTIVE', 'INEFFECTIVE')",
            name="ck_loop_action_item_verify_result",
        ),
        Index("idx_loop_action_item_run", "run_id"),
        Index("idx_loop_action_item_loop", "loop_id", "suggested_at"),
        # 处置清单主查询：状态 + 最近更新排序
        Index("idx_loop_action_item_status", "status", text("updated_at DESC")),
        {
            "comment": "回路处置建议（处置模块 Phase 1：建议-处置-验证-关闭全生命周期）",
        },
    )
