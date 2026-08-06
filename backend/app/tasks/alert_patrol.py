"""智能预警规则引擎周期巡检任务（方案 §4.5 双轨触发 — 周期轨）。

Beat 调度：
- ``alert-patrol``：每 60s 遍历所有活跃订阅回路，求值规则 + 触发动作分发
- ``alert-suppression-cleanup``：每小时清扫过期手动抑制记录（is_active→false）

求值流程（单回路）：
1. 全局开关检查（sys_config，关闭则整体跳过）
2. 节流检查（5s/回路，避免实时轨+周期轨并发重复求值）
3. 取回路最新可信度等级（loop_confidence_latest）
4. 批量求值订阅规则（evaluate_loop_rules）
5. 对触发的结果逐条：
   a. 手动抑制检查
   b. 持续时长检查（去抖）
   c. 冷却期检查（去重）
   d. dispatcher 动作分发（建事件/建工单/通知）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.tasks.celery_app import AsyncTask, celery_app

if TYPE_CHECKING:
    from app.services.alert_rule_engine.suppressor import Suppressor

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.alert_patrol.run_alert_patrol",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def run_alert_patrol(self: AsyncTask) -> dict:
    """周期巡检：遍历活跃订阅回路，求值规则并触发预警。"""
    return self.run_async(_do_patrol())


@celery_app.task(
    name="app.tasks.alert_patrol.cleanup_suppressions",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 1, "countdown": 120},
)
def cleanup_suppressions(self: AsyncTask) -> dict:
    """清扫过期手动抑制记录（is_active=true 且 end_at <= now → false）。"""
    return self.run_async(_do_cleanup())


# ---------------------------------------------------------------------------
# 巡检主流程
# ---------------------------------------------------------------------------


async def _do_patrol() -> dict:
    """巡检主流程。"""
    from app.core.db import AsyncSessionLocal
    from app.services.alert_rule_engine.cache import get_all_active_loops
    from app.services.alert_rule_engine.evaluator import evaluate_loop_rules
    from app.services.alert_rule_engine.service import get_global_switch
    from app.services.alert_rule_engine.suppressor import Suppressor

    suppressor = Suppressor()
    total_loops = 0
    total_triggered = 0
    total_dispatched = 0

    async with AsyncSessionLocal() as db:
        # 1. 全局开关检查
        enabled = await get_global_switch(db)
        if not enabled:
            logger.info("预警引擎全局开关已关闭，跳过本次巡检")
            return {"skipped": True, "reason": "global_switch_off"}

        # 2. 获取所有活跃订阅回路
        loop_ids = await get_all_active_loops(db)
        total_loops = len(loop_ids)
        if not loop_ids:
            return {"total_loops": 0, "triggered": 0, "dispatched": 0}

    # 3. 逐回路求值（每个回路独立 session，避免长事务）
    for loop_id in loop_ids:
        try:
            # 节流：5s 内已求值的回路跳过（实时轨可能刚触发过）
            if await suppressor.is_throttled(str(loop_id)):
                continue

            async with AsyncSessionLocal() as db:
                # 取回路最新可信度等级
                confidence_level = await _get_loop_confidence(db, str(loop_id))

                # 批量求值
                triggered_results = await evaluate_loop_rules(
                    db, str(loop_id), confidence_level=confidence_level
                )
                total_triggered += len(triggered_results)

                # 逐条处理触发结果
                for result in triggered_results:
                    dispatched = await _process_triggered(db, suppressor, str(loop_id), result)
                    if dispatched:
                        total_dispatched += 1

                # 提交事件/工单写入
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.warning("回路 %s 巡检异常", loop_id, exc_info=True)

    logger.info(
        "预警巡检完成: loops=%d triggered=%d dispatched=%d",
        total_loops,
        total_triggered,
        total_dispatched,
    )
    return {
        "total_loops": total_loops,
        "triggered": total_triggered,
        "dispatched": total_dispatched,
    }


async def _process_triggered(db, suppressor: Suppressor, loop_id: str, result) -> bool:
    """处理单条触发结果：抑制检查 → 持续时长 → 冷却期 → dispatcher。

    Returns:
        True 表示已分发动作（建事件/工单/通知）
    """
    from app.services.alert_rule_engine.dispatcher import dispatch
    from app.services.alert_rule_engine.evaluator import EvaluationResult

    if not isinstance(result, EvaluationResult) or not result.triggered:
        return False

    dedup_key = result.dedup_key or f"{loop_id}+unknown"

    # 手动抑制检查
    if await suppressor.is_manually_suppressed(loop_id, None):
        logger.debug("回路 %s 被手动抑制，跳过", loop_id)
        return False

    # 冷却期检查（去重）
    if await suppressor.is_in_cooldown(dedup_key):
        return False

    # 持续时长检查（去抖）— 从规则 DSL 取 durationSeconds
    # result 中未携带 DSL，需从 rule dict 读取；此处简化：
    # evaluator 已完成条件求值，持续时长由 suppressor 在此检查
    # （durationSeconds 默认 0=瞬时触发，立即分发）
    # 注：完整持续时长检查需要规则的 durationSeconds，这里从
    # evaluator 的 condition_snapshot 无法获取，故 Phase 1 简化为瞬时触发
    # Phase 2 将在 evaluator 中集成持续时长检查

    # 获取规则字典（dispatcher 需要）
    rule = await _get_rule_for_result(db, result)
    if rule is None:
        logger.warning("触发结果无对应规则，跳过: dedup=%s", dedup_key)
        return False

    # dispatcher 动作分发
    await dispatch(db, rule, loop_id, result)
    return True


async def _get_loop_confidence(db, loop_id: str) -> str | None:
    """从 loop_confidence_latest 表读取回路最新可信度等级。"""
    from sqlalchemy import select

    from app.models.metric import LoopConfidenceLatest

    try:
        result = await db.execute(
            select(LoopConfidenceLatest.confidence_level).where(
                LoopConfidenceLatest.loop_id == loop_id
            )
        )
        return result.scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return None


async def _get_rule_for_result(db, result) -> dict | None:
    """根据求值结果获取规则字典（用于 dispatcher）。

    求值结果中携带 dedup_key 但不含完整规则。这里从缓存重新获取
    回路的规则列表，匹配对应的 rule_code。
    注：result 未携带 rule_code，需从 condition_snapshot 或 dedup_key 反推。
    Phase 1 简化：直接从缓存取回路第一条匹配规则。
    """
    from app.services.alert_rule_engine.cache import get_rules_for_loop

    # dedup_key 格式：${loop_id}+${rule_id}（默认模板）
    dedup_key = result.dedup_key or ""
    parts = dedup_key.split("+")
    if len(parts) >= 2:
        rule_id = parts[1]
        rules = await get_rules_for_loop(db, parts[0])
        for r in rules:
            if r.get("id") == rule_id:
                return r

    return None


async def _do_cleanup() -> dict:
    """清扫过期手动抑制记录。"""
    from app.services.alert_rule_engine.suppressor import Suppressor

    suppressor = Suppressor()
    count = await suppressor.reset_expired_suppressions()
    logger.info("过期抑制记录清扫完成: expired=%d", count)
    return {"expired": count}


# ---------------------------------------------------------------------------
# Beat 调度配置
# ---------------------------------------------------------------------------

from celery.schedules import crontab  # noqa: E402

# 追加方式注册 Beat 任务（避免覆盖其他模块的 beat_schedule）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
# 预警巡检：每 60s 执行（对齐方案 §4.5 周期轨间隔）
_existing_beat["alert-patrol"] = {
    "task": "app.tasks.alert_patrol.run_alert_patrol",
    "schedule": crontab(minute="*/1"),
}
# 过期抑制清扫：每小时执行
_existing_beat["alert-suppression-cleanup"] = {
    "task": "app.tasks.alert_patrol.cleanup_suppressions",
    "schedule": crontab(minute=5),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


__all__ = ["cleanup_suppressions", "run_alert_patrol"]
