"""诊断证据保留策略（2026-08-18）。

证据（evidence_charts 波形快照 + operator_results 算子特征值）体积大且
随诊断次数线性增长：仅保留最近一个月；超期记录每回路仅保留最新 1 条
的完整证据，其余证据字段置 NULL。结论字段（分类/置信度/严重度/建议/
时间窗等）永久保留，历史列表与统计不受影响。

详情接口对证据已清理的记录（operator_results IS NULL）由前端提示
"证据已按保留策略清理"。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

#: 证据保留窗口（天）
EVIDENCE_RETENTION_DAYS = 30


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _cleanup_expired_evidence() -> dict:
    """超期证据清理：>保留窗的记录每回路仅保留最新 1 条证据。"""
    cutoff = _utcnow_naive() - timedelta(days=EVIDENCE_RETENTION_DAYS)
    sql = text(
        """
        UPDATE diagnosis_run dr
        SET evidence_charts = NULL, operator_results = NULL
        WHERE dr.created_at < :cutoff
          AND (dr.evidence_charts IS NOT NULL OR dr.operator_results IS NOT NULL)
          AND dr.id NOT IN (
              SELECT keep_id FROM (
                  SELECT DISTINCT ON (loop_id) id AS keep_id
                  FROM diagnosis_run
                  WHERE created_at < :cutoff
                  ORDER BY loop_id, created_at DESC
              ) t
          )
        """
    )
    async with AsyncSessionLocal() as db:
        result = await db.execute(sql, {"cutoff": cutoff})
        await db.commit()
        cleaned = result.rowcount
    logger.info(
        "诊断证据清理完成：保留窗=%d天 截止=%s 清理记录数=%d",
        EVIDENCE_RETENTION_DAYS,
        cutoff.isoformat(),
        cleaned,
    )
    return {"cutoff": cutoff.isoformat(), "cleaned": cleaned}


@celery_app.task(
    name="app.tasks.diagnosis_maintenance.cleanup_evidence",
    bind=True,
    base=AsyncTask,
)
def cleanup_evidence(self: AsyncTask) -> dict:
    """每日 03:40：清理超期诊断证据（每回路超期仅留最新 1 条）。"""
    return self.run_async(_cleanup_expired_evidence())


# ---------------------------------------------------------------------------
# Beat 调度配置（追加方式注册，避免覆盖其他模块）
# ---------------------------------------------------------------------------
from celery.schedules import crontab  # noqa: E402

_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["diagnosis-evidence-cleanup"] = {
    "task": "app.tasks.diagnosis_maintenance.cleanup_evidence",
    "schedule": crontab(hour=3, minute=40),
}
celery_app.conf.beat_schedule = _existing_beat
