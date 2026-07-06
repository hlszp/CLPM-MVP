"""Loop ledger service — CRUD + status derivation (IDS v3.2 §2.2.7~2.2.11).

状态推导规则：
- INACTIVE: is_active = false
- PARTIAL: is_active = true 但 PV/SP/OP/MODE 4 个必填 Tag 缺失任一
- READY: is_active = true 且 4 个必填 Tag 全部关联（PID_P/PID_I/PID_D 可选）
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

import openpyxl
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.plant_node import PlantNode
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

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


async def _get_descendant_node_ids(db: AsyncSession, parent_id: str) -> list[str]:
    """递归获取所有子孙节点 ID。"""
    result = await db.execute(select(PlantNode.id).where(PlantNode.parent_id == parent_id))
    child_ids = [str(row[0]) for row in result]
    all_ids = list(child_ids)
    for child_id in child_ids:
        all_ids.extend(await _get_descendant_node_ids(db, child_id))
    return all_ids


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
    loop_type: str | None = None,
    control_type: str | None = None,
    importance_level: int | None = None,
    monitor_status: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询回路列表。

    Args:
        importance_level: 按回路重要等级筛选（1/2/3）
        control_type: 按控制类型筛选（STABLE/SLOW/FAST/LOGIC）
        monitor_status: 按监控状态筛选（True=is_active=True，False=is_active=False）

    Raises:
        ValueError: is_active 与 monitor_status 同时传入但值不一致（语义冲突）
    """
    # P3 #42: is_active 与 monitor_status 都映射到 LoopLedger.is_active 字段，
    # 同时传入不同值会生成 is_active=X AND is_active=Y → 永远返回空结果。
    # 校验：两个都传时值必须一致；统一为单一条件避免重复 AND。
    if is_active is not None and monitor_status is not None:
        if is_active != monitor_status:
            raise ValueError(
                "isActive 与 monitorStatus 语义相同（均映射到 is_active 字段），"
                "同时传入时值必须一致"
            )
        # 值一致时只保留一个，避免重复 AND
        monitor_status = None

    conditions = []
    if plant_node_id:
        # 递归获取所有子孙节点 ID，包含自身
        all_node_ids = await _get_descendant_node_ids(db, plant_node_id)
        all_node_ids.append(plant_node_id)
        conditions.append(LoopLedger.unit_id.in_(all_node_ids))
    if is_active is not None:
        conditions.append(LoopLedger.is_active.is_(is_active))
    if status:
        conditions.append(func.upper(LoopLedger.status) == status.upper())
    if loop_type:
        conditions.append(func.upper(LoopLedger.loop_type) == loop_type.upper())
    if control_type:
        # P2 #24: 控制类型筛选（STABLE/SLOW/FAST/LOGIC）
        conditions.append(func.upper(LoopLedger.control_type) == control_type.upper())
    if importance_level is not None:
        conditions.append(LoopLedger.importance_level == importance_level)
    if monitor_status is not None:
        # monitor_status=True → is_active=True（在监控中）
        # monitor_status=False → is_active=False（已停用监控）
        conditions.append(LoopLedger.is_active.is_(monitor_status))
    if keyword:
        kw = f"%{keyword}%"
        conditions.append(
            or_(
                LoopLedger.tag_name.ilike(kw),
                LoopLedger.description.ilike(kw),
            )
        )

    # controlMode 过滤下沉到 SQL 层（EXISTS 子查询），避免后置过滤导致 total 与分页错乱
    if control_mode:
        mode_values = _control_mode_to_values(control_mode)
        if not mode_values:
            # 无法识别的控制模式标签，直接返回空结果
            return {"items": [], "total": 0, "page": page, "pageSize": page_size}
        mode_exists = (
            select(LoopTagMapping.tag_id)
            .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopTagMapping.loop_id == LoopLedger.id)
            .where(LoopTagMapping.tag_role == "MODE")
            .where(TagRegistry.current_value.in_(mode_values))
            .exists()
        )
        conditions.append(mode_exists)

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
                "loopType": loop.loop_type,
                "controlType": loop.control_type,
                "importanceLevel": loop.importance_level,
                "includeInEvaluation": loop.include_in_evaluation,
                "score": float(loop.score_weight) if loop.score_weight else None,
                "lastScoreAt": (
                    loop.last_aas_sync_at.isoformat() if loop.last_aas_sync_at else None
                ),
                "tagMappingStatus": _build_tag_mapping_status(mappings),
            }
        )

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


