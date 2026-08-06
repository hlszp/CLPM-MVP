"""抑制/去抖/冷却/去重（方案 §4.3 步骤 6-7）。

Redis 键设计：
- alert:cooldown:<dedupKey>   STRING with TTL = cooldownSeconds
- alert:duration:<dedupKey>   HASH（first_seen / trigger_count）
- alert:suppression:<loop_id>:<rule_id>  手动抑制标记
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

KEY_COOLDOWN = "alert:cooldown:{dedup_key}"
KEY_DURATION = "alert:duration:{dedup_key}"
KEY_THROTTLE = "alert:throttle:{loop_id}"


class Suppressor:
    """抑制器：冷却期 + 持续时长 + 手动抑制检查。

    所有方法为 async，依赖 Redis。Redis 不可用时降级为"放行"（不阻塞主链路），
    对齐 alerting.send_alert 现有"发送失败不影响主流程"模式。
    """

    async def is_in_cooldown(self, dedup_key: str) -> bool:
        """检查 dedupKey 是否在冷却期内。

        冷却期内重复触发计入 trigger_count（用于严重度升级），但不告警。
        """
        try:
            key = KEY_COOLDOWN.format(dedup_key=dedup_key)
            exists = await redis_client.exists(key)
            if exists:
                # 累加重复触发计数
                await redis_client.hincrby(
                    KEY_DURATION.format(dedup_key=dedup_key), "trigger_count", 1
                )
                return True
            return False
        except Exception:  # noqa: BLE001
            logger.warning("冷却期检查 Redis 异常，降级放行", exc_info=True)
            return False

    async def set_cooldown(self, dedup_key: str, cooldown_seconds: int) -> None:
        """设置冷却期标记。"""
        try:
            key = KEY_COOLDOWN.format(dedup_key=dedup_key)
            await redis_client.setex(key, cooldown_seconds, "1")
            # 重置持续时长计数（新冷却期开始）
            dur_key = KEY_DURATION.format(dedup_key=dedup_key)
            await redis_client.hset(dur_key, "trigger_count", "1")
            await redis_client.expire(dur_key, cooldown_seconds)
        except Exception:  # noqa: BLE001
            logger.warning("设置冷却期 Redis 异常", exc_info=True)

    async def get_trigger_count(self, dedup_key: str) -> int:
        """获取冷却期内重复触发次数（用于严重度升级）。"""
        try:
            dur_key = KEY_DURATION.format(dedup_key=dedup_key)
            count = await redis_client.hget(dur_key, "trigger_count")
            return int(count) if count else 1
        except Exception:  # noqa: BLE001
            return 1

    async def check_duration(
        self,
        dedup_key: str,
        duration_seconds: int,
        condition_met: bool,
    ) -> tuple[bool, bool]:
        """检查持续时长（去抖）。

        Args:
            dedup_key: 去重键
            duration_seconds: 需持续满足的秒数（0=瞬时触发）
            condition_met: 本次求值条件是否满足

        Returns:
            (should_alert, reset_counter):
                should_alert — 是否达到告警条件
                reset_counter — 是否需要重置持续计数（条件中断时）
        """
        if duration_seconds <= 0:
            # 瞬时触发，不检查持续时长
            return condition_met, False

        if not condition_met:
            # 条件中断，重置持续计数
            try:
                await redis_client.delete(KEY_DURATION.format(dedup_key=dedup_key))
            except Exception:  # noqa: BLE001
                pass
            return False, True

        try:
            dur_key = KEY_DURATION.format(dedup_key=dedup_key)
            now_ts = datetime.now(UTC).timestamp()
            first_seen = await redis_client.hget(dur_key, "first_seen")

            if first_seen is None:
                # 首次满足条件，记录开始时间
                await redis_client.hset(dur_key, "first_seen", str(now_ts))
                await redis_client.expire(dur_key, duration_seconds + 60)
                return False, False

            # 检查是否达到持续时长
            elapsed = now_ts - float(first_seen)
            if elapsed >= duration_seconds:
                return True, False
            return False, False
        except Exception:  # noqa: BLE001
            logger.warning("持续时长检查 Redis 异常，降级瞬时触发", exc_info=True)
            return True, False

    async def is_throttled(self, loop_id: str, throttle_seconds: int = 5) -> bool:
        """实时轨节流：每回路每 throttle_seconds 秒最多求值 1 次。

        Returns:
            True 表示被节流（跳过本次求值），False 表示可求值
        """
        try:
            key = KEY_THROTTLE.format(loop_id=loop_id)
            exists = await redis_client.exists(key)
            if exists:
                return True
            await redis_client.setex(key, throttle_seconds, "1")
            return False
        except Exception:  # noqa: BLE001
            return False

    async def is_manually_suppressed(self, loop_id: str, rule_id: str | None) -> bool:
        """检查回路×规则是否被手动抑制。

        查询 alert_suppression 表的有效记录（is_active=true 且 end_at > now）。
        """
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.alert import AlertSuppression

        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(UTC).replace(tzinfo=None)
                stmt = select(AlertSuppression).where(
                    AlertSuppression.is_active.is_(True),
                    AlertSuppression.end_at > now,
                    AlertSuppression.start_at <= now,
                )
                if rule_id is not None:
                    stmt = stmt.where(
                        (AlertSuppression.loop_id == loop_id)
                        | (AlertSuppression.loop_id.is_(None)),
                        (AlertSuppression.rule_id == rule_id)
                        | (AlertSuppression.rule_id.is_(None)),
                    )
                else:
                    stmt = stmt.where(
                        (AlertSuppression.loop_id == loop_id) | (AlertSuppression.loop_id.is_(None))
                    )
                result = await db.execute(stmt)
                return result.scalars().first() is not None
        except Exception:  # noqa: BLE001
            logger.warning("手动抑制检查异常，降级放行", exc_info=True)
            return False

    async def get_cooldown_ttl(self, dedup_key: str) -> int:
        """获取冷却期剩余 TTL（秒）。"""
        try:
            key = KEY_COOLDOWN.format(dedup_key=dedup_key)
            return await redis_client.ttl(key)
        except Exception:  # noqa: BLE001
            return 0

    async def clear_duration(self, dedup_key: str) -> None:
        """清除持续时长计数（告警后重置）。"""
        try:
            await redis_client.delete(KEY_DURATION.format(dedup_key=dedup_key))
        except Exception:  # noqa: BLE001
            pass

    async def increment_badge(self, user_ids: list[str]) -> None:
        """递增多个用户的未读事件计数（工作台徽章）。"""
        try:
            for uid in user_ids:
                await redis_client.incr(f"alert:badge:{uid}")
        except Exception:  # noqa: BLE001
            logger.warning("徽章计数 Redis 异常", exc_info=True)

    async def get_badge_count(self, user_id: str) -> int:
        """获取用户未读事件计数。"""
        try:
            count = await redis_client.get(f"alert:badge:{user_id}")
            return int(count) if count else 0
        except Exception:  # noqa: BLE001
            return 0

    async def reset_badge(self, user_id: str) -> None:
        """重置用户未读事件计数（查看事件列表后）。"""
        try:
            await redis_client.delete(f"alert:badge:{user_id}")
        except Exception:  # noqa: BLE001
            pass

    async def reset_expired_suppressions(self) -> int:
        """过期抑制记录自动失效（is_active=true 且 end_at <= now → is_active=false）。

        Returns:
            失效的记录数
        """
        from sqlalchemy import update

        from app.core.db import AsyncSessionLocal
        from app.models.alert import AlertSuppression

        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    update(AlertSuppression)
                    .where(
                        AlertSuppression.is_active.is_(True),
                        AlertSuppression.end_at <= now,
                    )
                    .values(is_active=False)
                )
                await db.commit()
                return result.rowcount or 0
        except Exception:  # noqa: BLE001
            logger.warning("过期抑制记录失效异常", exc_info=True)
            return 0

    async def publish_notification(self, channel: str, payload: dict[str, Any]) -> None:
        """发布通知到 Redis pub/sub（站内信 WebSocket 推送）。"""
        import json

        try:
            await redis_client.publish(channel, json.dumps(payload, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            logger.warning("通知发布 Redis 异常", exc_info=True)
