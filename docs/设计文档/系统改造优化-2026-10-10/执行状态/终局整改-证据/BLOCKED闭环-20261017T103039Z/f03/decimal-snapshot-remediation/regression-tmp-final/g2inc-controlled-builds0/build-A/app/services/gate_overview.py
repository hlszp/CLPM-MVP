"""评估数据门禁健康总览（0921）：回路断点比例 vs 门禁门槛监控数据源.

用途：评估得分缺口归因——"很多回路没有评估得分"的三层原因中，本接口聚焦
数据门禁层（断点比例>30%/点数不足/可信度 E 级）与轮次覆盖层（无快照回路），
为监控面板提供 latest-per-loop 口径的聚合与 TOP 榜。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.services.diagnosis_operators.gate import MAX_GAP_RATIO, MIN_DATA_POINTS

logger = logging.getLogger(__name__)

#: 断点比例分档（0~10/10~20/20~30/30~50/50+），最后一档含完全无数据
_GAP_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-10%", 0.0, 0.10),
    ("10-20%", 0.10, 0.20),
    ("20-30%", 0.20, 0.30),
    ("30-50%", 0.30, 0.50),
    ("50%+", 0.50, 1.01),
)

#: 观察窗：近 N 小时内有快照视为"本轮覆盖"（26h 覆盖完整小时节奏一轮）
_COVERAGE_WINDOW_HOURS = 26

#: TOP 榜条数上限
_TOP_N = 15


async def get_gate_overview(db: AsyncSession) -> dict[str, Any]:
    """门禁健康总览（latest-per-loop，活跃回路口径）.

    Returns:
        {
          "threshold": {"maxGapRatio": 0.3, "minDataPoints": 32},
          "coverage": {"totalLoops", "withSnapshot", "noSnapshot",
                        "scored", "inconclusive"},
          "gate": {"passed", "failed", "failReasons": {reason: n}},
          "gapBuckets": [{"label", "from", "to", "count"}],
          "topOffenders": [{"loopTagName", "gapRatio", "status",
                             "fitnessLevel", "reason", "tsEnd"}]
        }
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    window_start = now - timedelta(hours=_COVERAGE_WINDOW_HOURS)

    loops = (
        await db.execute(select(LoopLedger.id, LoopLedger.tag_name).where(LoopLedger.is_active))
    ).all()
    total_loops = len(loops)
    tag_of = {str(lid): name for lid, name in loops}

    sub = (
        select(
            KpiSnapshotHourly.loop_id,
            KpiSnapshotHourly.ts_end,
            KpiSnapshotHourly.status,
            KpiSnapshotHourly.score,
            KpiSnapshotHourly.fitness_level,
            KpiSnapshotHourly.fitness_detail,
            KpiSnapshotHourly.valid_rate,
        )
        .distinct(KpiSnapshotHourly.loop_id)
        .where(KpiSnapshotHourly.ts_end >= window_start)
        .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_end.desc())
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                sub.c.loop_id,
                sub.c.ts_end,
                sub.c.status,
                sub.c.score,
                sub.c.fitness_level,
                sub.c.fitness_detail,
                sub.c.valid_rate,
            )
        )
    ).all()

    scored = 0
    inconclusive = 0
    gate_passed = 0
    gate_failed = 0
    fail_reasons: dict[str, int] = {}
    bucket_counts = [0] * len(_GAP_BUCKETS)
    offenders: list[dict[str, Any]] = []

    for row in rows:
        loop_id, ts_end, status, score, fitness_level, detail, valid_rate = row
        lid = str(loop_id)
        if score is not None:
            scored += 1
            gate_passed += 1
        else:
            inconclusive += 1
            gate_failed += 1

        gate = None
        if isinstance(detail, dict):
            g = detail.get("gate")
            if isinstance(g, dict):
                gate = g

        # 断点比例：优先门禁精确值；成功快照不落 gate 详情（仅失败时写），
        # 回退用 1-valid_rate 估算（口径接近：均以点数占比为基）
        gap_f: float | None = None
        if gate is not None and isinstance(gate.get("gapRatio"), (int, float)):
            gap_f = float(gate["gapRatio"])
        elif valid_rate is not None:
            try:
                gap_f = max(0.0, min(1.0, 1.0 - float(valid_rate)))
            except (TypeError, ValueError):
                gap_f = None

        if gate is not None and not gate.get("passed"):
            reason = str(gate.get("reason") or "未知原因")
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
        elif score is None:
            reason = "无 gate 详情（旧快照或评估中断）"
        else:
            reason = None

        if gap_f is not None:
            for i, (_label, lo, hi) in enumerate(_GAP_BUCKETS):
                if lo <= gap_f < hi:
                    bucket_counts[i] += 1
                    break

        if score is None and gap_f is not None:
            offenders.append(
                {
                    "loopTagName": tag_of.get(lid, lid[:8]),
                    "gapRatio": round(gap_f, 4),
                    "status": status,
                    "fitnessLevel": fitness_level,
                    "reason": reason,
                    "tsEnd": ts_end.isoformat() if ts_end else None,
                }
            )

    offenders.sort(key=lambda x: -x["gapRatio"])
    return {
        "threshold": {"maxGapRatio": MAX_GAP_RATIO, "minDataPoints": MIN_DATA_POINTS},
        "coverage": {
            "totalLoops": total_loops,
            "withSnapshot": len(rows),
            "noSnapshot": max(total_loops - len(rows), 0),
            "scored": scored,
            "inconclusive": inconclusive,
        },
        "gate": {
            "passed": gate_passed,
            "failed": gate_failed,
            "failReasons": dict(sorted(fail_reasons.items(), key=lambda kv: -kv[1])),
        },
        "gapBuckets": [
            {"label": label, "from": lo, "to": min(hi, 1.0), "count": cnt}
            for (label, lo, hi), cnt in zip(_GAP_BUCKETS, bucket_counts, strict=True)
        ],
        "topOffenders": offenders[:_TOP_N],
    }
