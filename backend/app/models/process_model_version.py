"""``process_model_version`` model — 过程模型版本聚合（V62-P3-003）.

v6.2 Phase 3 模型实体 ADR 通过后新增的最小聚合，承载回路过程模型
G(s)=PV/OP 的不可变版本化证据，支持 CANDIDATE/CURRENT/RETIRED 生命周期。

设计依据（v6.2 方案 §7.3）：
- 首版不建 ``process_model`` 主表，Loop 已是模型所有者；
- 不建独立审批表、工况表、误差表，全部合并进本聚合；
- 不可变：核心字段一旦创建不允许修改，仅 status / retired_* 可流转
  （由服务层守护，P3-006 落地）。

并发一致性（V62-P3-004）：
- 同一 ``loop_id`` 下 ``status=CURRENT`` 至多一条，由部分唯一索引
  ``uk_process_model_version_current`` 在数据库层强制；
- ``(loop_id, version)`` 唯一，保证单回路版本号单调不重复。

迁移策略（V62-P3-005）：
- 本迁移只建表 + 给 ``tuning_record`` 加可空外键；
- 一次性回填 / 影子读比对 / 切换读取 / 停止旧参数新写在 P3-005 落地。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProcessModelVersion(Base):
    """过程模型版本聚合（V62-P3-003）.

    一条记录代表某回路在某数据窗口下辨识得到的不可变过程模型版本。
    生命周期：CANDIDATE（候选）→ CURRENT（当前生效）→ RETIRED（退役）。
    仅人工窗口模型可审批为 CURRENT；在线影子候选不得发布（Phase 4 守护）。
    """

    __tablename__ = "process_model_version"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ---------- 不可变版本标识 ----------
    # 单回路内单调递增的版本号；由服务层按 loop_id 分配 (MAX(version)+1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # CANDIDATE / CURRENT / RETIRED
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="CANDIDATE")

    # ---------- 数据窗口与工况（§7.3） ----------
    data_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 数据快照哈希（输入时序指纹，用于漂移比较与重复辨识识别）
    data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 工况摘要：MODE 占比、饱和占比、激励强度、采样率、有效样本率等
    condition_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---------- 算法与模型（§7.3） ----------
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    identify_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model_type: Mapped[str] = mapped_column(String(20), nullable=False)
    model_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    theta_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sampling_period: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---------- 验证指标（§7.3 train/validation/test） ----------
    # r2_train / r2_val / nrmse_val / aic / bic / fitting_score 等
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 残差检验摘要（白噪声 / 输入相关性 / 跨片段稳定性）
    residual_test: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 参数不确定度（K/tau/theta 95% CI）
    uncertainty: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 物理可行性门禁结果（稳定 / 增益符号 / 时间常数 / 纯滞后 / 采样比）
    physical_feasibility: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---------- 可信度 ----------
    confidence_level: Mapped[str | None] = mapped_column(String(12), nullable=True)
    confidence_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---------- 发布管理（§7.3 发布人/发布时间/替代版本/失效原因） ----------
    published_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 替代的上一版本（自引用；RETIRE 旧版本时回填）
    supersedes_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("process_model_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    retired_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ---------- 审计 ----------
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('CANDIDATE', 'CURRENT', 'RETIRED')",
            name="ck_process_model_version_status",
        ),
        CheckConstraint(
            "model_type IN ('FOPDT', 'SOPDT', 'IPDT')",
            name="ck_process_model_version_model_type",
        ),
        CheckConstraint(
            "theta_source IS NULL OR theta_source IN ('EXPLICIT', 'SEARCHED', 'HEURISTIC_2TS')",
            name="ck_process_model_version_theta_source",
        ),
        CheckConstraint(
            "identify_method IS NULL OR identify_method IN ("
            "'HISTORICAL_ARX', 'HISTORICAL_ARMAX', 'HISTORICAL_IV', "
            "'STEP_TWO_POINT', 'STEP_AREA', 'STEP_NLS')",
            name="ck_process_model_version_identify_method",
        ),
        CheckConstraint(
            "confidence_level IS NULL OR confidence_level IN "
            "('A', 'B', 'C', 'D', 'E', 'INCONCLUSIVE')",
            name="ck_process_model_version_confidence",
        ),
        # P3-004 并发一致性核心：同一回路至多一个 CURRENT
        # 部分唯一索引——仅对 status=CURRENT 的行生效，CANDIDATE/RETIRED 不限数量
        Index(
            "uk_process_model_version_current",
            "loop_id",
            unique=True,
            postgresql_where=text("status = 'CURRENT'"),
        ),
        # (loop_id, version) 唯一：版本号单回路单调不重复
        Index(
            "uk_process_model_version_loop_version",
            "loop_id",
            "version",
            unique=True,
        ),
        Index("idx_process_model_version_loop_status", "loop_id", "status"),
        {"comment": "过程模型版本聚合（V62-P3-003，不可变版本化辨识证据）"},
    )
