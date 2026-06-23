"""Celery task for report generation (S5-SYS-003).

Design:
- Celery Beat dispatches tasks per configured period (SHIFT/DAILY/WEEKLY/MONTHLY)
- Each task creates a ``ReportRecord`` with status PROCESSING
- Generates a PDF report (using reportlab; can be replaced with Headless Browser)
- Updates ``ReportRecord`` to COMPLETED with file_url, or FAILED on error
- Writes an audit log entry

S2-A4: 区分可重试/不可重试异常 — 业务错误（NonRetryableError）不重试，
       系统错误（DB/网络等）自动重试 3 次。
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.models.report import ReportRecord
from app.models.report_config import ReportConfig
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


class NonRetryableError(Exception):
    """业务错误，不应重试（如配置缺失、周期参数非法等）。"""


# ---------------------------------------------------------------------------
# Celery task: manual / triggered report generation
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.report_generator.generate_report_task",
    bind=True,
    base=AsyncTask,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def generate_report_task(
    self: AsyncTask,
    task_id: str | None = None,
    config_id: str | None = None,
    report_period: str = "DAILY",
) -> dict:
    """Generate a report asynchronously.

    S2-A4: 业务错误（NonRetryableError）不重试；系统错误自动重试 3 次。

    Args:
        task_id: Optional task ID (used as ReportRecord ID)
        config_id: Optional report config ID
        report_period: Report period (SHIFT/DAILY/WEEKLY/MONTHLY)
    """
    logger.info("报表生成任务开始, task_id=%s, config_id=%s", task_id, config_id)
    try:
        result = self.run_async(_do_generate(task_id, config_id, report_period))
        logger.info("报表生成任务完成: %s", result)
        return result
    except NonRetryableError as exc:
        # 业务错误：不重试，直接记录失败
        logger.error("报表生成业务错误（不重试）: %s", exc)
        raise
    except Exception as exc:
        # 系统错误：自动重试
        logger.exception("报表生成系统错误（将重试）")
        raise self.retry(exc=exc, countdown=60) from None


# ---------------------------------------------------------------------------
# Beat schedule: auto-generate per period
# ---------------------------------------------------------------------------


_beat_entries = {
    "report-shift": {
        "task": "app.tasks.report_generator.generate_report_task",
        "schedule": 28800.0,  # 8 hours
        "kwargs": {"report_period": "SHIFT"},
    },
    "report-daily": {
        "task": "app.tasks.report_generator.generate_report_task",
        "schedule": 86400.0,  # 24 hours
        "kwargs": {"report_period": "DAILY"},
    },
    "report-weekly": {
        "task": "app.tasks.report_generator.generate_report_task",
        "schedule": 604800.0,  # 7 days
        "kwargs": {"report_period": "WEEKLY"},
    },
    "report-monthly": {
        "task": "app.tasks.report_generator.generate_report_task",
        "schedule": 2592000.0,  # 30 days
        "kwargs": {"report_period": "MONTHLY"},
    },
}

_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat.update(_beat_entries)
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# Async generation logic
# ---------------------------------------------------------------------------


async def _do_generate(
    task_id: str | None,
    config_id: str | None,
    report_period: str,
) -> dict:
    """Execute the report generation logic."""
    from app.core.db import AsyncSessionLocal

    # S2-A4: 业务参数校验 — 非法周期不重试
    valid_periods = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
    if report_period not in valid_periods:
        raise NonRetryableError(
            f"非法报表周期: {report_period}，允许值: {valid_periods}"
        )

    record_id = task_id or str(uuid4())

    async with AsyncSessionLocal() as db:
        # Load config if provided
        config: ReportConfig | None = None
        if config_id:
            result = await db.execute(select(ReportConfig).where(ReportConfig.id == config_id))
            config = result.scalar_one_or_none()
            # S2-A4: 配置缺失为业务错误，不重试
            if config is None:
                raise NonRetryableError(f"报表配置不存在: config_id={config_id}")

        # Create ReportRecord with PROCESSING status
        now = datetime.now(UTC).replace(tzinfo=None)
        record = ReportRecord(
            id=record_id,
            report_period=report_period,
            generated_at=now,
            status="PROCESSING",
        )
        db.add(record)
        await db.commit()

        try:
            # Generate PDF content
            # NOTE: This uses reportlab for simple PDF generation.
            # Can be replaced with Headless Browser (Playwright/Puppeteer)
            # rendering an HTML template to PDF for richer formatting.
            pdf_bytes = _generate_pdf(
                report_period=report_period,
                config_name=config.name if config else None,
                content_template=(
                    _parse_content_template(config.content_template) if config else None
                ),
            )

            # In production, upload to S3/MinIO and store the URL.
            # For now, use a placeholder path.
            file_url = f"/reports/{record_id}.pdf"

            record.status = "COMPLETED"
            record.file_url = file_url
            await db.commit()

            return {
                "reportId": record_id,
                "status": "COMPLETED",
                "fileUrl": file_url,
                "fileSize": len(pdf_bytes),
            }
        except Exception as exc:
            logger.exception("报表生成失败")
            record.status = "FAILED"
            await db.commit()
            return {
                "reportId": record_id,
                "status": "FAILED",
                "error": str(exc),
            }


def _generate_pdf(
    report_period: str,
    config_name: str | None = None,
    content_template: dict | None = None,
) -> bytes:
    """Generate a simple PDF report using reportlab.

    NOTE: This is a minimal implementation. For production, replace with
    Headless Browser rendering (e.g. Playwright) an HTML template to PDF
    for richer formatting, charts, and styling.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()

    elements: list = []
    title = "控制回路性能评估报告"
    if config_name:
        title = f"{config_name} - {title}"
    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 10 * mm))

    period_map = {
        "SHIFT": "班报",
        "DAILY": "日报",
        "WEEKLY": "周报",
        "MONTHLY": "月报",
    }
    period_name = period_map.get(report_period, report_period)
    elements.append(Paragraph(f"报表周期: {period_name}", styles["Normal"]))
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    elements.append(Paragraph(f"生成时间: {generated_at}", styles["Normal"]))
    elements.append(Spacer(1, 10 * mm))

    if content_template:
        elements.append(Paragraph("报表内容:", styles["Heading2"]))
        for key, value in content_template.items():
            elements.append(Paragraph(f"  {key}: {value}", styles["Normal"]))
    else:
        elements.append(Paragraph("本报表由 CLPM 系统自动生成。", styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()


def _parse_content_template(value: str | None) -> dict | None:
    """Parse content_template JSON string."""
    if value is None:
        return None
    import json

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


__all__ = [
    "AsyncTask",
    "NonRetryableError",
    "generate_report_task",
]
