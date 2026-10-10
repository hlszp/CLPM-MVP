"""``calculation_result_record`` 追加式结果账本模型（P1-05）.

契约来源：《系统改造优化-2026-10-10/01-系统改造优化方案》§3.1 +
《执行状态/P0-02-数值语义冻结-v2》§2（字段级契约，DEC-10 已裁决按推荐）。

设计要点：
- 追加式（append-only）：record 只增不改；唯一键
  ``(logical_run_id, object_kind, object_id, ts_start, ts_end)`` 保证同
  logicalRunId 重试幂等（冲突即复用），显式重评换新 logicalRunId 追加新 record。
- 现有 hourly/latest/node/custom 四投影继续作为"当前投影"，新增可空
  ``result_record_id`` 指向本表；写 record 与切换投影同事务。
- 存量行由迁移归档为本表 record，algorithmVersion/configRevision 未知时
  标 ``LEGACY_UNVERIFIABLE``（不伪补版本）；旧表 ID → recordId 映射存
  ``calculation_result_legacy_map``，历史详情按旧 ID 读经映射兼容。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 兼容读：旧表 ID → recordId 映射覆盖的投影表名（迁移归档与读取共用）
LEGACY_TABLE_HOURLY = "kpi_snapshot_hourly"
LEGACY_TABLE_LATEST = "loop_confidence_latest"
LEGACY_TABLE_NODE_HOURLY = "kpi_node_snapshot_hourly"


class CalculationResultRecord(Base):
    """不可变计算结果记录（LOOP=回路评估 / NODE=节点聚合）。

    ``payload`` JSONB 保存完整结果：
    - LOOP：快照全部 KPI 字段 + 12 子指标明细（metrics_detail）+ 来源标注；
    - NODE：聚合字段 + ``loopRecordIds``（实际参与聚合的 loop recordId 列表）
      + ``metricDenominators``（各指标有效分母）。
    """

    __tablename__ = "calculation_result_record"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 逻辑运行身份：同任务重试复用；显式重评生成新值
    logical_run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    object_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    object_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 显式重评来源记录（FAILED 终态后再计算视为显式重评，不覆盖失败证据）
    source_record_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("calculation_result_record.id", ondelete="SET NULL"),
        nullable=True,
    )
    algorithm_version: Mapped[str] = mapped_column(String(50), nullable=False)
    config_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    # P1 期可空（输入包元数据表 P2-03 落地）；可空即"不可完整复现"
    dataset_snapshot_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "object_kind IN ('LOOP', 'NODE')",
            name="ck_calc_result_record_object_kind",
        ),
        CheckConstraint(
            "status IN ('COMPLETED', 'FAILED')",
            name="ck_calc_result_record_status",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_calc_result_record_window"),
        UniqueConstraint(
            "logical_run_id",
            "object_kind",
            "object_id",
            "ts_start",
            "ts_end",
            name="uq_calc_result_record_run_object_window",
        ),
        Index("idx_calc_result_record_object_window", "object_kind", "object_id", "ts_start"),
        {"comment": "追加式计算结果账本（不可变 record，P1-05）"},
    )


class CalculationResultLegacyMap(Base):
    """旧投影表行 ID → 结果账本 recordId 映射（迁移归档产物，兼容读）."""

    __tablename__ = "calculation_result_legacy_map"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    legacy_table: Mapped[str] = mapped_column(String(40), nullable=False)
    legacy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    record_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("calculation_result_record.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("legacy_table", "legacy_id", name="uq_calc_legacy_map_table_id"),
        Index("idx_calc_legacy_map_record_id", "record_id"),
        {"comment": "旧投影表 ID→结果账本 recordId 映射（P1-05 迁移归档）"},
    )


__all__ = [
    "CalculationResultLegacyMap",
    "CalculationResultRecord",
    "LEGACY_TABLE_HOURLY",
    "LEGACY_TABLE_LATEST",
    "LEGACY_TABLE_NODE_HOURLY",
]
