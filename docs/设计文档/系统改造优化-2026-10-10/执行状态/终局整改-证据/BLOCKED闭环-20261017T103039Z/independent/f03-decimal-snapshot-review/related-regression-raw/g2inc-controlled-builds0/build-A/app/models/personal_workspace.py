"""个人关系分片模型（P6-05，契约 01a §3 / 设计 03 号文）.

三张表（PostgreSQL 为个人收藏/偏好/最近继续的真相来源，浏览器只做缓存）：

1. ``user_loop_follow`` — 回路关注关系（账号级"我的收藏/关注"）。
   ``(user_id, loop_id)`` 复合主键即唯一约束；取消关注保留
   ``is_followed=false`` 版本墓碑不物理删行——版本链延续使"取消后迟到的
   旧关注请求"（携带过期 expectedRevision 或 expectedRevision=0 的重建
   尝试）被 CAS 拒绝，不得复活。回路名称/状态/评分不冗余存储，读取时
   从 ``loop_ledger`` 及证据表取（诚实化：撤权后不得经本表泄露）。
2. ``user_workspace_preference`` — 账号工作偏好（默认首页、筛选预设、
   排序预设），``(user_id, preference_key)`` 主键；按 key 独立版本，
   不整包覆盖。``resume_generation`` 为服务端保留键（不对客户端开放写），
   其 ``payload.generation`` 是"清空最近继续"的原子版本栅栏。
3. ``user_work_resume`` — 最近继续位置，``(user_id, resource_kind,
   resource_id)`` 主键；只存业务 ID/窗口/证据引用/恢复位置
   （``context`` JSONB），不存业务结果副本、凭据或任意 URL。
   ``generation`` 对齐清空栅栏；单项删除保留 ``deleted_at`` 版本墓碑。

外键口径：user/loop 删除沿原生命周期 CASCADE 清理个人关系（契约 01a §3：
"真正删除用户/回路的外键清理沿原生命周期处理"）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserLoopFollow(Base):
    """回路关注关系（``(user_id, loop_id)`` 唯一；取消留版本墓碑）.

    - ``revision``: 行级乐观锁版本，恒 >= 1；首次创建=1，每次状态变更 +1
    - ``last_request_id``: 最近一次生效写请求的客户端请求身份；同
      requestId 重试幂等返回已有结果，不重复推进版本
    - ``is_followed=false`` 行为取消墓碑：不进入关注列表、不改变任何
      工单责任，仅保留版本链防旧请求复活
    """

    __tablename__ = "user_loop_follow"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_followed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_user_loop_follow_revision"),
        Index("idx_user_loop_follow_user_list", "user_id", "is_followed", "updated_at"),
    )


class UserWorkspacePreference(Base):
    """账号工作偏好（按 ``preference_key`` 独立版本，不整包覆盖）.

    - ``payload``: 偏好内容（JSONB）；白名单键 + schema 校验在服务层
      （default_home / filter_presets / sort_presets）；不接受任意脚本、
      权限声明、凭据、原始工业数据或任意跳转网址
    - ``schema_version``: payload 结构版本（首版=1），供后续演进识别
    - ``revision``: 单 key 乐观锁版本，恒 >= 1
    - ``resume_generation`` 为服务端保留键：客户端写入一律 400；
      ``payload={"generation": N}`` 由清空最近继续事务原子递增
    """

    __tablename__ = "user_workspace_preference"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    preference_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_user_workspace_preference_revision"),
        CheckConstraint("schema_version >= 1", name="ck_user_workspace_preference_schema_version"),
    )


class UserWorkResume(Base):
    """最近继续位置（``(user_id, resource_kind, resource_id)`` 唯一）.

    - ``resource_kind``: 业务对象类型白名单（首版仅 ``loop``），服务层校验
    - ``resource_id``: 业务对象稳定 ID（kind=loop 时为 loop_ledger UUID
      文本；多态引用无法建外键，存在性/权限在写入前服务端校验）
    - ``context``: 恢复位置（JSONB：页面/剖面、时间窗、证据引用）；不存
      业务结果副本、凭据或任意 URL
    - ``revision``: 行级乐观锁版本，恒 >= 1
    - ``generation``: 对齐本人 ``resume_generation`` 栅栏；清空后旧代
      记录被清理，旧代迟到写入一律 409 拒绝
    - ``last_visited_at``: 服务端接收时间（不以客户端时钟排序）
    - ``deleted_at``: 单项删除版本墓碑（保留版本链，防旧请求复活）
    """

    __tablename__ = "user_work_resume"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    resource_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_visited_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_user_work_resume_revision"),
        CheckConstraint("generation >= 1", name="ck_user_work_resume_generation"),
        Index("idx_user_work_resume_user_visited", "user_id", "last_visited_at"),
    )


__all__ = [
    "UserLoopFollow",
    "UserWorkResume",
    "UserWorkspacePreference",
]