# 反向映射：控制模式标签 → MODE 值集合
# 与 _mode_value_to_label 保持一致（Cascade 对应 2 和 3）
_CONTROL_MODE_VALUES: dict[str, set[int]] = {
    "manual": {0},
    "auto": {1},
    "cascade": {2, 3},
}


def _control_mode_to_values(control_mode: str) -> list[int]:
    """控制模式标签 → MODE 值列表（大小写不敏感）。

    用于 SQL 层 EXISTS 子查询过滤，将前端传入的 controlMode label
    反向映射为 TagRegistry.current_value 的合法值集合。

    Args:
        control_mode: 控制模式标签（Manual/Auto/Cascade），大小写不敏感

    Returns:
        MODE 值列表（如 "Auto" → [1]）；无法识别时返回空列表
    """
    if not control_mode:
        return []
    return sorted(_CONTROL_MODE_VALUES.get(control_mode.lower(), set()))


async def create_loop(
    db: AsyncSession,
    tag_name: str,
    description: str | None,
    unit_id: str | None,
    score_weights: dict | None,
    is_active: bool,
    remark: str | None,
    operator: str,
    loop_type: str | None = None,
    control_type: str | None = None,
    importance_level: int | None = None,
    include_in_evaluation: bool | None = None,
    modeattr_tag_id: str | None = None,
    data_retention_days: int | None = None,
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
    # v5.3 对齐 FDS §5.2.3 / DDS v4.1：include_in_evaluation 默认 True（参与评估）
    if include_in_evaluation is None:
        include_in_evaluation = True

    loop = LoopLedger(
        id=str(uuid4()),
        tag_name=tag_name,
        description=description,
        unit_id=unit_id,
        is_active=is_active,
        status=status,
        loop_type=loop_type,
        control_type=control_type,
        importance_level=importance_level,
        include_in_evaluation=include_in_evaluation,
        modeattr_tag_id=modeattr_tag_id,
        data_retention_days=data_retention_days,
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
                "loopType": loop_type,
                "controlType": control_type,
                "importanceLevel": importance_level,
                "includeInEvaluation": include_in_evaluation,
                "modeattrTagId": modeattr_tag_id,
                "dataRetentionDays": data_retention_days,
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
        "loopType": loop.loop_type,
        "controlType": loop.control_type,
        "importanceLevel": loop.importance_level,
        "includeInEvaluation": loop.include_in_evaluation,
        "modeattrTagId": str(loop.modeattr_tag_id) if loop.modeattr_tag_id else None,
        "dataRetentionDays": loop.data_retention_days,
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
            "loopType": loop.loop_type,
            "controlType": loop.control_type,
            "importanceLevel": loop.importance_level,
            "includeInEvaluation": loop.include_in_evaluation,
            "modeattrTagId": str(loop.modeattr_tag_id) if loop.modeattr_tag_id else None,
            "dataRetentionDays": loop.data_retention_days,
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
    loop_type: str | None = None,
    control_type: str | None = None,
    importance_level: int | None = None,
    include_in_evaluation: bool | None = None,
    modeattr_tag_id: str | None = None,
    data_retention_days: int | None = None,
) -> dict:
    """更新回路（描述/评分权重/启用状态/备注/回路类型/控制类型/重要等级/参评/APC位号/保留周期）。

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
        "loopType": loop.loop_type,
        "controlType": loop.control_type,
        "importanceLevel": loop.importance_level,
        "includeInEvaluation": loop.include_in_evaluation,
        "modeattrTagId": str(loop.modeattr_tag_id) if loop.modeattr_tag_id else None,
        "dataRetentionDays": loop.data_retention_days,
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
    if loop_type is not None:
        loop.loop_type = loop_type
    if control_type is not None:
        loop.control_type = control_type
    if importance_level is not None:
        loop.importance_level = importance_level
    if include_in_evaluation is not None:
        loop.include_in_evaluation = include_in_evaluation
    if modeattr_tag_id is not None:
        loop.modeattr_tag_id = modeattr_tag_id
    if data_retention_days is not None:
        loop.data_retention_days = data_retention_days
    loop.updated_by = operator

    # 重新推导 status
    new_status = await derive_loop_status(db, loop)
    loop.status = new_status

    after = {
        "description": loop.description,
        "scoreWeights": loop.score_weights,
        "isActive": loop.is_active,
        "remark": loop.remark,
        "loopType": loop.loop_type,
        "controlType": loop.control_type,
        "importanceLevel": loop.importance_level,
        "includeInEvaluation": loop.include_in_evaluation,
        "modeattrTagId": str(loop.modeattr_tag_id) if loop.modeattr_tag_id else None,
        "dataRetentionDays": loop.data_retention_days,
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
    # 刷新对象以获取 onupdate=func.now() 生成的 updated_at（async session
    # 不能同步 lazy load，否则触发 MissingGreenlet）
    await db.refresh(loop)

    return {
        "loopId": str(loop.id),
        "description": loop.description,
        "scoreWeights": loop.score_weights,
        "isActive": bool(loop.is_active),
        "remark": loop.remark,
        "loopType": loop.loop_type,
        "controlType": loop.control_type,
        "importanceLevel": loop.importance_level,
        "includeInEvaluation": loop.include_in_evaluation,
        "modeattrTagId": str(loop.modeattr_tag_id) if loop.modeattr_tag_id else None,
        "dataRetentionDays": loop.data_retention_days,
        "updatedAt": loop.updated_at.isoformat() if loop.updated_at else None,
        "updatedBy": loop.updated_by,
    }


async def delete_loop(
    db: AsyncSession,
    loop_id: str,
    operator: str,
) -> dict:
    """软删除回路（P1 #9: 统一为软删，与批删行为对齐）。

    校验：若回路有关联 Tag → ERR_LOOP_HAS_TAGS。
    实际：置 is_active=False, status=INACTIVE（软删除，保留记录可追溯）。

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

    before_json = json.dumps(
        {"tagName": loop.tag_name, "is_active": loop.is_active, "status": loop.status},
        ensure_ascii=False,
    )

    # 软删除（P1 #9: 与 batch_delete_loops 行为对齐）
    loop.is_active = False
    loop.status = "INACTIVE"
    loop.updated_by = operator

    after_json = json.dumps(
        {"tagName": loop.tag_name, "is_active": False, "status": "INACTIVE"},
        ensure_ascii=False,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_DELETE",
        target_type="loop_ledger",
        target_id=loop_id,
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return {
        "loopId": loop_id,
        "deleted": True,
        "deletedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


# ---------------------------------------------------------------------------
# 批量导入导出 (S2-LOOP-009)
# ---------------------------------------------------------------------------

# Excel 列头（12 列，对齐 loopList.xlsx，新增"回路类型"列）
EXPORT_HEADERS = [
    "自控回路编号",
    "自控回路名称",
    "设定值位号",
    "测量值位号",
    "输出值位号",
    "控制方式位号",
    "所属区域编号",
    "是否启用",
    "比例带",
    "积分时间",
    "微分时间",
    "回路类型",
]

# 导入时列索引 → Tag 角色（索引从 0 开始）
_IMPORT_ROLE_COLUMNS: dict[int, str] = {
    2: "SP",
    3: "PV",
    4: "OP",
    5: "MODE",
    8: "PID_P",
    9: "PID_I",
    10: "PID_D",
}
# 回路类型列索引（"回路类型"列）
_LOOP_TYPE_COLUMN_INDEX = 11


def _cell_str(value: object) -> str:
    """将 Excel 单元格值转为去除首尾空白的字符串，None/空返回空串。"""
    if value is None:
        return ""
    return str(value).strip()


async def export_loops(
    db: AsyncSession,
    plant_node_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> bytes:
    """导出所有回路为 Excel 文件（.xlsx），返回文件字节。

    支持按 plantNodeId/status/keyword 筛选（可选）。
    """
    conditions = []
    if plant_node_id:
        conditions.append(LoopLedger.unit_id == plant_node_id)
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

    stmt = select(LoopLedger).order_by(LoopLedger.tag_name)
    for cond in conditions:
        stmt = stmt.where(cond)
    result = await db.execute(stmt)
    loops = result.scalars().all()

    loop_ids = [str(loop.id) for loop in loops]
    unit_ids = [str(loop.unit_id) for loop in loops if loop.unit_id]

    # 批量查询 unit_name
    unit_map: dict[str, str] = {}
    if unit_ids:
        u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
        for node in u_result.scalars().all():
            unit_map[str(node.id)] = node.name

    # 批量查询 Tag 关联 + Tag 名称
    tag_name_map: dict[str, dict[str, str]] = {}
    if loop_ids:
        m_result = await db.execute(
            select(LoopTagMapping, TagRegistry)
            .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopTagMapping.loop_id.in_(loop_ids))
        )
        for mapping, tag in m_result:
            tag_name_map.setdefault(str(mapping.loop_id), {})[mapping.tag_role] = tag.tag_name

    # 构建 Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "回路台账"
    ws.append(EXPORT_HEADERS)

    for loop in loops:
        tags = tag_name_map.get(str(loop.id), {})
        unit_name = unit_map.get(str(loop.unit_id)) if loop.unit_id else ""
        is_active_str = "是" if loop.is_active else "否"
        ws.append(
            [
                loop.tag_name,
                loop.description or "",
                tags.get("SP", ""),
                tags.get("PV", ""),
                tags.get("OP", ""),
                tags.get("MODE", ""),
                unit_name,
                is_active_str,
                tags.get("PID_P", ""),
                tags.get("PID_I", ""),
                tags.get("PID_D", ""),
                loop.loop_type or "",
            ]
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def import_loops(
    db: AsyncSession,
    file_bytes: bytes,
    operator: str,
) -> dict:
    """批量导入回路（Excel .xlsx）。

    逐行处理：回路编号已存在则更新，否则新建。
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

    # 缓存：plant_node name → id，tag name → id
    plant_node_cache: dict[str, str] = {}
    tag_cache: dict[str, str] = {}

    for row_idx, row in enumerate(rows, start=2):  # 第 1 行为表头
        total += 1
        tag_name = _cell_str(row[0]) if len(row) > 0 else ""

        if not tag_name:
            errors.append({"row": row_idx, "message": "回路编号不能为空"})
            failed += 1
            continue

        # 解析单元格（不涉及 DB，放在 savepoint 外）
        description = _cell_str(row[1]) if len(row) > 1 else ""
        role_tag_values: dict[str, str] = {}
        for col_idx, role in _IMPORT_ROLE_COLUMNS.items():
            role_tag_values[role] = _cell_str(row[col_idx]) if len(row) > col_idx else ""
        unit_name = _cell_str(row[6]) if len(row) > 6 else ""
        is_active_str = _cell_str(row[7]) if len(row) > 7 else "是"
        is_active = is_active_str in ("是", "true", "True", "1", "YES", "yes", "Y", "y")
        loop_type = (
            _cell_str(row[_LOOP_TYPE_COLUMN_INDEX]) if len(row) > _LOOP_TYPE_COLUMN_INDEX else ""
        ) or None

        is_update = False
        try:
            # 使用 SAVEPOINT 保证单行失败不影响其他行
            async with db.begin_nested():
                is_update = await _import_one_row(
                    db=db,
                    tag_name=tag_name,
                    description=description,
                    unit_name=unit_name,
                    is_active=is_active,
                    role_tag_values=role_tag_values,
                    operator=operator,
                    plant_node_cache=plant_node_cache,
                    tag_cache=tag_cache,
                    loop_type=loop_type,
                )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"row": row_idx, "tagName": tag_name, "message": str(exc)})
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


async def _import_one_row(
    db: AsyncSession,
    tag_name: str,
    description: str,
    unit_name: str,
    is_active: bool,
    role_tag_values: dict[str, str],
    operator: str,
    plant_node_cache: dict[str, str],
    tag_cache: dict[str, str],
    loop_type: str | None = None,
) -> bool:
    """处理单行导入，返回是否为更新（True）或新建（False）。

    在调用方的 SAVEPOINT 内执行，异常会触发回滚至 SAVEPOINT。
    """
    # 查找/创建 plant_node
    unit_id: str | None = None
    if unit_name:
        if unit_name in plant_node_cache:
            unit_id = plant_node_cache[unit_name]
        else:
            p_result = await db.execute(select(PlantNode).where(PlantNode.name == unit_name))
            node = p_result.scalar_one_or_none()
            if node is None:
                node = PlantNode(
                    id=str(uuid4()),
                    name=unit_name,
                    type="UNIT",
                )
                db.add(node)
                await db.flush()
            unit_id = str(node.id)
            plant_node_cache[unit_name] = unit_id

    # 查找/创建回路
    result = await db.execute(select(LoopLedger).where(LoopLedger.tag_name == tag_name))
    loop = result.scalar_one_or_none()
    is_update = loop is not None

    if is_update:
        loop.description = description or loop.description
        loop.unit_id = unit_id
        loop.is_active = is_active
        if loop_type is not None:
            loop.loop_type = loop_type
        loop.updated_by = operator
        # 删除现有关联 Tag
        await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id == str(loop.id)))
    else:
        loop = LoopLedger(
            id=str(uuid4()),
            tag_name=tag_name,
            description=description or None,
            unit_id=unit_id,
            is_active=is_active,
            status="PARTIAL",
            loop_type=loop_type,
            created_by=operator,
            updated_by=operator,
        )
        db.add(loop)
        await db.flush()

    # 创建 Tag 关联
    new_mappings: dict[str, LoopTagMapping] = {}
    for role, t_name in role_tag_values.items():
        if not t_name:
            continue
        if t_name in tag_cache:
            tag_id = tag_cache[t_name]
        else:
            t_result = await db.execute(select(TagRegistry).where(TagRegistry.tag_name == t_name))
            tag = t_result.scalar_one_or_none()
            if tag is None:
                # P3 #47: 不再静默创建 TagRegistry（绕过 AAS 同步会引入"幽灵 Tag"）
                # 改为显式警告 + 标记 tag_description，提示运维人员后续需通过 AAS 同步补全元数据
                logger.warning(
                    "Excel 导入自动创建 Tag（未通过 AAS 同步）: tag_name=%s, role=%s, "
                    "operator=%s — 该 Tag 缺少量程/单位/measure_type 等元数据，"
                    "请尽快执行 AAS 同步以补全",
                    t_name,
                    role,
                    operator,
                )
                tag = TagRegistry(
                    id=str(uuid4()),
                    tag_name=t_name,
                    tag_description="[Excel 导入自动创建，未通过 AAS 同步，元数据待补全]",
                    tag_type=role,
                    last_sync_at=datetime.now(UTC).replace(tzinfo=None),
                    is_linked=True,
                )
                db.add(tag)
                await db.flush()
            else:
                tag.is_linked = True
            tag_id = str(tag.id)
            tag_cache[t_name] = tag_id

        mapping = LoopTagMapping(
            id=str(uuid4()),
            loop_id=str(loop.id),
            tag_id=tag_id,
            tag_role=role,
            is_required=role in REQUIRED_ROLES,
        )
        db.add(mapping)
        new_mappings[role] = mapping

    # 推导 status
    new_status = await derive_loop_status(db, loop, mappings=new_mappings)
    loop.status = new_status

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_IMPORT_UPDATE" if is_update else "LOOP_IMPORT",
        target_type="loop_ledger",
        target_id=str(loop.id),
        after_value=json.dumps(
            {"tagName": tag_name, "status": new_status, "isActive": is_active},
            ensure_ascii=False,
        ),
    )

    await db.flush()
    return is_update


__all__ = [
    "ALL_ROLES",
    "REQUIRED_ROLES",
    "ROLE_TO_FIELD",
    "create_loop",
    "delete_loop",
    "derive_loop_status",
    "export_loops",
    "get_loop_detail",
    "import_loops",
    "list_loops",
    "update_loop",
]
