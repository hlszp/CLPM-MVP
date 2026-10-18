"""Alerting service (S3-B2).

通过 webhook 推送告警通知：
- 支持 info/warning/critical 三级严重度
- webhook URL 为空时仅记录日志
- 发送失败不影响主流程（仅记录 ERROR 日志）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_alert(title: str, message: str, severity: str = "warning") -> None:
    """发送 webhook 告警。

    Args:
        title: 告警标题
        message: 告警内容
        severity: 严重级别 "info"/"warning"/"critical"
    """
    payload: dict[str, Any] = {
        "title": title,
        "message": message,
        "severity": severity,
        "timestamp": datetime.now(UTC).isoformat(),
        "source": settings.APP_NAME,
    }

    # webhook URL 为空时仅记录日志
    if not settings.ALERT_WEBHOOK_URL:
        log_method = {
            "info": logger.info,
            "warning": logger.warning,
            "critical": logger.critical,
        }.get(severity, logger.warning)
        log_method("[告警] %s: %s", title, message)
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.ALERT_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
        logger.info("告警已发送: %s (severity=%s)", title, severity)
    except Exception as exc:  # noqa: BLE001
        # 发送失败不影响主流程
        logger.error("告警发送失败: %s | title=%s", exc, title)


async def send_alert_if_condition(
    title: str, message: str, condition: bool, severity: str = "warning"
) -> None:
    """仅当 condition=True 时发送告警。"""
    if condition:
        await send_alert(title, message, severity)


__all__ = ["send_alert", "send_alert_if_condition"]
