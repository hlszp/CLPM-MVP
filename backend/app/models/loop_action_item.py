"""回路处置建议模型（处置闭环：建议-处置-验证-关闭）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.4
一条处置建议 = 诊断详情弹窗"处置建议" Tab 的一行：
- 来源 SYSTEM：系统根据诊断结论/人工复核结论自动带出的标准处置建议
- 来源 MANUAL：工程师在诊断详情中手工增加的处置措施
后续一级模块"处置"将基于本表统一管理生命周期（建议→处置→验证→关闭），
当前阶段仅落建议（status 恒 PENDING），处置流转待该模块实施时扩展。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


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
    #: 冗余回路 ID（后续"处置"模块按回路聚合查询）
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
    #: 生命周期：PENDING 待处置（后续扩展 IN_PROGRESS/VERIFYING/CLOSED）
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    #: 建议人（SYSTEM="系统"；MANUAL=登录用户名）
    suggested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    suggested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "source IN ('SYSTEM', 'MANUAL')",
            name="ck_loop_action_item_source",
        ),
        CheckConstraint(
            "status IN ('PENDING')",
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
        {
            "comment": "回路处置建议（建议-处置-验证-关闭闭环，当前仅建议态）",
        },
    )
