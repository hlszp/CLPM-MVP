#!/usr/bin/env python3
"""D01-A1/A2 屏障观察器：九条件 firstMet 轮询（15s），输出 barrier JSON。

复用 G5 口径：ledger/source/run_binding/algorithm_version/expected_status/latest/
nodes/TaskRecord终态/Celery父终态。仅只读 PG + Celery result backend。
用法: g5-d01-barrier-watch.py <parentTaskId> <tsStartISO> <out.json>
"""
import asyncio
import json
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend")

PARENT = sys.argv[1]
TS_START = sys.argv[2]  # '2026-10-14T05:00:00'（UTC naive 口径同 G5）
OUT = sys.argv[3]
# 可选第 4 参：投递时刻（UTC ISO），firstMet 以投递为基准而非观察器启动
T0_OVERRIDE = None
if len(sys.argv) > 4:
    T0_OVERRIDE = datetime.fromisoformat(sys.argv[4]).replace(tzinfo=UTC)

CONDITIONS = [
    "ledger",
    "source",
    "run_binding",
    "algorithm_version",
    "expected_status",
    "latest",
    "nodes",
    "taskrecord_terminal",
    "celery_parent_terminal",
]


async def check_once() -> dict:
    from sqlalchemy import func, select

    from app.core.db import AsyncSessionLocal
    from app.models.calculation_result import CalculationResultRecord
    from app.models.metric import (
        KpiSnapshotHourly,
        LoopConfidenceLatest,
    )
    from app.models.node_kpi import KpiNodeSnapshotHourly

    start = datetime.fromisoformat(TS_START)  # naive UTC（列口径同 G5）
    async with AsyncSessionLocal() as db:
        recs = (
            await db.execute(
                select(CalculationResultRecord).where(
                    CalculationResultRecord.ts_start >= start,
                    CalculationResultRecord.ts_start < start.replace(hour=start.hour + 1),
                    CalculationResultRecord.object_kind == "LOOP",
                )
            )
        ).scalars().all()
        snaps = (
            await db.execute(
                select(func.count()).select_from(KpiSnapshotHourly).where(
                    KpiSnapshotHourly.ts_start >= start,
                    KpiSnapshotHourly.ts_start < start.replace(hour=start.hour + 1),
                )
            )
        ).scalar()
        latest = (
            await db.execute(
                select(func.count()).select_from(LoopConfidenceLatest)
            )
        ).scalar()
        node_snaps = (
            await db.execute(
                select(func.count(func.distinct(KpiNodeSnapshotHourly.plant_node_id)))
                .select_from(KpiNodeSnapshotHourly)
                .where(
                    KpiNodeSnapshotHourly.ts_start >= start,
                    KpiNodeSnapshotHourly.ts_start < start.replace(hour=start.hour + 1),
                )
            )
        ).scalar()

    from app.services.task_tracker import get_task_status

    tr_status = await get_task_status(PARENT)
    tr_terminal = tr_status in {"SUCCESS", "FAILED", "PARTIAL"}

    n_rec = len(recs)
    bound = sum(1 for r in recs if r.logical_run_id)
    versioned = sum(1 for r in recs if r.algorithm_version)
    ok_status = sum(
        1 for r in recs if str(r.status) in {"SUCCESS", "INCONCLUSIVE"}
    )
    return {
        "ledger": n_rec >= 1030 and (snaps or 0) >= 1030,
        "source": bound >= 1030,
        "run_binding": bound >= 1030,
        "algorithm_version": versioned >= 1030,
        "expected_status": ok_status >= 1030,
        "latest": (latest or 0) >= 1030,
        "nodes": (node_snaps or 0) >= 20,
        "taskrecord_terminal": tr_terminal,
        "counts": {
            "records": n_rec,
            "snapshots": snaps,
            "bound": bound,
            "versioned": versioned,
            "ok_status": ok_status,
            "latest": latest,
            "nodeSnaps": node_snaps,
        },
    }


def celery_terminal() -> bool:
    from app.tasks.celery_app import celery_app

    res = celery_app.AsyncResult(PARENT)
    return res.status in {"SUCCESS", "FAILURE"}


async def celery_terminal_safe() -> bool:
    try:
        return await asyncio.wait_for(asyncio.to_thread(celery_terminal), timeout=5)
    except Exception:
        return False


async def main() -> None:
    t0 = time.monotonic()
    t0_wall = T0_OVERRIDE or datetime.now(UTC)
    first_met: dict[str, float] = {}
    last_counts: dict = {}
    while True:
        try:
            conds = await asyncio.wait_for(check_once(), timeout=20)
        except Exception as exc:  # 负载下 PG/Redis 抖动或悬挂不终止观察
            print(f"[poll-error] {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(15)
            continue
        last_counts = conds.pop("counts")
        conds["celery_parent_terminal"] = await celery_terminal_safe()
        elapsed = (datetime.now(UTC) - t0_wall).total_seconds()
        for name in CONDITIONS:
            if name not in first_met and conds.get(name):
                first_met[name] = round(elapsed, 3)
        print(
            f"[{elapsed:7.1f}s] met={len(first_met)}/9 counts={last_counts}",
            flush=True,
        )
        if len(first_met) == len(CONDITIONS):
            break
        if elapsed > 5400:
            break
        await asyncio.sleep(15)
    total = (datetime.now(UTC) - t0_wall).total_seconds()
    report = {
        "parentTaskId": PARENT,
        "window": {"tsStart": TS_START},
        "firstMet": first_met,
        "totalSeconds": round(total, 3),
        "wallClockFrom": t0_wall.isoformat(),
        "watcherNote": "观察器于投递后启动，firstMet 以投递时刻为基准；启动前已达成的条件记为该时刻下限",
        "allMet": len(first_met) == len(CONDITIONS),
        "lastCounts": last_counts,
        "observedAt": datetime.now(UTC).isoformat(),
    }
    with open(OUT, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("ALL_MET" if report["allMet"] else "INCOMPLETE", round(total, 1))


asyncio.run(main())
