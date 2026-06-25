"""``clpm_metric_data_requirement`` model — 指标数据需求契约.

定义每个性能/诊断指标对底层数据的契约化需求声明，包括所需 Tag 组、
采样策略、质量策略、Metric Validity Mask 表达式、聚合策略、依赖关系等。
该表为算法服务与数据采集层之间的"数据契约"，支撑数据血缘追溯
（与 ``kpi_snapshot_hourly.data_lineage`` 字段配合）与指标可信度判定。

设计依据：DDS §2.15, ADS §2/§8, 算法说明 §3.5-3.6
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ClpmMetricDataRequirement(Base):
    """Metric data requirement contract (DDS §2.15).

    每个指标的数据获取与预处理需求契约，DataPlanner 据此合并查询计划、
    选择采样策略与质量策略，并生成各指标的 Metric Validity Mask。

    设计依据：DDS §2.15, ADS §2, 算法说明 §3.5(tagGroup)/§3.6(契约)
    """

    __tablename__ = "clpm_metric_data_requirement"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 指标代码（如 accuracy_rate / fast_response_rate），与数据库列名一致
    metric_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 所需 Tag 组（BASE / PVOP_HF / OP_HF / MODE_HF / QUALITY_HF / CONFIG）
    tag_group: Mapped[str] = mapped_column(String(20), nullable=False)
    # 所需 Tag 角色列表，如 ["pv","sp","op","mode"]
    tags: Mapped[list] = mapped_column(JSONB, nullable=False)
    # 采样策略：BY_CONTROL_TYPE（按控制类型降采样）/ FIXED_1S / NONE
    sampling_strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    # 质量策略：KEEP_ALL_WITH_VALIDITY / KEEP_ALL / NONE
    quality_policy: Mapped[str] = mapped_column(String(30), nullable=False)
    # Metric Validity Mask 表达式，如 "pv_valid && sp_valid"
    mask_expression: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 聚合策略：LAST / MEAN / RATIO 等
    aggregation_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 依赖的其他指标，如 ["settling_time","ideal_settling_time"]
    depends_on: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'v1'"), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), nullable=True
    )

    __table_args__ = (
        {"comment": "指标数据需求契约：定义每个指标的数据获取和预处理需求"},
    )
