"""``unit_kpi_summary`` model — 装置级 KPI 汇总表.

承载装置（plant_node 中 type=UNIT 的节点）级 KPI 汇总快照，按周期对装置下
所有启用回路的 ``kpi_snapshot_hourly`` 进行加权聚合。

**装置级汇总仅基于标准任务（kpi_snapshot_hourly），自定义任务
（kpi_snapshot_custom）不参与聚合。**

设计依据：DDS §2.17, 算法说明 §4.11, PRD §4.3.3
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UnitKpiSummary(Base):
    """Unit-level KPI summary (DDS §2.17).

    装置级 KPI 汇总快照，按回路级别权重（一级=3, 二级=2, 三级=1）
    加权平均聚合。INCONCLUSIVE 回路不参与聚合，单独统计。

    设计依据：DDS §2.17, 算法说明 §4.11, PRD §4.3.3
    """

    __tablename__ = "unit_kpi_summary"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    node_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("plant_node.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    avg_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    auto_mode_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fast_response_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good_value_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    oscillation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    saturation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_loops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_loops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inconclusive_loops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("node_id", "snapshot_time", name="uq_unit_kpi_summary_node_time"),
        Index("ix_unit_kpi_summary_node_time", "node_id", "snapshot_time"),
        {"comment": "装置级KPI汇总表：仅基于标准任务（kpi_snapshot_hourly）聚合，自定义任务不参与"},
    )
