"""Loop batch operations & monitor trigger services (配置增强).

提供：
- ``batch_update_loops``: 批量更新回路（is_monitored/is_stat_enabled/level）
- ``batch_delete_loops``: 批量软删除回路（is_active=False）
- ``check_node_monitor_trigger``: SVC-10 位号触发监控检查

所有写操作均记录审计日志。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.tdengine import query_trend_data
from app.models.audit import SysAuditLog
from app.models.loop import LoopLedger
from app.models.plant_node import PlantNode
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# 允许批量更新的字段白名单
_BATCH_UPDATABLE_FIELDS = {"is_monitored", "is_stat_enabled", "importance_level"}


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
    """批量更新回路配置（is_monitored/is_stat_enabled/level）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表
        updates: 更新字段字典，支持：
            - is_monitored: bool — 是否监控（写入 is_active 字段，True=监控/启用）
            - is_stat_enabled: bool — 是否纳入统计（写入 is_kpi_enabled 不适用，
              此处映射到 is_active 的语义；当前 LoopLedger 无独立字段，
              实际写入 is_active 兼容；后续可扩展）
            - level: int — 回路级别 1/2/3
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

    # 构造审计 before/after 摘要
    audit_items: list[dict] = []
    for loop in loops:
        before = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "importanceLevel": loop.importance_level,
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
        loop.updated_by = operator

        after = {
            "loopId": str(loop.id),
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "importanceLevel": loop.importance_level,
        }
        audit_items.append({"before": before, "after": after})

    before_json = json.dumps(
        [item["before"] for item in audit_items],
        ensure_ascii=False,
        default=str,
    )
    after_json = json.dumps(
        [item["after"] for item in audit_items],
        ensure_ascii=False,
        default=str,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_BATCH_UPDATE",
        target_type="loop_ledger",
        target_id=None,  # 批量操作无单一目标，完整列表在 before_value/after_value
        before_value=before_json,
        after_value=after_json,
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
# SVC-09: 批量软删除回路
# ---------------------------------------------------------------------------


async def batch_delete_loops(
    db: AsyncSession,
    loop_ids: list[str],
    operator: str,
) -> dict:
    """批量软删除回路（is_active=False，不实际删除记录）。

    P1 #9 修正：补 Tag 关联校验，有关联 Tag 的回路跳过并记入 skipped 列表，
    与单删 delete_loop 行为对齐（单删有 Tag 直接抛错，批删跳过）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表
        operator: 操作人

    Returns:
        {"deleted": int, "skipped": list[dict]}
        - deleted: 软删除的回路数量
        - skipped: 跳过的回路列表（每项含 loopId/reason）

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

    if not loops:
        return {"deleted": 0, "skipped": []}

    # P1 #9: 批量查询有关联 Tag 的回路 ID
    from app.models.loop import LoopTagMapping

    tag_result = await db.execute(
        select(LoopTagMapping.loop_id)
        .where(LoopTagMapping.loop_id.in_([str(l.id) for l in loops]))
        .distinct()
    )
    loops_with_tags: set[str] = {str(row[0]) for row in tag_result.all()}

    audit_items: list[dict] = []
    skipped: list[dict] = []
    deleted_count = 0

    for loop in loops:
        loop_id_str = str(loop.id)
        if loop_id_str in loops_with_tags:
            skipped.append(
                {"loopId": loop_id_str, "tagName": loop.tag_name, "reason": "存在关联 Tag"}
            )
            continue

        before = {
            "loopId": loop_id_str,
            "tagName": loop.tag_name,
            "is_active": loop.is_active,
            "status": loop.status,
        }
        loop.is_active = False
        loop.status = "INACTIVE"
        loop.updated_by = operator
        after = {
            "loopId": loop_id_str,
            "tagName": loop.tag_name,
            "is_active": False,
            "status": "INACTIVE",
        }
        audit_items.append({"before": before, "after": after})
        deleted_count += 1

    if audit_items:
        before_json = json.dumps(
            [item["before"] for item in audit_items],
            ensure_ascii=False,
            default=str,
        )
        after_json = json.dumps(
            [item["after"] for item in audit_items],
            ensure_ascii=False,
            default=str,
        )

        await _write_audit(
            db=db,
            operator=operator,
            operation_type="LOOP_BATCH_DELETE",
            target_type="loop_ledger",
            target_id=None,  # 批量操作无单一目标，完整列表在 before_value/after_value
            before_value=before_json,
            after_value=after_json,
        )

    await db.commit()

    logger.info(
        "[批量软删除] 已软删除 %d 个回路，跳过 %d 个（操作人: %s）",
        deleted_count,
        len(skipped),
        operator,
    )

    return {"deleted": deleted_count, "skipped": skipped}


# ---------------------------------------------------------------------------
# SVC-10: 位号触发监控检查
# ---------------------------------------------------------------------------


async def check_node_monitor_trigger(
    db: AsyncSession,
    plant_node_id: str,
) -> bool:
    """位号触发监控检查（SVC-10）。

    查询 plant_node.monitor_tag_id：
    - 无 monitor_tag_id 配置时返回 True（默认监控）
    - 有配置时查询 TDengine 最新值，值等于 monitor_trigger_value 时返回 True
    - 否则返回 False

    Args:
        db: 异步数据库会话
        plant_node_id: 工厂节点 ID

    Returns:
        是否应监控该节点下的回路

    Raises:
        BizError: ERR_NODE_NOT_FOUND (节点不存在)
    """
    result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise BizError(
            code="ERR_NODE_NOT_FOUND",
            message=f"工厂节点不存在: {plant_node_id}",
            status_code=404,
        )

    # 无 monitor_tag_id 配置 → 默认监控
    if not node.monitor_tag_id:
        return True

    # 查询 monitor_tag_id 对应的 tag_name
    tag_result = await db.execute(select(TagRegistry).where(TagRegistry.id == node.monitor_tag_id))
    tag = tag_result.scalar_one_or_none()
    if tag is None:
        # 配置了 monitor_tag_id 但 tag 已删除 → 默认监控
        logger.warning(
            "[SVC-10] 节点 %s 的 monitor_tag_id %s 对应 Tag 不存在，回退默认监控",
            plant_node_id,
            node.monitor_tag_id,
        )
        return True

    # 查询 TDengine 最新值（最近 5 分钟）
    end_time = datetime.now(UTC).replace(tzinfo=None)
    start_time = end_time - timedelta(minutes=5)
    trend_data = await query_trend_data(
        tag_name=tag.tag_name,
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
    )

    if not trend_data:
        # 无数据时默认不监控（避免误报）
        logger.info(
            "[SVC-10] 节点 %s 的监控位号 %s 无最近数据，返回 False",
            plant_node_id,
            tag.tag_name,
        )
        return False

    # 取最新一条数据点
    latest = trend_data[-1]
    latest_value = latest.get("value")

    # 与 monitor_trigger_value 比较（字符串化后比较，支持 "true"/"1"/"ON"）
    trigger_value = node.monitor_trigger_value
    if trigger_value is None:
        # 配置了 monitor_tag_id 但无 trigger_value → 默认监控
        return True

    # 数值与字符串均可比较：将 latest_value 字符串化
    latest_str = str(latest_value).strip().lower() if latest_value is not None else ""
    trigger_str = str(trigger_value).strip().lower()

    matched = latest_str == trigger_str
    logger.info(
        "[SVC-10] 节点 %s 监控位号 %s 最新值=%s, 触发值=%s, 匹配=%s",
        plant_node_id,
        tag.tag_name,
        latest_value,
        trigger_value,
        matched,
    )
    return matched


__all__ = [
    "batch_delete_loops",
    "batch_update_loops",
    "check_node_monitor_trigger",
]
