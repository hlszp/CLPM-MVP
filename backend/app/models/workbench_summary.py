"""``workbench_window_summary`` — 三窗口（24h/7d/30d）KPI + trend sparkline + flags 预计算表。

由 Celery ``workbench-precalc@5min`` 任务增量 upsert。
对应原型 W1 WINDOWS 与各 Tab 范围聚合卡片首屏快速渲染。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

WINDOWS = ("24h", "7d", "30d")
SCOPE_TYPES = ("GLOBAL", "FACTORY", "AREA", "UNIT", "LOOP")
STATUSES = (
    "EXCELLENT",
    "GOOD",
    "FAIR",
    "POOR",
    "CRITICAL",
    "INCONCLUSIVE",
)


class WorkbenchWindowSummary(Base):
    """Pre-aggregated KPI row per (scope × window), with score sparkline and flag list."""

    __tablename__ = "workbench_window_summary"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)  # GLOBAL -> 0
    # NOTE: column named window_w (PG reserved keyword); ORM exposes .window via alias.
    # WINDOWS = ("24h", "7d", "30d")
    window_w: Mapped[str] = mapped_column("window", String(8), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    score: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    loop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    good_value_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    auto_mode_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    effective_auto_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    steady_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    accuracy_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    fast_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    oscillation_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    saturation_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    instrument_fault_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)

    # score_trend: [{t: iso, v: 0-100}] — 24h: 24pts / 7d: 7pts / 30d: 15pts
    score_trend: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # flags: [{kind:'dip'|'spike'|..., severity, t, desc}]
    flags: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "window",
            "window_end",
            name="uniq_ws_scope_window_end",
        ),
        # NOTE: '"window"' 必须双引号以避开 PG 保留字
        CheckConstraint(
            f'"window" IN ({", ".join(repr(w) for w in WINDOWS)})',
            name="ck_ws_window",
        ),
        CheckConstraint(
            f"scope_type IN ({', '.join(repr(s) for s in SCOPE_TYPES)})",
            name="ck_ws_scope_type",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in STATUSES)})",
            name="ck_ws_status",
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_ws_score_range"),
        Index("idx_ws_scope_window", "scope_type", "scope_id", text('"window"')),
        Index("idx_ws_snapshot", "snapshot_at"),
    )
