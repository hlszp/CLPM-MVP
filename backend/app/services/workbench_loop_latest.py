"""``workbench_loop_latest`` 刷新服务 — 每回路最新态快照全量重算（5min Celery）.

生产 P1 根治（2026-10-07）：把驾驶舱接口里每请求执行的 per-loop latest
重查询（kpi_snapshot_hourly DISTINCT ON + diagnosis_run 双 LATERAL）搬到
本后台任务一次算齐，接口改纯读表。数据新鲜度 5min，对齐驾驶舱
5min 自动刷新语义与诊断调度（00:30）门禁口径。

SQL 与 workbench_diagnosis._query_open_tag_rows 同构（去掉窗口/LIMIT，
取每回路全局最新一条异常 run），LATERAL 结果（terminal_cnt/top_symptom）
随行固化。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models.workbench_loop_latest import WorkbenchLoopLatest

logger = logging.getLogger(__name__)

# 与 workbench_diagnosis._ACTION_LATERAL / _TOP_SYMPTOM_LATERAL 同构（复用口径）
_ACTION_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE a.status IN ('CONVERTED', 'REJECTED', 'IGNORED'))
               AS terminal_cnt
        FROM loop_action_item a
        WHERE a.run_id = r.id
    ) act ON true
"""
_TOP_SYMPTOM_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT kv.key AS top_symptom
        FROM jsonb_each(r.symptom_tags) kv
        WHERE kv.value->>'detected' = 'true'
        ORDER BY (kv.value->>'confidence')::numeric DESC
        LIMIT 1
    ) sym ON true
"""

# 每回路最新小时快照（fitness 面 + score）
_LATEST_SNAPSHOT_SQL = """
    SELECT DISTINCT ON (s.loop_id)
           s.loop_id, s.score, s.fitness_level, s.fitness_tags,
           s.fitness_detail, s.assess_level, s.diagnose_level,
           s.tune_level, s.ts_start
    FROM kpi_snapshot_hourly s
    JOIN loop_ledger l ON l.id = s.loop_id AND l.is_active
    ORDER BY s.loop_id, s.ts_start DESC
"""

# 每回路最新异常 run（SUCCESS + category 非空；无窗口取全局最新）
_LATEST_RUN_SQL = f"""
    SELECT * FROM (
        SELECT DISTINCT ON (r.loop_id)
               r.loop_id, r.id AS run_id, r.severity, r.created_at,
               r.primary_category, r.primary_confidence AS confidence,
               r.rationale->>0 AS conclusion,
               sym.top_symptom,
               act.terminal_cnt,
               l.unit_id
        FROM diagnosis_run r
        JOIN loop_ledger l ON l.id = r.loop_id AND l.is_active
        {_ACTION_LATERAL}
        {_TOP_SYMPTOM_LATERAL}
        WHERE r.status = 'SUCCESS'
          AND r.primary_category IS NOT NULL
        ORDER BY r.loop_id, r.created_at DESC
    ) t
"""

# 回路台账静态面（位号/单元/装置名，冗余免 join）
_LOOP_LEDGER_SQL = """
    SELECT l.id AS loop_id, l.tag_name,
           un.id AS unit_id, un.name AS unit_name, fa.name AS factory_name
    FROM loop_ledger l
    LEFT JOIN plant_node un ON un.id = l.unit_id
    LEFT JOIN plant_node fa ON fa.id = un.parent_id
    WHERE l.is_active
"""


def _to_iso(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    return None


async def refresh_workbench_loop_latest(db: AsyncSession) -> dict[str, int]:
    """全量重算 workbench_loop_latest（活跃回路基数；upsert + 下线回路清理）。"""

    ledger = {
        str(row.loop_id): dict(row._mapping)
        for row in (await db.execute(text(_LOOP_LEDGER_SQL))).all()
    }
    snapshots = {
        str(row.loop_id): dict(row._mapping)
        for row in (await db.execute(text(_LATEST_SNAPSHOT_SQL))).all()
    }
    runs = {
        str(row.loop_id): dict(row._mapping)
        for row in (await db.execute(text(_LATEST_RUN_SQL))).all()
    }

    now = datetime.now(UTC)
    upserted = 0
    for loop_id, meta in ledger.items():
        snap = snapshots.get(loop_id)
        run = runs.get(loop_id)
        terminal_cnt = int(run.get("terminal_cnt") or 0) if run else 0
        values = {
            "loop_id": loop_id,
            "tag_name": meta.get("tag_name"),
            "unit_id": meta.get("unit_id"),
            "unit_name": meta.get("unit_name"),
            "factory_name": meta.get("factory_name"),
            "score": snap.get("score") if snap else None,
            "fitness_level": snap.get("fitness_level") if snap else None,
            "fitness_tags": snap.get("fitness_tags") if snap else None,
            "fitness_detail": snap.get("fitness_detail") if snap else None,
            "assess_level": snap.get("assess_level") if snap else None,
            "diagnose_level": snap.get("diagnose_level") if snap else None,
            "tune_level": snap.get("tune_level") if snap else None,
            "snapshot_ts": _to_iso(snap.get("ts_start")) if snap else None,
            "latest_run_id": run.get("run_id") if run else None,
            "latest_category": run.get("primary_category") if run else None,
            "latest_severity": run.get("severity") if run else None,
            "latest_confidence": run.get("confidence") if run else None,
            "latest_conclusion": run.get("conclusion") if run else None,
            "latest_run_at": _to_iso(run.get("created_at")) if run else None,
            "top_symptom": (
                {"key": run["top_symptom"]} if run and run.get("top_symptom") else None
            ),
            "terminal_cnt": terminal_cnt,
            "is_open": bool(run) and terminal_cnt == 0,
            "refreshed_at": now,
        }
        stmt = (
            pg_insert(WorkbenchLoopLatest)
            .values(**values)
            .on_conflict_do_update(
                constraint="uniq_wll_loop",
                set_={k: v for k, v in values.items() if k != "loop_id"},
            )
        )
        await db.execute(stmt)
        upserted += 1

    # 清理已下线/删除回路的残留行
    if ledger:
        keep_ids = list(ledger.keys())
        await db.execute(
            text(
                "DELETE FROM workbench_loop_latest "
                "WHERE loop_id NOT IN (SELECT unnest(CAST(:ids AS uuid[])))"
            ),
            {"ids": keep_ids},
        )
    await db.commit()
    open_cnt = sum(
        1
        for loop_id, _ in ledger.items()
        if loop_id in runs and int(runs[loop_id].get("terminal_cnt") or 0) == 0
    )
    logger.info(
        "workbench_loop_latest 刷新完成：%d 回路（未处置 %d），快照 %d / run %d",
        upserted,
        open_cnt,
        len(snapshots),
        len(runs),
    )
    return {"loops": upserted, "open": open_cnt}


async def refresh_workbench_loop_latest_task() -> dict[str, int]:
    """Celery 入口（独立会话）。"""
    async with AsyncSessionLocal() as db:
        return await refresh_workbench_loop_latest(db)
