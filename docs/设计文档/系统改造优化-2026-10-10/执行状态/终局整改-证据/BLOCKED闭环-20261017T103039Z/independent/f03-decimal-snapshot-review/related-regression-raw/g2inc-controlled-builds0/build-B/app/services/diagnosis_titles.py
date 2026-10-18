"""诊断任务标题命名（2026-10-05 用户裁决：任务标题约定「回路诊断-YYMMDD-X」）。

三个诊断发起写入点（手动批量 / 定时 daily·weekly / 预警事件触发）共用：
- 日期按 Asia/Shanghai 本地日（Celery 时区同源，见 celery_app.conf.timezone）
- X = 当日全渠道共享自增序号（Redis INCR，并发发起不重号；
  定时任务多批连续创建时天然形成 1..N 批次号）
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.task_tracker import next_daily_sequence

_TZ = ZoneInfo("Asia/Shanghai")

_TITLE_PREFIX = "回路诊断"


async def next_diagnosis_title() -> str:
    """生成下一个诊断任务标题：回路诊断-YYMMDD-X（X=当日自增序号）."""
    seq = await next_daily_sequence("diagnosis")
    day = datetime.now(_TZ).strftime("%y%m%d")
    return f"{_TITLE_PREFIX}-{day}-{seq}"
