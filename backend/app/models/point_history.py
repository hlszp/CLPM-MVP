"""测点子表重构 PG 元数据模型（P1）.

设计依据：设计文档 §4.2（六实体最小信息/约束；允许合并职责相同的实体，
本实现按职责分立六表，各表列保持最小）+ §4.3（重复、迟到与写入确认）。

时间口径：表内全部 TIMESTAMPTZ（aware UTC）；
- 绑定/覆盖/布局区间一律**半开 [a, b)**（设计 §5.3）；
- valid_to 为 NULL 表示生效中（开放区间右端）。

生命周期（设计 §4.2）：已确认批次可归档合并、覆盖区间按相同语义合并、
锚点低频维护——由 repository 的合并/归档函数实现，不靠删表。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LoopTagBindingHistory(Base):
    """回路-角色-测点 历史绑定（T05）.

    现行绑定仍以 loop_tag_mapping 为准（视图/API 不变）；本表记录生效区间，
    与 loop_tag_mapping 的变更在**同一 PG 事务**内推进（P1-2 契约由
    repository.record_binding_change 提供事务封装，P3 接入 loop.py）。

    约束：同 (loop_id, tag_role) 的生效区间不得重叠（EXCLUDE，需 btree_gist）。
    """

    __tablename__ = "loop_tag_binding_history"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_ltbh_valid_range"),
        Index("ix_ltbh_loop_role", "loop_id", "tag_role"),
        Index("ix_ltbh_tag", "tag_id"),
        UniqueConstraint("loop_id", "tag_role", "valid_from", name="uq_ltbh_from"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("loop_ledger.id", ondelete="CASCADE"), nullable=False
    )
    tag_role: Mapped[str] = mapped_column(String(20), nullable=False)
    tag_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tag_registry.id", ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 映射代次：同一绑定连续区间内递增；改绑/回绑产生新行
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 依据：MVP_INIT（初次建史）/ REBIND（改绑）/ UNBIND（解绑，tag_id=哨兵）/ RESTORE
    basis: Mapped[str] = mapped_column(String(32), nullable=False, default="MVP_INIT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class HistoryCoverageSegment(Base):
    """历史覆盖区间（按 source 会话/点作用域，半开区间，T06）.

    - 会话作用域（point_id IS NULL, session_id 非空）：一段连续采集会话的
      已确认覆盖（合并用）；
    - 点作用域（point_id 非空）：单点导入/补数的确认覆盖。
    状态：confirmed（已确认覆盖）/ pending（写入未确认，builder 不得当覆盖）/ gap
    （已知未知窗口——队列满/租约丢失/停机丢失登记，禁止默填为正常）。
    """

    __tablename__ = "history_coverage_segment"
    __table_args__ = (
        CheckConstraint("seg_end > seg_start", name="ck_hcs_half_open"),
        CheckConstraint("(point_id IS NULL) <> (session_id IS NULL)", name="ck_hcs_scope"),
        Index("ix_hcs_point_window", "point_id", "seg_start"),
        Index("ix_hcs_session", "session_id", "seg_start"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    point_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tag_registry.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seg_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seg_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 绑定/订阅集合版本（会话作用域登记当时集合指纹）
    binding_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("history_write_batch.batch_id", ondelete="SET NULL"),
        nullable=True,
    )
    source_task: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class HistoryLayoutManifest(Base):
    """本地布局清单（T10）：精确决定某窗口用 legacy 还是 point 读取.

    scope: global / loop / source；layout: legacy / shadow / point。
    半开区间 [valid_from, valid_to)，valid_to=NULL 表示开放。
    """

    __tablename__ = "history_layout_manifest"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_hlm_range"),
        CheckConstraint("layout IN ('legacy', 'shadow', 'point')", name="ck_hlm_layout"),
        CheckConstraint("scope_type IN ('global', 'loop', 'source')", name="ck_hlm_scope"),
        Index("ix_hlm_scope", "scope_type", "scope_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="global")
    scope_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    layout: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy")
    # 数据/覆盖 revision（失效联动用）
    data_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    # 发布依据（迁移步骤/审批记录引用）
    basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class HistoryWriteBatch(Base):
    """写入批次生命周期（T06/T07）：pending → confirmed / partial / failed.

    TD 写入确认后推进 confirmed（设计 §4.3：元数据成功在 TD 确认后）；
    partial 记录分块结果与失败原因，供恢复重放。
    """

    __tablename__ = "history_write_batch"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'partial', 'failed', 'cancelled')",
            name="ck_hwb_status",
        ),
    )

    batch_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    # 来源任务（realtime session / import task id / backfill id）
    source_task: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="point_events")
    # 目标窗口（点事件批次为其覆盖意图窗口）
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 输入摘要（幂等重放校验）
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stats: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )  # {rows, conflicts, skipped}
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_info: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class HistoryPointConflict(Base):
    """同点同 ts 不同 payload 的冲突登记（设计 §4.3：不得静默按到达顺序覆盖）.

    默认处理：保留既有事实（existing_payload），新事实登记待裁决；
    获授权 overwrite 才执行带备份的更正。
    """

    __tablename__ = "history_point_conflict"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'resolved_skip', 'resolved_overwrite')",
            name="ck_hpc_status",
        ),
        UniqueConstraint("point_id", "ts_ms", "existing_hash", name="uq_hpc_point_ts"),
        Index("ix_hpc_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    point_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tag_registry.id", ondelete="CASCADE"), nullable=False
    )
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    existing_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    existing_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    new_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_task: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PointStateAnchor(Base):
    """测点状态锚点（设计 §4.2/§5.5）：保留期边界常值的可信依据.

    锚点携带**原 sourceTime** 与覆盖依据，不伪造新变化事件；
    builder 在窗口前无事件但存在适用锚点（anchor_ts 覆盖到窗口起点）
    时可用其作为初始状态。
    """

    __tablename__ = "point_state_anchor"
    __table_args__ = (
        CheckConstraint("quality_class IN (1, 0, -1)", name="ck_psa_quality"),
        Index("ix_psa_point_ts", "point_id", "anchor_ts"),
    )

    point_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tag_registry.id", ondelete="CASCADE"), primary_key=True
    )
    anchor_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float | None] = mapped_column(nullable=True)
    quality_raw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_class: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_schema: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # 覆盖依据：该锚点适用的已知覆盖边界（半开右端；anchor 可用到该时刻）
    coverage_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
