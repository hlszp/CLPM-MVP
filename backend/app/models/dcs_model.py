"""DCS 型号模型（DcsModel）.

对齐 DDS §3.1，回路配置只关联到型号（全局唯一 code），
由型号确定 MODE 映射关系（dcs_mode_mapping）。

设计要点：
- ``code`` 全局唯一（如 hollysys-macs / supcon-ecs700）
- ``vendor_id`` 关联到品牌
- loop_ledger.dcs_model_id 关联到本表
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DcsModel(Base, TimestampMixin):
    """DCS 型号（全局唯一）.

    回路配置（loop_ledger.dcs_model_id）只关联到具体型号，
    型号 code 全局唯一，便于跨品牌统一管理。

    MODE 映射通过 dcs_mode_mapping 表查询：
    - dcs_mode_mapping.dcs_model_id = 本表.id → 该型号的实际 MODE 值映射
    - dcs_mode_mapping.dcs_model_id IS NULL → 本系统默认映射（1:1）
    """

    __tablename__ = "dcs_model"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    vendor_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("dcs_vendor.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属品牌 ID",
    )
    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="型号代码（全局唯一）：如 hollysys-macs / supcon-ecs700",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="型号名称"
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序权重"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否启用"
    )

    __table_args__ = (
        Index("idx_dcs_model_vendor", "vendor_id"),
        Index("idx_dcs_model_sort", "sort_order"),
    )


__all__ = ["DcsModel"]
