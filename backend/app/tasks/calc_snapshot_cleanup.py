"""不可变计算输入包隔离清理任务（P2-03，DEC-10 保留契约）.

清理规则（P0-02 冻结 v2 §3.3，有序）：
① 文件存在但无 DB 引用且创建超过 24h → 删文件（写入失败/崩溃残留）；
② retentionClass=NORMAL 且 expires_at 已过且**未被任何 record/方案/工单/
   模型版本引用**且无固定引用 → 先 DB 标 ``cleaned_at`` → 删文件；
③ FROZEN 永不清理（解除引用 + 过期两条件同时满足才降级 NORMAL 再进 ②）。

调度：复用后端 lifespan 管理的 Celery Beat 每日低峰（03:40，错开审计
归档 03:00），**不新增独立进程**（符合"严禁手工再启动 Worker/Beat"红线）；
单 worker 串行执行（任务内单一 DB 会话顺序处理）。
"""

from __future__ import annotations

import logging

from app.services.calc_snapshot import cleanup_expired_snapshots
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


async def _do_cleanup(dry_run: bool = False) -> dict:
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        report = await cleanup_expired_snapshots(db, dry_run=dry_run)
        await db.commit()
    logger.info(
        "输入包清理完成 dryRun=%s 残留文件删除=%d 过期清理=%d 固定跳过=%d 引用中跳过=%d",
        report["dryRun"],
        len(report["orphanFilesRemoved"]),
        len(report["expiredCleaned"]),
        report["frozenSkipped"],
        len(report["referencedExpiredSkipped"]),
    )
    return report


@celery_app.task(name="app.tasks.calc_snapshot_cleanup.cleanup_calc_snapshots")
def cleanup_calc_snapshots(self: AsyncTask, dry_run: bool = False) -> dict:
    """每日输入包清理（Beat 调度；失败记 ERROR 不中断其他任务）."""
    try:
        return self.run_async(_do_cleanup(dry_run=dry_run))
    except Exception:  # noqa: BLE001
        logger.exception("输入包清理任务失败")
        return {"error": "cleanup_failed"}


# ---------------------------------------------------------------------------
# Celery Beat 调度：每天 03:40（错开审计归档 03:00 的低峰争用）
# ---------------------------------------------------------------------------
from celery.schedules import crontab  # noqa: E402

# 追加方式注册 Beat 任务（避免覆盖其他模块的 beat_schedule；时区沿袭
# celery_app.conf.timezone=Asia/Shanghai，与审计归档 03:00 同口径）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["calc-snapshot-cleanup-daily-0340"] = {
    "task": "app.tasks.calc_snapshot_cleanup.cleanup_calc_snapshots",
    "schedule": crontab(hour=3, minute=40),
}
celery_app.conf.beat_schedule = _existing_beat

__all__ = ["cleanup_calc_snapshots"]
