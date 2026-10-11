"""``config_override`` / ``config_publication`` models — P2-02 分层配置发布.

统一作用域链（方案 01 §3.2，低→高）：

    DEFAULT（算法默认 + algorithm_parameter 全局覆盖 + metric_config.threshold
            兼容全局指标级覆盖）
    < TEMPLATE（响应/控制特征模板，``scope_id`` = 模板键，引用不可变发布版本）
    < NODE（装置/单元，``scope_id`` = plant_node.id）
    < LOOP（回路，``scope_id`` = loop_ledger.id）
    < TASK（本次任务临时覆盖，仅内存传递不落库）

表职责划分（复用既有表，新增仅补统一发布快照缺失部分）：

- DEFAULT 层全局覆盖：复用 ``algorithm_parameter``（按 metric_code × control_type）
  与 ``metric_config.threshold`` JSONB（迁移诊断归入全局层，保持兼容读取，
  不静默改变旧优先级——三层内部次序 算法默认 < algorithm_parameter <
  metric_config.threshold 维持原状，TEMPLATE/NODE/LOOP/TASK 为更高的新增层）。
- TEMPLATE / NODE / LOOP 层：本文件 ``config_override``（``scope_id`` 统一携带
  作用域目标键，避免可空三列 + 表达式索引的复杂度）。
- 统一发布快照（全局单调 revision + before/after + 原因 + 操作者 + 影响范围 +
  回退版本）：本文件 ``config_publication``（追加式，审计入既有 SysAuditLog）。
- TASK 层：任务启动时经 ``app.services.config_publish.pin_config_snapshot()``
  固定的内存快照，不建表。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConfigOverride(Base):
    """分层配置覆盖（TEMPLATE / NODE / LOOP 三层；每行 = 一层 × 一作用域 × 一指标）.

    覆盖语义为**整组替换**：发布即以本行 ``params`` 全量替换该层该作用域该指标的
    覆盖内容（与部分合并的 PUT 兼容通道不同——发布语义要求 before/after 可精确
    对账）。参数键与值域经 P2-01 单源注册表 ``PARAM_META`` 统一校验。

    Attributes:
        id: UUID 主键
        layer: 层（TEMPLATE / NODE / LOOP）
        scope_id: 作用域目标键——TEMPLATE 层=模板键（响应/控制特征模板名），
            NODE 层=plant_node.id，LOOP 层=loop_ledger.id（统一字符串形态）
        metric_code: 指标代码（须在 PARAM_META 注册表内）
        params: 该层覆盖的参数键值对 JSONB
        is_enabled: 是否启用（False 时该层不参与解析）
        published_revision: 最近一次发布本行的全局 config revision（sourceRevision）
        updated_by / updated_at / version: 维护信息 + 行级版本
    """

    __tablename__ = "config_override"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(50), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    published_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "layer IN ('TEMPLATE', 'NODE', 'LOOP')",
            name="ck_config_override_layer",
        ),
        UniqueConstraint(
            "layer",
            "scope_id",
            "metric_code",
            name="uq_config_override_scope",
        ),
        Index("ix_config_override_metric", "metric_code"),
        Index("ix_config_override_scope", "layer", "scope_id"),
    )


class ConfigPublication(Base):
    """统一配置发布快照（追加式发布账本，全局单调 revision）.

    每次发布（新覆盖 / 重置 / 回退 / 兼容通道写入）追加一行：

    - ``revision``：全局单调递增，**持久版本号**（Redis 仅承载加速通知，
      不作为版本真相源）；并发发布以 revision 唯一约束实现 expectedRevision
      乐观锁——两方同持一个 expectedRevision 时仅一方成功，另一方 409。
    - ``before_value`` / ``after_value``：该发布作用范围内变更前/后的覆盖内容。
    - ``reason``：原因（必填，入审计）。
    - ``operator``：操作者。
    - ``scope``：作用范围（层 + 指标 + 作用域键 + 写入通道）。
    - ``affected_loops``：受影响回路数（无法精确计数时为 NULL 并以 note 说明）。
    - ``rollback_revision``：回退目标版本（= 本次发布前 revision）。
    - ``operation``：PUBLISH（发布覆盖）/ RESET（重置层）/ ROLLBACK（回退）/
      LEGACY_SYNC（algorithm-params PUT 等兼容通道写入）。

    回退语义：``rollback(R)`` 将 R 号发布的 before 状态重新应用为一个**新
    revision**（方案 §6 "配置回退生成新 revision"），不删改任何历史行。
    """

    __tablename__ = "config_publication"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    layer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    operator: Mapped[str] = mapped_column(String(50), nullable=False)
    before_value: Mapped[Text | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[Text | None] = mapped_column(Text, nullable=True)
    affected_loops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rollback_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "operation IN ('PUBLISH', 'RESET', 'ROLLBACK', 'LEGACY_SYNC')",
            name="ck_config_publication_operation",
        ),
        UniqueConstraint("revision", name="uq_config_publication_revision"),
        Index("ix_config_publication_created_at", "created_at"),
    )


__all__ = ["ConfigOverride", "ConfigPublication"]
