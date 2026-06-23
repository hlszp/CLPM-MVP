"""Loop ledger service — CRUD + status derivation (IDS v3.2 §2.2.7~2.2.11).

状态推导规则：
- INACTIVE: is_active = false
- PARTIAL: is_active = true 但 PV/SP/OP/MODE 4 个必填 Tag 缺失任一
- READY: is_active = true 且 4 个必填 Tag 全部关联（PID_P/PID_I/PID_D 可选）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.plant_node import PlantNode
from app.models.tag import TagRegistry

# 必填 Tag 角色
REQUIRED_ROLES = ("PV", "SP", "OP", "MODE")
# 全部 7 个 Tag 角色
ALL_ROLES = ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")
# 角色映射到响应字段名
ROLE_TO_FIELD = {
    "PV": "pv",
    "SP": "sp",
    "OP": "op",
    "MODE": "mode",
    "PID_P": "pid_p",
    "PID_I": "pid_i",
    "PID_D": "pid_d",
}


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志。"""
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


async def _get_loop_tag_mappings(db: AsyncSession, loop_id: str) -> dict[str, LoopTagMapping]:
    """获取回路的所有 Tag 关联（按角色索引）。"""
    result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    return {m.tag_role: m for m in result.scalars().all()}


async def derive_loop_status(
    db: AsyncSession,
    loop: LoopLedger,
    mappings: dict[str, LoopTagMapping] | None = None,
) -> str:
    """根据 is_active 和 Tag 关联状态推导回路 status。

    Args:
        db: 异步数据库会话
        loop: 回路对象
        mappings: 已查询的 Tag 关联（可选，避免重复查询）

    Returns:
        新状态：READY / PARTIAL / INACTIVE
    """
    # INACTIVE: 未激活
    if not loop.is_active:
        return "INACTIVE"

    # 查询 Tag 关联
    if mappings is None:
        mappings = await _get_loop_tag_mappings(db, str(loop.id))

    # 检查 4 个必填 Tag 是否全部关联
    for role in REQUIRED_ROLES:
        if role not in mappings:
            return "PARTIAL"
    return "READY"


async def _get_unit_name(db: AsyncSession, unit_id: str | None) -> str | None:
    """获取单元名称。"""
    if not unit_id:
        return None
    result = await db.execute(select(PlantNode).where(PlantNode.id == unit_id))
    node = result.scalar_one_or_none()
    return node.name if node else None


def _build_tag_mapping_status(mappings: dict[str, LoopTagMapping]) -> dict:
    """构建 7 Tag 关联状态摘要。"""
    return {
        "pv": "PV" in mappings,
        "sp": "SP" in mappings,
        "op": "OP" in mappings,
        "mode": "MODE" in mappings,
        "pid_p": "PID_P" in mappings,
        "pid_i": "PID_I" in mappings,
        "pid_d": "PID_D" in mappings,
    }


