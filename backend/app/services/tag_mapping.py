"""Loop Tag mapping service (IDS v3.2 §2.2.12~2.2.13).

7 个 Tag 槽位：PV/SP/OP/MODE（必填）+ PID_P/PID_I/PID_D（可选）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.services.loop import (
    ALL_ROLES,
    REQUIRED_ROLES,
    derive_loop_status,
)


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


async def get_loop_tags(db: AsyncSession, loop_id: str) -> dict:
    """获取回路 7 个 Tag 槽位关联状态。

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

    # 查询所有 Tag 关联
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    # 查询关联的 Tag 详情
    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 构建 7 个槽位响应
    tags_list = []
    for role in ALL_ROLES:
        mapping = mappings.get(role)
        if mapping and str(mapping.tag_id) in tags_map:
            tag = tags_map[str(mapping.tag_id)]
            tags_list.append(
                {
                    "role": role,
                    "tagId": str(tag.id),
                    "tagName": tag.tag_name,
                    "description": tag.tag_description,
                    "required": role in REQUIRED_ROLES,
                    "associated": True,
                    "currentValue": tag.current_value,
                    "quality": tag.quality if role == "PV" else None,
                    "lastSyncAt": (tag.last_sync_at.isoformat() if tag.last_sync_at else None),
                }
            )
        else:
            tags_list.append(
                {
                    "role": role,
                    "tagId": None,
                    "tagName": None,
                    "description": None,
                    "required": role in REQUIRED_ROLES,
                    "associated": False,
                    "currentValue": None,
                    "quality": None,
                    "lastSyncAt": None,
                }
            )

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "status": loop.status,
        "tags": tags_list,
    }


async def update_loop_tags(
    db: AsyncSession,
    loop_id: str,
    operator: str,
    pv: str | None = None,
    sp: str | None = None,
    op: str | None = None,
    mode: str | None = None,
    pid_p: str | None = None,
    pid_i: str | None = None,
    pid_d: str | None = None,
) -> dict:
    """批量更新回路 Tag 关联。

    规则：
    - PV/SP/OP/MODE 必填，缺失时 status→PARTIAL（API 调用成功，不报错）
    - PID_P/PID_I/PID_D 可选
    - 全部必填为 null → ERR_LOOP_TAG_REQUIRED
    - Tag 不存在于 tag_registry → ERR_TAG_NOT_FOUND

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_LOOP_TAG_REQUIRED / ERR_TAG_NOT_FOUND
    """
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 构建角色 → tag_id 映射
    role_tag_map: dict[str, str | None] = {
        "PV": pv,
        "SP": sp,
        "OP": op,
        "MODE": mode,
        "PID_P": pid_p,
        "PID_I": pid_i,
        "PID_D": pid_d,
    }

    # 校验：全部必填为 null → ERR_LOOP_TAG_REQUIRED
    required_all_null = all(role_tag_map[role] is None for role in REQUIRED_ROLES)
    if required_all_null:
        raise BizError(
            code="ERR_LOOP_TAG_REQUIRED",
            message="至少需要提供一个必填 Tag（PV/SP/OP/MODE）",
            status_code=400,
        )

    # 校验：所有非 null 的 tag_id 必须存在于 tag_registry
    non_null_tag_ids = [tid for tid in role_tag_map.values() if tid is not None]
    existing_tags: dict[str, TagRegistry] = {}
    if non_null_tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(non_null_tag_ids)))
        for t in t_result.scalars().all():
            existing_tags[str(t.id)] = t
        # 检查是否有不存在的 tag_id
        for role, tid in role_tag_map.items():
            if tid is not None and tid not in existing_tags:
                raise BizError(
                    code="ERR_TAG_NOT_FOUND",
                    message=f"角色 {role} 对应的 Tag {tid} 不存在于 tag_registry",
                    status_code=404,
                )

    # 查询现有关联（用于审计 before_value）
    existing_result = await db.execute(
        select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
    )
    existing_mappings = {m.tag_role: m for m in existing_result.scalars().all()}
    before_json = json.dumps(
        {role: (str(m.tag_id) if m else None) for role, m in existing_mappings.items()},
        ensure_ascii=False,
    )

    # 删除现有关联
    await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))

    # 插入新关联
    new_mappings: dict[str, LoopTagMapping] = {}
    for role in ALL_ROLES:
        tag_id = role_tag_map.get(role)
        if tag_id is not None:
            mapping = LoopTagMapping(
                id=str(uuid4()),
                loop_id=loop_id,
                tag_id=tag_id,
                tag_role=role,
                is_required=role in REQUIRED_ROLES,
            )
            db.add(mapping)
            new_mappings[role] = mapping

    # 更新 tag_registry.is_linked
    # 先把之前关联的 tag 设为未关联（仅当该 tag 不再被任何回路映射引用时）
    # 注意：本回路的旧映射已在上方删除，此处剩余引用均来自其他回路
    for old_mapping in existing_mappings.values():
        old_tag_id = str(old_mapping.tag_id)
        if old_tag_id not in [tid for tid in role_tag_map.values() if tid]:
            # 这个 tag 不再被本回路关联，检查是否仍被其他回路引用
            ref_count_result = await db.execute(
                select(func.count())
                .select_from(LoopTagMapping)
                .where(LoopTagMapping.tag_id == old_tag_id)
            )
            if (ref_count_result.scalar() or 0) > 0:
                continue
            old_tag = existing_tags.get(old_tag_id)
            if old_tag is None:
                t_r = await db.execute(select(TagRegistry).where(TagRegistry.id == old_tag_id))
                old_tag = t_r.scalar_one_or_none()
            if old_tag:
                old_tag.is_linked = False
    # 新关联的 tag 设为已关联
    for tid in role_tag_map.values():
        if tid is not None and tid in existing_tags:
            existing_tags[tid].is_linked = True

    # 重新推导 status
    new_status = await derive_loop_status(db, loop, mappings=new_mappings)
    loop.status = new_status
    loop.updated_by = operator

    after_json = json.dumps(
        {role: (role_tag_map[role] if role_tag_map[role] else None) for role in ALL_ROLES},
        ensure_ascii=False,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_TAG_MAPPING_UPDATE",
        target_type="loop_tag_mapping",
        target_id=loop_id,
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    # 绑定关系变更可能改变 is_linked 订阅集合：通知实时订阅 Leader 刷新（免重启生效）
    from app.services.data_source.realtime_subscriber import notify_subscription_changed

    await notify_subscription_changed(source="tag-mapping")

    # 构建响应
    tags_list = []
    for role in ALL_ROLES:
        tag_id = role_tag_map.get(role)
        if tag_id and tag_id in existing_tags:
            tag = existing_tags[tag_id]
            tags_list.append(
                {
                    "role": role,
                    "tagId": str(tag.id),
                    "tagName": tag.tag_name,
                    "required": role in REQUIRED_ROLES,
                    "associated": True,
                }
            )
        else:
            tags_list.append(
                {
                    "role": role,
                    "tagId": None,
                    "tagName": None,
                    "required": role in REQUIRED_ROLES,
                    "associated": False,
                }
            )

    return {
        "loopId": loop_id,
        "status": new_status,
        "tags": tags_list,
        "updatedAt": loop.updated_at.isoformat() if loop.updated_at else None,
        "updatedBy": loop.updated_by,
    }


__all__ = ["get_loop_tags", "update_loop_tags"]
