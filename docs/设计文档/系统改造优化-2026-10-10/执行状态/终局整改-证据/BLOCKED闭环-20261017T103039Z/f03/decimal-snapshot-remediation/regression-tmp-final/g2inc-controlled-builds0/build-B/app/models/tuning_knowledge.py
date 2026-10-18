"""``tuning_knowledge_entry`` model — 整定知识库条目（P3-01）。

在 ActionTracker 验证完成（effect_verified 回写）时由 tracker_verification
周期任务聚合生成，是不可变快照。数据来源：ActionTracker + TuningRecord（可选）
+ LoopLedger。

设计依据：PRD §5.6 / IA 整改 P3-01 / DDS §新增
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TuningKnowledgeEntry(Base):
    """整定知识库条目（P3-01）。

    验证通过/恶化的整定案例快照，支持按控制类型/问题类型查询和相似案例推荐。
    一个 tracker 只生成一条知识库条目（tracker_id 唯一约束，幂等）。
    """

    __tablename__ = "tuning_knowledge_entry"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 来源 tracker（唯一，防重复生成）
    tracker_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("action_tracker.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 关联整定记录（可空，用户未走整定流程时为 NULL）
    tuning_record_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tuning_record.id", ondelete="SET NULL"),
        nullable=True,
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 冗余字段（生成时快照，避免 JOIN；源表后续变更不影响）
    loop_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    control_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    diagnosis_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # 整定元数据（来自 TuningRecord，可能为空）
    model_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    algorithm: Mapped[str | None] = mapped_column(String(50), nullable=True)
    identify_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(12), nullable=True)

    # PID 变化（pid_before 来自 TuningRecord.current_pid；pid_after 来自 tracker.new_pid_*）
    pid_before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pid_after: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 改善幅度（直接复用 tracker.ab_compare_summary 的结构）
    kpi_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    effect_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    improved_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    deteriorated_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # 关联匹配方式（exact=外键指定 / time_window=时间窗口兜底 / none=无整定记录）
    match_source: Mapped[str] = mapped_column(String(20), nullable=False, default="none")

    implemented_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "match_source IN ('exact', 'time_window', 'none')",
            name="ck_tke_match_source",
        ),
        # 核心查询：按控制类型/问题类型查询（知识库列表 + 相似推荐）
        Index("idx_tke_loop_type_label", "loop_type", "diagnosis_label"),
        Index("idx_tke_label", "diagnosis_label"),
        Index("idx_tke_loop_id", "loop_id"),
        Index("idx_tke_effect", "effect_verified"),
        # 幂等：一个 tracker 只生成一条知识库条目
        Index("idx_tke_tracker", "tracker_id", unique=True),
    )
