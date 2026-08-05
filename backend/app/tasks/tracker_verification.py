"""Tracker 整改效果自动验证任务（整改计划 D4-2）。

IMPLEMENTED/VERIFYING 满 N 小时（默认 24H，可通过 sys_config ``tracker.verification_interval_hours``
人工调节）的 tracker，由本周期任务自动调用 A/B 对比，回写 ``effect_verified`` 系列字段。

P3-01 修复：P1a 闭环状态机将实施后状态从 IMPLEMENTED 改为 VERIFYING，本任务查询
条件同步覆盖 VERIFYING，否则 P1a 流程的 tracker 永远不会被自动验证。
implemented_at 优先取 tracker.implemented_at（P1a 新增字段），回退 updated_at。

判定逻辑：
- ``dataInsufficient=True`` → 跳过（等下一周期重试，避免数据不足误判）
- 改善指标数 > 恶化指标数 → ``effect_verified=True``
- 恶化指标数 > 改善指标数 → ``effect_verified=False``
- 全部持平或改善==恶化 → ``effect_verified=True``（无明显变化但已验证）

Beat 调度：每小时 minute=30 执行（避开诊断事件轨 10 分 / 体检轨 20 分）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from celery.schedules import crontab
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models.sys_config import SysConfig
from app.models.tracker import ActionTracker
from app.services.tracker import get_ab_compare
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# sys_config key：整改效果验证周期（小时），默认 24
VERIFICATION_INTERVAL_KEY = "tracker.verification_interval_hours"
VERIFICATION_INTERVAL_DEFAULT = 24


async def _get_verification_interval_hours(db: AsyncSession) -> int:
    """从 sys_config 读取验证周期（小时），缺失或非法时回落默认值 24。"""
    result = await db.execute(select(SysConfig).where(SysConfig.key == VERIFICATION_INTERVAL_KEY))
    cfg = result.scalar_one_or_none()
    if cfg is None or not cfg.value:
        return VERIFICATION_INTERVAL_DEFAULT
    try:
        hours = int(cfg.value)
    except (ValueError, TypeError):
        logger.warning(
            "sys_config %s 值非法（%s），回落默认 %d 小时",
            VERIFICATION_INTERVAL_KEY,
            cfg.value,
            VERIFICATION_INTERVAL_DEFAULT,
        )
        return VERIFICATION_INTERVAL_DEFAULT
    if hours < 1:
        logger.warning(
            "sys_config %s 值过小（%d），回落默认 %d 小时",
            VERIFICATION_INTERVAL_KEY,
            hours,
            VERIFICATION_INTERVAL_DEFAULT,
        )
        return VERIFICATION_INTERVAL_DEFAULT
    return hours


async def _fetch_pending_trackers(db: AsyncSession, cutoff: datetime) -> list[ActionTracker]:
    """查询待验证的 tracker：IMPLEMENTED/VERIFYING 且未验证 且 updated_at <= cutoff。"""
    result = await db.execute(
        select(ActionTracker).where(
            ActionTracker.action_status.in_(["IMPLEMENTED", "VERIFYING"]),
            ActionTracker.effect_verified.is_(None),
            ActionTracker.updated_at <= cutoff,
        )
    )
    return list(result.scalars().all())


def _judge_effect(ab_result: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """根据 A/B 对比结果判定整改效果。

    Returns:
        (effect_verified, summary_snapshot)
        - effect_verified: True=改善 / False=恶化或无明显变化
        - summary_snapshot: 写入 ab_compare_summary 的精简快照
    """
    kpi_comparison = ab_result.get("kpiComparison", [])
    improved_count = sum(1 for item in kpi_comparison if item.get("improved") is True)
    deteriorated_count = sum(1 for item in kpi_comparison if item.get("improved") is False)
    unchanged_count = sum(1 for item in kpi_comparison if item.get("improved") is None)

    logger.info(
        "A/B 对比判定: improved=%d, deteriorated=%d, unchanged=%d, dataInsufficient=%s",
        improved_count,
        deteriorated_count,
        unchanged_count,
        ab_result.get("dataInsufficient", False),
    )

    # 改善指标数 > 恶化指标数 → 改善；反之 → 恶化；相等或全持平 → 已验证（无明显变化）
    if improved_count > deteriorated_count:
        effect_verified = True
    elif deteriorated_count > improved_count:
        effect_verified = False
    else:
        # 改善==恶化（含全持平）→ 已验证，无明显变化
        effect_verified = True

    logger.info(
        "最终判定: effect_verified=%s (improved=%d vs deteriorated=%d)",
        effect_verified,
        improved_count,
        deteriorated_count,
    )

    summary = {
        "improvedCount": improved_count,
        "deterioratedCount": deteriorated_count,
        "unchangedCount": unchanged_count,
        "dataInsufficient": ab_result.get("dataInsufficient", False),
        "kpiComparison": [
            {
                "metricKey": item.get("metricKey"),
                "metricName": item.get("metricName"),
                "before": item.get("before"),
                "after": item.get("after"),
                "change": item.get("change"),
                "improved": item.get("improved"),
            }
            for item in kpi_comparison
        ],
    }
    return effect_verified, summary


async def _verify_single_tracker(db: AsyncSession, tracker: ActionTracker) -> bool:
    """验证单条 tracker 并回写结果。

    Returns:
        True=已验证并回写 / False=跳过（数据不足等）
    """
    if not tracker.loop_id:
        logger.warning("tracker %s 无 loop_id，跳过验证", tracker.id)
        return False

    # 以实施时间为 T，调用 A/B 对比
    # P1a 后优先用 implemented_at 字段（专用实施时间），回退 updated_at
    implemented_at = tracker.implemented_at or tracker.updated_at
    if implemented_at is None:
        logger.warning("tracker %s 无 updated_at，跳过验证", tracker.id)
        return False

    if implemented_at.tzinfo is None:
        implemented_at_iso = implemented_at.replace(tzinfo=UTC).isoformat()
    else:
        implemented_at_iso = implemented_at.isoformat()

    logger.info(
        "开始验证 tracker %s: loop_id=%s, label=%s, implemented_at=%s",
        tracker.id,
        tracker.loop_id,
        tracker.diagnosis_label,
        implemented_at_iso,
    )

    try:
        ab_result = await get_ab_compare(
            db,
            tracker.loop_id,
            implemented_at=implemented_at_iso,
        )
    except Exception:
        logger.exception("tracker %s A/B 对比计算失败，跳过本次验证", tracker.id)
        return False

    logger.info(
        "tracker %s A/B 对比完成: dataInsufficient=%s, kpiCount=%d",
        tracker.id,
        ab_result.get("dataInsufficient", False),
        len(ab_result.get("kpiComparison", [])),
    )

    # 数据不足 → 跳过，等下一周期重试
    if ab_result.get("dataInsufficient"):
        logger.info(
            "tracker %s after 窗口数据不足，跳过本次验证（等下一周期重试）",
            tracker.id,
        )
        return False

    effect_verified, summary = _judge_effect(ab_result)
    verified_at = datetime.now(UTC).replace(tzinfo=None)

    tracker.effect_verified = effect_verified
    tracker.effect_verified_at = verified_at
    tracker.ab_compare_summary = summary
    await db.commit()

    logger.info(
        "tracker %s 验证完成: effect_verified=%s, improved=%d, deteriorated=%d, unchanged=%d",
        tracker.id,
        effect_verified,
        summary["improvedCount"],
        summary["deterioratedCount"],
        summary["unchangedCount"],
    )
    return True


async def _do_verify_implementation_effect() -> dict[str, Any]:
    """周期任务主逻辑：扫描待验证 tracker 并逐条验证。"""
    async with AsyncSessionLocal() as db:
        # 1. 读取验证周期（可人工调节，默认 24H）
        interval_hours = await _get_verification_interval_hours(db)
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=interval_hours)
        logger.info(
            "整改效果验证任务启动, 验证周期=%dH, cutoff=%s",
            interval_hours,
            cutoff.isoformat(),
        )

        # 2. 查询待验证 tracker
        trackers = await _fetch_pending_trackers(db, cutoff)
        if not trackers:
            logger.info("无待验证 tracker，任务结束")
            return {"total": 0, "verified": 0, "skipped": 0}

        logger.info("待验证 tracker %d 条", len(trackers))

        # 3. 逐条验证
        verified_count = 0
        skipped_count = 0
        for tracker in trackers:
            # 每条 tracker 用独立会话，避免单条失败影响其他
            try:
                async with AsyncSessionLocal() as item_db:
                    # 重新查询以绑定到本会话
                    result = await item_db.execute(
                        select(ActionTracker).where(ActionTracker.id == tracker.id)
                    )
                    item_tracker = result.scalar_one_or_none()
                    if item_tracker is None:
                        skipped_count += 1
                        continue
                    done = await _verify_single_tracker(item_db, item_tracker)
                    if done:
                        verified_count += 1
                    else:
                        skipped_count += 1
            except Exception:
                logger.exception("tracker %s 验证过程异常，跳过", tracker.id)
                skipped_count += 1

        logger.info(
            "整改效果验证任务完成: total=%d, verified=%d, skipped=%d",
            len(trackers),
            verified_count,
            skipped_count,
        )
        return {
            "total": len(trackers),
            "verified": verified_count,
            "skipped": skipped_count,
        }


@celery_app.task(
    name="app.tasks.tracker_verification.verify_implementation_effect",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def verify_implementation_effect() -> dict[str, Any]:
    """周期任务入口：扫描 IMPLEMENTED 满 N 小时且未验证的 tracker，自动计算 A/B 对比并回写。

    验证周期通过 sys_config ``tracker.verification_interval_hours`` 配置，默认 24 小时。
    Beat 调度：每小时 minute=30 执行（避开诊断事件轨 10 分 / 体检轨 20 分）。
    """
    logger.info("整改效果验证周期任务触发")
    return AsyncTask().run_async(_do_verify_implementation_effect())


# ---------------------------------------------------------------------------
# Beat 调度配置（追加方式，避免覆盖其他模块的 beat_schedule）
# ---------------------------------------------------------------------------

_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["tracker-verification-hourly"] = {
    "task": "app.tasks.tracker_verification.verify_implementation_effect",
    # 每小时第 30 分钟执行，避开诊断事件轨（minute=10）和体检轨（minute=20）
    "schedule": crontab(minute=30),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"
