"""Declarative Base and shared mixins for SQLAlchemy 2.0 models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base."""

    pass


class TimestampMixin:
    """Mixin with ``created_at`` and ``updated_at`` columns.

    Matches the DDL pattern ``TIMESTAMP DEFAULT NOW()`` used by tables that
    track both creation and modification timestamps (e.g. ``sys_user``,
    ``plant_node``, ``loop_ledger``).
    """

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
