"""处置工单模型（处置模块 v2.0 双实体：执行对象）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §3.2 / §4.2 状态机
一条处置工单 = 处置执行闭环的载体：
- 来源 DIAGNOSIS：审核通过的建议转化生成（多建议可合一单，suggestion_ids 回溯）
- 来源 MANUAL：工程师手动新建（现场处置不经诊断流程）
状态机 6 态：PENDING → EXECUTING → VERIFYING → CLOSED/REOPENED；PENDING → CANCELLED。
处置编号 order_no = HD-YYYYMMDD-NNN（按日重置序号，唯一约束）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: 处置类型枚举（§5，8 类，自 v1.x 平移）
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

#: 工单状态机枚举（§4.2，6 态）
ORDER_STATUSES = ("PENDING", "EXECUTING", "VERIFYING", "CLOSED", "REOPENED", "CANCELLED")


class HandlingOrder(Base, TimestampMixin):
    """处置工单（排程、执行反馈、验证、闭环的执行载体）。"""

    __tablename__ = "handling_order"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    #: 处置编号：HD-YYYYMMDD-NNN（按日重置序号，服务端生成）
    order_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    #: 关联回路
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 来源：DIAGNOSIS 建议转化 / MANUAL 手动新建
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 来源建议 id 数组（多建议合一单；MANUAL 为空）
    suggestion_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: 工单标题（缺省取首条建议内容前 50 字）
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 处置类型（8 类，§5）
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 结构化处置详情（schema 按 action_type，§5.2）
    action_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 排程：计划处置时间 / 排程人（转工单/新建工单的操作人）
    planned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 处置人（start 时手工填写，缺省当前用户）
    handler: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 执行反馈数组 [{at, by, content}]（EXECUTING 中多次追加）
    feedback_log: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: 提交验证时间（KPI 前后窗口分界）
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
    #: 验证提交时固化的 KPI 前后快照摘要（前窗 started_at 口径，§4.3）
    kpi_before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    kpi_after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 预留：关联整定记录
    tuning_record_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    #: 作废原因（cancel 必填）
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: 生命周期：PENDING 待执行 / EXECUTING 执行中 / VERIFYING 验证中 /
    #: CLOSED 已闭环（终态）/ REOPENED 重开 / CANCELLED 已作废（终态）
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")

    __table_args__ = (
        CheckConstraint(
            "source IN ('DIAGNOSIS', 'MANUAL')",
            name="ck_handling_order_source",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'EXECUTING', 'VERIFYING', 'CLOSED', 'REOPENED', 'CANCELLED')",
            name="ck_handling_order_status",
        ),
        CheckConstraint(
            "action_type IN ('TUNING', 'VALVE', 'INSTRUMENT', 'LINK', 'PROCESS', "
            "'UTILIZATION', 'RECONFIG', 'OTHER')",
            name="ck_handling_order_action_type",
        ),
        CheckConstraint(
            "verify_result IS NULL OR verify_result IN ('EFFECTIVE', 'INEFFECTIVE')",
            name="ck_handling_order_verify_result",
        ),
        Index("idx_handling_order_status", "status", text("updated_at DESC")),
        Index("idx_handling_order_loop", "loop_id"),
        Index("idx_handling_order_planned", "planned_at"),
        {
            "comment": "处置工单（处置模块 v2.0：排程-执行-验证-闭环执行载体）",
        },
    )
