"""``calculation_dataset_snapshot`` 不可变输入包元数据模型（P2-03）.

契约来源：《系统改造优化-2026-10-10/01-系统改造优化方案》§3.2 +
《执行状态/P0-02-数值语义冻结-v2》§3（字段级契约，DEC-10 已裁决按推荐）+
《执行状态/P2-03-辨识证据契约冻结.md》（07 号文六层语义，schemaVersion
由 P2-03 Owner 定稿=主版本 1 版本化扩展，见交接文档）。

设计要点：
- 现有 ``datasetRef`` 只表示绑定/改绑身份（logical_wide_builder 按
  loop+绑定+边界摘要生成），**不是数值输入身份**；本表把数值输入身份
  落到 ``inputHash``（NPZ 数组 + manifest 全部内容 SHA256，volatile
  字段除外）——相同绑定但值/质量/mask 改变必须得到不同 inputHash。
- 不可变包文件（``{snapshotId}.npz`` + ``{snapshotId}.manifest.json``）
  存本地持久化共享目录（``CLPM_CALC_SNAPSHOT_DIR``，未配置时落
  ``data/calc-snapshots``）；写入次序=先原子写文件（临时名+fsync+
  rename+核 hash）再提交本表引用；文件不开放静态目录下载，访问走
  带 scope 校验的服务。
- 保留契约（DEC-10）：NORMAL 默认 90 天（``expires_at``）；被结果
  record/方案/审核证据引用的包 FROZEN 永不清理；解除引用且过期后才
  可清理；清理先标 ``cleaned_at`` 再删文件。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RETENTION_NORMAL = "NORMAL"
RETENTION_FROZEN = "FROZEN"

# 包种类（manifest.kind 同值）：重放按种类分派计算内核
KIND_KPI_GRID = "KPI_GRID"  # KPI/评估：DataBlock 网格（缺口=逐槽 mask，不删点）
KIND_IDENTIFICATION = "IDENTIFICATION"  # 辨识：完整分析输入（多段+审计目录）
KIND_DIAGNOSIS_RAW = "DIAGNOSIS_RAW"  # 诊断：宽表原始序列+质量码


class CalculationDatasetSnapshot(Base):
    """不可变计算输入包元数据（一行对应一个 NPZ+manifest 包）.

    ``created_by_record_id`` 是创建引用：KPI/节点链为结果账本 recordId，
    辨识链为 tuning_record.id，诊断链为 diagnosis_run.id——三类宿主表
    不同，故不设硬外键（引用完整性由 GC 前的 find_referencing_records
    跨表核查保证，见 services/calc_snapshot.py）。
    """

    __tablename__ = "calculation_dataset_snapshot"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 绑定身份（≠数值身份）：logical_wide_builder 的 dataset_ref 或显式合成
    dataset_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    # 内容 SHA256（NPZ 数组 + manifest 全部内容，volatile 字段除外）
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # manifest 结构版本（主版本 1 版本化扩展；读者按主版本门禁）
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # 包种类（重放分派；同 manifest.kind）
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # 创建引用（跨表：结果账本 record / tuning_record / diagnosis_run；无硬 FK）
    created_by_record_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    # 保留类别与固定引用（DEC-10）
    retention_class: Mapped[str] = mapped_column(
        String(8), nullable=False, default=RETENTION_NORMAL
    )
    frozen_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    frozen_by_record_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    # NORMAL 保留期终点（创建 + 90 天）；FROZEN 行忽略
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 作用域（访问/下载继承回路 scope；包文件不开放静态目录）
    loop_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "retention_class IN ('NORMAL', 'FROZEN')",
            name="ck_calc_dataset_snapshot_retention",
        ),
        CheckConstraint(
            "kind IN ('KPI_GRID', 'IDENTIFICATION', 'DIAGNOSIS_RAW')",
            name="ck_calc_dataset_snapshot_kind",
        ),
        CheckConstraint(
            "retention_class <> 'FROZEN' OR frozen_reason IS NOT NULL",
            name="ck_calc_dataset_snapshot_frozen_reason",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_calc_dataset_snapshot_window"),
        # 同一创建引用重试（同任务同窗口同内容）复用同一包行（幂等）；
        # 显式重评=新创建引用 → 新行（即使内容相同，按 v2 重评保存实际
        # 输入身份的独立归属）
        UniqueConstraint(
            "dataset_ref",
            "input_hash",
            "ts_start",
            "ts_end",
            "created_by_record_id",
            name="uq_calc_dataset_snapshot_content_ref",
        ),
        Index("idx_calc_dataset_snapshot_input_hash", "input_hash"),
        Index("idx_calc_dataset_snapshot_retention", "retention_class", "expires_at"),
        Index("idx_calc_dataset_snapshot_loop", "loop_id"),
        {"comment": "不可变计算输入包元数据（P2-03，NPZ+manifest 内容寻址）"},
    )


__all__ = [
    "KIND_DIAGNOSIS_RAW",
    "KIND_IDENTIFICATION",
    "KIND_KPI_GRID",
    "RETENTION_FROZEN",
    "RETENTION_NORMAL",
    "CalculationDatasetSnapshot",
]
