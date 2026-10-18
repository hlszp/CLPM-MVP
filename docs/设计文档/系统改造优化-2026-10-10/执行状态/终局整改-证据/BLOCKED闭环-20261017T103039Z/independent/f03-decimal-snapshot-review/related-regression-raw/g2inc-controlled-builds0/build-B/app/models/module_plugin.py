"""``module_plugin`` model — 工作台模块插件注册表 + 4 态状态机持久化。

替代 ``app/core/modules.py`` 静态字典：启动时若表空，从 MODULES 字典写入
8 条种子（monitor/assess/diagnosis/tuning/handling/reports/config/system）。
状态机 4 态：CORE 内置 · ENABLED 在线 · MAINTENANCE 维护中 · UNINSTALLED 未安装。
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

MODULE_STATUSES = ("CORE", "ENABLED", "MAINTENANCE", "UNINSTALLED")


class ModulePlugin(Base):
    """Module/plugin registry carrying 4-state lifecycle + version + audit trail."""

    __tablename__ = "module_plugin"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    module_key: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_core: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    dependencies: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # maintenance_window: {start_at: iso, end_at: iso, progress_pct: 0-100, message: str}
    maintenance_window: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_maintenance_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("module_key", name="uniq_module_plugin_key"),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in MODULE_STATUSES)})",
            name="ck_module_plugin_status",
        ),
        Index("idx_module_plugin_status", "status"),
        Index("idx_module_plugin_order", "order_index"),
    )
