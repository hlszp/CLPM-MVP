"""数据源配置服务 — 读写 sys_config 表 + 同步 settings 内存 + 连通性测试.

对齐 .env 中的数据源配置项，支持运行时通过 UI 修改。

生效规则：
- historyApiUrl / historyApiToken / historyApiTimeout / signalrHubUrl / signalrReconnectInterval
  可即时生效（下次请求读取 settings 时生效）。
- dataSourceType 切换需重启后端（Provider 单例在首次调用时创建，见 factory.py）。
- signalrEnabled 切换需重启后端（订阅器后台任务在 lifespan 启动时初始化）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig

# sys_config 表中的数据源配置键
DATASOURCE_CONFIG_KEYS = {
    "dataSourceType": "datasource.type",
    "historyApiUrl": "datasource.history_api_url",
    "historyApiToken": "datasource.history_api_token",
    "historyApiTimeout": "datasource.history_api_timeout",
    "signalrHubUrl": "datasource.signalr_hub_url",
    "signalrEnabled": "datasource.signalr_enabled",
    "signalrReconnectInterval": "datasource.signalr_reconnect_interval",
}

# 字段 → settings 属性名 映射（用于同步内存）
_SETTINGS_ATTR_MAP = {
    "dataSourceType": "DATA_SOURCE_TYPE",
    "historyApiUrl": "HISTORY_DATA_API_URL",
    "historyApiToken": "HISTORY_DATA_API_TOKEN",
    "historyApiTimeout": "HISTORY_DATA_API_TIMEOUT",
    "signalrHubUrl": "SIGNALR_HUB_URL",
    "signalrEnabled": "SIGNALR_ENABLED",
    "signalrReconnectInterval": "SIGNALR_RECONNECT_INTERVAL",
}

# 字段 → 类型转换（sys_config 存字符串，需转回原类型）
_TYPE_CASTERS: dict[str, type] = {
    "historyApiTimeout": float,
    "signalrEnabled": lambda v: v.lower() == "true",
    "signalrReconnectInterval": int,
}

_KEY_DESCRIPTIONS = {
    "dataSourceType": "历史数据源类型 tdengine/remote_api",
    "historyApiUrl": "外部历史数据 API 地址",
    "historyApiToken": "外部历史数据 API 鉴权 Token",
    "historyApiTimeout": "外部历史数据 API 超时（秒）",
    "signalrHubUrl": "实时数据 SignalR Hub URL",
    "signalrEnabled": "实时数据订阅启停",
    "signalrReconnectInterval": "SignalR 断线重连间隔（秒）",
}


async def _get_config_value(db: AsyncSession, key: str) -> str | None:
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
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type="sys_config",
        target_id=None,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


def _cast_value(field: str, raw: str | None):
    """将 sys_config 字符串值转回原类型；raw 为 None 时回退到 settings 默认值。"""
    if raw is None:
        return getattr(settings, _SETTINGS_ATTR_MAP[field])
    caster = _TYPE_CASTERS.get(field)
    return caster(raw) if caster else raw


async def get_datasource_config(db: AsyncSession) -> dict:
    """获取数据源配置。优先 sys_config，缺失回退 settings 默认值。"""
    values: dict[str, str | None] = {}
    for field, key in DATASOURCE_CONFIG_KEYS.items():
        values[field] = await _get_config_value(db, key)

    return {
        "dataSourceType": values["dataSourceType"] or settings.DATA_SOURCE_TYPE,
        "historyApiUrl": (
            values["historyApiUrl"]
            if values["historyApiUrl"] is not None
            else settings.HISTORY_DATA_API_URL
        ),
        "historyApiToken": (
            values["historyApiToken"]
            if values["historyApiToken"] is not None
            else settings.HISTORY_DATA_API_TOKEN
        ),
        "historyApiTimeout": _cast_value("historyApiTimeout", values["historyApiTimeout"]),
        "signalrHubUrl": (
            values["signalrHubUrl"]
            if values["signalrHubUrl"] is not None
            else settings.SIGNALR_HUB_URL
        ),
        "signalrEnabled": _cast_value("signalrEnabled", values["signalrEnabled"]),
        "signalrReconnectInterval": _cast_value(
            "signalrReconnectInterval", values["signalrReconnectInterval"]
        ),
        # 运行态：从 settings 读取（启动时初始化的实际状态）
        "historyProviderActive": settings.DATA_SOURCE_TYPE,
        "signalrSubscriberRunning": settings.SIGNALR_ENABLED,
    }


async def update_datasource_config(
    db: AsyncSession,
    operator: str,
    **kwargs,
) -> dict:
    """更新数据源配置（即时同步到 settings 内存）。

    即时生效项：historyApiUrl / historyApiToken / historyApiTimeout / signalrHubUrl / signalrReconnectInterval
    重启生效项：dataSourceType（Provider 单例）/ signalrEnabled（订阅器后台任务）
    """
    before = await get_datasource_config(db)
    before_json = json.dumps(before, ensure_ascii=False)

    for field, value in kwargs.items():
        if value is None or field not in DATASOURCE_CONFIG_KEYS:
            continue
        await _set_config_value(
            db,
            DATASOURCE_CONFIG_KEYS[field],
            str(value).lower() if isinstance(value, bool) else str(value),
            _KEY_DESCRIPTIONS[field],
            operator,
        )
        # 同步 settings 内存
        setattr(settings, _SETTINGS_ATTR_MAP[field], value)

    after = await get_datasource_config(db)
    after_json = json.dumps(after, ensure_ascii=False)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DATASOURCE_CONFIG_UPDATE",
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


async def test_history_api_connection(
    url: str | None,
    token: str | None,
    timeout: float,
) -> dict:
    """测试外部历史数据 API 连通性（发一个最小查询请求，不校验数据内容）。"""
    if not url:
        return {"success": False, "latencyMs": None, "message": "未配置历史数据 API 地址"}

    now = datetime.now(UTC).replace(tzinfo=None)
    start = now
    one_sec_ago = now - timedelta(seconds=1)

    payload = {
        "tagCodes": ["__CONNECTIVITY_TEST__"],
        "startTime": one_sec_ago.isoformat(),
        "endTime": now.isoformat(),
        "sampleInterval": 1,
    }
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        latency = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        if resp.status_code == 200:
            return {"success": True, "latencyMs": latency, "message": "历史数据 API 连接成功"}
        return {
            "success": False,
            "latencyMs": latency,
            "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
    except Exception as exc:
        latency = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        return {"success": False, "latencyMs": latency, "message": f"连接失败: {exc}"}


async def test_signalr_hub_connection(hub_url: str | None) -> dict:
    """测试 SignalR Hub 连通性（尝试 WebSocket 握手）。"""
    if not hub_url:
        return {"success": False, "latencyMs": None, "message": "未配置 SignalR Hub URL"}

    start = datetime.now(UTC).replace(tzinfo=None)
    try:
        async with websockets.connect(hub_url, open_timeout=5, close_timeout=1):
            # 连接成功即视为可达
            pass
        latency = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        return {"success": True, "latencyMs": latency, "message": "SignalR Hub 连接成功"}
    except Exception as exc:
        latency = int(
            (datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000
        )
        return {"success": False, "latencyMs": latency, "message": f"连接失败: {exc}"}


__all__ = [
    "get_datasource_config",
    "test_history_api_connection",
    "test_signalr_hub_connection",
    "update_datasource_config",
]
