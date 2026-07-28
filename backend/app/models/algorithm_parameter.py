"""``algorithm_parameter`` model — 指标算法参数配置（P0-B 配置化基础设施）.

存储每个指标在每个控制类型（STABLE/SLOW/FAST/LOGIC）下的算法参数覆盖。
配置合并链（三层）：
    1. 算法默认值（计算器内硬编码常量，如 ``SIMILARITY_THRESHOLD = 0.4``）
    2. ``algorithm_parameter`` 表（本表，系统级默认覆盖，按 control_type 分组）
    3. ``metric_config.threshold`` JSONB（指标级覆盖，已有字段复用）

热路径（指标计算器）通过 ``app.services.algorithm_config.get_algorithm_params()``
读取合并后的进程内缓存，不查库。

设计依据：HiaMonitor 借鉴重构计划评审报告 P0-B, P0-3, P1-2
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AlgorithmParameter(Base):
    """指标算法参数配置（按 metric_code × control_type 分组）.

    Attributes:
        id: UUID 主键
        metric_code: 指标代码（关联 ``metric_config.metric_code``），如 ``oscillation_rate``
        control_type: 控制类型（STABLE/SLOW/FAST/LOGIC，对齐 ``LoopLedger.control_type``）
        params: 算法参数键值对 JSONB，如 ``{"similarity_threshold": 0.4, "min_ratio": 0.05}``
        description: 参数说明
        is_enabled: 是否启用（False 时回落算法默认值）
        updated_by: 更新人
        updated_at: 更新时间
        version: 版本号（乐观锁）
    """

    __tablename__ = "algorithm_parameter"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    metric_code: Mapped[str] = mapped_column(String(50), nullable=False)
    control_type: Mapped[str] = mapped_column(String(20), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "control_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC')",
            name="ck_algorithm_parameter_control_type",
        ),
        UniqueConstraint(
            "metric_code",
            "control_type",
            name="uk_algorithm_param_code_type",
        ),
    )


__all__ = ["AlgorithmParameter"]
