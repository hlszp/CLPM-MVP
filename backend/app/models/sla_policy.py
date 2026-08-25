"""``sla_policy`` — SLA 到期模板（action_type × priority × optional scope 覆盖）。

8 类 action_type × 4 级 priority × (可选 scope 覆盖) 的阈值模板。
handling_order 的 SLA 扩展列（sla_policy_id / sla_deadline_at / sla_stage /
reopen_count / reopen_reasons）直接定义在 handling_order.py 中，本文件仅提供
SlaPolicy 实体。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

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
PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
SLA_STAGES = ("NONE", "WARN", "BREACH")


class SlaPolicy(Base):
    """SLA template (action_type × priority × optional scope) for handling orders."""

    __tablename__ = "sla_policy"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    warn_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    breach_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            f"action_type IN ({', '.join(repr(a) for a in ACTION_TYPES)})",
            name="ck_sla_action_type",
        ),
        CheckConstraint(
            f"priority IN ({', '.join(repr(p) for p in PRIORITIES)})",
            name="ck_sla_priority",
        ),
        CheckConstraint("warn_minutes > 0", name="ck_sla_warn_pos"),
        CheckConstraint("breach_minutes > warn_minutes", name="ck_sla_breach_gt_warn"),
        # NULL scope 视为全局默认；PG 默认 NULLS DISTINCT 使多 NULL 行不冲突，
        # 种子幂等性依赖 ON CONFLICT DO NOTHING（不指定目标列，命中任意 unique index）。
        UniqueConstraint(
            "action_type",
            "priority",
            "scope_type",
            "scope_id",
            name="uniq_sla_policy_scope",
        ),
        Index("idx_sla_policy_default", "action_type", "is_default"),
    )
