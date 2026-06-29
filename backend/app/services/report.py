"""Report configuration service (S5-SYS-003).

Business logic:
- List report configs
- Create report config (with audit log)
- Update report config (with audit log)
- Trigger report generation (dispatches Celery task, returns taskId)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.report import ReportRecord
from app.models.report_config import ReportConfig

# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """Write an audit log entry."""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Report config CRUD
# ---------------------------------------------------------------------------


async def list_configs(db: AsyncSession) -> list[dict]:
    """Return all report configurations."""
    result = await db.execute(select(ReportConfig).order_by(ReportConfig.created_at.desc()))
    configs = result.scalars().all()
    return [_config_to_dict(c) for c in configs]


async def create_config(
    db: AsyncSession,
    *,
    operator: str,
    name: str,
    report_period: str,
    recipients: list[str],
    content_template: dict | None = None,
    is_enabled: bool = True,
) -> dict:
    """Create a new report configuration."""
    config_id = str(uuid4())
    config = ReportConfig(
        id=config_id,
        name=name,
        report_period=report_period,
        recipients=json.dumps(recipients, ensure_ascii=False),
        content_template=(
            json.dumps(content_template, ensure_ascii=False) if content_template else None
        ),
        is_enabled=is_enabled,
        created_by=operator,
        updated_by=operator,
    )
    db.add(config)

    after = _config_to_dict(config)
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="REPORT_CONFIG_CREATE",
        target_type="report_config",
        target_id=config_id,
        before_value=None,
        after_value=json.dumps(after, ensure_ascii=False, default=str),
    )
    await db.commit()

    return after


async def update_config(
    db: AsyncSession,
    *,
    operator: str,
    config_id: str,
    name: str | None = None,
    report_period: str | None = None,
    recipients: list[str] | None = None,
    content_template: dict | None = None,
    is_enabled: bool | None = None,
) -> dict:
    """Update a report configuration (partial update).

    Raises ``BizError(ERR_REPORT_CONFIG_NOT_FOUND)`` if config does not exist.
    """
    result = await db.execute(select(ReportConfig).where(ReportConfig.id == config_id))
    config = result.scalar_one_or_none()
    if config is None:
        raise BizError(
            code="ERR_REPORT_CONFIG_NOT_FOUND",
            message="报表配置不存在",
            status_code=404,
        )

    before = _config_to_dict(config)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if name is not None:
        config.name = name
    if report_period is not None:
        config.report_period = report_period
    if recipients is not None:
        config.recipients = json.dumps(recipients, ensure_ascii=False)
    if content_template is not None:
        config.content_template = json.dumps(content_template, ensure_ascii=False)
    if is_enabled is not None:
        config.is_enabled = is_enabled
    config.updated_by = operator
    config.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _config_to_dict(config)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="REPORT_CONFIG_UPDATE",
        target_type="report_config",
        target_id=config_id,
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


async def trigger_report_generation(
    db: AsyncSession,
    *,
    operator: str,
    config_id: str | None = None,
    report_period: str | None = None,
) -> dict:
    """Trigger report generation via Celery task.

    Returns ``{"taskId": ..., "taskType": "REPORT_GENERATE", "status": "PROCESSING", ...}``.
    """
    # If config_id is provided, verify it exists
    if config_id:
        result = await db.execute(select(ReportConfig).where(ReportConfig.id == config_id))
        config = result.scalar_one_or_none()
        if config is None:
            raise BizError(
                code="ERR_REPORT_CONFIG_NOT_FOUND",
                message="报表配置不存在",
                status_code=404,
            )
        report_period = config.report_period

    period = report_period or "DAILY"
    task_id = str(uuid4())

    # Dispatch Celery task (imported lazily to avoid circular deps in tests)
    from app.tasks.report_generator import generate_report_task

    generate_report_task.delay(task_id=task_id, config_id=config_id, report_period=period)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="REPORT_GENERATE",
        target_type="report_record",
        target_id=task_id,
        before_value=None,
        after_value=json.dumps(
            {"taskId": task_id, "configId": config_id, "reportPeriod": period},
            ensure_ascii=False,
        ),
    )
    await db.commit()

    return {
        "taskId": task_id,
        "taskType": "REPORT_GENERATE",
        "status": "PROCESSING",
        "checkUrl": f"/api/v1/reports/tasks/{task_id}",
        "estimatedSeconds": 30,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_to_dict(c: ReportConfig) -> dict:
    recipients = _safe_json_loads_list(c.recipients)
    content_template = _safe_json_loads(c.content_template)
    return {
        "id": str(c.id),
        "name": c.name,
        "reportPeriod": c.report_period,
        "recipients": recipients,
        "contentTemplate": content_template,
        "isEnabled": bool(c.is_enabled) if c.is_enabled is not None else True,
        "createdBy": c.created_by,
        "updatedBy": c.updated_by,
        "createdAt": c.created_at.isoformat() if c.created_at else None,
        "updatedAt": c.updated_at.isoformat() if c.updated_at else None,
    }


def _safe_json_loads(value: str | None) -> dict | None:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _safe_json_loads_list(value: str | None) -> list[str]:
    if value is None:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Task status query
# ---------------------------------------------------------------------------

# Mapping from ReportRecord.status to frontend-expected status
_STATUS_MAP = {
    "COMPLETED": "SUCCESS",
    "FAILED": "FAILED",
    "PROCESSING": "RUNNING",
}


async def get_task_status(db: AsyncSession, *, task_id: str) -> dict:
    """Query report task status by task_id (ReportRecord.id).

    Raises ``BizError(ERR_REPORT_TASK_NOT_FOUND)`` if task does not exist.
    """
    result = await db.execute(select(ReportRecord).where(ReportRecord.id == task_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise BizError(
            code="ERR_REPORT_TASK_NOT_FOUND",
            message="报表任务不存在",
            status_code=404,
        )

    mapped_status = _STATUS_MAP.get(record.status, record.status)
    progress = 100 if mapped_status == "SUCCESS" else (50 if mapped_status == "RUNNING" else 0)

    return {
        "downloadUrl": record.file_url if mapped_status == "SUCCESS" else None,
        "message": None,
        "progress": progress,
        "status": mapped_status,
        "taskId": str(record.id),
    }


__all__ = [
    "create_config",
    "get_task_status",
    "list_configs",
    "trigger_report_generation",
    "update_config",
]
