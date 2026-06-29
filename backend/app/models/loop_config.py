"""Loop configuration models (重构方案 v1.2).

对齐 GB/T 44693.2-2024 的 3 张配置表：
- ``LoopModeMapping`` — 投用定义（MODE 值到控制模式的映射）
- ``LoopTypeWeight`` — 回路类型权重（附表1，用于回路级综合评分）
- ``LoopLevelWeight`` — 回路级别权重（附表2，用于装置级聚合加权）
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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoopModeMapping(Base):
    """回路投用定义 — MODE 值到控制模式的映射。

    用于实时自控率/有效自控率/投用率计算，替代硬编码 {1,2,3}=自动。
    每个回路可配置多个 MODE 值的语义，由用户按 DCS 实际语义配置。
    """

    __tablename__ = "loop_mode_mapping"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联回路 ID",
    )
    mode_value: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="DCS 返回的 MODE 值（整数）"
    )
    mode_label: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="控制模式：AUTO/CAS/REMOTE/APC/MANUAL",
    )
    is_auto: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否算自动控制（AUTO/CAS/REMOTE/APC 为 True）",
    )
    is_effective: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否算有效自动（不饱和的自动模式为 True）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "mode_label IN ('AUTO', 'CAS', 'REMOTE', 'APC', 'MANUAL')",
            name="ck_loop_mode_mapping_label",
        ),
        Index("uk_loop_mode_mapping_loop_mode", "loop_id", "mode_value", unique=True),
        Index("idx_loop_mode_mapping_loop_id", "loop_id"),
    )


class LoopTypeWeight(Base):
    """回路类型权重 — 对齐 GB/T 44693.2-2024 附表1。

    用于回路级综合评分公式：P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R

    4 种回路类型：
    - STABLE（稳定型）：a=0.2, f=0.3, s=0.5 — 温度/压力控制
    - SLOW（慢速型）：a=0.3, f=0.1, s=0.6 — 缓慢调节
    - FAST（快速型）：a=0.2, f=0.5, s=0.3 — 副回路/速度控制
    - LOGIC（逻辑型）：a=0.0, f=0.5, s=0.6 — 逻辑规则控制
    """

    __tablename__ = "loop_type_weight"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        comment="回路类型：STABLE/SLOW/FAST/LOGIC",
    )
    type_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="类型名称（稳定型/慢速型/快速型/逻辑型）"
    )
    weight_a: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, comment="准确率权重 a")
    weight_f: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, comment="快速率权重 f")
    weight_s: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, comment="平稳率权重 s")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "loop_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC')",
            name="ck_loop_type_weight_type",
        ),
    )


class LoopLevelWeight(Base):
    """回路级别权重 — 对齐 GB/T 44693.2-2024 附表2。

    用于装置级聚合公式：装置平均性能评分 = Σ(w_i * P_i) / Σw_i

    3 个级别：
    - 1级（一级）：weight=3.0 — 决定性影响：负荷控制/联锁相关
    - 2级（二级）：weight=2.0 — 辅助保障：稳定性/设备安全
    - 3级（三级）：weight=1.0 — 次要辅助：维持辅助设备运行
    """

    __tablename__ = "loop_level_weight"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, comment="回路级别：1/2/3"
    )
    level_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="级别名称（一级/二级/三级）"
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), nullable=False, comment="级别权重：3.0/2.0/1.0"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "level IN (1, 2, 3)",
            name="ck_loop_level_weight_level",
        ),
    )
