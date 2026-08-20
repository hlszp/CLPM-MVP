"""``plant_node`` model — factory → area → unit 三层结构."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlantNode(Base):
    """Plant node — 三层工厂结构（DDL §2）.

    支持三层结构：FACTORY（工厂）→ AREA（装置/车间）→ UNIT（单元）
    回路挂在 UNIT 节点下。
    """

    __tablename__ = "plant_node"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("plant_node.id", ondelete="RESTRICT"), nullable=True
    )
    is_kpi_enabled: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    # SVC-10 位号触发监控：配置后查询 TDengine 最新值，
    # 等于 monitor_trigger_value 时该节点下回路应监控
    monitor_tag_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tag_registry.id", ondelete="RESTRICT"),
        nullable=True,
        comment="位号触发监控的位号 ID（NULL 表示默认监控）",
    )
    monitor_trigger_value: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment='触发监控的位号值（如 "true"/"1"/"ON"）',
    )
    source_node_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="AAS AreaNode Id 同步来源标记（有值=AAS 同步节点，本地改名会被下次同步覆盖）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('FACTORY', 'AREA', 'UNIT')",
            name="ck_plant_node_type",
        ),
        Index("idx_plant_node_monitor_tag_id", "monitor_tag_id"),
        Index("idx_plant_node_source_node_id", "source_node_id"),
    )
