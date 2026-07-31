"""``tuning_record`` model — loop tuning task records (Phase 2).

Phase 2.2 扩展（2026-07-28）：
- 新增 identify_method / data_source / confidence_level 等辨识元数据字段
- 新增 pid_candidates / candidate_results 支持多 PID 对比
- 新增 task_id 关联 Celery 异步任务
- 状态机对齐实现契约：DRAFT/RUNNING/IDENTIFIED/SIMULATED/COMPLETED/INCONCLUSIVE/ROLLED_BACK
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TuningRecord(Base):
    """Loop tuning record (DDL §12, Phase 2)."""

    __tablename__ = "tuning_record"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_type: Mapped[str] = mapped_column(String(20), nullable=False)
    model_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm: Mapped[str] = mapped_column(String(50), nullable=False)
    recommended_pid: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    simulation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fitting_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Phase 2.2 新增字段（辨识元数据）
    identify_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    time_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(12), nullable=True)
    confidence_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    excitation_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    residual_test_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Phase 2.3 新增字段（多 PID 对比）
    pid_candidates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidate_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Phase 2.2 新增字段（异步任务关联）
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # V62-P3-006：引用过程模型版本（可空，兼容旧 record 继续用自身 model_params）
    # 由 P3-005 一次性回填后，新辨识记录应携带 version_id；旧记录保持 NULL。
    process_model_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("process_model_version.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "model_type IN ('FOPDT', 'SOPDT', 'IPDT')",
            name="ck_tuning_record_model",
        ),
        CheckConstraint(
            "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC')",
            name="ck_tuning_record_algo",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'RUNNING', 'IDENTIFIED', 'SIMULATED', "
            "'COMPLETED', 'INCONCLUSIVE', 'ROLLED_BACK', "
            "'PENDING', 'APPLIED', 'VERIFIED')",
            name="ck_tuning_record_status",
        ),
        CheckConstraint(
            "identify_method IS NULL OR identify_method IN ("
            "'HISTORICAL_ARX', 'HISTORICAL_ARMAX', 'HISTORICAL_IV', "
            "'STEP_TWO_POINT', 'STEP_AREA', 'STEP_NLS')",
            name="ck_tuning_record_identify_method",
        ),
        CheckConstraint(
            "data_source IS NULL OR data_source IN ('HISTORY', 'STEP_EXPERIMENT', 'fallback_step')",
            name="ck_tuning_record_data_source",
        ),
    )
