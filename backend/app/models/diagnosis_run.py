"""诊断运行模型（MVP v2 诊断模块）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §8
一次回路诊断 = 一条完整结论（含全算子结果 + 原因分类 + 建议 + 证据波形快照）。
与旧 diagnosis_result（一行一标签）语义不同，旧表按 MVP 约束保留不动。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DiagnosisRun(Base, TimestampMixin):
    """单回路一次诊断运行（自包含可追溯）。"""

    __tablename__ = "diagnosis_run"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, server_default="system")
    #: 触发类型（§12 自动诊断）：MANUAL 手动 / SCHEDULED 分级定时 / EVENT 预警事件
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="MANUAL")
    time_window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    time_window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    operator_group: Mapped[str] = mapped_column(String(8), nullable=False, server_default="full")

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="RUNNING")
    data_gate: Mapped[dict] = mapped_column(JSONB, nullable=True)
    operator_results: Mapped[dict] = mapped_column(JSONB, nullable=True)
    fusion_results: Mapped[dict] = mapped_column(JSONB, nullable=True)
    symptom_tags: Mapped[dict] = mapped_column(JSONB, nullable=True)
    primary_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    secondary_categories: Mapped[dict] = mapped_column(JSONB, nullable=True)
    pending_review: Mapped[dict] = mapped_column(JSONB, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rationale: Mapped[dict] = mapped_column(JSONB, nullable=True)
    recommendations: Mapped[dict] = mapped_column(JSONB, nullable=True)
    evidence_charts: Mapped[dict] = mapped_column(JSONB, nullable=True)
    #: 诊断指标汇总（方案 A，2026-08-19）：诊断时间窗内 KPI 快照均值 + 算子特征，
    #: 统一 0~100 口径（坏值率/饱和率/振荡率/粘滞系数/稳定时间/行程指数 + 6 正向率）
    metric_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    threshold_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # --- 人工复核（§9.3 复核闭环，2026-08-18）---
    #: 复核状态：PENDING 待复核 / REVIEWED 已复核
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    #: 复核结论（多选，存原因分类代码数组，与 primary_category 同域）
    review_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')",
            name="ck_diagnosis_run_status",
        ),
        CheckConstraint(
            "review_status IN ('PENDING', 'REVIEWED')",
            name="ck_diagnosis_run_review_status",
        ),
        CheckConstraint(
            "primary_category IS NULL OR primary_category IN "
            "('TUNING', 'VALVE', 'INSTRUMENT', 'COMMUNICATION', 'PROCESS', "
            "'UTILIZATION', 'DESIGN', 'DATA_INSUFFICIENT')",
            name="ck_diagnosis_run_category",
        ),
        CheckConstraint(
            "severity IS NULL OR severity IN ('HIGH', 'MEDIUM', 'LOW')",
            name="ck_diagnosis_run_severity",
        ),
        CheckConstraint(
            "trigger_type IN ('MANUAL', 'SCHEDULED', 'EVENT')",
            name="ck_diagnosis_run_trigger_type",
        ),
        Index("idx_diagnosis_run_loop_created", "loop_id", "created_at"),
        Index("idx_diagnosis_run_category", "primary_category"),
        Index("idx_diagnosis_run_task", "task_id"),
        {
            "comment": "诊断运行记录（MVP v2：一次诊断一条完整结论）",
        },
    )
