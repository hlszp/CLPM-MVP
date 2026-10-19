"""数据范围运行时与授权发布栅栏（P4-03，01§3.5）.

两类原语：

1. **policy_publication_guard 栅栏**（01§3.5 末段冻结）：
   - 策略变更事务（角色授权 / 用户 scope / 用户禁用 / 令牌撤销 / 节点归属
     变更 / tag 改绑）先取单行 guard **排他锁**（FOR UPDATE）并递增
     revision，同时入 ``sys_authz_policy`` 账本（线性化审计记录）；
   - 人工任务提交 / WS 发送批先取同一行 **共享锁**（FOR SHARE）再读取主体
     策略——共享锁跨业务提交/发送批持有时，任何撤权事务都无法在中间提交，
     由此得到"撤权先完成则后续不得越权，提交先完成则保留之前合法证据"的
     可线性排序；
   - 固定锁顺序：guard → 主体（sys_user / sys_user_scope）→ 对象
     （矩阵行 / plant_node / loop_ledger / loop_tag_mapping）。

2. **数据范围（scope）运行时**：``sys_user_scope``（ALL / NODE_SUBTREE，
   P4-01 落库，本批首次消费）解析为 ``ScopeContext``：

   - ALL → 全厂（``allowed_node_ids=None``）；
   - NODE_SUBTREE → 绑定节点 + 全部子孙（WITH RECURSIVE 一次展开）；
   - 进程内缓存按 guard revision 比对失效（与 authz_runtime 同模式；
     无 asyncio 原语——红线合规）。

   贯穿规则（01§3.5）：

   - 列表/详情/聚合/导出/批量/任务/缓存及原始 tag 接口都执行数据范围；
     批量写入先验证全部对象，越界不返回外部对象详情（通用拒绝）；
   - **tag 归属规则**：tag 按 ``tag_registry → loop_tag_mapping →
     loop_ledger.unit_id → plant_node`` 实际关系解析可见性——非 ADMIN 仅可
     读"有授权回路绑定"的 tag（绑定回路的 unit 在范围内即可见；共享 tag
     只返回 tag 本身，不泄露其他回路元信息）；**未绑定 tag 默认仅 ADMIN
     可读，不能推定 ALL 等于可看任何 tag**；
   - 订阅/查询取请求集合与授权集合交集，越界只给通用拒绝。

单测环境兼容（``CLPM_TEST_MODE=1``，conftest 全局设置；真实部署恒不设置）：
scope 解析直接返回 ALL 默认（种子用户全 ALL，P4-01 迁移口径），不触碰 mock
DB；DB 驱动路径由集成测试（真实 PG）与本服务去标记单测直测。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.authz import PolicyPublicationGuard, SysAuthzPolicy, SysUserScope
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.services.plant_node_tree import collect_descendant_node_ids

logger = logging.getLogger(__name__)

SCOPE_ALL = "ALL"
SCOPE_NODE_SUBTREE = "NODE_SUBTREE"

#: 域外策略变更 operation（与账本检查约束/迁移 p403scope01 同批）
OP_NODE_REBIND = "NODE_REBIND"
OP_TAG_REBIND = "TAG_REBIND"
OP_USER_STATE = "USER_STATE"
OP_TOKEN_REVOKE = "TOKEN_REVOKE"

_GUARD_ROW_ID = 1


def _is_unit_test_env() -> bool:
    """单测/mock 环境（与 authz_runtime 同口径）。"""
    return os.environ.get("CLPM_TEST_MODE") == "1"


def domain_guard_enabled() -> bool:
    """域外策略变更者 guard 是否生效（单测环境短路，避免位移位置敏感 mock）.

    真实部署恒 True（域外变更者必须入 guard）；单测/mock 环境的 guard 行为
    由集成测试（真实 PG）覆盖。
    """
    return not _is_unit_test_env()


def _scope_denied(message: str = "无权访问该数据范围外的对象") -> BizError:
    """越界通用拒绝（不携带对象详情）。"""
    return BizError(
        code="ERR_SCOPE_FORBIDDEN",
        message=message,
        status_code=403,
    )


# ---------------------------------------------------------------------------
# ScopeContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeContext:
    """某主体在某 revision 时刻的数据范围快照."""

    user_id: str
    role: str
    scope_type: str
    node_id: str | None
    #: None=ALL（全厂）；frozenset=NODE_SUBTREE 允许节点集（含绑定节点自身）
    allowed_node_ids: frozenset[str] | None
    #: 解析时的 guard revision（栅栏比对/缓存 key 用）
    revision: int

    @property
    def is_all(self) -> bool:
        return self.allowed_node_ids is None

    @property
    def scope_hash(self) -> str:
        """授权节点集合 hash（聚合缓存 key 组成部分；ALL='ALL'）."""
        if self.allowed_node_ids is None:
            return "ALL"
        digest = hashlib.sha1("\n".join(sorted(self.allowed_node_ids)).encode("utf-8")).hexdigest()
        return f"nodes-{digest[:16]}"

    def cache_key(self, base: str) -> str:
        """聚合缓存 key：base + 授权节点集合 hash + policyRevision.

        01§3.5：聚合缓存包含授权节点集合 hash 和 policyRevision，防止跨
        范围共用全厂缓存（"全厂缓存不越界"）；revision 推进（含节点改挂/
        tag 改绑/scope 变更）自然换 key，旧 key 按 TTL 过期。
        """
        return f"{base}~{self.scope_hash}~r{self.revision}"

    def node_in_scope(self, node_id: str | None) -> bool:
        """节点是否在范围内（unit_id 为 NULL 的回路：仅 ALL 可见）."""
        if self.allowed_node_ids is None:
            return True
        return node_id is not None and node_id in self.allowed_node_ids

    def intersect_node_ids(self, requested: list[str] | None) -> list[str] | None:
        """ALL/NODE_SUBTREE 与筛选取交集（01§3.5）.

        返回 None 表示"不过滤"（ALL 且未显式筛选，或交集为空由调用方语义
        决定）；NODE_SUBTREE 下：requested=None → 范围全集；requested 非
        None → 交集（空交集返回 []——空结果而非报错，列表语义）。

        ⚠️ 调用方注意：**不要**把 UI 装置筛选（plantNodeId）当作 requested
        传入后又同时传 plant_node_id——ALL 下会退化成 `unit_id IN (plant)`
        仅本节点，丢掉子树（表现为装置树计 961/25 而卡片墙只显示挂在
        工厂节点上的少量回路）。UI 装置筛选请只走服务的 plant_node_id
        （含子孙）；本方法仅用于**数据范围**约束。
        """
        if self.allowed_node_ids is None:
            return requested  # ALL：尊重显式筛选，不额外收紧
        if requested is None:
            return sorted(self.allowed_node_ids)
        return sorted(set(requested) & self.allowed_node_ids)

    def scope_node_ids_for_filter(self) -> list[str] | None:
        """数据范围约束（供 plant_node_id 已单独承载 UI 筛选时使用）。

        ALL → None（不限）；NODE_SUBTREE → 允许节点集。避免与 plant 子树
        过滤 AND 后丢子孙。
        """
        return None if self.allowed_node_ids is None else sorted(self.allowed_node_ids)


# ---------------------------------------------------------------------------
# guard 栅栏原语
# ---------------------------------------------------------------------------


async def read_guard_revision(db: AsyncSession) -> int:
    """当前 guard revision（无锁轻读；guard 行缺失回退账本 max——迁移未应用环境）."""
    result = await db.execute(
        select(PolicyPublicationGuard.revision).where(PolicyPublicationGuard.id == _GUARD_ROW_ID)
    )
    rev = result.scalar_one_or_none()
    if rev is not None:
        return int(rev)
    result = await db.execute(select(func.max(SysAuthzPolicy.revision)))
    raw = result.scalar_one_or_none()
    return int(raw) if raw is not None else 0


async def lock_guard_for_update(db: AsyncSession) -> int:
    """策略变更入口：取 guard 行排他锁（持有至事务结束），返回当前 revision."""
    result = await db.execute(
        select(PolicyPublicationGuard.revision)
        .where(PolicyPublicationGuard.id == _GUARD_ROW_ID)
        .with_for_update()
    )
    rev = result.scalar_one_or_none()
    if rev is None:
        # 迁移未应用的防御路径：guard 行不存在时以账本 max 兜底（不加锁；
        # 正式环境迁移与代码同批，不出现该分支）
        return await read_guard_revision(db)
    return int(rev)


async def lock_guard_for_share(db: AsyncSession) -> int:
    """任务提交/WS 发送批入口：取 guard 行共享锁（持有至事务结束）.

    共享锁与策略变更的排他锁互斥：持锁期间任何授权/scope/节点/tag 改绑
    变更事务都无法提交——"发送批前重验"由此获得与策略发布的全序。
    """
    result = await db.execute(
        select(PolicyPublicationGuard.revision)
        .where(PolicyPublicationGuard.id == _GUARD_ROW_ID)
        .with_for_update(read=True)  # FOR SHARE
    )
    rev = result.scalar_one_or_none()
    if rev is None:
        return await read_guard_revision(db)
    return int(rev)


async def set_guard_revision(db: AsyncSession, revision: int) -> None:
    """推进 guard revision（调用方必须已持有 guard 排他锁）。"""
    await db.execute(
        text(
            "UPDATE policy_publication_guard SET revision = :rev, updated_at = now() "
            "WHERE id = :row_id"
        ),
        {"rev": revision, "row_id": _GUARD_ROW_ID},
    )


async def record_domain_policy_change(
    db: AsyncSession,
    *,
    current_revision: int,
    operation: str,
    operator: str,
    reason: str | None = None,
    user_id: str | None = None,
    target: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> int:
    """域外策略变更者（节点归属/tag 改绑/用户状态/令牌撤销）入账.

    前置：调用方已 ``lock_guard_for_update`` 持有排他锁且尚未提交。
    行为：guard.revision = current+1 + 账本行 + SysAuditLog（复用
    authz_catalog._append_policy 的审计形态，operation 前缀 AUTHZ_ 去掉
    ——域外变更用自身 operation 名）。
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.models.audit import SysAuditLog

    new_rev = current_revision + 1
    await set_guard_revision(db, new_rev)
    db.add(
        SysAuthzPolicy(
            revision=new_rev,
            operation=operation,
            user_id=user_id,
            reason=reason,
            operator=operator,
            before_value={"revision": current_revision, **(before or {})},
            after_value={**(after or {}), "target": target} if (after or target) else None,
        )
    )
    db.add(
        SysAuditLog(
            id=str(uuid4()),
            operator=operator,
            operation_type=f"AUTHZ_{operation}",
            target_type="User" if user_id else "PlantTopology",
            target_id=str(user_id or target or new_rev),
            before_value=json.dumps(before, ensure_ascii=False, default=str)
            if before is not None
            else None,
            after_value=json.dumps(
                {**(after or {}), "target": target}, ensure_ascii=False, default=str
            )
            if (after or target)
            else None,
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    return new_rev


# ---------------------------------------------------------------------------
# scope 解析（进程内缓存按 guard revision 失效）
# ---------------------------------------------------------------------------

_scope_cache: dict[str, tuple[int, ScopeContext]] = {}


def reset_scope_cache() -> None:
    """清空 scope 缓存（测试用）。"""
    _scope_cache.clear()


async def _expand_subtree(db: AsyncSession, node_id: str) -> frozenset[str]:
    descendants = await collect_descendant_node_ids(db, node_id)
    return frozenset({node_id, *descendants})


async def resolve_scope(
    db: AsyncSession,
    user_id: str,
    role: str,
    *,
    revision: int | None = None,
) -> ScopeContext:
    """解析用户当前数据范围（缓存按 revision 比对失效）.

    单测环境（CLPM_TEST_MODE=1）直接返回 ALL 默认（种子口径），不触碰
    mock DB——与 authz_runtime.get_role_codes 同策略。
    """
    user_id = str(user_id)
    if _is_unit_test_env():
        return ScopeContext(
            user_id=user_id,
            role=str(role),
            scope_type=SCOPE_ALL,
            node_id=None,
            allowed_node_ids=None,
            revision=0,
        )
    if revision is None:
        revision = await read_guard_revision(db)
    cached = _scope_cache.get(user_id)
    if cached is not None and cached[0] == revision:
        return cached[1]
    row = (
        await db.execute(select(SysUserScope).where(SysUserScope.user_id == user_id))
    ).scalar_one_or_none()
    if row is None or row.scope_type == SCOPE_ALL:
        ctx = ScopeContext(
            user_id=user_id,
            role=str(role),
            scope_type=SCOPE_ALL,
            node_id=None,
            allowed_node_ids=None,
            revision=revision,
        )
    else:
        assert row.node_id is not None  # ck_sys_user_scope_node 保证
        ctx = ScopeContext(
            user_id=user_id,
            role=str(role),
            scope_type=SCOPE_NODE_SUBTREE,
            node_id=str(row.node_id),
            allowed_node_ids=await _expand_subtree(db, str(row.node_id)),
            revision=revision,
        )
    _scope_cache[user_id] = (revision, ctx)
    return ctx


async def authorized_tag_names(db: AsyncSession, ctx: ScopeContext) -> set[str] | None:
    """计算主体当前可读的实时 tag 全集（WS 分发过滤/旧客户端授权集）.

    - ADMIN → None（不过滤：含未绑定 tag，01§3.5 未绑定 tag 仅 ADMIN 可读）；
    - 非 ADMIN → "有授权回路绑定"的 tag 名全集（ALL=任意绑定；
      NODE_SUBTREE=范围内回路绑定）——未绑定 tag 不在集合内。
    """
    if ctx.role == "ADMIN":
        return None
    stmt = (
        select(TagRegistry.tag_name)
        .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
        .join(LoopLedger, LoopLedger.id == LoopTagMapping.loop_id)
    )
    if ctx.allowed_node_ids is not None:
        stmt = stmt.where(LoopLedger.unit_id.in_(ctx.allowed_node_ids))
    rows = await db.execute(stmt.distinct())
    return {str(r[0]) for r in rows.all()}


# ---------------------------------------------------------------------------
# 回路/节点范围校验
# ---------------------------------------------------------------------------

#: 范围外显式筛选的哨兵节点（不存在 UUID——列表语义下"交集为空=空结果"，
#: 服务层递归展开后自然返回空聚合，不报对象详情）
OUT_OF_SCOPE_SENTINEL_NODE = "00000000-0000-0000-0000-000000000000"


def scoped_plant_node_id(ctx: ScopeContext, requested: str | None) -> str | None:
    """单节点筛选的端点级范围交集（读路径列表语义：越界 → 哨兵空结果）.

    - ALL：原样返回（尊重显式筛选）；
    - NODE_SUBTREE：未筛选 → 范围根节点（其子树展开=范围全集）；显式筛选
      在范围内 → 原样（子树封闭：范围内节点的子孙仍在范围内）；越界 →
      哨兵（递归展开为空集，服务层返回空数据——通用拒绝不泄露对象详情）。
    """
    if ctx.allowed_node_ids is None:
        return requested
    if requested is None:
        return ctx.node_id
    return requested if requested in ctx.allowed_node_ids else OUT_OF_SCOPE_SENTINEL_NODE


def scoped_plant_node_ids(ctx: ScopeContext, requested: list[str] | None) -> list[str] | None:
    """多节点筛选的端点级范围交集（读路径列表语义）."""
    if ctx.allowed_node_ids is None:
        return requested
    if not requested:
        return sorted(ctx.allowed_node_ids)
    kept = [n for n in requested if n in ctx.allowed_node_ids]
    return kept or [OUT_OF_SCOPE_SENTINEL_NODE]


async def scoped_loop_id_filter(
    db: AsyncSession, ctx: ScopeContext, requested: list[str] | None
) -> list[str] | None:
    """回路 ID 列表筛选的端点级范围交集（读路径列表语义：越界回路剔除）."""
    if ctx.allowed_node_ids is None or not requested:
        return requested
    allowed = await loops_in_scope(db, ctx, requested)
    return sorted(allowed) or [OUT_OF_SCOPE_SENTINEL_NODE]


def ensure_node_in_scope(ctx: ScopeContext, node_id: str, *, action: str = "访问") -> None:
    """节点级对象校验（详情/写路径）：越界 403 通用拒绝."""
    if ctx.allowed_node_ids is not None and node_id not in ctx.allowed_node_ids:
        raise _scope_denied(f"节点不在数据范围内，已拒绝{action}")


async def loops_in_scope(
    db: AsyncSession,
    ctx: ScopeContext,
    loop_ids: list[str] | set[str],
) -> set[str]:
    """给定回路 ID 集合中在范围内的子集（空入参直接返回空集）."""
    ids = [str(i) for i in loop_ids]
    if not ids:
        return set()
    stmt = select(LoopLedger.id).where(LoopLedger.id.in_(ids))
    if ctx.allowed_node_ids is not None:
        stmt = stmt.where(LoopLedger.unit_id.in_(ctx.allowed_node_ids))
    rows = await db.execute(stmt)
    return {str(r[0]) for r in rows.all()}


async def ensure_loops_in_scope(
    db: AsyncSession,
    ctx: ScopeContext,
    loop_ids: list[str],
    *,
    action: str = "操作",
) -> None:
    """批量/关联校验：任一目标回路跨 scope 即整体拒绝（通用拒绝，不列对象）."""
    if ctx.allowed_node_ids is None or not loop_ids:
        return
    wanted = {str(i) for i in loop_ids}
    allowed = await loops_in_scope(db, ctx, wanted)
    if len(allowed) != len(wanted):
        raise _scope_denied(f"存在数据范围外的回路，已拒绝{action}")


async def ensure_loop_in_scope(db: AsyncSession, ctx: ScopeContext, loop_id: str) -> None:
    """单对象校验：越界 403 通用拒绝（不回显对象详情）."""
    if ctx.allowed_node_ids is None:
        return
    if not await loops_in_scope(db, ctx, [str(loop_id)]):
        raise _scope_denied()


async def scoped_loop_ids(db: AsyncSession, ctx: ScopeContext) -> list[str] | None:
    """范围内全部回路 ID（ALL → None=不过滤）."""
    if ctx.allowed_node_ids is None:
        return None
    rows = await db.execute(
        select(LoopLedger.id).where(LoopLedger.unit_id.in_(ctx.allowed_node_ids))
    )
    return [str(r[0]) for r in rows.all()]


async def loop_id_to_unit_map(db: AsyncSession, loop_ids: list[str]) -> dict[str, str | None]:
    """回路 → unit_id 映射（预警按关联 loop/node 过滤用）."""
    if not loop_ids:
        return {}
    rows = await db.execute(
        select(LoopLedger.id, LoopLedger.unit_id).where(LoopLedger.id.in_(loop_ids))
    )
    return {str(r[0]): (str(r[1]) if r[1] is not None else None) for r in rows.all()}


# ---------------------------------------------------------------------------
# tag 可见性（01§3.5 tag 归属规则）
# ---------------------------------------------------------------------------


async def classify_tags(
    db: AsyncSession,
    ctx: ScopeContext,
    tag_names: list[str],
) -> tuple[list[str], list[str]]:
    """按归属规则把请求 tag 集合分为（可见, 拒绝）.

    规则（01§3.5）：
    - ADMIN：tag_registry 中存在即可见（含未绑定 tag）；
    - 非 ADMIN：仅"存在且有 ≥1 条绑定回路位于范围内"的 tag 可见
      （ALL 范围 = 任意绑定；未绑定 tag 仅 ADMIN，ALL 不推定全可见）；
    - 不存在的 tag 一律拒绝（与既有订阅协议 rejected 口径一致——存在性
      与越界共用同一拒绝列表，不泄露额外信息）。

    顺序保持请求顺序去重；返回 (visible, rejected)。
    """
    seen: list[str] = []
    for t in tag_names:
        if isinstance(t, str) and t and t not in seen:
            seen.append(t)
    if not seen:
        return [], []

    # 1. 存在性
    rows = await db.execute(
        select(TagRegistry.id, TagRegistry.tag_name).where(TagRegistry.tag_name.in_(seen))
    )
    known: dict[str, str] = {name: str(tid) for tid, name in rows.all()}
    if not known:
        return [], list(seen)

    # 2. ADMIN：存在的全可见
    if ctx.role == "ADMIN":
        return [t for t in seen if t in known], [t for t in seen if t not in known]

    # 3. 非 ADMIN：经 loop_tag_mapping → loop_ledger.unit_id 解析"授权回路绑定"
    unit_cond = (
        LoopLedger.unit_id.in_(ctx.allowed_node_ids) if ctx.allowed_node_ids is not None else True
    )
    bound_rows = await db.execute(
        select(LoopTagMapping.tag_id)
        .join(LoopLedger, LoopLedger.id == LoopTagMapping.loop_id)
        .where(LoopTagMapping.tag_id.in_(list(known.values())), unit_cond)
    )
    bound_tag_ids = {str(r[0]) for r in bound_rows.all()}
    visible = [t for t in seen if t in known and known[t] in bound_tag_ids]
    rejected = [t for t in seen if t not in known or known[t] not in bound_tag_ids]
    return visible, rejected


# ---------------------------------------------------------------------------
# 连接主体快照（WS 握手/发送批重验共用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectSnapshot:
    """WS 连接主体在某时刻的有效性快照（连接固定主体，不固定永久授权）."""

    user_id: str
    username: str
    role: str
    is_active: bool
    revision: int


async def load_subject(db: AsyncSession, user_id: str) -> SubjectSnapshot | None:
    """读取连接主体当前状态（不存在/禁用 → None，调用方停止推送）."""
    from app.models.sys_user import SysUser

    row = (
        await db.execute(
            select(SysUser.id, SysUser.username, SysUser.role, SysUser.is_active).where(
                SysUser.id == user_id
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return SubjectSnapshot(
        user_id=str(row.id),
        username=str(row.username),
        role=str(row.role),
        is_active=bool(row.is_active),
        revision=-1,
    )


__all__ = [
    "OP_NODE_REBIND",
    "OUT_OF_SCOPE_SENTINEL_NODE",
    "domain_guard_enabled",
    "authorized_tag_names",
    "OP_TAG_REBIND",
    "OP_TOKEN_REVOKE",
    "OP_USER_STATE",
    "SCOPE_ALL",
    "SCOPE_NODE_SUBTREE",
    "ScopeContext",
    "SubjectSnapshot",
    "classify_tags",
    "ensure_loop_in_scope",
    "ensure_loops_in_scope",
    "ensure_node_in_scope",
    "load_subject",
    "lock_guard_for_share",
    "lock_guard_for_update",
    "loop_id_to_unit_map",
    "loops_in_scope",
    "read_guard_revision",
    "record_domain_policy_change",
    "reset_scope_cache",
    "resolve_scope",
    "scoped_loop_id_filter",
    "scoped_loop_ids",
    "scoped_plant_node_id",
    "scoped_plant_node_ids",
    "set_guard_revision",
]
