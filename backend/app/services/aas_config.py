"""AAS config service — read/write sys_config table (IDS v3.2 §3.2.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig

# sys_config 表中的 AAS 配置键
AAS_CONFIG_KEYS = {
    "endpoint": "aas.endpoint",
    "syncIntervalSeconds": "aas.sync_interval_seconds",
    "enabled": "aas.sync_enabled",
    "securityMode": "aas.security_mode",
    "lastSyncAt": "aas.last_sync_at",
    "lastSyncStatus": "aas.last_sync_status",
}

# 同步状态枚举（与前端 SyncStatus 对齐）
SYNC_STATUS_PROCESSING = "PROCESSING"
SYNC_STATUS_SUCCESS = "SUCCESS"
SYNC_STATUS_FAILED = "FAILED"


async def _get_config_value(db: AsyncSession, key: str) -> str | None:
    """读取 sys_config 表中某个 key 的值。"""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def _set_config_value(
    db: AsyncSession,
    key: str,
    value: str,
    description: str | None,
    operator: str,
) -> None:
    """写入 sys_config 表（upsert）。"""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = SysConfig(
            key=key,
            value=value,
            description=description,
            updated_by=operator,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(cfg)
    else:
        cfg.value = value
        cfg.updated_by = operator
        cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志。"""
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


async def get_aas_config(db: AsyncSession) -> dict:
    """获取 AAS 连接配置。

    优先从 sys_config 表读取，缺失则回退到 settings 默认值。
    lastSyncAt/lastSyncStatus 来自 sys_config 的 aas.last_sync_at/aas.last_sync_status 键。
    """
    endpoint = await _get_config_value(db, AAS_CONFIG_KEYS["endpoint"])
    sync_interval = await _get_config_value(db, AAS_CONFIG_KEYS["syncIntervalSeconds"])
    enabled = await _get_config_value(db, AAS_CONFIG_KEYS["enabled"])
    security_mode = await _get_config_value(db, AAS_CONFIG_KEYS["securityMode"])
    last_sync_at = await _get_config_value(db, AAS_CONFIG_KEYS["lastSyncAt"])
    last_sync_status = await _get_config_value(db, AAS_CONFIG_KEYS["lastSyncStatus"])

    return {
        "endpoint": endpoint if endpoint is not None else settings.AAS_ENDPOINT,
        "syncIntervalSeconds": (
            int(sync_interval) if sync_interval is not None else settings.AAS_SYNC_INTERVAL_SECONDS
        ),
        "enabled": (
            enabled.lower() == "true" if enabled is not None else settings.AAS_SYNC_ENABLED
        ),
        "mockMode": settings.AAS_MOCK_MODE,
        "securityMode": (
            security_mode if security_mode is not None else settings.AAS_SECURITY_MODE
        ),
        "lastSyncAt": last_sync_at,
        "lastSyncStatus": last_sync_status,
    }


async def set_last_sync_status(
    db: AsyncSession,
    status: str,
    sync_at: datetime | None = None,
) -> None:
    """更新 AAS 同步状态（写入 sys_config，立即提交）。

    用于 POST /aas/sync 触发时设置为 PROCESSING、同步完成时设置为 SUCCESS/FAILED。
    前端通过轮询 GET /aas/config 获取 lastSyncStatus 判断同步进度。

    Args:
        db: 数据库会话
        status: 同步状态 PROCESSING/SUCCESS/FAILED
        sync_at: 同步完成时间，None 则使用当前 UTC 时间（仅 SUCCESS/FAILED 时设置）
    """
    if sync_at is None and status != SYNC_STATUS_PROCESSING:
        sync_at = datetime.now(UTC).replace(tzinfo=None)
    sync_at_str = sync_at.isoformat() if sync_at else None
    await _set_config_value(
        db,
        AAS_CONFIG_KEYS["lastSyncStatus"],
        status,
        "AAS 最近一次同步状态",
        operator="system",
    )
    if sync_at_str:
        await _set_config_value(
            db,
            AAS_CONFIG_KEYS["lastSyncAt"],
            sync_at_str,
            "AAS 最近一次同步完成时间",
            operator="system",
        )
    await db.commit()


async def update_aas_config(
    db: AsyncSession,
    operator: str,
    endpoint: str | None = None,
    sync_interval_seconds: int | None = None,
    enabled: bool | None = None,
    security_mode: str | None = None,
) -> dict:
    """更新 AAS 连接配置（即时生效）。

    仅更新传入的非 None 字段。变更记录审计日志。
    """
    before = await get_aas_config(db)
    before_json = json.dumps(before, ensure_ascii=False)

    if endpoint is not None:
        await _set_config_value(
            db, AAS_CONFIG_KEYS["endpoint"], endpoint, "AAS OPC UA 端点", operator
        )
        # 即时生效：更新 settings 内存值
        settings.AAS_ENDPOINT = endpoint
    if sync_interval_seconds is not None:
        await _set_config_value(
            db,
            AAS_CONFIG_KEYS["syncIntervalSeconds"],
            str(sync_interval_seconds),
            "AAS 同步周期（秒）",
            operator,
        )
        settings.AAS_SYNC_INTERVAL_SECONDS = sync_interval_seconds
    if enabled is not None:
        await _set_config_value(
            db,
            AAS_CONFIG_KEYS["enabled"],
            str(enabled).lower(),
            "AAS 同步启停状态",
            operator,
        )
        settings.AAS_SYNC_ENABLED = enabled
    if security_mode is not None:
        await _set_config_value(
            db,
            AAS_CONFIG_KEYS["securityMode"],
            security_mode,
            "AAS 安全模式",
            operator,
        )
        settings.AAS_SECURITY_MODE = security_mode

    after = await get_aas_config(db)
    after_json = json.dumps(after, ensure_ascii=False)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="AAS_CONFIG_UPDATE",
        target_type="sys_config",
        target_id=None,  # 配置级操作无单一目标记录
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


__all__ = [
    "SYNC_STATUS_FAILED",
    "SYNC_STATUS_PROCESSING",
    "SYNC_STATUS_SUCCESS",
    "get_aas_config",
    "set_last_sync_status",
    "update_aas_config",
]
