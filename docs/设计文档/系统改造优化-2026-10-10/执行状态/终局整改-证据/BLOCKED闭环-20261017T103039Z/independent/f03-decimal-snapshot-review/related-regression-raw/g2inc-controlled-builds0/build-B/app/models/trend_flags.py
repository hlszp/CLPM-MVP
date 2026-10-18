"""``trend_flags`` — KPI/得分趋势标注点（dip/spike/deterioration/jump/...）。

由 Celery ``workbench-precalc@5min`` 任务对 workbench_window_summary.score_trend
差分检测写入；对应原型 W1/W3 KPI 卡气泡 flags 点。
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
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FLAG_KINDS = (
    "dip",
    "spike",
    "deterioration",
    "jump",
    "oscillation_start",
    "saturation_event",
)
SEVERITIES = ("INFO", "WARN", "ERROR", "CRITICAL")
WINDOWS = ("24h", "7d", "30d")
SCOPE_TYPES = ("GLOBAL", "FACTORY", "AREA", "UNIT", "LOOP")


class TrendFlag(Base):
    """One trend flag detected on a KPI score/point-series in a given scope-window."""

    __tablename__ = "trend_flags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )
    # NOTE: column name "window" 是 PG 保留字，ORM 属性重命名 window_w，DB 列仍为 window。
    window_w: Mapped[str] = mapped_column("window", String(8), nullable=False)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    flagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prev_value: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)
    curr_value: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)
    delta_pct: Mapped[float | None] = mapped_column(Numeric(7, 2), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            f"kind IN ({', '.join(repr(k) for k in FLAG_KINDS)})",
            name="ck_tf_kind",
        ),
        CheckConstraint(
            f"severity IN ({', '.join(repr(s) for s in SEVERITIES)})",
            name="ck_tf_severity",
        ),
        CheckConstraint(
            f'"window" IN ({", ".join(repr(w) for w in WINDOWS)})',
            name="ck_tf_window",
        ),
        CheckConstraint(
            f"scope_type IN ({', '.join(repr(s) for s in SCOPE_TYPES)})",
            name="ck_tf_scope_type",
        ),
        Index(
            "idx_tf_scope_window_flagged_desc",
            "scope_type",
            "scope_id",
            text('"window"'),
            text("flagged_at DESC"),
        ),
        Index("idx_tf_loop", "loop_id", "flagged_at"),
        Index("idx_tf_kind_severity", "kind", "severity"),
    )
