"""回路数据健康标记判定任务（2026-10-10 运维圈选功能）。

SP_FOLLOWS_PV：自动时段 SP 持续跟随 PV 的位号错配嫌疑。
正常回路 SP 由操作员给定（近恒定）；串级副回路 SP 跟踪主回路 OP。
自动时段内 sp_std/pv_std 比值持续 ≈1 且样本充足 → SP 与 PV 同源嫌疑。

判定口径（避免两类误伤）：
- 只统计自动时段（auto_mode_rate ≥ 50% 的小时快照）——手动回路的
  SP tracking（DCS 无扰切换设计）会让 SP 贴着 PV 走，属正常伴生现象；
- PV 波动近死点（pv_std ≤ 0.05，归一化 0~100 量纲）的小时剔除，
  避免分母趋零导致比值虚高；
- 当前处于串级模式（MODE=2）的命中行 suspected_cascade=True，
  串级 SP 随动属正常，默认筛选圈选时排除（信息保留供核对）。

输出：loop_health_flag 表按 flag_type 全量重建（DELETE+INSERT 单事务，
天然幂等）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from celery.schedules import crontab
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

#: 判定窗口（天）
WINDOW_DAYS = 7

#: 自动时段门槛：小时快照 auto_mode_rate ≥ 该值才计入统计
AUTO_RATE_THRESHOLD = 50.0

#: PV 波动下限（归一化 0~100 量纲），低于视为死点不计入
PV_STD_FLOOR = 0.05

#: SP/PV 波动比命中区间（跟随 = 比值贴近 1；远超 1.15 属 SP 剧烈
#: 变化而非跟随，如间歇工况换产）
RATIO_MIN = 0.9
RATIO_MAX = 1.15

#: 窗口内自动时段最少小时数（不足则证据不够，不标记）
MIN_AUTO_HOURS = 24

_FLAG_TYPE = "SP_FOLLOWS_PV"

_AGG_SQL = text(
    """
    WITH agg AS (
        SELECT k.loop_id,
               AVG(k.sp_std) AS avg_sp_std,
               AVG(k.pv_std) AS avg_pv_std,
               COUNT(*) AS auto_hours
        FROM kpi_snapshot_hourly k
        JOIN loop_ledger ll ON ll.id = k.loop_id
        WHERE k.ts_start >= :start
          AND k.status = 'SUCCESS'
          AND k.auto_mode_rate >= :auto_thresh
          AND k.sp_std IS NOT NULL
          AND k.pv_std IS NOT NULL
          AND k.pv_std > :pv_floor
          AND ll.is_active = TRUE
        GROUP BY k.loop_id
    )
    SELECT a.loop_id,
           a.avg_sp_std,
           a.avg_pv_std,
           a.auto_hours,
           m.mode_now
    FROM agg a
    LEFT JOIN LATERAL (
        SELECT tr.current_value AS mode_now
        FROM loop_tag_mapping tm
        JOIN tag_registry tr ON tr.id = tm.tag_id
        WHERE tm.loop_id = a.loop_id AND tm.tag_role = 'MODE'
        LIMIT 1
    ) m ON TRUE
    WHERE a.avg_sp_std / a.avg_pv_std BETWEEN :ratio_min AND :ratio_max
      AND a.auto_hours >= :min_hours
    """
)


async def _do_compute_sp_follows_pv() -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    window_start = now - timedelta(days=WINDOW_DAYS)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                _AGG_SQL,
                {
                    "start": window_start,
                    "auto_thresh": AUTO_RATE_THRESHOLD,
                    "pv_floor": PV_STD_FLOOR,
                    "ratio_min": RATIO_MIN,
                    "ratio_max": RATIO_MAX,
                    "min_hours": MIN_AUTO_HOURS,
                },
            )
        ).all()

        await db.execute(
            text("DELETE FROM loop_health_flag WHERE flag_type = :ft"),
            {"ft": _FLAG_TYPE},
        )
        inserted = 0
        suspected = 0
        for r in rows:
            mode_now = float(r.mode_now) if r.mode_now is not None else None
            is_cascade = mode_now == 2.0
            if is_cascade:
                suspected += 1
            await db.execute(
                text(
                    """
                    INSERT INTO loop_health_flag
                        (loop_id, flag_type, evidence, suspected_cascade, computed_at)
                    VALUES (:loop_id, :flag_type, CAST(:evidence AS jsonb),
                            :suspected_cascade, :now)
                    ON CONFLICT (loop_id, flag_type) DO UPDATE
                        SET evidence = CAST(:evidence AS jsonb),
                            suspected_cascade = :suspected_cascade,
                            computed_at = :now
                    """
                ),
                {
                    "loop_id": str(r.loop_id),
                    "flag_type": _FLAG_TYPE,
                    "evidence": json.dumps(
                        {
                            "ratio": round(float(r.avg_sp_std) / float(r.avg_pv_std), 3),
                            "avgSpStd": round(float(r.avg_sp_std), 4),
                            "avgPvStd": round(float(r.avg_pv_std), 4),
                            "autoHours": int(r.auto_hours),
                            "windowDays": WINDOW_DAYS,
                            "modeNow": mode_now,
                        },
                        ensure_ascii=False,
                    ),
                    "suspected_cascade": is_cascade,
                    "now": now,
                },
            )
            inserted += 1
        await db.commit()
    logger.info(
        "SP随动判定完成: 命中=%d（其中疑似串级=%d，默认不进圈选）窗口=%dd",
        inserted,
        suspected,
        WINDOW_DAYS,
    )
    return {"flagType": _FLAG_TYPE, "flagged": inserted, "suspectedCascade": suspected}


@celery_app.task(name="app.tasks.loop_health.compute_loop_health_flags")
def compute_loop_health_flags() -> dict:
    """每日 SP 随动等健康标记判定（同步壳）。"""
    return AsyncTask().run_async(_do_compute_sp_follows_pv())


# ---------------------------------------------------------------------------
# Beat 调度（追加方式注册）：03:40，避开诊断 00:30 / KPI 整点 / 归档 04:30
# ---------------------------------------------------------------------------
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["loop-health-daily"] = {
    "task": "app.tasks.loop_health.compute_loop_health_flags",
    "schedule": crontab(hour=3, minute=40),
}
celery_app.conf.beat_schedule = _existing_beat
