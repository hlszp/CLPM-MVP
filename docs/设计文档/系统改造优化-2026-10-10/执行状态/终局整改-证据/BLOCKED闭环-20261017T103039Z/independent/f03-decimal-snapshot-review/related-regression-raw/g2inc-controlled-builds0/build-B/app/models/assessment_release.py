"""``assessment_release_registry`` 评估发布代际登记表（P3-08a）.

契约来源：《系统改造优化-2026-10-10/10-评估算法改造增补方案》§3.5/§7.2 +
《11-三方案交叉复核与融入计划》§5 上线清单（EA-D05：验收后直接采用，
取消评估线上影子/节点试点前置）。

一行 = 一个已登记的评估发布代际（上线清单）：六要素
（assessmentProfile/algorithmVersion/dataPolicyVersion/configRevision/
buildDigest（可空=登记时未冻结构建，消费时回落当前构建摘要）/
releaseGeneration + snapshotSchemaVersion）+ 消费者最低版本、作用范围、
回退 profile。任务开始经 ``services/assessment_profile.resolve_task_release_context``
读取 ACTIVE 代际并固定；发布/回退=登记状态迁移（STAGED→ACTIVE→RETIRED），
不删行、不删记录（回退保留能解释新记录的 reader，11§5）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssessmentReleaseRegistry(Base):
    """评估发布代际登记（上线清单；追加式，状态迁移不改历史要素）."""

    __tablename__ = "assessment_release_registry"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    #: 发布代际标识（唯一；如 rg-20261011-0001）
    release_generation: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 评估 profile（服务端策略表登记的标识，见 services/assessment_profile）
    assessment_profile: Mapped[str] = mapped_column(String(50), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(50), nullable=False)
    data_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 任务边界固定的配置 revision（如 rev:{n}:{digest16}；legacy 口径可空）
    config_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 发布时冻结的受控构建摘要（空=登记时未冻结，消费回落当前构建）
    build_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 消费者最低版本（读端兼容门槛——新版 writer 在兼容 reader 之后启用）
    consumer_min_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 作用范围（JSONB：生效窗口/参评范围等，EA-D05"既定正式范围"）
    scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: 回退 profile（回退方式的登记面；回退=activate 旧代际）
    rollback_profile: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: STAGED 已登记未生效 / ACTIVE 正式生效 / RETIRED 已被新代际取代
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="STAGED")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('STAGED', 'ACTIVE', 'RETIRED')",
            name="ck_assessment_release_status",
        ),
        UniqueConstraint("release_generation", name="uq_assessment_release_generation"),
        # 同一 profile 至多一条 ACTIVE（发布代际唯一现行口径；回退=迁移状态）
        Index(
            "uq_assessment_release_active_profile",
            "assessment_profile",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("idx_assessment_release_profile", "assessment_profile", "status"),
        {"comment": "评估发布代际登记（上线清单六要素，P3-08a，EA-D05 直接采用）"},
    )


__all__ = ["AssessmentReleaseRegistry"]
