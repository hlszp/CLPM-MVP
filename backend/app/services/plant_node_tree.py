"""Plant node 树形递归公共 CTE 工具（Phase 10 性能优化）。

历史背景：原 ``loop.py`` / ``monitor.py`` / ``tag.py`` 各自重复实现了一份
``_get_descendant_node_ids``——递归 select 子节点 + Python 层 N 次 db.execute，
节点深度较大时产生 N 次 round-trip。本模块统一收敛为 1 次 ``WITH RECURSIVE`` CTE，
既消除重复代码，也把 N 次查询压成 1 次。

注意：
- 返回的 list **不含** parent_id 自身（与原 ``_get_descendant_node_ids`` 语义一致），
  调用方按需 append。
- ``plant_node`` 表使用 ``parent_id`` 自引用，``id`` 为 UUID。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def collect_descendant_node_ids(
    db: AsyncSession,
    parent_id: str,
) -> list[str]:
    """递归 CTE 一次返回指定节点的所有子孙节点 ID（不含自身）。

    用 1 次 ``WITH RECURSIVE`` 替代原 N 次 ``select(PlantNode.id).where(parent_id=...)``
    递归 round-trip。设计参考 ``node_performance.batch_collect_descendant_loop_ids``。

    Args:
        db: 异步数据库会话
        parent_id: 起始父节点 ID

    Returns:
        子孙节点 ID 字符串列表（无序，不含 parent_id 自身）。
        无子孙时返回空列表。
    """
    cte_sql = text(
        """
        WITH RECURSIVE node_tree AS (
            SELECT id FROM plant_node WHERE parent_id = :parent_id
            UNION ALL
            SELECT child.id
            FROM plant_node child
            JOIN node_tree nt ON child.parent_id = nt.id
        )
        SELECT id FROM node_tree
        """
    )
    result = await db.execute(cte_sql, {"parent_id": parent_id})
    return [str(row[0]) for row in result.all()]


__all__ = ["collect_descendant_node_ids"]
