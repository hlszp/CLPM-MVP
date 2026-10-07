"""处置域主动提醒（每日 08:30，2026-10-05 用户裁决②）。

背景：处置闭环的断点之一是"零主动提醒"——PENDING 建议堆积、工单超计划、
VERIFYING 挂起全部依赖用户主动翻页面（关注队列/dashboard/cockpit 均为拉取式）。
本任务每日聚合一次，命中任一阈值时推送**单条** NOTIFY 站内信（复用预警域
``alert:notify`` Redis 频道 → ws_alert → 顶栏铃铛），不打散逐条轰炸。

阈值口径（与关注队列 monitor_attention 现行口径对齐，避免两套数字打架）：
- 待审核建议：PENDING 且 suggested_at 早于 now − pendingDays（默认 3 天）
- 超计划工单：PENDING/EXECUTING 且 planned_at 已过（超期即计入，同关注队列）
- 验证挂起：VERIFYING 且 submitted_at 早于 now − verifyHours（默认 24h，
  同关注队列 HIGH 口径）

配置：sys_config key ``handling_remind``，JSON：
``{"enabled": true, "pendingDays": 3, "verifyHours": 24}``（缺省/坏值回退默认）。
受众：处置操作角色 ADMIN/IC_ENGINEER/PE_ENGINEER 活跃用户（徽章计数；
WS 推送本身为频道广播）。推送时机 08:30 避开夜间诊断（00:30 起）与
小时 KPI 轮（整点），且能带上当晚新增建议。

模块热插拔：handling 禁用时跳过（beat 条目亦随 beat_registry 摘除，双保险）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from celery.schedules import crontab

from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

#: 提醒配置 sys_config key（JSON：enabled/pendingDays/verifyHours）
_CONFIG_KEY = "handling_remind"

#: 默认配置（sys_config 缺失/坏值回退）
_DEFAULTS = {"enabled": True, "pendingDays": 3, "verifyHours": 24}

#: 徽章计数受众：处置操作角色（§7）的活跃用户
_REMIND_ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _load_config() -> dict[str, Any]:
    """读 sys_config `handling_remind` JSON；缺失/坏值回退默认值。"""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.sys_config import SysConfig

    try:
        async with AsyncSessionLocal() as db:
            raw = await db.scalar(select(SysConfig.value).where(SysConfig.key == _CONFIG_KEY))
        stored = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        logger.warning("处置提醒配置读取失败，回退默认值", exc_info=True)
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return {
        "enabled": bool(stored.get("enabled", _DEFAULTS["enabled"])),
        "pendingDays": int(stored.get("pendingDays", _DEFAULTS["pendingDays"])),
        "verifyHours": int(stored.get("verifyHours", _DEFAULTS["verifyHours"])),
    }


async def _collect_counts(cfg: dict[str, Any]) -> dict[str, Any]:
    """聚合三项滞留指标（一条 SQL；计数为 0 不推送）。"""
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    now = _utcnow_naive()
    pending_cutoff = now - timedelta(days=cfg["pendingDays"])
    verify_cutoff = now - timedelta(hours=cfg["verifyHours"])
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM loop_action_item
                        WHERE status = 'PENDING'
                          AND suggested_at < :pending_cutoff) AS pending_overdue,
                      (SELECT MIN(suggested_at) FROM loop_action_item
                        WHERE status = 'PENDING') AS oldest_pending_at,
                      (SELECT COUNT(*) FROM handling_order
                        WHERE status IN ('PENDING', 'EXECUTING')
                          AND planned_at IS NOT NULL
                          AND planned_at < :now_ts) AS schedule_overdue,
                      (SELECT COUNT(*) FROM handling_order
                        WHERE status = 'VERIFYING'
                          AND submitted_at IS NOT NULL
                          AND submitted_at < :verify_cutoff) AS verifying_stuck
                    """
                ),
                {
                    "pending_cutoff": pending_cutoff,
                    "now_ts": now,
                    "verify_cutoff": verify_cutoff,
                },
            )
        ).one()
    oldest = row.oldest_pending_at
    return {
        "pendingOverdue": int(row.pending_overdue or 0),
        "oldestPendingDays": (now - oldest).days if oldest is not None else None,
        "scheduleOverdue": int(row.schedule_overdue or 0),
        "verifyingStuck": int(row.verifying_stuck or 0),
    }


async def _push_notify(cfg: dict[str, Any], counts: dict[str, Any]) -> None:
    """发布单条 NOTIFY（payload 与 dispatcher._notify 同构）+ 徽章计数。"""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.core.redis import redis_client
    from app.models.sys_user import SysUser
    from app.services.alert_rule_engine.dispatcher import NOTIFY_CHANNEL
    from app.services.alert_rule_engine.suppressor import Suppressor

    parts = [
        f"待审核建议 {counts['pendingOverdue']} 条超 {cfg['pendingDays']} 天"
        + (
            f"（最老 {counts['oldestPendingDays']} 天）"
            if counts["oldestPendingDays"] is not None
            else ""
        ),
        f"超计划工单 {counts['scheduleOverdue']} 单",
        f"验证挂起超 {cfg['verifyHours']}h {counts['verifyingStuck']} 单",
    ]
    payload = {
        "type": "alert",
        "ruleCode": "HANDLING_REMIND",
        "ruleName": "处置待办提醒",
        "severity": "INFO",
        "triggeredAt": datetime.now(UTC).isoformat(),
        "snapshot": {"message": "；".join(parts), **counts},
    }
    try:
        await redis_client.publish(NOTIFY_CHANNEL, json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.warning("处置提醒通知发布异常", exc_info=True)
        return

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(SysUser.id).where(
                    SysUser.role.in_(_REMIND_ROLES),
                    SysUser.is_active.is_(True),
                )
            )
            user_ids = [r[0] for r in res.all()]
        await Suppressor().increment_badge(user_ids)
    except Exception:  # noqa: BLE001
        logger.warning("处置提醒徽章计数异常", exc_info=True)


async def _handling_remind_async() -> dict[str, Any]:
    from app.core.modules import is_module_enabled

    if not is_module_enabled("handling"):
        return {"status": "skipped", "reason": "handling 模块未启用"}
    cfg = await _load_config()
    if not cfg["enabled"]:
        return {"status": "disabled", "config": cfg}
    counts = await _collect_counts(cfg)
    if not (counts["pendingOverdue"] or counts["scheduleOverdue"] or counts["verifyingStuck"]):
        return {"status": "ok", "pushed": False, **counts}
    await _push_notify(cfg, counts)
    logger.info(
        "处置提醒已推送：待审核超期 %s 条 / 超计划 %s 单 / 验证挂起 %s 单",
        counts["pendingOverdue"],
        counts["scheduleOverdue"],
        counts["verifyingStuck"],
    )
    return {"status": "ok", "pushed": True, **counts}


@celery_app.task(name="app.tasks.handling_remind.handling_remind", bind=True, base=AsyncTask)
def handling_remind(self: AsyncTask) -> dict:
    """每日 08:30：处置待办聚合提醒（单条 NOTIFY，全零静默）。"""
    return self.run_async(_handling_remind_async())


# ---------------------------------------------------------------------------
# Beat 调度注册（追加式，不覆盖其他模块的 beat_schedule）
# ---------------------------------------------------------------------------
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["handling-remind"] = {
    "task": "app.tasks.handling_remind.handling_remind",
    "schedule": crontab(hour=8, minute=30),
}
celery_app.conf.beat_schedule = _existing_beat
