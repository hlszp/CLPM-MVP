"""回路处置建议模型（处置模块 v2.0 双实体：审核对象）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §3.1 / §4.1 状态机
一条处置建议 = 诊断详情弹窗"处置建议" Tab 的一行 / 处置工作台建议清单的一行：
- 来源 SYSTEM：系统根据诊断结论/人工复核结论自动带出的标准处置建议
- 来源 MANUAL：工程师手动新增的处置措施（run_id 可空）
处置模块 v2.0（2026-08-20）：双实体重构——本表收敛为"建议 + 审核"域，
处置执行域 13 字段整体平移至 handling_order（工单）；
状态机 4 态：PENDING → ACCEPTED → CONVERTED；PENDING → REJECTED / IGNORED（终态）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: 建议状态机枚举（§4.1，4 态 + 忽略终态）
SUGGESTION_STATUSES = ("PENDING", "ACCEPTED", "CONVERTED", "REJECTED", "IGNORED")


class LoopActionItem(Base, TimestampMixin):
    """回路处置建议（审核对象；一次诊断 N 条，同一 run 重复拉取不重复生成）。"""

    __tablename__ = "loop_action_item"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    #: 关联诊断记录（一次诊断的建议集合；手动建议无诊断来源，可空）
    run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("diagnosis_run.id", ondelete="SET NULL"),
        nullable=True,
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
    #: 生命周期：PENDING 待审核 / ACCEPTED 已接受 / CONVERTED 已转工单（终态）/
    #: REJECTED 已驳回（终态）/ IGNORED 已忽略（终态）
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    #: 建议人（SYSTEM="系统"；MANUAL=登录用户名）
    suggested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    suggested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # ---- 审核域（§3.1 v2.0 新增）----
    #: 审核人 / 审核时间（accept 或 reject 时记录）
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 驳回原因（reject 必填）
    rejected_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: 转工单回链（convert 时写；工单删除后置空）
    converted_order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("handling_order.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: 忽略原因（PENDING → IGNORED 时必填）
    ignore_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "source IN ('SYSTEM', 'MANUAL')",
            name="ck_loop_action_item_source",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'CONVERTED', 'REJECTED', 'IGNORED')",
            name="ck_loop_action_item_status",
        ),
        CheckConstraint(
            "category IS NULL OR category IN "
            "('TUNING', 'VALVE', 'INSTRUMENT', 'COMMUNICATION', 'PROCESS', "
            "'UTILIZATION', 'DESIGN', 'DATA_INSUFFICIENT')",
            name="ck_loop_action_item_category",
        ),
        Index("idx_loop_action_item_run", "run_id"),
        Index("idx_loop_action_item_loop", "loop_id", "suggested_at"),
        # 建议清单主查询：状态 + 最近建议排序
        Index("idx_loop_action_item_status", "status", text("suggested_at DESC")),
        {
            "comment": "回路处置建议（处置模块 v2.0：建议汇聚与审核对象）",
        },
    )
