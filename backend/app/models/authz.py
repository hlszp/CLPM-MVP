"""授权目录与持久策略模型（P4-01，方案 01 §3.5 / DEC-04）.

四张表：

1. ``sys_permission`` — 页面码/动作码目录（码表）。页面码对应现行路由模块
   清单（导航投影的授权事实源），动作码对应"读/发起/取消/删除/发布/审核"
   等服务端操作。码间依赖显式（``requires_codes``，如"发起整定"依赖
   "读整定"）；**无机械通配展开**——目录中不存在也不接受 ``loop:*`` 型
   通配码，IC 历史通配按 P1-04 真实裁决落为显式码集合。
2. ``sys_role_permission`` — 五角色授权矩阵（管理员可按角色开关页面/动作码）。
3. ``sys_user_scope`` — 用户数据范围（ALL/NODE_SUBTREE 两档 + plant_node
   绑定；本批仅落数据结构，scope 的贯穿查询/推送/缓存消费属 P4-03）。
4. ``sys_authz_policy`` — 策略版本账本（不可变追加式；expectedRevision
   乐观锁 + 审计信封 + 回退=发布逆操作，模式复用 P2-02 config_publication）。

module enabled 与 fitness 门禁不被授权覆盖：目录码带 ``module_key`` 标注
所属热插拔模块，运行时生效判定为"授权 AND 模块启用 AND 业务门禁"——授权
只收紧不放宽（接线在 P4-02）。
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SysPermission(Base):
    """页面码/动作码目录（DDL：P4-01 §授权目录）.

    - ``kind``: PAGE_GROUP（导航分组）/ PAGE（可导航页面）/ ACTION（服务端操作码）
    - ``parent_code``: 页面码所属分组（PAGE_GROUP）；组码/动作码为 NULL
    - ``requires_codes``: 显式依赖码集合（JSONB 数组）；如 ``tuning:identify``
      requires ``tuning:view``（"发起整定依赖读整定"）。写入矩阵时校验
      依赖闭包，漏依赖拒绝。
    - ``module_key``: 所属热插拔模块（monitor/assess/diagnosis/tuning/
      handling/reports/config/system）；NULL=全局页/横切动作，不受模块开关约束
    - ``is_protected``: ADMIN 关键管理能力标记——protected 码不得从 ADMIN
      角色矩阵移除（最后一名管理员失权保护：所有 ADMIN 共享矩阵，移除即
      全体失权，故任何移除尝试直接拒绝）
    """

    __tablename__ = "sys_permission"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("sys_permission.code", ondelete="RESTRICT"), nullable=True
    )
    requires_codes: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    module_key: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('PAGE_GROUP', 'PAGE', 'ACTION')",
            name="ck_sys_permission_kind",
        ),
        # 组码/动作码不得挂父级（parent_code 仅用于 PAGE 归属导航组；
        # 横切详情页如 page:task.detail 可为 NULL）
        CheckConstraint(
            "kind = 'PAGE' OR parent_code IS NULL",
            name="ck_sys_permission_parent_kind",
        ),
        Index("idx_sys_permission_kind", "kind"),
        Index("idx_sys_permission_module_key", "module_key"),
    )


class SysRolePermission(Base):
    """五角色授权矩阵（role × permission_code）.

    种子按 P1-04 实际裁决矩阵（2026-10-11 AUTH-02 后口径）落库；存量库
    升级前后有效授权一致。变更仅经 ``services/authz_catalog`` 走策略账本。
    """

    __tablename__ = "sys_role_permission"

    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sys_permission.code", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT')",
            name="ck_sys_role_permission_role",
        ),
        Index("idx_sys_role_permission_role", "role"),
    )


class SysUserScope(Base):
    """用户数据范围（ALL/NODE_SUBTREE 两档，DEC-04）.

    - ``scope_type='ALL'``: 全厂（node_id 必为 NULL）
    - ``scope_type='NODE_SUBTREE'``: 绑定 plant_node 子树（node_id 必填）
    - 存量用户迁移种子=ALL（迁移前无 scope 概念=全量可见，前后等效）
    - 贯穿消费（查询/推送/缓存交集）属 P4-03，本批仅落数据结构与变更链
    """

    __tablename__ = "sys_user_scope"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    node_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("plant_node.id", ondelete="RESTRICT"),
        nullable=True,
    )
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('ALL', 'NODE_SUBTREE')",
            name="ck_sys_user_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'ALL' AND node_id IS NULL) "
            "OR (scope_type = 'NODE_SUBTREE' AND node_id IS NOT NULL)",
            name="ck_sys_user_scope_node",
        ),
    )


class PolicyPublicationGuard(Base):
    """单一 ``policy_publication_guard`` 行（P4-03，01§3.5 末段冻结）.

    统一序列化全部授权相关变更（角色授权、用户 scope/禁用、令牌撤销、
    节点归属和 tag 改绑）：变更事务先取该行 **排他锁**（FOR UPDATE）并
    递增 revision；人工任务提交/WS 发送批先取同一行 **共享锁**（FOR SHARE）
    再读取主体策略——并发撤权与提交/发送按 guard 获得顺序线性化。

    固定锁顺序：guard → 主体（sys_user/sys_user_scope）→ 对象（矩阵行/
    plant_node/loop_tag_mapping）。``revision`` 与 ``sys_authz_policy`` 账本
    对齐推进（每次 guard 推进必有对应账本行，operation 枚举含非授权域的
    节点/tag 改绑与用户状态/令牌撤销变更）。
    """

    __tablename__ = "policy_publication_guard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SysAuthzPolicy(Base):
    """授权策略版本账本（不可变追加式；模式复用 P2-02 config_publication）.

    - ``revision``: 全局单调递增；expectedRevision 乐观锁前置校验 +
      主键唯一约束并发兜底（并发双写恰一方成功）
    - ``operation``: SEED（初始种子）/ ROLE_GRANT（角色矩阵更新）/
      USER_SCOPE（用户 scope 更新）/ ROLLBACK（回退逆操作）/
      CATALOG_EXTENSION（目录基线扩展，P4-02 起随迁移追加，不可回退）；
      P4-03 起新增影响有效授权/数据范围的域外变更：NODE_REBIND（节点归属/
      回路 unit 改挂）/ TAG_REBIND（tag 改绑）/ USER_STATE（用户禁用/启用）/
      TOKEN_REVOKE（令牌撤销）——均先取 guard 排他锁再入账
    - ``before_value``/``after_value``: 审计信封（JSONB 快照 + 原因/作用域）
    - ``rollback_of``: ROLLBACK 行指向被回退的源 revision；同一 revision
      不允许二次回退（防重复逆操作震荡）
    - SEED 行不可回退（初始态无逆操作）
    """

    __tablename__ = "sys_authz_policy"

    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    operator: Mapped[str] = mapped_column(String(50), nullable=False)
    before_value: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    rollback_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('SEED', 'ROLE_GRANT', 'USER_SCOPE', 'ROLLBACK', 'CATALOG_EXTENSION', "
            "'NODE_REBIND', 'TAG_REBIND', 'USER_STATE', 'TOKEN_REVOKE')",
            name="ck_sys_authz_policy_operation",
        ),
        UniqueConstraint("rollback_of", name="uq_sys_authz_policy_rollback_of"),
        Index("idx_sys_authz_policy_operation", "operation"),
        Index("idx_sys_authz_policy_created_at", "created_at"),
    )


__all__ = [
    "PolicyPublicationGuard",
    "SysAuthzPolicy",
    "SysPermission",
    "SysRolePermission",
    "SysUserScope",
]
