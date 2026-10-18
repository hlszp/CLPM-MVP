"""``wb_cache_log`` — BFF 缓存命中/失效日志（性能调优观察，保留 7 天）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WbCacheLog(Base):
    """Workbench BFF cache hit/miss + build time log (rotated daily, keep 7 days)."""

    __tablename__ = "wb_cache_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(200), nullable=False)
    hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    build_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)  # A-01..A-13 code
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_wbcl_created_desc", text("created_at DESC")),
        Index("idx_wbcl_key", "cache_key", "created_at"),
        Index("idx_wbcl_endpoint_hit", "endpoint", "hit"),
    )