async def list_loops(
    db: AsyncSession,
    plant_node_id: str | None = None,
    control_mode: str | None = None,
    is_active: bool | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询回路列表。"""
    conditions = []
    if plant_node_id:
        conditions.append(LoopLedger.unit_id == plant_node_id)
    if is_active is not None:
        conditions.append(LoopLedger.is_active.is_(is_active))
    if status:
        conditions.append(func.upper(LoopLedger.status) == status.upper())
    if keyword:
        kw = f"%{keyword}%"
        conditions.append(
            or_(
                LoopLedger.tag_name.ilike(kw),
                LoopLedger.description.ilike(kw),
            )
        )

    # controlMode 从 MODE tag 读取，需要 join loop_tag_mapping + tag_registry
    # 简化处理：先查回路，再过滤 controlMode
    count_stmt = select(func.count()).select_from(LoopLedger)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = select(LoopLedger).order_by(LoopLedger.created_at.desc())
    for cond in conditions:
        stmt = stmt.where(cond)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    loops = result.scalars().all()

    # 批量查询 unit_name
    unit_ids = [str(loop.unit_id) for loop in loops if loop.unit_id]
    unit_map: dict[str, str] = {}
    if unit_ids:
        unit_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
        for node in unit_result.scalars().all():
            unit_map[str(node.id)] = node.name

    # 批量查询 Tag 关联状态
    loop_ids = [str(loop.id) for loop in loops]
    mappings_map: dict[str, dict[str, LoopTagMapping]] = {}
    if loop_ids:
        m_result = await db.execute(
            select(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids))
        )
        for m in m_result.scalars().all():
            mappings_map.setdefault(str(m.loop_id), {})[m.tag_role] = m

    # 批量查询 controlMode（MODE tag 的 current_value）
    mode_map: dict[str, str] = {}
    if loop_ids:
        mode_result = await db.execute(
            select(LoopTagMapping, TagRegistry)
            .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopTagMapping.loop_id.in_(loop_ids))
            .where(LoopTagMapping.tag_role == "MODE")
        )
        for mapping, tag in mode_result:
            mode_map[str(mapping.loop_id)] = _mode_value_to_label(tag.current_value)

    items = []
    for loop in loops:
        mappings = mappings_map.get(str(loop.id), {})
        items.append(
            {
                "loopId": str(loop.id),
                "tagName": loop.tag_name,
                "description": loop.description,
                "unitId": str(loop.unit_id) if loop.unit_id else None,
                "unitName": unit_map.get(str(loop.unit_id)) if loop.unit_id else None,
                "controlMode": mode_map.get(str(loop.id)),
                "isActive": bool(loop.is_active),
                "status": loop.status,
                "score": float(loop.score_weight) if loop.score_weight else None,
                "lastScoreAt": (
                    loop.last_aas_sync_at.isoformat() if loop.last_aas_sync_at else None
                ),
                "tagMappingStatus": _build_tag_mapping_status(mappings),
            }
        )

    # controlMode 过滤（后置过滤）
    if control_mode:
        items = [i for i in items if (i.get("controlMode") or "").lower() == control_mode.lower()]
        total = len(items)

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


def _mode_value_to_label(value: float | None) -> str | None:
    """MODE tag 值 → 控制模式标签。"""
    if value is None:
        return None
    mapping = {0: "Manual", 1: "Auto", 2: "Cascade", 3: "Cascade"}
    return mapping.get(int(value), "Unknown")


async def create_loop(
    db: AsyncSession,
    tag_name: str,
    description: str | None,
    unit_id: str | None,
    score_weights: dict | None,
    is_active: bool,
    remark: str | None,
    operator: str,
) -> dict:
    """创建回路。

    Raises:
        BizError: ERR_LOOP_DUPLICATE (tag_name 重复) / ERR_NODE_NOT_FOUND (unit_id 不存在)
    """
    # tag_name 唯一校验
    existing = await db.execute(select(LoopLedger).where(LoopLedger.tag_name == tag_name))
    if existing.scalar_one_or_none() is not None:
        raise BizError(
            code="ERR_LOOP_DUPLICATE",
            message=f"回路位号 {tag_name} 已存在",
            status_code=400,
        )

    # 校验 unit_id 存在
    if unit_id:
        unit_result = await db.execute(select(PlantNode).where(PlantNode.id == unit_id))
        if unit_result.scalar_one_or_none() is None:
            raise BizError(
                code="ERR_NODE_NOT_FOUND",
                message="所属单元不存在",
                status_code=404,
            )

    # 新建回路默认状态：INACTIVE（未激活）或 PARTIAL（已激活但无 Tag）
    status = "PARTIAL" if is_active else "INACTIVE"

    loop = LoopLedger(
        id=str(uuid4()),
        tag_name=tag_name,
        description=description,
        unit_id=unit_id,
        is_active=is_active,
        status=status,
        score_weights=score_weights,
        remark=remark,
        created_by=operator,
        updated_by=operator,
    )
    db.add(loop)
    await db.flush()

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_CREATE",
        target_type="loop_ledger",
        target_id=str(loop.id),
        after_value=json.dumps(
            {
                "tagName": tag_name,
                "description": description,
                "unitId": unit_id,
                "isActive": is_active,
                "status": status,
            },
            ensure_ascii=False,
        ),
    )
    await db.commit()

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "description": loop.description,
        "unitId": str(loop.unit_id) if loop.unit_id else None,
        "status": loop.status,
        "isActive": bool(loop.is_active),
        "scoreWeights": loop.score_weights,
        "remark": loop.remark,
        "createdAt": loop.created_at.isoformat() if loop.created_at else None,
        "createdBy": loop.created_by,
    }


async def get_loop_detail(db: AsyncSession, loop_id: str) -> dict:
    """获取回路详情（含 basicInfo/tagMapping/runtimeParams/aasSyncStatus）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
    """
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    unit_name = await _get_unit_name(db, str(loop.unit_id) if loop.unit_id else None)

    # 查询 Tag 关联 + Tag 详情
    mappings = await _get_loop_tag_mappings(db, loop_id)
    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 构建 tagMapping 块
    tag_mapping: dict[str, dict] = {}
    for role in ALL_ROLES:
        field = ROLE_TO_FIELD[role]
        mapping = mappings.get(role)
        if mapping and str(mapping.tag_id) in tags_map:
            tag = tags_map[str(mapping.tag_id)]
            tag_mapping[field] = {
                "tagId": str(tag.id),
                "tagName": tag.tag_name,
                "required": role in REQUIRED_ROLES,
                "associated": True,
            }
        else:
            tag_mapping[field] = {
                "tagId": None,
                "tagName": None,
                "required": role in REQUIRED_ROLES,
                "associated": False,
            }

    # 构建 runtimeParams（从 Tag 当前值读取）
    runtime_params: dict = {
        "controlMode": None,
        "pidP": None,
        "pidI": None,
        "pidD": None,
        "readAt": None,
    }
    for role in ALL_ROLES:
        mapping = mappings.get(role)
        if mapping and str(mapping.tag_id) in tags_map:
            tag = tags_map[str(mapping.tag_id)]
            if role == "MODE":
                runtime_params["controlMode"] = _mode_value_to_label(tag.current_value)
            elif role == "PID_P":
                runtime_params["pidP"] = tag.current_value
            elif role == "PID_I":
                runtime_params["pidI"] = tag.current_value
            elif role == "PID_D":
                runtime_params["pidD"] = tag.current_value
            # readAt 取最新的 last_sync_at
            if tag.last_sync_at:
                ts = tag.last_sync_at.isoformat()
                if runtime_params["readAt"] is None or ts > runtime_params["readAt"]:
                    runtime_params["readAt"] = ts

    # aasSyncStatus
    last_sync = None
    for tag in tags_map.values():
        if tag.last_sync_at:
            ts = tag.last_sync_at
            if last_sync is None or ts > last_sync:
                last_sync = ts

    return {
        "basicInfo": {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "description": loop.description,
            "unitId": str(loop.unit_id) if loop.unit_id else None,
            "unitName": unit_name,
            "isActive": bool(loop.is_active),
            "status": loop.status,
            "scoreWeights": loop.score_weights,
            "remark": loop.remark,
            "createdAt": loop.created_at.isoformat() if loop.created_at else None,
            "createdBy": loop.created_by,
            "updatedAt": loop.updated_at.isoformat() if loop.updated_at else None,
            "updatedBy": loop.updated_by,
        },
        "tagMapping": tag_mapping,
        "runtimeParams": runtime_params,
        "aasSyncStatus": {
            "lastSyncAt": last_sync.isoformat() if last_sync else None,
            "associatedTagCount": len(mappings),
        },
    }


async def update_loop(
    db: AsyncSession,
    loop_id: str,
    operator: str,
    description: str | None = None,
    score_weights: dict | None = None,
    is_active: bool | None = None,
    remark: str | None = None,
) -> dict:
    """更新回路（描述/评分权重/启用状态/备注）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
    """
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    before = {
        "description": loop.description,
        "scoreWeights": loop.score_weights,
        "isActive": loop.is_active,
        "remark": loop.remark,
    }
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if description is not None:
        loop.description = description
    if score_weights is not None:
        loop.score_weights = score_weights
    if is_active is not None:
        loop.is_active = is_active
    if remark is not None:
        loop.remark = remark
    loop.updated_by = operator

    # 重新推导 status
    new_status = await derive_loop_status(db, loop)
    loop.status = new_status

    after = {
        "description": loop.description,
        "scoreWeights": loop.score_weights,
        "isActive": loop.is_active,
        "remark": loop.remark,
        "status": new_status,
    }
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_UPDATE",
        target_type="loop_ledger",
        target_id=str(loop.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return {
        "loopId": str(loop.id),
        "description": loop.description,
        "scoreWeights": loop.score_weights,
        "isActive": bool(loop.is_active),
        "remark": loop.remark,
        "updatedAt": loop.updated_at.isoformat() if loop.updated_at else None,
        "updatedBy": loop.updated_by,
    }


async def delete_loop(
    db: AsyncSession,
    loop_id: str,
    operator: str,
) -> dict:
    """删除回路。

    校验：若回路有关联 Tag → ERR_LOOP_HAS_TAGS。
    实际：loop_tag_mapping 表设置了 ON DELETE CASCADE，但根据 IDS 要求需校验。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_LOOP_HAS_TAGS
    """
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 校验是否有关联 Tag
    tag_count_result = await db.execute(
        select(func.count()).select_from(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
    )
    tag_count = tag_count_result.scalar() or 0
    if tag_count > 0:
        raise BizError(
            code="ERR_LOOP_HAS_TAGS",
            message=f"回路存在 {tag_count} 个关联 Tag，无法删除",
            status_code=400,
        )

    before_json = json.dumps({"tagName": loop.tag_name, "status": loop.status}, ensure_ascii=False)

    # 删除回路（loop_tag_mapping 会因 CASCADE 自动清理，但此处已校验无关联）
    await db.execute(delete(LoopLedger).where(LoopLedger.id == loop_id))

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_DELETE",
        target_type="loop_ledger",
        target_id=loop_id,
        before_value=before_json,
    )
    await db.commit()

    return {
        "loopId": loop_id,
        "deleted": True,
        "deletedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


__all__ = [
    "ALL_ROLES",
    "REQUIRED_ROLES",
    "ROLE_TO_FIELD",
    "create_loop",
    "delete_loop",
    "derive_loop_status",
    "get_loop_detail",
    "list_loops",
    "update_loop",
]
