"""回路数据健康标记（运维圈选用，2026-10-10）。

独立于 fitness（评估适用性）体系：本表承载"数据/组态形态异常"类标记
（如 SP 跟随 PV 的位号错配嫌疑），由定时任务按统计窗口全量重建，
供回路配置页筛选圈选 → 批量处置（停用等）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoopHealthFlag(Base):
    """回路健康标记。一行 = 一个回路 × 一种标记类型。"""

    __tablename__ = "loop_health_flag"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    loop_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="回路 ID（loop_ledger.id）"
    )
    flag_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="标记类型：SP_FOLLOWS_PV=自动时段 SP 跟随 PV 嫌疑",
    )
    evidence: Mapped[dict | None] = mapped_column(
        JSONB, comment="判定证据（比值/样本数/窗口等，展示与人工核对用）"
    )
    suspected_cascade: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="疑似未登记串级（当前 Cascade 模式，随动属正常，默认不进圈选）",
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="本次判定时间",
    )

    __table_args__ = (
        UniqueConstraint("loop_id", "flag_type", name="uniq_lhf_loop_flag"),
        Index("idx_lhf_flag_type", "flag_type"),
    )
