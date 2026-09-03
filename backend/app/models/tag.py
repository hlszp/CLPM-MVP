"""``tag_registry`` model — AAS-synced OPC tag registry."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TagRegistry(Base):
    """AAS Tag registry — OPC tag metadata synced from AAS (DDL §4)."""

    __tablename__ = "tag_registry"

    # 回路 Excel 导入自动创建 tag 时的占位描述（机器写入，非人工维护值）；
    # AAS 同步时视为"未人工维护"，允许被 AAS 真实描述覆盖
    # （aas_sync WS-C 7-11 防回冲规则的例外放行，写入方：loop.py 导入）
    IMPORT_PLACEHOLDER_DESC = "[Excel 导入自动创建，未通过 AAS 同步，元数据待补全]"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tag_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag_type: Mapped[str] = mapped_column(String(20), nullable=False)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_linked: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    # 扩展字段：量程/单位/测点类型/TDengine tag ID
    range_min: Mapped[float | None] = mapped_column(Float, nullable=True, comment="量程下限")
    range_max: Mapped[float | None] = mapped_column(Float, nullable=True, comment="量程上限")
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="工程单位")
    measure_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="测点类型: TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER",
    )
    tdengine_tag_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="TDengine 中的 tag ID"
    )

    __table_args__ = (
        CheckConstraint(
            "tag_type IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D', 'OTHER')",
            name="ck_tag_registry_type",
        ),
        CheckConstraint(
            "quality IS NULL OR quality IN ('GOOD', 'BAD', 'UNCERTAIN')",
            name="ck_tag_registry_quality",
        ),
        CheckConstraint(
            "measure_type IS NULL OR measure_type IN "
            "('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER')",
            name="ck_tag_registry_measure_type",
        ),
        UniqueConstraint("tag_name", name="uk_tag_registry_tag_name"),
        Index("idx_tag_registry_tag_name", "tag_name"),
        Index("idx_tag_registry_tag_type", "tag_type"),
        Index("idx_tag_registry_is_linked", "is_linked"),
    )
