"""动作分发（方案 §4.3 步骤 8-9）。

根据规则 actions 列表执行：
- CREATE_EVENT  写入 alert_event 表 + 设置冷却期 + 严重度升级
- CREATE_TRACKER 写入 action_tracker 表（诊断中心异常跟踪）
- NOTIFY        发布 Redis pub/sub 通知 + 徽章计数

调用前已由 evaluator + suppressor 完成"是否触发"判定；dispatcher 仅负责
"触发后做什么"，且每个 action 独立 try/except，单个动作失败不阻塞其他动作。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.models.alert import AlertEvent
from app.services.alert_rule_engine.evaluator import EvaluationResult, upgrade_severity
from app.services.alert_rule_engine.suppressor import Suppressor

logger = logging.getLogger(__name__)

# 通知频道（WebSocket 站内信推送订阅此频道）
NOTIFY_CHANNEL = "alert:notify"
# 徽章按用户计数，通知目标角色 → 用户列表在 dispatcher 内解析
_NOTIFY_ROLES = ("ADMIN", "IC_ENGINEER")

_suppressor = Suppressor()


async def dispatch(
    db: AsyncSession,
    rule: dict[str, Any],
    loop_id: str,
    result: EvaluationResult,
) -> dict[str, Any]:
    """执行规则 actions 列表中的所有动作。

    Args:
        db: 数据库会话
        rule: 规则缓存字典
        loop_id: 回路 ID
        result: 求值结果（已通过持续时长 + 冷却期检查）

    Returns:
        各动作执行结果汇总（{action_type: outcome}）
    """
    dsl = rule.get("dsl", {})
    actions = dsl.get("actions", [])
    outcomes: dict[str, Any] = {}

    # 严重度升级：基于冷却期内重复触发次数
    trigger_count = await _suppressor.get_trigger_count(result.dedup_key or "")
    final_severity = upgrade_severity(result.severity or dsl.get("severity", "WARN"), trigger_count)

    created_event_id: str | None = None
    for action in actions:
        act_type = action.get("type")
        try:
            if act_type == "CREATE_EVENT":
                created_event_id = await _create_event(
                    db, rule, loop_id, result, final_severity, trigger_count
                )
                outcomes["CREATE_EVENT"] = created_event_id
            elif act_type == "CREATE_TRACKER":
                tracker_id = await _create_tracker(db, rule, loop_id, result, final_severity)
                outcomes["CREATE_TRACKER"] = tracker_id
                # 若同时建了事件，回填 tracker_id
                if created_event_id and tracker_id:
                    await _link_event_to_tracker(db, created_event_id, tracker_id)
            elif act_type == "NOTIFY":
                await _notify(rule, loop_id, result, final_severity, created_event_id)
                outcomes["NOTIFY"] = "published"
        except Exception:  # noqa: BLE001
            logger.warning(
                "动作执行失败 rule=%s action=%s loop=%s",
                rule.get("ruleCode"),
                act_type,
                loop_id,
                exc_info=True,
            )
            outcomes[act_type or "UNKNOWN"] = "failed"

    # 设置冷却期（所有动作执行后，避免冷却期内重复告警）
    cooldown = dsl.get("cooldownSeconds", 1800)
    if cooldown > 0 and result.dedup_key:
        await _suppressor.set_cooldown(result.dedup_key, cooldown)
        await _suppressor.clear_duration(result.dedup_key)

    return outcomes


async def _create_event(
    db: AsyncSession,
    rule: dict[str, Any],
    loop_id: str,
    result: EvaluationResult,
    final_severity: str,
    trigger_count: int,
) -> str:
    """创建预警事件记录。"""
    event_id = str(uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    dsl = rule.get("dsl", {})

    event = AlertEvent(
        id=event_id,
        rule_id=rule.get("id"),
        rule_code=rule.get("ruleCode", ""),
        rule_version=rule.get("version", 1),
        loop_id=loop_id,
        severity=final_severity,
        status="ACTIVE",
        trigger_condition_snapshot=result.condition_snapshot or {},
        data_window=result.data_window,
        triggered_value=result.triggered_value,
        confidence_level=result.confidence_level,
        rule_dsl_snapshot=dsl,
        trigger_count=trigger_count,
        triggered_at=now,
    )
    db.add(event)
    await db.flush()
    logger.info(
        "预警事件已创建 event=%s rule=%s loop=%s severity=%s",
        event_id,
        rule.get("ruleCode"),
        loop_id,
        final_severity,
    )
    return event_id


async def _create_tracker(
    db: AsyncSession,
    rule: dict[str, Any],
    loop_id: str,
    result: EvaluationResult,
    final_severity: str,
) -> str | None:
    """创建异常跟踪工单（action_tracker）。

    diagnosis_label 使用规则代码，便于按规则维度筛选工单。
    trigger_type=auto 标识系统自动建单。
    """
    # 同一回路同一标签在开放态下唯一约束，先检查是否已有开放工单
    from sqlalchemy import select

    from app.models.tracker import ActionTracker

    existing = await db.execute(
        select(ActionTracker.id).where(
            ActionTracker.loop_id == loop_id,
            ActionTracker.diagnosis_label == rule.get("ruleCode", ""),
            ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS", "VERIFYING"]),
        )
    )
    existing_id = existing.scalar_one_or_none()
    if existing_id:
        logger.debug(
            "回路 %s 规则 %s 已有开放工单 %s，跳过建单",
            loop_id,
            rule.get("ruleCode"),
            existing_id,
        )
        return existing_id

    tracker_id = str(uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    tracker = ActionTracker(
        id=tracker_id,
        loop_id=loop_id,
        diagnosis_label=rule.get("ruleCode", ""),
        action_status="PENDING",
        trigger_type="auto",
        triggered_by="system",
        severity=final_severity,
        created_at=now,
        comment=f"预警规则自动建单：{rule.get('ruleName', '')}",
    )
    db.add(tracker)
    await db.flush()
    logger.info(
        "异常跟踪工单已创建 tracker=%s rule=%s loop=%s",
        tracker_id,
        rule.get("ruleCode"),
        loop_id,
    )
    return tracker_id


async def _link_event_to_tracker(db: AsyncSession, event_id: str, tracker_id: str) -> None:
    """回填事件的 tracker_id 关联。"""
    from sqlalchemy import update

    from app.models.alert import AlertEvent

    await db.execute(
        update(AlertEvent).where(AlertEvent.id == event_id).values(tracker_id=tracker_id)
    )


async def _notify(
    rule: dict[str, Any],
    loop_id: str,
    result: EvaluationResult,
    final_severity: str,
    event_id: str | None = None,
) -> None:
    """发布通知到 Redis pub/sub（站内信 WebSocket 推送）+ 徽章计数。

    通知目标：所有 ADMIN/IC_ENGINEER 用户（Phase 1 简化，按角色广播）。
    Phase 2 可扩展为按回路订阅关系精准推送。

    MW-P2-08：payload 携带 eventId，供前端铃铛深链接精确打开关注队列目标项。
    """
    payload = {
        "type": "alert",
        "ruleCode": rule.get("ruleCode", ""),
        "ruleName": rule.get("ruleName", ""),
        "loopId": loop_id,
        "severity": final_severity,
        "triggeredValue": result.triggered_value,
        "triggeredAt": datetime.now(UTC).isoformat(),
        "snapshot": result.condition_snapshot,
    }
    if event_id:
        payload["eventId"] = event_id
    try:
        await redis_client.publish(NOTIFY_CHANNEL, json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.warning("通知发布异常", exc_info=True)

    # 徽章计数：按角色查用户列表后递增（Phase 1 简化，广播给所有相关角色）
    try:
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.sys_user import SysUser

        async with AsyncSessionLocal() as db:
            stmt = select(SysUser.id).where(
                SysUser.role.in_(_NOTIFY_ROLES),
                SysUser.is_active.is_(True),
            )
            res = await db.execute(stmt)
            user_ids = [row[0] for row in res]
        await _suppressor.increment_badge(user_ids)
    except Exception:  # noqa: BLE001
        logger.warning("徽章计数异常", exc_info=True)
