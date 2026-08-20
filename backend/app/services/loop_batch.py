"""Loop batch operations services (配置增强).

提供：
- ``batch_update_loops``: 批量更新回路（监控/统计/重要等级/参评）
- ``batch_delete_loops``: 批量硬删除回路（解绑 Tag 映射 + 级联清理关联数据）

所有写操作均记录审计日志。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# 允许批量更新的字段白名单（v5.3：level → importance_level）
_BATCH_UPDATABLE_FIELDS = {
    "is_monitored",
    "is_stat_enabled",
    "importance_level",
    "include_in_evaluation",
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


# ---------------------------------------------------------------------------
# SVC-09: 批量更新回路
# ---------------------------------------------------------------------------


async def batch_update_loops(
    db: AsyncSession,
    loop_ids: list[str],
    updates: dict,
    operator: str,
) -> int:
    """批量更新回路配置（is_monitored/is_stat_enabled/importance_level/include_in_evaluation）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表
        updates: 更新字段字典，支持：
            - is_monitored: bool — 是否监控（写入 is_active 字段，True=监控/启用）
            - is_stat_enabled: bool — 是否纳入统计（写入 is_kpi_enabled 不适用，
              此处映射到 is_active 的语义；当前 LoopLedger 无独立字段，
              实际写入 is_active 兼容；后续可扩展）
            - importance_level: int — 回路重要等级 1/2/3
            - include_in_evaluation: bool — 是否参与评估
        operator: 操作人

    Returns:
        更新的回路数量

    Raises:
        BizError: ERR_BATCH_EMPTY (loop_ids 为空) / ERR_BATCH_INVALID_FIELD (非法字段)
    """
    if not loop_ids:
        raise BizError(
            code="ERR_BATCH_EMPTY",
            message="批量更新回路列表不能为空",
            status_code=422,
        )

    # 校验 updates 字段白名单
    invalid_fields = set(updates.keys()) - _BATCH_UPDATABLE_FIELDS
    if invalid_fields:
        raise BizError(
            code="ERR_BATCH_INVALID_FIELD",
            message=f"批量更新不支持的字段: {','.join(sorted(invalid_fields))}",
            status_code=422,
        )

    # 校验 importance_level 取值
    if "importance_level" in updates and updates["importance_level"] is not None:
        level = updates["importance_level"]
        if level not in (1, 2, 3):
            raise BizError(
                code="ERR_BATCH_INVALID_FIELD",
                message=f"importance_level 必须为 1/2/3，当前为 {level}",
                status_code=422,
            )

    # 查询待更新回路
    result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
    loops = result.scalars().all()

    if not loops:
        return 0

    # 逐回路应用更新并写审计（target_id 为 UUID 单列，每回路一条）
    for loop in loops:
        before = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "importance_level": loop.importance_level,
            "include_in_evaluation": loop.include_in_evaluation,
        }

        if "is_monitored" in updates and updates["is_monitored"] is not None:
            # is_monitored=True 表示启用监控（is_active=True）
            loop.is_active = bool(updates["is_monitored"])
        if "is_stat_enabled" in updates and updates["is_stat_enabled"] is not None:
            # 当前 LoopLedger 无独立 is_stat_enabled 字段，复用 is_active 语义
            # 后续若新增字段可在此扩展
            loop.is_active = bool(updates["is_stat_enabled"])
        if "importance_level" in updates and updates["importance_level"] is not None:
            loop.importance_level = updates["importance_level"]
        if "include_in_evaluation" in updates and updates["include_in_evaluation"] is not None:
            loop.include_in_evaluation = bool(updates["include_in_evaluation"])
        loop.updated_by = operator

        after = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "importance_level": loop.importance_level,
            "include_in_evaluation": loop.include_in_evaluation,
        }
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="LOOP_BATCH_UPDATE",
            target_type="loop_ledger",
            target_id=str(loop.id),
            before_value=json.dumps(before, ensure_ascii=False, default=str),
            after_value=json.dumps(after, ensure_ascii=False, default=str),
        )
    await db.commit()

    logger.info(
        "[批量更新] 已更新 %d 个回路（操作人: %s, 字段: %s）",
        len(loops),
        operator,
        list(updates.keys()),
    )

    return len(loops)


# ---------------------------------------------------------------------------
# SVC-09: 批量硬删除回路
# ---------------------------------------------------------------------------


async def batch_delete_loops(
    db: AsyncSession,
    loop_ids: list[str],
    operator: str,
) -> dict:
    """批量硬删除回路（与单个删除口径一致，不可恢复）。

    每个回路：
    1. 删除 LoopTagMapping 关联记录（通常 7 条：PV/SP/OP/MODE/PID_P/PID_I/PID_D），
       解除关联后不再被任何回路引用的 Tag 其 is_linked 一并清除；
    2. 硬删除回路本体，ON DELETE CASCADE 自动级联清理 kpi_snapshot /
       loop_confidence_latest / diagnosis_run / action_tracker / tuning_record /
       alert_event 等 18 张关联表；
    3. 写审计日志。

    单事务提交（任一回路失败全部回滚）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表
        operator: 操作人

    Returns:
        {"deleted": 硬删除数量, "skipped": [{"loopId", "reason"}]}
        skipped 为请求中但未找到的回路 ID

    Raises:
        BizError: ERR_BATCH_EMPTY (loop_ids 为空)
    """
    if not loop_ids:
        raise BizError(
            code="ERR_BATCH_EMPTY",
            message="批量删除回路列表不能为空",
            status_code=422,
        )

    result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
    loops = result.scalars().all()

    found_ids = {str(loop.id) for loop in loops}
    skipped = [{"loopId": lid, "reason": "回路不存在"} for lid in loop_ids if lid not in found_ids]

    if not loops:
        return {"deleted": 0, "skipped": skipped}

    for loop in loops:
        loop_id = str(loop.id)
        before = {
            "loopId": loop_id,
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "status": loop.status,
        }

        # 1. 级联解绑：删除本回路的 Tag 映射
        mappings_result = await db.execute(
            select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
        )
        mappings = mappings_result.scalars().all()
        mapped_tag_ids = [str(m.tag_id) for m in mappings]
        if mappings:
            await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
            # is_linked 由映射派生：仅当 Tag 不再被任何回路引用时才清除
            for tag_id in mapped_tag_ids:
                ref_count_result = await db.execute(
                    select(func.count())
                    .select_from(LoopTagMapping)
                    .where(LoopTagMapping.tag_id == tag_id)
                )
                if (ref_count_result.scalar() or 0) > 0:
                    continue
                t_result = await db.execute(select(TagRegistry).where(TagRegistry.id == tag_id))
                tag = t_result.scalar_one_or_none()
                if tag:
                    tag.is_linked = False

        # 2. 硬删除回路本体（ON DELETE CASCADE 级联清理关联表）
        await db.delete(loop)

        # 3. 审计
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="LOOP_BATCH_DELETE",
            target_type="loop_ledger",
            target_id=loop_id,
            before_value=json.dumps(before, ensure_ascii=False, default=str),
            after_value=json.dumps(
                {"tagName": loop.tag_name, "deleted": True},
                ensure_ascii=False,
            ),
        )

    await db.commit()

    logger.info(
        "[批量删除] 已硬删除 %d 个回路（操作人: %s）",
        len(loops),
        operator,
    )

    return {"deleted": len(loops), "skipped": skipped}


# ---------------------------------------------------------------------------
# SVC-10 位号触发监控（check_node_monitor_trigger）已于 2026-08-20 移除：
# 该功能从未接线（全代码库无调用方），plant_node.monitor_tag_id /
# monitor_trigger_value 字段全 NULL，属死代码 + 死字段。迁移
# e1f2a3b4c5d6 已同步删除两列。若将来需要"按位号值启停节点监控"，
# 按实际需求重新设计。
# ---------------------------------------------------------------------------


__all__ = [
    "batch_delete_loops",
    "batch_update_loops",
]
