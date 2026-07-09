"""DCS 厂商品牌模型（DcsVendor）.

对齐 DDS §3.1 / 算法说明 §4.0.3，配置驱动的 DCS 品牌管理。

种子数据（5 家主流厂商）：
- hollysys   和利时
- supcon     中控
- honeywell  霍尼韦尔
- yokogawa   横河
- emerson    艾默生

品牌下挂多个型号（DcsModel），型号 code 全局唯一，
loop_ledger.dcs_model_id 关联到具体型号以确定 MODE 映射关系。
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DcsVendor(Base, TimestampMixin):
    """DCS 厂商品牌.

    一个品牌下可有多个型号（DcsModel），回路通过 dcs_model_id 关联到
    具体型号，再由 dcs_mode_mapping 表查得 MODE 值映射关系。
    """

    __tablename__ = "dcs_vendor"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        comment="品牌代码（唯一）：hollysys/supcon/honeywell/yokogawa/emerson",
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="品牌中文名"
    )
    name_en: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="品牌英文名"
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序权重（越小越靠前）"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否启用"
    )

    __table_args__ = (
        Index("idx_dcs_vendor_sort", "sort_order"),
    )


__all__ = ["DcsVendor"]
