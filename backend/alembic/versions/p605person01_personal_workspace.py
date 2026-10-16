"""个人关系分片三表（P6-05，契约 01a §3 / 设计 03 号文）

新建账号级个人关系存储（PostgreSQL 为真相来源，浏览器只做缓存）：

1. ``user_loop_follow`` — 回路关注关系；``(user_id, loop_id)`` 复合主键；
   取消关注保留 ``is_followed=false`` 版本墓碑不物理删行（版本链防旧
   请求复活）；``revision>=1`` 行级乐观锁 + ``last_request_id`` 幂等。
2. ``user_workspace_preference`` — 账号工作偏好；``(user_id,
   preference_key)`` 复合主键；``payload`` JSONB + ``schema_version`` +
   按 key 独立 ``revision``；服务端保留键 ``resume_generation`` 用作
   清空最近继续的原子代际栅栏（不对客户端开放写，服务层强制）。
3. ``user_work_resume`` — 最近继续位置；``(user_id, resource_kind,
   resource_id)`` 复合主键；``context`` JSONB（只存 ID/窗口/证据引用/
   恢复位置）；``generation`` 对齐清空栅栏；``deleted_at`` 单项删除
   版本墓碑；``last_visited_at`` 服务端时间排序。

外键：user/loop 删除沿原生命周期 CASCADE 清理个人关系。

downgrade 删三表（本批新建，无存量数据语义）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p605person01"
down_revision: str | None = "p504srvflt01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 回路关注关系
    # ------------------------------------------------------------------
    op.create_table(
        "user_loop_follow",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("is_followed", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_request_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("revision >= 1", name="ck_user_loop_follow_revision"),
    )
    op.create_index(
        "idx_user_loop_follow_user_list",
        "user_loop_follow",
        ["user_id", "is_followed", "updated_at"],
    )

    # ------------------------------------------------------------------
    # 2. 账号工作偏好
    # ------------------------------------------------------------------
    op.create_table(
        "user_workspace_preference",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("preference_key", sa.String(64), primary_key=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_request_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("revision >= 1", name="ck_user_workspace_preference_revision"),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_user_workspace_preference_schema_version"
        ),
    )

    # ------------------------------------------------------------------
    # 3. 最近继续位置
    # ------------------------------------------------------------------
    op.create_table(
        "user_work_resume",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("resource_kind", sa.String(32), primary_key=True),
        sa.Column("resource_id", sa.String(64), primary_key=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("last_visited_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("last_request_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("revision >= 1", name="ck_user_work_resume_revision"),
        sa.CheckConstraint("generation >= 1", name="ck_user_work_resume_generation"),
    )
    op.create_index(
        "idx_user_work_resume_user_visited",
        "user_work_resume",
        ["user_id", "last_visited_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_user_work_resume_user_visited", table_name="user_work_resume")
    op.drop_table("user_work_resume")
    op.drop_table("user_workspace_preference")
    op.drop_index("idx_user_loop_follow_user_list", table_name="user_loop_follow")
    op.drop_table("user_loop_follow")
