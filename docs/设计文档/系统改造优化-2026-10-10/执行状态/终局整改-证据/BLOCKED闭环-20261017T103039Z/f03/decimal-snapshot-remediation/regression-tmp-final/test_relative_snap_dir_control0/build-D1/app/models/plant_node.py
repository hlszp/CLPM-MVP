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
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlantNode(Base):
    """Plant node — 三层工厂结构（DDL §2）.

    支持三层结构：FACTORY（工厂）→ AREA（装置/车间）→ UNIT（单元）
    回路挂在 UNIT 节点下。

    字段说明：
    - ``source_node_id``: AAS AreaNode Id 同步来源标记（有值=AAS 同步节点，
      本地改名会被下次同步覆盖，主数据语义；NULL=本地维护）
    - ``sort_order``: 展示排序（同级按 sort_order → name 排序；AAS 同步
      节点取 AAS Id 映射，本地节点手工调整）
    - ``updated_by``: 最后操作人（手工 CRUD 为用户名，AAS 同步为 "aas:sync"，
      Excel 导入为 "import:{用户名}"）
    """

    __tablename__ = "plant_node"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("plant_node.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_kpi_enabled: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    source_node_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="AAS AreaNode Id 同步来源标记（有值=AAS 同步节点，本地改名会被下次同步覆盖）",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="同级展示排序（小值在前，同值按名称）",
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="最后操作人（用户名 / aas:sync / import:用户名）",
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
        # 同父重名唯一约束（parent_id 为 NULL 的根节点用固定 UUID 归一化，
        # 保证多个工厂根节点之间也不重名）
        Index(
            "uq_plant_node_parent_name",
            text("COALESCE(parent_id::text, '00000000-0000-0000-0000-000000000000')"),
            text("name"),
            unique=True,
        ),
        Index("idx_plant_node_source_node_id", "source_node_id"),
    )
