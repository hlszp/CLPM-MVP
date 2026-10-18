"""授权运行时消费（P4-02）——DB 持久矩阵驱动服务端 authorizer 的唯一事实源.

P4-01 落地的 ``sys_role_permission`` 持久矩阵在这里被运行时消费：

- ``get_role_codes``：每次校验先读当前策略 revision（``SELECT max(revision)``
  单行轻查询），与进程内缓存的 revision 不一致才重载全量矩阵——满足
  01§3.5 "配置变更后服务端下一请求取新版本"；跨进程同样生效（各进程
  各自比对 revision），更快的主动失效（pub/sub 广播）归 P4-04。
- revision=0（``sys_authz_policy`` 账本为空：P4-01 迁移未应用）→ 回退
  ``ROLE_SEED`` 常量（与迁移种子逐码一致，P4-01 集成测试三方一致钉死），
  迁移未应用的环境行为与旧静态链完全一致。单测/mock 环境
  （``CLPM_TEST_MODE=1``，真实部署恒不设置）同样直用种子，不触碰 mock
  DB 会话。
- ``get_effective_codes``：/auth/me 与登录响应的 DB 驱动动作码/页面码
  下发口——前端"菜单按可达页生成 + 按钮按动作码显隐"的事实源，
  数据库保存驱动真实授权，不依赖前端常量。

SPONSOR 零写红线（P1-04 裁决）：``READ_ONLY_ACTION_CODES`` 之外的动作码
对 SPONSOR 结构性拒绝（``deps.require_perms`` 强制）——矩阵编辑无法把
写码授予 SPONSOR，红线不依赖种子纪律。

module enabled 与 fitness 门禁不被授权覆盖：运行时授权只收紧不放宽，
模块开关（``core/modules.require_module``）与整定服务层 fitness L0/L1
门禁在授权之外独立执行（AND 语义）。
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import SysRolePermission
from app.services.authz_catalog import CATALOG, ROLE_SEED

logger = logging.getLogger(__name__)


def _is_unit_test_env() -> bool:
    """单测/mock 环境（conftest 置 CLPM_TEST_MODE=1；真实部署恒不设置）。

    该环境下 DB 会话为 AsyncMock，revision 查询既无意义也会错位位移
    位置敏感的 mock 序列——直接用 ROLE_SEED（与 DB 种子逐码一致，
    P4-01 集成测试三方一致钉死），行为等价旧静态链。DB 驱动路径由
    test_p4_02_authz_consumption.py 去掉该标记后直测。
    """
    return os.environ.get("CLPM_TEST_MODE") == "1"


#: 只读动作码（SPONSOR 零写红线允许通过的码域：读码 + tracker:review 只读复核）
READ_ONLY_ACTION_CODES: frozenset[str] = frozenset(
    code
    for code, definition in CATALOG.items()
    if definition.kind == "ACTION" and (code.endswith(":view") or code == "tracker:review")
)

# ---------------------------------------------------------------------------
# 进程内矩阵缓存（revision 比对失效；无 asyncio 原语——红线合规）
# ---------------------------------------------------------------------------

_matrix_cache: dict[str, frozenset[str]] | None = None
_cached_revision: int | None = None


def reset_runtime_cache() -> None:
    """清空运行时缓存（测试与迁移后强失效用）。"""
    global _matrix_cache, _cached_revision
    _matrix_cache = None
    _cached_revision = None


async def _read_revision(db: AsyncSession) -> int:
    """当前策略 revision（P4-03 起读 policy_publication_guard 行）.

    - guard 行缺失（P4-03 迁移未应用）→ 回退账本 max；账本空（未播种）
      → 0（回退 ROLE_SEED）；
    - guard revision 覆盖授权域与域外策略变更者（节点归属/tag 改绑/用户
      状态/令牌撤销）——矩阵缓存对域外变更多 reload 一次无害（正确性优先）；
    - 标量形态异常（单测 mock DB 返回用户对象等）→ 视为无账本（0），
      与旧静态链行为一致；真实 DB 连接错误原样抛出（fail-closed）。
    """
    from app.services.authz_scope import read_guard_revision

    try:
        return await read_guard_revision(db)
    except (TypeError, ValueError):
        return 0


async def _load_matrix(db: AsyncSession) -> dict[str, frozenset[str]]:
    result = await db.execute(select(SysRolePermission.role, SysRolePermission.permission_code))
    matrix: dict[str, set[str]] = {}
    for role, code in result.fetchall():
        matrix.setdefault(role, set()).add(code)
    return {role: frozenset(codes) for role, codes in matrix.items()}


async def get_role_codes(db: AsyncSession, role: str) -> frozenset[str]:
    """角色当前授权码全集（DB 持久矩阵优先，空账本回退种子）。

    每请求一次 revision 比对：revision 变化（含跨进程经 DB 提交的变更）
    即重载矩阵——移除一个授权，所有入口在下一请求同步收紧。
    """
    global _matrix_cache, _cached_revision
    if _is_unit_test_env():
        return ROLE_SEED.get(role, frozenset())
    revision = await _read_revision(db)
    if revision == 0:
        # 账本未播种：P4-01 迁移未应用（或单测 mock DB）——回退与迁移种子
        # 逐码一致的 ROLE_SEED，行为等价旧静态链
        return ROLE_SEED.get(role, frozenset())
    if _matrix_cache is None or _cached_revision != revision:
        _matrix_cache = await _load_matrix(db)
        _cached_revision = revision
        logger.info("授权矩阵缓存已重载: revision=%s roles=%s", revision, len(_matrix_cache))
    return _matrix_cache.get(role, frozenset())


async def get_effective_codes(db: AsyncSession, role: str) -> dict[str, list[str]]:
    """/auth/me 与登录响应下发口：动作码（buttons）+ 页面码（菜单可达页）。"""
    codes = await get_role_codes(db, role)
    actions = sorted(c for c in codes if c in CATALOG and CATALOG[c].kind == "ACTION")
    pages = sorted(c for c in codes if c in CATALOG and CATALOG[c].kind == "PAGE")
    return {"actions": actions, "pages": pages}


__all__ = [
    "READ_ONLY_ACTION_CODES",
    "get_effective_codes",
    "get_role_codes",
    "reset_runtime_cache",
]
