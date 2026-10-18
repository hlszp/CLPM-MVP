"""低频慢变信号周期锚点补点（0929 诚实化：趋势窗口零点致 FILL(PREV) 无前值）。

问题：SP/MODE/PID_* 慢变位号在 AAS COV 推送下长时间无变化 = 零写点（实时
订阅器逐条推送直接落点、无锚点机制），趋势窗口内无任何原始点时读取侧
FILL(PREV) 无前值可填 → 趋势空白。导入链路有 600s 锚点（值未变也周期落
点），实时链路没有——两侧口径不对称。

方案（用户口径"至少每小时填写一个数据"）：Beat 每小时对每个活跃回路的
低频角色位号（SP/MODE/PID_P/PID_I/PID_D），取点表内最后有值状态
（read_last_states_before）前向延展一个 SNAPSHOT 锚点
（source_kind=SOURCE_KIND_SNAPSHOT）。值未变也落点，前向填充有界；
慢变信号（设定值/模式）数据源短时中断时延展仍物理正确。

保护：最后状态值为 None（无效值）不延展；PV/OP 高频位号不在补点范围
（COV 推送密集无空白问题，补点反增写入量）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery.schedules import crontab
from sqlalchemy import select

from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

#: 低频角色（对齐 data_import 高低频分层：PV/OP 高频不补）
LOW_FREQ_ROLES = ("SP", "MODE", "PID_P", "PID_I", "PID_D")


@celery_app.task(
    name="app.tasks.lowfreq_anchor.run_lowfreq_anchor",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 1, "countdown": 300},
)
def run_lowfreq_anchor(self: AsyncTask, backfill_hours: int = 0) -> dict:
    """每小时低频锚点补点。

    Args:
        backfill_hours: 历史回填小时数（0=只补当前锚点）。首次部署后可手动
            触发一次回填（如 720=30 天）：COV 语义下"最后有值点到 now 之间
            无变化记录 = 值未变"，按小时回填是语义正确的还原而非推断。
            上限 2160（90 天）。
    """
    backfill_hours = max(0, min(int(backfill_hours), 2160))
    return self.run_async(_do_anchor(backfill_hours))


async def _do_anchor(backfill_hours: int = 0) -> dict:
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.tag import TagRegistry
    from app.services.data_source.point_history_repository import (
        SOURCE_KIND_SNAPSHOT,
        PointEvent,
        read_last_states_before,
        write_events,
    )

    async with AsyncSessionLocal() as db:
        # 活跃回路的低频角色位号 → 点身份 ID（TagRegistry.id = 点身份）
        stmt = (
            select(TagRegistry.id, TagRegistry.tag_name)
            .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
            .join(LoopLedger, LoopTagMapping.loop_id == LoopLedger.id)
            .where(
                LoopLedger.is_active.is_(True),
                LoopTagMapping.tag_role.in_(LOW_FREQ_ROLES),
            )
            .distinct()
        )
        rows = (await db.execute(stmt)).all()

    if not rows:
        logger.info("低频锚点补点：无活跃回路的低频位号，跳过")
        return {"candidates": 0, "anchors_built": 0, "anchors_written": 0, "skipped_no_value": 0}

    point_ids = [str(r[0]) for r in rows]
    now = datetime.now(UTC)

    # 每点最后有值状态（不看未来；None 值不作为延展种子）
    last_states = await read_last_states_before(point_ids, now)

    events: list[PointEvent] = []
    skipped_no_value = 0
    now_hour = now.replace(minute=0, second=0, microsecond=0)
    for pid in point_ids:
        state = last_states.get(pid)
        if not state or state.get("value") is None:
            skipped_no_value += 1
            continue
        # 锚点时刻序列：从 max(now-回填窗, 最后有值点的下一整点) 起，
        # 按整点每小时一个（COV 语义：last_ts 到 now 无变化记录 = 值未变）。
        # 起点晚于当前整点（该小时已有真实推送）时序列为空，不补冗余点。
        anchor_start = now_hour - timedelta(hours=backfill_hours)
        last_ts = state.get("ts")
        if last_ts is not None:
            last_hour = (
                last_ts.replace(tzinfo=UTC) if last_ts.tzinfo is None else last_ts.astimezone(UTC)
            ).replace(minute=0, second=0, microsecond=0)
            anchor_start = max(anchor_start, last_hour + timedelta(hours=1))
        cursor = anchor_start
        while cursor <= now_hour:
            events.append(
                PointEvent(
                    point_id=pid,
                    ts=cursor,
                    value=state["value"],
                    quality_class=int(state.get("quality_class") or 1),
                    quality_raw=state.get("quality_raw"),
                    quality_schema=state.get("quality_schema"),
                    source_kind=SOURCE_KIND_SNAPSHOT,
                    source_id="clpm-lowfreq-anchor",
                )
            )
            cursor += timedelta(hours=1)

    result = await write_events(events, dedup=False, source_task="lowfreq-anchor")
    logger.info(
        "低频锚点补点完成: candidates=%d built=%d inserted=%d skipped_no_value=%d",
        len(rows),
        len(events),
        result.inserted,
        skipped_no_value,
    )
    return {
        "candidates": len(rows),
        "anchors_built": len(events),
        "anchors_written": result.inserted,
        "skipped_no_value": skipped_no_value,
    }


# 追加式注册 Beat（与 alert_patrol 同模式；minute=45 避开整点 KPI 任务窗口）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["lowfreq-anchor"] = {
    "task": "app.tasks.lowfreq_anchor.run_lowfreq_anchor",
    "schedule": crontab(minute=45),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"

__all__ = ["run_lowfreq_anchor"]
