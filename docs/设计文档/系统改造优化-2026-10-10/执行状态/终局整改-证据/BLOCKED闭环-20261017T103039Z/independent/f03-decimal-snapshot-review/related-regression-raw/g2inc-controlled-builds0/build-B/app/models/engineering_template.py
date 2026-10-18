"""工程模板（响应/控制特征）与不可变版本（P2-04）.

契约来源：《01-系统改造优化方案》§3.2（"模板按响应/控制特征组织……模板引用
不可变版本"）+ 《03-阶段任务计划》P2-04 任务卡 + P2-02 交接（TEMPLATE 层
此前可发布/解析但影响回路数诚实降级 None，待本批映射收口）。

设计要点：
- ``engineering_template.template_key`` 即 P2-02 ``config_override`` TEMPLATE
  层的 ``scope_id``（模板键）——模板实体化后 TEMPLATE→回路映射可精确计数。
- ``engineering_template_version`` 为**追加式不可变版本**：发布新版本只新增
  行，不改不删旧行；已固定引用（回路 pin 的版本号 / 任务与结果上下文记录
  的 revisionRef）在发布新版后解析结果不变。
- 回路经 ``loop_ledger.engineering_template_id`` 关联模板（NULL=未关联，
  TEMPLATE 层对该回路不参与）；``engineering_template_version`` 非空时 pin
  到该不可变版本（发布新版不自动改），NULL=跟随当前最新版本。
- 版本内容 ``profile`` 按响应/控制特征组织的工程特征档案（JSONB，键值
  含义由维护方约定；本批只做载体与不可变性，算法消费归后续阶段）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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

#: 模板组织维度（按响应/控制特征组织，01 §3.2）
TEMPLATE_CATEGORY_RESPONSE = "RESPONSE"
TEMPLATE_CATEGORY_CONTROL = "CONTROL"


class EngineeringTemplate(Base):
    """工程模板实体（响应/控制特征模板；TEMPLATE 层覆盖的作用域目标）.

    Attributes:
        template_key: 模板键（全局唯一；= config_override.scope_id@TEMPLATE 层）
        name: 模板名称
        category: 组织维度：RESPONSE（响应特征）/ CONTROL（控制特征）
        description: 说明
    """

    __tablename__ = "engineering_template"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    template_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="模板键（全局唯一；TEMPLATE 层 config_override.scope_id）",
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="模板名称")
    category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TEMPLATE_CATEGORY_RESPONSE,
        comment="组织维度：RESPONSE(响应特征) / CONTROL(控制特征)",
    )
    # 当前版本指针（发布新版本 = 追加版本行 + 前进指针；回退 = 指针回拨，
    # 版本历史保持追加式不删改）。未 pin 版本的回路跟随本指针。
    current_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="当前版本号；NULL=尚无发布版本。发布/回退经 P2-02 统一发布链推进 revision",
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('RESPONSE', 'CONTROL')",
            name="ck_engineering_template_category",
        ),
        UniqueConstraint("template_key", name="uq_engineering_template_key"),
        Index("idx_engineering_template_key", "template_key"),
    )


class EngineeringTemplateVersion(Base):
    """工程模板不可变版本（追加式；发布新版不自动改已固定引用）.

    每次发布追加一行（``version`` = 该模板当前最大版本 + 1）：
    - 不提供更新/删除路径（服务层唯一写入口 ``publish_template_version``
      只 INSERT；旧行不可变是模板引用语义的载体）；
    - 已固定引用按 ``(template_id, version)`` 精确解析，发布新版本后解析
      结果不变（"模板升级不自动改固定历史版本"，03 §P2-04 验收）。
    """

    __tablename__ = "engineering_template_version"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    template_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("engineering_template.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="版本号（模板内单调）")
    profile: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, comment="版本内容（响应/控制特征档案）"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, comment="发布原因")
    published_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_engineering_template_version"),
        Index("idx_engineering_template_version_template", "template_id", "version"),
    )


__all__ = [
    "TEMPLATE_CATEGORY_CONTROL",
    "TEMPLATE_CATEGORY_RESPONSE",
    "EngineeringTemplate",
    "EngineeringTemplateVersion",
]
