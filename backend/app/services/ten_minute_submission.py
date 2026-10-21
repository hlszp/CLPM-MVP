"""正式评估只提交轻量影子请求；配置、租约和报告由背景任务处理。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

TASK_NAME = "app.tasks.ten_minute_shadow.request"
SUBMIT_TIMEOUT = 0.2
REQUEST_LIFETIME = timedelta(seconds=120)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _publish(kwargs: dict, expires: datetime):
    from app.tasks.celery_app import celery_app

    return celery_app.send_task(TASK_NAME, kwargs=kwargs, priority=9, retry=False, expires=expires)


async def submit(eligible, start, end, source, source_task_id) -> dict:
    """最多等待 broker 提交 0.2s，不读取 PG/Redis 配置或写报告。

    超时只能取消等待，不能终止已运行的提交线程。消息使用请求时刻固定的
    绝对过期时间，迟完成的提交不会重新获得完整排队寿命；Worker 须验证
    requested_at，并在后台检查开关、白名单和并发租约。
    """
    loop_ids = list(dict.fromkeys(str(value) for value in eligible))
    if not loop_ids:
        return {"status": "NOT_SELECTED"}
    requested_at = datetime.now(UTC)
    kwargs = {
        "loop_ids": loop_ids,
        "start": _utc(start).isoformat(),
        "end": _utc(end).isoformat(),
        "kind": f"ASSESS:{source or 'AUTO'}",
        "source_task_id": source_task_id,
        "requested_at": requested_at.isoformat(),
    }
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_publish, kwargs, requested_at + REQUEST_LIFETIME),
            timeout=SUBMIT_TIMEOUT,
        )
    except TimeoutError:
        logger.warning("影子请求提交等待超时；broker 线程可能迟完成，消息绝对过期时间保持不变")
        return {"status": "SUBMISSION_TIMEOUT"}
    except Exception:
        logger.exception("影子请求提交失败（正式评估结果已独立完成）")
        return {"status": "SUBMISSION_FAILED"}
    return {"status": "SUBMITTED"}
