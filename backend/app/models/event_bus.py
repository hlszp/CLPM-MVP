"""``event_bus`` — 跨模块事件归一总线（铃铛未读 + SLA + 模块变更 + 趋势 flags 统一入口）。

所有业务状态变更必经 ``app.core.event_bus.EventBus.publish()``：
  1. 写本表；2. 通过 WS 通道 /api/v1/ws/bell 推送未读增量 + Toast 摘要。

read_by_users 使用 JSONB 数组存 user_id（GIN 索引支持"我未读"查询），
避免另建 event_bus_reads 关联表。日归档 > 90d 记录到 event_bus_archive。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SOURCE_MODULES = (
    "monitor",
    "assess",
    "diagnosis",
    "tuning",
    "handling",
    "alert",
    "system",
)
SEVERITIES = ("INFO", "WARN", "ERROR", "CRITICAL")
EVENT_TYPES = (
    "ALERT_NEW",
    "DIAG_TAG_OPENED",
    "DIAG_TAG_CONFIRMED",
    "DIAG_CONCL_READY",
    "ORDER_CREATED",
    "ORDER_SLA_WARN",
    "ORDER_SLA_BREACH",
    "ORDER_REOPENED",
    "ORDER_CLOSED",
    "TUNE_BATCH_READY",
    "TUNE_COMPLETED",
    "TUNE_ROLLBACK",
    "MODULE_STATUS_CHANGED",
    "TREND_FLAG_DETECTED",
    "CONFIG_CHANGED",
)
SCOPE_TYPES = ("GLOBAL", "FACTORY", "AREA", "UNIT", "LOOP")


class EventBus(Base):
    """Normalized event feed aggregating alert/diagnosis/tuning/handling/system events."""

    __tablename__ = "event_bus"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_module: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)

    scope_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("handling_order.id", ondelete="SET NULL"),
        nullable=True,
    )
    record_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tuning_record.id", ondelete="SET NULL"),
        nullable=True,
    )
    tag_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("diagnosis_tag.id", ondelete="SET NULL"),
        nullable=True,
    )
    alert_event_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alert_event.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 注：列名用 ext_metadata 避免与 SQLAlchemy Declarative .metadata 属性冲突
    # e.g. {sla_level: 'BREACH', disposition: 'CONVERTED', reopen_count: 2, ...}
    ext_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # list[int] — user ids that have read this event
    read_by_users: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            f"source_module IN ({', '.join(repr(s) for s in SOURCE_MODULES)})",
            name="ck_eb_source_module",
        ),
        CheckConstraint(
            f"severity IN ({', '.join(repr(s) for s in SEVERITIES)})",
            name="ck_eb_severity",
        ),
        Index("idx_eb_scope", "scope_type", "scope_id", "occurred_at"),
        Index("idx_eb_occurred_desc", text("occurred_at DESC")),
        # GIN on read_by_users supports "user X has not read" queries
        # (use jsonb_array_elements + LEFT JOIN combined w/ Redis counter)
        Index("idx_eb_read_users", "read_by_users", postgresql_using="gin"),
        # Partial index for fast "my unread" when JSON is empty:
        Index(
            "idx_eb_unread_count",
            "id",
            postgresql_where=text("jsonb_array_length(read_by_users) = 0"),
        ),
        Index("idx_eb_source_type", "source_module", "event_type"),
    )
