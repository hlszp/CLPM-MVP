"""数据完整性定时巡检告警任务.

Beat 调度：
- ``data-integrity-daily-check``：每日 02:00 执行，检查前 24 小时
  （昨日 00:00 ~ 今日 00:00，Asia/Shanghai）本地 TDengine 数据完整性，
  PV 列完整度 < 95% 的回路经 alerting 发送告警。

设计依据：
- 复用 ``app.services.data_integrity.check_integrity``（本地 TDengine 宽表完整性检查）
- PV 完整度阈值 95%（与 A 级可信度门槛对齐，valid_rate ≥ 0.95 → A）
- 告警经 ``alerting.send_alert``（webhook 或日志降级）
- 数据源不可用（TDengine 故障）时跳过告警判定，避免误导

巡检窗口选择昨日 00:00 ~ 今日 00:00（24h）：
- 确保窗口已完整闭合（不含当日还在写入的数据）
- 与 KPI 整点评估窗口错开（02:00 避开整点 10 分诊断、0/8/16 点体检）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

#: PV 完整度告警阈值（与 A 级可信度门槛对齐：valid_rate ≥ 0.95 → A）
PV_COMPLETENESS_ALERT_THRESHOLD = 0.95

#: 告警回路清单最大列示数（超出部分仅记日志，避免告警消息过长）
_MAX_LISTED_LOOPS = 20


@celery_app.task(
    name="app.tasks.data_integrity_check.run_daily_integrity_check",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 300},
    retry_backoff=True,
    retry_backoff_max=1800,
    retry_jitter=True,
)
def run_daily_integrity_check(self: AsyncTask) -> dict:
    """执行每日数据完整性巡检（前 24 小时，PV 完整度 < 95% 告警）."""
    logger.info("每日数据完整性巡检任务开始")

    async def _do_check() -> dict:
        from app.core.db import AsyncSessionLocal
        from app.services.data_integrity import check_integrity

        # 时间窗口：昨日 00:00 ~ 今日 00:00（Asia/Shanghai）
        _SHANGHAI = timezone(timedelta(hours=8))
        now_sh = datetime.now(_SHANGHAI)
        today_start = now_sh.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        ts_start = yesterday_start.strftime("%Y-%m-%d %H:%M:%S")
        ts_end = today_start.strftime("%Y-%m-%d %H:%M:%S")

        async with AsyncSessionLocal() as db:
            result = await check_integrity(
                db=db,
                loop_ids=None,  # 查全部 READY 活跃回路
                ts_start=ts_start,
                ts_end=ts_end,
                expected_interval_s=1,
            )

        overall = result.get("overallCompleteness", 0.0)
        loop_details = result.get("loopDetails", [])
        data_unavailable = result.get("dataSourceUnavailable", False)

        if data_unavailable:
            logger.warning(
                "完整性巡检：TDengine 数据源不可用（failedLoopIds=%s），跳过告警判定",
                result.get("failedLoopIds", []),
            )
            return {
                "status": "data_source_unavailable",
                "tsStart": ts_start,
                "tsEnd": ts_end,
                "failedLoopIds": result.get("failedLoopIds", []),
            }

        # 收集 PV 完整度 < 95% 的回路
        low_completeness_loops: list[dict] = []
        for loop in loop_details:
            tag_name = loop.get("tagName", "")
            loop_id = loop.get("loopId", "")
            col_details = loop.get("colDetails", {})
            pv_completeness = col_details.get("pv", {}).get("completeness", 1.0)
            if pv_completeness < PV_COMPLETENESS_ALERT_THRESHOLD:
                low_completeness_loops.append(
                    {
                        "loopId": loop_id,
                        "tagName": tag_name,
                        "pvCompleteness": round(pv_completeness * 100, 2),
                        "missingColumns": loop.get("missingColumns", []),
                        "status": loop.get("status", ""),
                    }
                )

        if low_completeness_loops:
            from app.services.alerting import send_alert

            lines = [f"巡检窗口 {ts_start} ~ {ts_end}（Asia/Shanghai）"]
            lines.append(f"整体 PV 完整度: {overall * 100:.2f}%")
            lines.append(
                f"PV 完整度 < 95% 的回路 {len(low_completeness_loops)} 个:"
            )
            for loop in low_completeness_loops[:_MAX_LISTED_LOOPS]:
                lines.append(
                    f"  - {loop['tagName'] or loop['loopId']}: PV {loop['pvCompleteness']}%"
                )
            if len(low_completeness_loops) > _MAX_LISTED_LOOPS:
                lines.append(
                    f"  ... 其余 {len(low_completeness_loops) - _MAX_LISTED_LOOPS} 个详见巡检日志"
                )
            message = "\n".join(lines)

            await send_alert(
                title=f"数据完整性告警：{len(low_completeness_loops)} 个回路 PV 完整度 < 95%",
                message=message,
                severity="warning",
            )
            logger.warning(
                "完整性巡检告警已发送: %d 个回路 PV<95%% (整体 %.2f%%)",
                len(low_completeness_loops),
                overall * 100,
            )
        else:
            logger.info(
                "完整性巡检通过: 整体 PV 完整度 %.2f%%, 无 PV<95%% 回路",
                overall * 100,
            )

        return {
            "status": "alert" if low_completeness_loops else "ok",
            "overallCompleteness": overall,
            "lowCompletenessLoopCount": len(low_completeness_loops),
            "lowCompletenessLoops": low_completeness_loops,
            "tsStart": ts_start,
            "tsEnd": ts_end,
        }

    return self.run_async(_do_check())


# ---------------------------------------------------------------------------
# Beat 调度配置
# ---------------------------------------------------------------------------

from celery.schedules import crontab  # noqa: E402

# 追加方式注册 Beat 任务（避免覆盖其他模块的 beat_schedule）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
# 每日 02:00 执行（避开整点 KPI 评估与诊断调度，Asia/Shanghai 时区）
_existing_beat["data-integrity-daily-check"] = {
    "task": "app.tasks.data_integrity_check.run_daily_integrity_check",
    "schedule": crontab(hour=2, minute=0),
}
celery_app.conf.beat_schedule = _existing_beat


__all__ = ["run_daily_integrity_check"]
