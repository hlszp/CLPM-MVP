"""规则缓存（方案 §4.3 缓存一致性）。

Redis 单层缓存 + 30s 短 TTL：
- 读路径：Redis GET → 命中返回；未命中 → 查 DB → 回填 Redis
- 写路径：CRUD 后立即 Redis DEL，下次读触发回填
- 时效保证：CRUD 后最迟 30s 全进程生效；DEL 命中后即时生效

键设计：
- alert:rules:<loop_id>     订阅该回路的启用规则列表（JSON）
- alert:rule:<rule_id>      单条规则详情（JSON）
- alert:rules:all           全回路规则（scope_type=ALL）
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.models.alert import AlertRule, AlertRuleSubscription

logger = logging.getLogger(__name__)

KEY_RULES_BY_LOOP = "alert:rules:{loop_id}"
KEY_RULE_DETAIL = "alert:rule:{rule_id}"
KEY_RULES_ALL = "alert:rules:all"
CACHE_TTL = 30  # 秒


def _rule_to_dict(rule: AlertRule) -> dict[str, Any]:
    """ORM → dict（缓存序列化）。"""
    return {
        "id": rule.id,
        "ruleCode": rule.rule_code,
        "ruleName": rule.rule_name,
        "ruleType": rule.rule_type,
        "dsl": rule.dsl,
        "priority": rule.priority,
        "isEnabled": rule.is_enabled,
        "version": rule.version,
        "cooldownSeconds": rule.dsl.get("cooldownSeconds", 1800),
        "dedupKey": rule.dsl.get("dedupKey", "${loop_id}+${rule_id}"),
    }


async def get_rules_for_loop(db: AsyncSession, loop_id: str) -> list[dict[str, Any]]:
    """获取订阅指定回路的启用规则列表（带缓存）。

    合并两类订阅：
    1. 显式订阅该回路的规则（scope_type=LOOP）
    2. 全回路规则（scope_type=ALL）
    """
    cache_key = KEY_RULES_BY_LOOP.format(loop_id=loop_id)
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached if isinstance(cached, str) else cached.decode())
    except Exception:  # noqa: BLE001
        logger.warning("规则缓存读取异常，降级查库", exc_info=True)

    # 查 DB：订阅该回路的规则 + ALL 规则
    stmt = (
        select(AlertRule)
        .join(AlertRuleSubscription, AlertRuleSubscription.rule_id == AlertRule.id)
        .where(
            AlertRule.is_enabled.is_(True),
            AlertRuleSubscription.is_active.is_(True),
            (
                (AlertRuleSubscription.loop_id == loop_id)
                | (AlertRuleSubscription.scope_type == "ALL")
            ),
        )
        .order_by(AlertRule.priority)
    )
    result = await db.execute(stmt)
    rules = [_rule_to_dict(r) for r in result.scalars()]

    # 回填缓存
    try:
        await redis_client.setex(cache_key, CACHE_TTL, json.dumps(rules, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.warning("规则缓存回填异常", exc_info=True)

    return rules


async def invalidate_loop_cache(loop_id: str) -> None:
    """CRUD 后失效回路的规则缓存。"""
    try:
        await redis_client.delete(KEY_RULES_BY_LOOP.format(loop_id=loop_id))
    except Exception:  # noqa: BLE001
        pass


async def invalidate_rule_cache(rule_id: str) -> None:
    """CRUD 后失效单条规则缓存。"""
    try:
        await redis_client.delete(KEY_RULE_DETAIL.format(rule_id=rule_id))
    except Exception:  # noqa: BLE001
        pass


async def invalidate_all_cache() -> None:
    """全量失效（批量启停/全局开关切换时）。"""
    try:
        # 扫描并删除所有 alert:rules:* 键
        async for key in redis_client.scan_iter(match="alert:rules:*", count=100):
            await redis_client.delete(key)
    except Exception:  # noqa: BLE001
        logger.warning("全量缓存失效异常", exc_info=True)


async def get_all_active_loops(db: AsyncSession) -> list[str]:
    """获取所有有活跃订阅的回路 ID 列表（周期巡检用）。"""
    stmt = (
        select(AlertRuleSubscription.loop_id)
        .where(
            AlertRuleSubscription.is_active.is_(True),
            AlertRuleSubscription.scope_type != "ALL",
        )
        .distinct()
    )
    result = await db.execute(stmt)
    loop_ids = [row[0] for row in result]

    # ALL 类型的规则需要展开到所有活跃回路
    from app.models.loop import LoopLedger

    stmt_all = select(AlertRuleSubscription).where(
        AlertRuleSubscription.is_active.is_(True),
        AlertRuleSubscription.scope_type == "ALL",
    )
    result_all = await db.execute(stmt_all)
    if result_all.scalars().first() is not None:
        stmt_loops = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result_loops = await db.execute(stmt_loops)
        all_loop_ids = {row[0] for row in result_loops}
        loop_ids = list(set(loop_ids) | all_loop_ids)

    return loop_ids
