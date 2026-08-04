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
    # 指标代码（如 accuracy_rate / fast_rate），与数据库列名一致
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
    # .. deprecated:: v6.2 可信度统一 Phase 2（P2-5 / D3）
    #     改用 ``required_tags`` 声明（即 ``tags`` 字段）判定可计算性。
    #     保留此列做兼容过渡，assessor 仍可解析，但新代码应使用 ``required_tags``。
    mask_expression: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 聚合策略：LAST / MEAN / RATIO 等
    aggregation_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 依赖的其他指标，如 ["settling_time","ideal_settling_time"]
    depends_on: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'v1'"), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=True
    )

    __table_args__ = ({"comment": "指标数据需求契约：定义每个指标的数据获取和预处理需求"},)

    @property
    def required_tags(self) -> list[str]:
        """指标可计算性所需的核心 tag 声明（可信度统一 Phase 2 / D3）.

        返回 ``tags`` 字段（如 ``["pv","sp"]``），作为 ``mask_expression`` 的
        声明式替代。assessor 按 ``required_tags`` 求交集判定可计算性，
        可读性与可维护性优于布尔表达式字符串。

        Returns:
            所需 tag 列表（空列表表示无 tag 依赖，如 CONFIG 组指标）
        """
        return list(self.tags) if self.tags else []
