"""Plant node service — CRUD + tree building (IDS v3.2 §2.2.1~2.2.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.loop import LoopLedger
from app.models.plant_node import PlantNode

# 节点类型枚举
VALID_NODE_TYPES = {"FACTORY", "UNIT", "EQUIPMENT"}


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志（不抛异常，不影响主流程）。"""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


def _node_to_dict(node: PlantNode) -> dict:
    """将 PlantNode ORM 转为字典。"""
    return {
        "id": str(node.id),
        "name": node.name,
        "type": node.type,
        "parentId": str(node.parent_id) if node.parent_id else None,
    }


async def list_plant_tree(db: AsyncSession, parent_id: str | None = None) -> list[dict]:
    """获取工厂节点树形结构。

    Args:
        db: 异步数据库会话
        parent_id: 父节点 ID。None 表示从顶层开始

    Returns:
        树形结构列表（递归 children）
    """
    result = await db.execute(select(PlantNode))
    all_nodes = result.scalars().all()

    # 构建父子映射
    children_map: dict[str | None, list[PlantNode]] = {}
    for node in all_nodes:
        key = str(node.parent_id) if node.parent_id else None
        children_map.setdefault(key, []).append(node)

    def build_tree(pid: str | None) -> list[dict]:
        nodes = children_map.get(pid, [])
        tree: list[dict] = []
        for node in nodes:
            node_dict = _node_to_dict(node)
            node_dict["children"] = build_tree(str(node.id))
            tree.append(node_dict)
        return tree

    # 若指定 parent_id，则返回该节点的子树；否则从顶层开始
    root_pid = parent_id if parent_id else None
    return build_tree(root_pid)


async def create_plant_node(
    db: AsyncSession,
    name: str,
    node_type: str,
    parent_id: str | None,
    operator: str,
) -> dict:
    """创建工厂节点。

    Raises:
        BizError: ERR_VALIDATION (类型非法/FACTORY 必须为顶层) / ERR_NODE_NOT_FOUND (父节点不存在)
    """
    if node_type not in VALID_NODE_TYPES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"节点类型非法，必须为 {','.join(VALID_NODE_TYPES)}",
            status_code=400,
        )

    # FACTORY 类型必须为顶层节点
    if node_type == "FACTORY" and parent_id:
        raise BizError(
            code="ERR_VALIDATION",
            message="FACTORY 类型节点必须为顶层节点（parentId 为 null）",
            status_code=400,
        )

    # 校验父节点存在
    if parent_id:
        result = await db.execute(select(PlantNode).where(PlantNode.id == parent_id))
        parent = result.scalar_one_or_none()
        if parent is None:
            raise BizError(
                code="ERR_NODE_NOT_FOUND",
                message="父节点不存在",
                status_code=404,
            )

    # 创建节点
    node = PlantNode(
        id=str(uuid4()),
        name=name,
        type=node_type,
        parent_id=parent_id,
    )
    db.add(node)
    await db.flush()

    # 审计日志
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="PLANT_NODE_CREATE",
        target_type="plant_node",
        target_id=str(node.id),
        after_value=f'{{"name":"{name}","type":"{node_type}","parentId":"{parent_id}"}}',
    )
    await db.commit()

    return {
        "id": str(node.id),
        "name": node.name,
        "type": node.type,
        "parentId": str(node.parent_id) if node.parent_id else None,
    }


async def update_plant_node(
    db: AsyncSession,
    node_id: str,
    name: str,
    operator: str,
) -> dict:
    """更新工厂节点名称。

    Raises:
        BizError: ERR_NODE_NOT_FOUND (节点不存在)
    """
    result = await db.execute(select(PlantNode).where(PlantNode.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise BizError(
            code="ERR_NODE_NOT_FOUND",
            message="节点不存在",
            status_code=404,
        )

    before_value = f'{{"name":"{node.name}"}}'
    node.name = name
    after_value = f'{{"name":"{name}"}}'

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="PLANT_NODE_UPDATE",
        target_type="plant_node",
        target_id=str(node.id),
        before_value=before_value,
        after_value=after_value,
    )
    await db.commit()

    return {"success": True}


async def delete_plant_node(
    db: AsyncSession,
    node_id: str,
    operator: str,
) -> dict:
    """删除工厂节点。

    校验：节点存在子节点 → ERR_NODE_HAS_CHILDREN；节点关联回路 → ERR_NODE_HAS_LOOPS。

    Raises:
        BizError: ERR_NODE_NOT_FOUND / ERR_NODE_HAS_CHILDREN / ERR_NODE_HAS_LOOPS
    """
    result = await db.execute(select(PlantNode).where(PlantNode.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise BizError(
            code="ERR_NODE_NOT_FOUND",
            message="节点不存在",
            status_code=404,
        )

    # 校验子节点
    children_count_result = await db.execute(
        select(func.count()).select_from(PlantNode).where(PlantNode.parent_id == node_id)
    )
    children_count = children_count_result.scalar() or 0
    if children_count > 0:
        raise BizError(
            code="ERR_NODE_HAS_CHILDREN",
            message="该节点存在子节点，无法删除",
            status_code=400,
        )

    # 校验关联回路（loop_ledger.unit_id）
    loops_count_result = await db.execute(
        select(func.count()).select_from(LoopLedger).where(LoopLedger.unit_id == node_id)
    )
    loops_count = loops_count_result.scalar() or 0
    if loops_count > 0:
        raise BizError(
            code="ERR_NODE_HAS_LOOPS",
            message="该节点存在关联回路，无法删除",
            status_code=400,
        )

    before_value = f'{{"name":"{node.name}","type":"{node.type}"}}'
    await db.execute(delete(PlantNode).where(PlantNode.id == node_id))

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="PLANT_NODE_DELETE",
        target_type="plant_node",
        target_id=str(node.id),
        before_value=before_value,
    )
    await db.commit()

    return {"success": True}


__all__ = [
    "create_plant_node",
    "delete_plant_node",
    "list_plant_tree",
    "update_plant_node",
]
