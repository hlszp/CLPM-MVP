"""诊断阈值差异化覆盖服务（C3 差异化阈值，FDS §5.4.1）。

职责：
- 阈值覆盖 CRUD（loop_type/plant/loop 三级 scope）
- 控制类型模板查询
- 变更审计 + 缓存失效
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisThresholdOverride

logger = logging.getLogger(__name__)


async def list_overrides(
    db: AsyncSession,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> list[dict]:
    """列出阈值覆盖（可按 scope 筛选）。"""
    stmt = select(DiagnosisThresholdOverride).order_by(
        DiagnosisThresholdOverride.scope_type,
        DiagnosisThresholdOverride.diag_code,
    )
    if scope_type:
        stmt = stmt.where(DiagnosisThresholdOverride.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(DiagnosisThresholdOverride.scope_id == scope_id)
    result = await db.execute(stmt)
    return [_override_to_dict(o) for o in result.scalars().all()]


async def list_templates(db: AsyncSession) -> list[dict]:
    """列出控制类型模板（loop_type scope 的覆盖）。"""
    result = await db.execute(
        select(DiagnosisThresholdOverride)
        .where(DiagnosisThresholdOverride.scope_type == "loop_type")
        .order_by(DiagnosisThresholdOverride.scope_id, DiagnosisThresholdOverride.diag_code)
    )
    return [_override_to_dict(o) for o in result.scalars().all()]


async def upsert_override(
    db: AsyncSession,
    operator: str,
    *,
    diag_code: str,
    scope_type: str,
    scope_id: str,
    threshold: dict,
) -> dict:
    """创建或更新阈值覆盖。

    Raises:
        BizError: ERR_INVALID_SCOPE — scope_type 不合法
    """
    valid_scopes = {"loop_type", "plant", "loop"}
    if scope_type not in valid_scopes:
        raise BizError(
            code="ERR_INVALID_SCOPE",
            message=f"scope_type 必须为 {valid_scopes} 之一",
            status_code=422,
        )

    # 查找已有覆盖（唯一约束：diag_code + scope_type + scope_id）
    result = await db.execute(
        select(DiagnosisThresholdOverride).where(
            DiagnosisThresholdOverride.diag_code == diag_code,
            DiagnosisThresholdOverride.scope_type == scope_type,
            DiagnosisThresholdOverride.scope_id == scope_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        override = DiagnosisThresholdOverride(
            id=str(uuid4()),
            diag_code=diag_code,
            scope_type=scope_type,
            scope_id=scope_id,
            threshold=threshold,
            version=1,
            updated_by=operator,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(override)
        action = "CREATE"
        before_json = None
    else:
        before_json = json.dumps(_override_to_dict(existing), ensure_ascii=False, default=str)
        existing.threshold = threshold
        existing.version = (existing.version or 1) + 1
        existing.updated_by = operator
        existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
        override = existing
        action = "UPDATE"

    after_json = json.dumps(_override_to_dict(override), ensure_ascii=False, default=str)

    # 审计日志
    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=f"DIAG_THRESHOLD_{action}",
        target_type="diagnosis_threshold_override",
        target_id=str(override.id),
        before_value=before_json,
        after_value=after_json,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(audit_log)
    await db.commit()

    return _override_to_dict(override)


async def delete_override(db: AsyncSession, override_id: str, operator: str) -> None:
    """删除阈值覆盖。

    Raises:
        BizError: ERR_OVERRIDE_NOT_FOUND
    """
    result = await db.execute(
        select(DiagnosisThresholdOverride).where(DiagnosisThresholdOverride.id == override_id)
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise BizError(
            code="ERR_OVERRIDE_NOT_FOUND",
            message="阈值覆盖不存在",
            status_code=404,
        )

    before_json = json.dumps(_override_to_dict(override), ensure_ascii=False, default=str)
    await db.delete(override)

    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="DIAG_THRESHOLD_DELETE",
        target_type="diagnosis_threshold_override",
        target_id=override_id,
        before_value=before_json,
        after_value=None,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(audit_log)
    await db.commit()


def _override_to_dict(o: DiagnosisThresholdOverride) -> dict:
    """覆盖对象转字典。"""
    return {
        "overrideId": str(o.id),
        "diagCode": o.diag_code,
        "scopeType": o.scope_type,
        "scopeId": o.scope_id,
        "threshold": o.threshold or {},
        "version": o.version,
        "updatedBy": o.updated_by,
        "updatedAt": o.updated_at.isoformat() if o.updated_at else None,
    }


# ---------------------------------------------------------------------------
# C4: 配置版本与回滚
# ---------------------------------------------------------------------------


async def list_config_versions(db: AsyncSession, diag_id: str) -> list[dict]:
    """获取诊断配置的版本历史（从 sys_audit_log 读取）。

    返回该配置的所有变更记录（DIAG_CONFIG_UPDATE），按时间倒序排列。
    """
    result = await db.execute(
        select(SysAuditLog)
        .where(SysAuditLog.target_type == "diagnosis_config")
        .where(SysAuditLog.target_id == diag_id)
        .where(SysAuditLog.operation_type == "DIAG_CONFIG_UPDATE")
        .order_by(SysAuditLog.operated_at.desc())
    )
    logs = result.scalars().all()

    versions: list[dict] = []
    for idx, log in enumerate(logs):
        after_dict = None
        if log.after_value:
            try:
                after_dict = json.loads(log.after_value)
            except (json.JSONDecodeError, TypeError):
                pass
        before_dict = None
        if log.before_value:
            try:
                before_dict = json.loads(log.before_value)
            except (json.JSONDecodeError, TypeError):
                pass
        # 从 after_value 提取版本号
        version = (after_dict or {}).get("version", len(logs) - idx)
        versions.append(
            {
                "auditLogId": str(log.id),
                "version": version,
                "beforeValue": before_dict,
                "afterValue": after_dict,
                "operatedBy": log.operator,
                "operatedAt": log.operated_at.isoformat() if log.operated_at else None,
            }
        )
    return versions


async def rollback_config(db: AsyncSession, diag_id: str, audit_log_id: str, operator: str) -> dict:
    """回滚诊断配置到指定版本。

    从 sys_audit_log 读取目标版本的 before_value，恢复到该状态。
    回滚本身也记录审计日志。

    Raises:
        BizError: ERR_AUDIT_LOG_NOT_FOUND / ERR_NO_BEFORE_VALUE
    """
    # 读取目标审计日志
    log_result = await db.execute(select(SysAuditLog).where(SysAuditLog.id == audit_log_id))
    audit_log = log_result.scalar_one_or_none()
    if audit_log is None:
        raise BizError(
            code="ERR_AUDIT_LOG_NOT_FOUND",
            message="审计日志不存在",
            status_code=404,
        )

    if not audit_log.before_value:
        raise BizError(
            code="ERR_NO_BEFORE_VALUE",
            message="目标版本无 before_value（可能是首次创建），无法回滚",
            status_code=422,
        )

    # 解析 before_value
    try:
        before_dict = json.loads(audit_log.before_value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BizError(
            code="ERR_INVALID_BEFORE_VALUE",
            message=f"before_value 解析失败: {exc}",
            status_code=422,
        ) from exc

    # 加载当前配置
    from app.models.diagnosis import DiagnosisConfig

    config_result = await db.execute(select(DiagnosisConfig).where(DiagnosisConfig.id == diag_id))
    config = config_result.scalar_one_or_none()
    if config is None:
        raise BizError(
            code="ERR_DIAG_CONFIG_NOT_FOUND",
            message="诊断指标配置不存在",
            status_code=404,
        )

    # 记录回滚前的值
    current_before = json.dumps(_config_to_dict(config), ensure_ascii=False, default=str)

    # 恢复字段
    config.diag_name = before_dict.get("diagName", config.diag_name)
    config.algorithm_type = before_dict.get("algorithmType", config.algorithm_type)
    config.calc_method = before_dict.get("calcMethod", config.calc_method)
    config.params = before_dict.get("params", config.params)
    config.threshold = before_dict.get("threshold", config.threshold)
    config.is_enabled = before_dict.get("isEnabled", config.is_enabled)
    config.version = (config.version or 1) + 1
    config.updated_by = operator
    config.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _config_to_dict(config)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    # 回滚审计日志
    rollback_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="DIAG_CONFIG_ROLLBACK",
        target_type="diagnosis_config",
        target_id=str(config.id),
        before_value=current_before,
        after_value=after_json,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(rollback_log)
    await db.commit()

    return after


def _config_to_dict(config) -> dict:
    """DiagnosisConfig 转字典（CamelCase）。"""
    return {
        "diagId": str(config.id),
        "diagCode": config.diag_code,
        "diagName": config.diag_name,
        "algorithmType": config.algorithm_type,
        "calcMethod": config.calc_method,
        "params": config.params,
        "threshold": config.threshold,
        "isEnabled": config.is_enabled,
        "version": config.version,
        "updatedBy": config.updated_by,
        "updatedAt": config.updated_at.isoformat() if config.updated_at else None,
    }
