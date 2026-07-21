"""``loop_ledger`` and ``loop_tag_mapping`` models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoopLedger(Base):
    """Loop ledger — core entity of the system (DDL §3)."""

    __tablename__ = "loop_ledger"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("plant_node.id", ondelete="RESTRICT"), nullable=True
    )
    score_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    last_aas_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PARTIAL")
    loop_type: Mapped[str | None] = mapped_column(
        String(20),
        default="OTHER",
        nullable=True,
        comment="回路类型: TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER",
    )
    # P2 #24: 控制类型（与 loop_type 业务类型独立，用于评分权重分类）
    control_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="控制类型: STABLE/SLOW/FAST/LOGIC（对齐 GB/T 44693.2-2024 附表1）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # S2-LOOP-004 新增字段
    score_weights: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 重构方案 v1.2 新增字段（对齐国标 GB/T 44693.2-2024）
    # v5.3 对齐 DDS v4.1：level → importance_level，NOT NULL，DEFAULT 2
    importance_level: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=2,
        comment="回路重要等级 1/2/3（默认2，对齐附表2，用于装置级聚合加权 1:3, 2:2, 3:1）",
    )
    # v5.3 新增：是否参与评估（对齐 FDS §5.2.3 / DDS v4.1）
    include_in_evaluation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否参与评估：true=参与综合评分与装置级聚合，false=仅计算单回路 KPI 不参与聚合",
    )
    modeattr_tag_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tag_registry.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "APC 识别位号 ID（保留字段，未参与 KPI 计算链路）："
            "原设计为 APC 系统识别位号，当该位号值为 program 时算自动控制；"
            "实际实现采用 MODE 信号值（1=Auto/2=Cascade/3=Remote）判定自控率，"
            "本字段仅作为元数据保留，供未来 APC 集成或运维追溯使用"
        ),
    )
    data_retention_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="数据保存周期（天），NULL 表示用系统默认",
    )
    # v6.1 新增字段：OP 输出限位（用于饱和率算法）
    # 设计依据：docs/设计文档/00-BASELINE/loop-range-and-output-limits-design-v1.0.md §5.1
    # 优先级：Loop 表字段 > OP Tag range_min/range_max > 默认值 0.0/100.0
    op_output_lower_limit: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "OP 输出下限位（用于饱和率算法）。"
            "NULL 时取 OP Tag range_min，再 NULL 时取默认值 0.0。"
            "应用层校验：>= OP Tag range_min 且 < op_output_upper_limit"
        ),
    )
    op_output_upper_limit: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "OP 输出上限位（用于饱和率算法）。"
            "NULL 时取 OP Tag range_max，再 NULL 时取默认值 100.0。"
            "应用层校验：<= OP Tag range_max 且 > op_output_lower_limit"
        ),
    )
    # v6.1 新增字段：关联 DCS 型号（用于 MODE 值映射）
    # 设计依据：用户需求"回路配置只对应型号，型号全局唯一"
    # NULL 表示使用本系统默认 MODE 映射（dcs_mode_mapping.dcs_model_id IS NULL）
    dcs_model_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("dcs_model.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联 DCS 型号 ID；NULL=使用本系统默认 MODE 映射",
    )
    # 理想稳态时间（秒），回路级手动配置（最高优先级，算法说明 §4.5）
    # NULL 时由 IdealSettlingTimeCalculator 按 模型计算 > 控制类型默认值 回退
    ideal_settling_time: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="理想稳态时间（秒），空则按控制类型默认值",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('READY', 'PARTIAL', 'INACTIVE')",
            name="ck_loop_ledger_status",
        ),
        CheckConstraint(
            "loop_type IN ('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', "
            "'ANALYSIS', 'SPEED', 'OTHER')",
            name="ck_loop_ledger_loop_type",
        ),
        CheckConstraint(
            "importance_level IN (1, 2, 3)",
            name="ck_loop_ledger_importance_level",
        ),
        Index("uk_loop_ledger_tag_name", "tag_name", unique=True),
        Index("idx_loop_ledger_unit_id", "unit_id"),
        Index("idx_loop_ledger_status", "status"),
        Index("idx_loop_ledger_tag_name", "tag_name"),
        Index("idx_loop_ledger_importance_level", "importance_level"),
    )


class LoopTagMapping(Base):
    """Loop ↔ Tag association — 7 OPC tag roles per loop (DDL §5)."""

    __tablename__ = "loop_tag_mapping"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    tag_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tag_registry.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tag_role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "tag_role IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D')",
            name="ck_loop_tag_mapping_role",
        ),
        Index("uk_loop_tag_mapping_loop_role", "loop_id", "tag_role", unique=True),
        Index("idx_loop_tag_mapping_loop_id", "loop_id"),
        Index("idx_loop_tag_mapping_tag_id", "tag_id"),
    )
