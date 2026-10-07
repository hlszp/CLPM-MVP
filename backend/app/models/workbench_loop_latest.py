"""``workbench_loop_latest`` — 回路最新态快照预计算表（2026-10-07 驾驶舱 P1 根治）.

生产 1209 回路下，驾驶舱三个实时聚合接口慢（9~25s）的结构性根因是
per-loop latest 型查询（``kpi_snapshot_hourly`` / ``diagnosis_run`` 上的
DISTINCT ON + 双 LATERAL）每请求全量执行。本表由 Celery
``workbench-loop-latest@5min`` 全量重算，固化每回路两份 latest：

1. 最新小时快照的 fitness 面（等级/标签/三性列 + score + ts）——
   ``get_latest_fitness_per_loop`` 读表后全链路调用方零改动受益；
2. 最新诊断 run 摘要（未处置口径 top_symptom/terminal_cnt 预展开）——
   A-03 open_tags 与 A-04 diag_src 直接读表，消除双 LATERAL。

驾驶舱 5min 自动刷新语义下 5min 新鲜度足够；诊断调度门禁（00:30）同口径。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkbenchLoopLatest(Base):
    """Per-loop latest fitness snapshot + latest open diagnosis run digest."""

    __tablename__ = "workbench_loop_latest"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="回路 ID（loop_ledger.id）"
    )
    tag_name: Mapped[str | None] = mapped_column(String(64), comment="回路位号（冗余，免 join）")
    unit_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), comment="所属单元 plant_node.id"
    )
    unit_name: Mapped[str | None] = mapped_column(String(128), comment="单元名")
    factory_name: Mapped[str | None] = mapped_column(String(128), comment="装置名")

    # ---- 最新小时快照 fitness 面（kpi_snapshot_hourly DISTINCT ON ts_start）----
    score: Mapped[float | None] = mapped_column(Numeric(6, 2), comment="最新综合评分")
    fitness_level: Mapped[str | None] = mapped_column(String(8), comment="适用性等级 L0~L4")
    fitness_tags: Mapped[dict | None] = mapped_column(JSONB, comment="适用性标签（原样）")
    fitness_detail: Mapped[dict | None] = mapped_column(JSONB, comment="适用性明细（原样）")
    assess_level: Mapped[str | None] = mapped_column(String(8), comment="可评估性档位")
    diagnose_level: Mapped[str | None] = mapped_column(String(8), comment="可诊断性档位")
    tune_level: Mapped[str | None] = mapped_column(String(8), comment="可整定性档位")
    snapshot_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最新快照 ts_start"
    )

    # ---- 最新诊断 run 摘要（SUCCESS 且 primary_category 非空的最新一条）----
    latest_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), comment="最新异常 run id"
    )
    latest_category: Mapped[str | None] = mapped_column(String(32), comment="主分类")
    latest_severity: Mapped[str | None] = mapped_column(String(16), comment="严重度")
    latest_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), comment="主结论置信度")
    latest_conclusion: Mapped[str | None] = mapped_column(Text, comment="结论摘要（rationale[0]）")
    latest_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="run created_at"
    )
    top_symptom: Mapped[dict | None] = mapped_column(
        JSONB, comment="最高置信症状（symptom_tags 展开 top1，预计算）"
    )
    terminal_cnt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="该 run 关联终态处置建议数（0=未处置）",
    )
    is_open: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="最新异常 run 未处置（terminal_cnt=0）",
    )

    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本行刷新时间",
    )

    __table_args__ = (
        UniqueConstraint("loop_id", name="uniq_wll_loop"),
        Index("idx_wll_open", "is_open", "latest_severity", "latest_run_at"),
        Index("idx_wll_unit", "unit_id"),
    )
