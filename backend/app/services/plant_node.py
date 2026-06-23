"""Plant node service — CRUD + tree building (IDS v3.2 §2.2.1~2.2.4)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from uuid import uuid4

import openpyxl
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


# ---------------------------------------------------------------------------
# 批量导入导出
# ---------------------------------------------------------------------------

# Excel 列头（4 列）
EXPORT_HEADERS = ["节点名称", "节点类型", "父节点名称", "层级路径"]


def _cell_str(value: object) -> str:
    """将 Excel 单元格值转为去除首尾空白的字符串，None/空返回空串。"""
    if value is None:
        return ""
    return str(value).strip()


async def export_plant_nodes(db: AsyncSession) -> bytes:
    """导出所有工厂节点为 Excel 文件（.xlsx），返回文件字节。

    列结构（4 列）：节点名称 / 节点类型 / 父节点名称 / 层级路径。
    节点按层级顺序输出（父节点在子节点之前），便于导入。
    """
    result = await db.execute(select(PlantNode))
    all_nodes = result.scalars().all()

    # id → node 映射
    node_map: dict[str, PlantNode] = {str(n.id): n for n in all_nodes}

    # 构建父子映射，用于层级排序
    children_map: dict[str | None, list[PlantNode]] = {}
    for node in all_nodes:
        key = str(node.parent_id) if node.parent_id else None
        children_map.setdefault(key, []).append(node)

    # 递归构建层级路径（带 memoization）
    path_cache: dict[str, str] = {}

    def build_path(node_id: str) -> str:
        if node_id in path_cache:
            return path_cache[node_id]
        node = node_map.get(node_id)
        if node is None:
            return ""
        if node.parent_id is None:
            path = node.name
        else:
            parent_path = build_path(str(node.parent_id))
            path = f"{parent_path}/{node.name}" if parent_path else node.name
        path_cache[node_id] = path
        return path

    # DFS 遍历，确保父节点在子节点之前
    ordered_nodes: list[PlantNode] = []

    def traverse(pid: str | None) -> None:
        for node in children_map.get(pid, []):
            ordered_nodes.append(node)
            traverse(str(node.id))

    traverse(None)

    # 构建 Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "工厂层级"
    ws.append(EXPORT_HEADERS)

    for node in ordered_nodes:
        parent_name = ""
        if node.parent_id:
            parent = node_map.get(str(node.parent_id))
            if parent:
                parent_name = parent.name
        path = build_path(str(node.id))
        ws.append([node.name, node.type, parent_name, path])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def import_plant_nodes(
    db: AsyncSession,
    file_bytes: bytes,
    operator: str,
) -> dict:
    """批量导入工厂节点（Excel .xlsx）。

    逐行处理：按 name + parent 查找节点，存在则更新类型，不存在则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise BizError(
            code="ERR_FILE_PARSE",
            message=f"Excel 文件解析失败: {exc}",
            status_code=400,
        ) from exc

    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    total = 0
    inserted = 0
    updated = 0
    failed = 0
    errors: list[dict] = []

    # 缓存：parent_name → parent_id，避免重复查询
    parent_cache: dict[str, str] = {}

    for row_idx, row in enumerate(rows, start=2):  # 第 1 行为表头
        total += 1
        name = _cell_str(row[0]) if len(row) > 0 else ""

        if not name:
            errors.append({"row": row_idx, "message": "节点名称不能为空"})
            failed += 1
            continue

        node_type = _cell_str(row[1]) if len(row) > 1 else ""
        parent_name = _cell_str(row[2]) if len(row) > 2 else ""
        # 第 4 列（层级路径）仅用于展示，导入时不使用

        # 节点类型校验
        if node_type not in VALID_NODE_TYPES:
            errors.append(
                {
                    "row": row_idx,
                    "name": name,
                    "message": f"节点类型非法，必须为 {','.join(VALID_NODE_TYPES)}",
                }
            )
            failed += 1
            continue

        try:
            # 使用 SAVEPOINT 保证单行失败不影响其他行
            async with db.begin_nested():
                is_update = await _import_one_node(
                    db=db,
                    name=name,
                    node_type=node_type,
                    parent_name=parent_name,
                    operator=operator,
                    parent_cache=parent_cache,
                )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"row": row_idx, "name": name, "message": str(exc)})
            continue

        if is_update:
            updated += 1
        else:
            inserted += 1

    await db.commit()

    return {
        "total": total,
        "inserted": inserted,
        "updated": updated,
        "failed": failed,
        "errors": errors,
    }


async def _import_one_node(
    db: AsyncSession,
    name: str,
    node_type: str,
    parent_name: str,
    operator: str,
    parent_cache: dict[str, str],
) -> bool:
    """处理单行导入，返回是否为更新（True）或新建（False）。

    在调用方的 SAVEPOINT 内执行，异常会触发回滚至 SAVEPOINT。
    """
    # 查找父节点
    parent_id: str | None = None
    if parent_name:
        if parent_name in parent_cache:
            parent_id = parent_cache[parent_name]
        else:
            p_result = await db.execute(
                select(PlantNode).where(PlantNode.name == parent_name)
            )
            parent = p_result.scalars().first()
            if parent is None:
                raise ValueError(f"父节点 '{parent_name}' 不存在")
            parent_id = str(parent.id)
            parent_cache[parent_name] = parent_id

    # FACTORY 类型必须为顶层节点
    if node_type == "FACTORY" and parent_id:
        raise ValueError("FACTORY 类型节点必须为顶层节点（无父节点）")

    # 按 name + parent 查找节点是否已存在
    if parent_id:
        result = await db.execute(
            select(PlantNode).where(
                PlantNode.name == name,
                PlantNode.parent_id == parent_id,
            )
        )
    else:
        result = await db.execute(
            select(PlantNode).where(
                PlantNode.name == name,
                PlantNode.parent_id.is_(None),
            )
        )
    node = result.scalars().first()
    is_update = node is not None

    if is_update:
        before_value = json.dumps(
            {
                "name": node.name,
                "type": node.type,
                "parentId": str(node.parent_id) if node.parent_id else None,
            },
            ensure_ascii=False,
        )
        node.type = node_type
        after_value = json.dumps(
            {"name": name, "type": node_type, "parentId": parent_id},
            ensure_ascii=False,
        )
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="PLANT_NODE_IMPORT_UPDATE",
            target_type="plant_node",
            target_id=str(node.id),
            before_value=before_value,
            after_value=after_value,
        )
    else:
        node = PlantNode(
            id=str(uuid4()),
            name=name,
            type=node_type,
            parent_id=parent_id,
        )
        db.add(node)
        await db.flush()
        # 缓存新建节点，供后续行作为父节点查找
        parent_cache[name] = str(node.id)
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="PLANT_NODE_IMPORT",
            target_type="plant_node",
            target_id=str(node.id),
            after_value=json.dumps(
                {"name": name, "type": node_type, "parentId": parent_id},
                ensure_ascii=False,
            ),
        )

    await db.flush()
    return is_update


__all__ = [
    "create_plant_node",
    "delete_plant_node",
    "export_plant_nodes",
    "import_plant_nodes",
    "list_plant_tree",
    "update_plant_node",
]
