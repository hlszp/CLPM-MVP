"""KPI 快照共享查询（处置 KPI 对比 / 整定效果验证共用）。

口径来源：app/api/v1/endpoints/handling.py 的 _kpi_summary /
_latest_snapshot_in_window（08 设计方案 §4.3）。09 设计方案 §5.3 T5
提取为本共享模块；handling.py 私有副本的去重随 T11 回写改动一并进行
（避免与其并行开发期的工作区纠缠）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import KpiSnapshotHourly


def iso_z(dt: datetime | None) -> str | None:
    """naive UTC → ISO + Z（前端补 Z 转本地，同诊断模块口径）。"""
    return dt.isoformat() + "Z" if dt else None


def kpi_summary(snap: KpiSnapshotHourly | None) -> dict[str, Any] | None:
    """KPI 快照摘要：score + 六率 + 可信度 + 窗口；无快照侧为 None。"""

    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    if snap is None:
        return None
    return {
        "score": _f(snap.score),
        "goodValueRate": _f(snap.good_value_rate),
        "effectiveAutoRate": _f(snap.effective_auto_rate),
        "steadyRate": _f(snap.steady_rate),
        "accuracyRate": _f(snap.accuracy_rate),
        "fastRate": _f(snap.fast_rate),
        "oscillationRate": _f(snap.oscillation_rate),
        "saturationRate": _f(snap.saturation_rate),
        "confidenceLevel": snap.confidence_level,
        "tsStart": iso_z(snap.ts_start),
        "tsEnd": iso_z(snap.ts_end),
    }


async def latest_snapshot_in_window(
    db: AsyncSession, loop_id: str, win_start: datetime, win_end: datetime
) -> KpiSnapshotHourly | None:
    """窗口内最新一条有 score 的 kpi_snapshot_hourly 记录。

    win_start/win_end 必须为 naive UTC（PostgreSQL TIMESTAMP WITHOUT
    TIME ZONE 列，禁止传入 aware datetime）。
    """
    return (
        await db.execute(
            select(KpiSnapshotHourly)
            .where(
                KpiSnapshotHourly.loop_id == loop_id,
                KpiSnapshotHourly.score.is_not(None),
                KpiSnapshotHourly.ts_start >= win_start,
                KpiSnapshotHourly.ts_start <= win_end,
            )
            .order_by(KpiSnapshotHourly.ts_start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
