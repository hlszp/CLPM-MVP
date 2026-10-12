"""DCS PID 结构模板（DcsPidStructure）.

对齐 HiaMonitor 借鉴重构计划 §6（评审 P1-1：复用既有 DCS 体系，不新建独立表族）。

每个 DCS 型号至多一条 PID 结构定义（1:1），描述该型号 DCS 如何表示 PID 参数：
- ``p_type``：比例项是"比例度（PB）"还是"增益（Proportion）"
- ``i_unit`` / ``d_unit``：积分/微分时间单位（秒/分）
- ``d_filter_*``：微分滤波器配置

用途：回路整定时把 DCS 私有 PID 表示转换为标准形式（Kp / Ti / Td）。
MODE 映射不复用本表，仍走 ``dcs_mode_mapping``（绝不 JSONB 重复存储）。
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: 比例项类型枚举值（校验在 schema 层做 Literal 约束）
P_TYPE_PROPORTION = "PROPORTION"  # 增益 Kp
P_TYPE_PROPORTION_BAND = "PROPORTION_BAND"  # 比例度 PB = 100/Kp

#: 时间单位枚举值
UNIT_SECONDS = "SECONDS"
UNIT_MINUTES = "MINUTES"

#: PID 表示已知状态（P2-04，07 号文 §3.3）：UNKNOWN 为显式未知态——
#: 未绑定 DCS 型号/未配置结构/结构显式标记未知时，解析结果恒为 UNKNOWN，
#: 不得默认成标准 kp/ti/td（转换归 P3-03）。
PID_REPRESENTATION_KNOWN = "KNOWN"
PID_REPRESENTATION_UNKNOWN = "UNKNOWN"


class DcsPidStructure(Base, TimestampMixin):
    """DCS 型号 PID 结构模板（1:1 关联 ``dcs_model``）.

    - ``dcs_model_id`` 唯一：每个型号至多一条结构定义，缺失时整定按默认假设处理
    - 删除型号级联删除结构（ondelete=CASCADE，对齐 dcs_mode_mapping）
    """

    __tablename__ = "dcs_pid_structure"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    dcs_model_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("dcs_model.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="关联型号 ID（1:1，唯一）",
    )
    p_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=P_TYPE_PROPORTION,
        comment="比例项类型：PROPORTION(增益) / PROPORTION_BAND(比例度)",
    )
    i_unit: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=UNIT_SECONDS,
        comment="积分时间单位：SECONDS / MINUTES",
    )
    d_unit: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=UNIT_SECONDS,
        comment="微分时间单位：SECONDS / MINUTES",
    )
    d_filter_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否启用微分滤波",
    )
    d_filter_unit: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        default=None,
        comment="微分滤波单位：SECONDS / MINUTES（d_filter_enabled=False 时为 NULL）",
    )
    d_filter_multiplier: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="微分滤波是否为乘法因子（True=乘法，False=加法/独立时间常数）",
    )
    # P2-04（07 号文 §3.3）：PID 表示已知状态。UNKNOWN=该型号 PID 表示显式
    # 未知（p_type/i_unit/d_unit 仅占位，无转换语义；转换与算法消费归
    # P3-03，禁止把未知 DCS 型号默认成标准 kp/ti/td）。
    representation_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="KNOWN",
        comment="PID 表示状态：KNOWN(已知，字段有转换语义) / UNKNOWN(显式未知，禁止默认标准表示)",
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        # 反向查询索引：按型号查结构（unique 约束已建索引，此处显式命名便于运维）
        Index("idx_dcs_pid_structure_model", "dcs_model_id"),
        # CHECK 约束：启用微分滤波时单位必填（PostgreSQL 层强约束）
        CheckConstraint(
            "d_filter_enabled = false OR d_filter_unit IS NOT NULL",
            name="ck_dcs_pid_structure_filter_unit",
        ),
        # P2-04：PID 表示已知状态显式二态
        CheckConstraint(
            "representation_status IN ('KNOWN', 'UNKNOWN')",
            name="ck_dcs_pid_structure_representation_status",
        ),
    )


__all__ = [
    "DcsPidStructure",
    "PID_REPRESENTATION_KNOWN",
    "PID_REPRESENTATION_UNKNOWN",
    "P_TYPE_PROPORTION",
    "P_TYPE_PROPORTION_BAND",
    "UNIT_MINUTES",
    "UNIT_SECONDS",
]
