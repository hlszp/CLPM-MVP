"""数据源配置服务 — 读写 sys_config 表 + 同步 settings 内存 + 连通性测试.

对齐 .env 中的数据源配置项，支持运行时通过 UI 修改。

生效规则：
- historyApiUrl / historyApiToken / historyApiTimeout / signalrHubUrl / signalrReconnectInterval
  可即时生效（下次请求读取 settings 时生效）。
- dataSourceType 切换需重启后端（Provider 单例在首次调用时创建，见 factory.py）。
- signalrEnabled 切换需重启后端（订阅器后台任务在 lifespan 启动时初始化）。

安全约定（2026-07-21 链路配置整改）：
- GET 响应与审计日志中的 historyApiToken 一律打码（保留前后各 4 位）；
  内部真实调用通过 mask_token=False 取原始值
- 更新语义：字段不传＝不变；空串＝显式清空；打码值回传＝忽略
- networkMode 触发的 Tailscale 切换失败时回滚 sys_config，保持 DB 与实际链路一致
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.system import _is_tailscale_available, switch_network_mode
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig

logger = logging.getLogger(__name__)

# Token 打码标记：保留前后各 4 位，中间以 **** 替代
_TOKEN_MASK = "****"
_TOKEN_KEEP = 4

# sys_config 表中的数据源配置键
DATASOURCE_CONFIG_KEYS = {
    "dataSourceType": "datasource.type",
    "networkMode": "datasource.network_mode",
    "historyApiUrl": "datasource.history_api_url",
    "historyApiToken": "datasource.history_api_token",
    "historyApiTimeout": "datasource.history_api_timeout",
    "signalrHubUrl": "datasource.signalr_hub_url",
    "signalrEnabled": "datasource.signalr_enabled",
    "signalrReconnectInterval": "datasource.signalr_reconnect_interval",
    "realtimeWritebackEnabled": "datasource.realtime_writeback_enabled",
}

# 字段 → settings 属性名 映射（用于同步内存）
_SETTINGS_ATTR_MAP = {
    "dataSourceType": "DATA_SOURCE_TYPE",
    "networkMode": "NETWORK_MODE",
    "historyApiUrl": "HISTORY_DATA_API_URL",
    "historyApiToken": "HISTORY_DATA_API_TOKEN",
    "historyApiTimeout": "HISTORY_DATA_API_TIMEOUT",
    "signalrHubUrl": "SIGNALR_HUB_URL",
    "signalrEnabled": "SIGNALR_ENABLED",
    "signalrReconnectInterval": "SIGNALR_RECONNECT_INTERVAL",
    "realtimeWritebackEnabled": "REALTIME_WRITEBACK_ENABLED",
}

# 字段 → 类型转换（sys_config 存字符串，需转回原类型）
_TYPE_CASTERS: dict[str, type] = {
    "historyApiTimeout": float,
    "signalrEnabled": lambda v: v.lower() == "true",
    "signalrReconnectInterval": int,
    "realtimeWritebackEnabled": lambda v: v.lower() == "true",
}

_KEY_DESCRIPTIONS = {
    "dataSourceType": "历史数据源类型 tdengine/remote_api",
    "networkMode": "网络模式 lan（局域网直连）/wan（公网走 Tailscale）",
    "historyApiUrl": "外部历史数据 API 地址",
    "historyApiToken": "外部历史数据 API 鉴权 Token",
    "historyApiTimeout": "外部历史数据 API 超时（秒）",
    "signalrHubUrl": "实时数据 SignalR Hub URL",
    "signalrEnabled": "实时数据订阅启停",
    "signalrReconnectInterval": "SignalR 断线重连间隔（秒）",
    "realtimeWritebackEnabled": "实时数据写回本地 TDengine 宽表（仅 tdengine 模式）",
}

# 支持的网络模式
_VALID_NETWORK_MODES = {"lan", "wan"}


async def _get_config_rows(db: AsyncSession, keys: Iterable[str]) -> dict[str, SysConfig]:
    """一次 IN 查询批量读取 sys_config 行，返回 {key: row}。"""
    result = await db.execute(select(SysConfig).where(SysConfig.key.in_(list(keys))))
    return {row.key: row for row in result.scalars().all()}


async def _set_config_values(
    db: AsyncSession,
    items: dict[str, tuple[str, str | None]],
    operator: str,
) -> None:
    """批量写入 sys_config（一次 IN 查询取已存在行，存在则更新，否则新建）。

    Args:
        items: {sys_config key: (value, description)}
    """
    if not items:
        return
    existing = await _get_config_rows(db, items.keys())
    now = datetime.now(UTC).replace(tzinfo=None)
    for key, (value, description) in items.items():
        cfg = existing.get(key)
        if cfg is None:
            db.add(
                SysConfig(
                    key=key,
                    value=value,
                    description=description,
                    updated_by=operator,
                    updated_at=now,
                )
            )
        else:
            cfg.value = value
            cfg.updated_by = operator
            cfg.updated_at = now


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
    """将 sys_config 字符串值转回原类型；raw 为 None 时回退到 settings 默认值。

    脏数据容错：转换失败（如手工改库写入非数值）时回退原字符串并记日志，
    避免 GET /config 因单条脏数据整体 500。
    """
    if raw is None:
        return getattr(settings, _SETTINGS_ATTR_MAP[field])
    caster = _TYPE_CASTERS.get(field)
    if caster is None:
        return raw
    try:
        return caster(raw)
    except (TypeError, ValueError):
        logger.warning(
            "sys_config 脏数据：%s=%r 无法转换为目标类型，回退原字符串",
            _SETTINGS_ATTR_MAP[field],
            raw,
        )
        return raw


def _mask_token(token: str | None) -> str | None:
    """Token 打码：保留前后各 4 位，中间以 **** 替代；长度不足则全打码。"""
    if not token:
        return token
    if len(token) <= _TOKEN_KEEP * 2:
        return _TOKEN_MASK
    return f"{token[:_TOKEN_KEEP]}{_TOKEN_MASK}{token[-_TOKEN_KEEP:]}"


def _signalr_subscriber_running() -> bool:
    """查询 SignalR 订阅器真实运行状态（非 settings 配置镜像）。

    订阅器在 lifespan 启动时初始化，signalrEnabled 配置变更需重启后端
    才生效，因此运行状态必须读订阅器实例而非配置。
    """
    try:
        from app.services.data_source.realtime_subscriber import get_subscriber

        return bool(getattr(get_subscriber(), "_running", False))
    except Exception:
        logger.warning("无法获取 SignalR 订阅器运行状态，回退 settings 配置", exc_info=True)
        return bool(settings.SIGNALR_ENABLED)


async def get_datasource_config(db: AsyncSession, *, mask_token: bool = True) -> dict:
    """获取数据源配置。优先 sys_config，缺失回退 settings 默认值（一次 IN 查询）。

    Args:
        mask_token: True（默认）时 historyApiToken 打码返回（保留前后各 4 位），
            用于 GET 响应与审计日志；内部真实调用（如连通性测试、启动预载）
            必须传 False 取原始 Token。
    """
    rows = await _get_config_rows(db, DATASOURCE_CONFIG_KEYS.values())
    values: dict[str, str | None] = {
        field: (row.value if (row := rows.get(key)) is not None else None)
        for field, key in DATASOURCE_CONFIG_KEYS.items()
    }

    token = (
        values["historyApiToken"]
        if values["historyApiToken"] is not None
        else settings.HISTORY_DATA_API_TOKEN
    )
    return {
        "dataSourceType": values["dataSourceType"] or settings.DATA_SOURCE_TYPE,
        "networkMode": values["networkMode"] or settings.NETWORK_MODE,
        "historyApiUrl": (
            values["historyApiUrl"]
            if values["historyApiUrl"] is not None
            else settings.HISTORY_DATA_API_URL
        ),
        "historyApiToken": _mask_token(token) if mask_token else token,
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
        "realtimeWritebackEnabled": _cast_value(
            "realtimeWritebackEnabled", values["realtimeWritebackEnabled"]
        ),
        # 运行态：计算类历史数据查询一律本地 TDengine（2026-07-20 架构决策）
        "historyProviderActive": "tdengine",
        "signalrSubscriberRunning": _signalr_subscriber_running(),
        # tailscale 客户端可用性预检（容器内为 False）
        "tailscaleAvailable": _is_tailscale_available(),
    }


async def update_datasource_config(
    db: AsyncSession,
    operator: str,
    **kwargs,
) -> dict:
    """更新数据源配置（即时同步到 settings 内存）。

    即时生效项：networkMode（触发 Tailscale 切换）/ historyApiUrl / historyApiToken
    / historyApiTimeout / signalrHubUrl / signalrReconnectInterval
    重启生效项：dataSourceType（Provider 单例）/ signalrEnabled（订阅器后台任务）

    更新语义（PR 约定）：
    - 字段不传（None）＝保持不变
    - 字符串字段传空串 "" ＝显式清空（historyApiUrl / historyApiToken / signalrHubUrl）
    - historyApiToken 传入含打码标记 **** 的值视为前端误回传打码 Token，忽略该字段
    - networkMode 切换 Tailscale 失败时回滚 sys_config 与 settings，保持 DB 与实际链路一致
    """
    # 校验 networkMode 值域
    if kwargs.get("networkMode") is not None:
        nm = kwargs["networkMode"]
        if nm not in _VALID_NETWORK_MODES:
            raise ValueError(f"不支持的 networkMode: {nm!r}，可选: lan / wan")

    before = await get_datasource_config(db)
    before_json = json.dumps(before, ensure_ascii=False)

    # 批量写入：收集待更新项后一次 IN 查询 upsert
    items: dict[str, tuple[str, str | None]] = {}
    settings_updates: dict[str, Any] = {}
    for field, value in kwargs.items():
        if value is None or field not in DATASOURCE_CONFIG_KEYS:
            continue
        if field == "historyApiToken" and isinstance(value, str) and _TOKEN_MASK in value:
            logger.warning(
                "historyApiToken 传入打码值（含 %r），按误回传处理忽略本次更新", _TOKEN_MASK
            )
            continue
        items[DATASOURCE_CONFIG_KEYS[field]] = (
            str(value).lower() if isinstance(value, bool) else str(value),
            _KEY_DESCRIPTIONS[field],
        )
        settings_updates[field] = value
    await _set_config_values(db, items, operator)
    # 同步 settings 内存
    for field, value in settings_updates.items():
        setattr(settings, _SETTINGS_ATTR_MAP[field], value)

    after = await get_datasource_config(db)

    # 检测 networkMode 变化 → 触发 Tailscale 子网路由切换
    tailscale_result: dict | None = None
    requested_mode = kwargs.get("networkMode")
    if requested_mode is not None and before["networkMode"] != after["networkMode"]:
        tailscale_result = dict(switch_network_mode(after["networkMode"]))
        if tailscale_result["status"] == "failed":
            # 回滚 sys_config 与 settings，避免 DB 记录的模式与实际路由发散
            await _set_config_values(
                db,
                {
                    DATASOURCE_CONFIG_KEYS["networkMode"]: (
                        before["networkMode"],
                        _KEY_DESCRIPTIONS["networkMode"],
                    )
                },
                operator,
            )
            setattr(
                settings,
                _SETTINGS_ATTR_MAP["networkMode"],
                before["networkMode"],
            )
            after = await get_datasource_config(db)
            tailscale_result["rolledBack"] = True
            tailscale_result["message"] = (
                f"{tailscale_result['message']}；已回滚为 "
                f"{before['networkMode']}（数据库与实际链路保持一致）"
            )
            logger.warning("Tailscale 切换失败，networkMode 已回滚为 %s", before["networkMode"])
        # 审计日志记录 Tailscale 切换结果
        await _write_audit(
            db=db,
            operator=operator,
            operation_type="TAILSCALE_SWITCH",
            before_value=before["networkMode"],
            after_value=(
                f"{requested_mode} -> {tailscale_result['status']}: {tailscale_result['message']}"
            ),
        )

    after_json = json.dumps(after, ensure_ascii=False)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DATASOURCE_CONFIG_UPDATE",
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    # 若触发了 Tailscale 切换，在返回值中附加结果
    if tailscale_result is not None:
        after["tailscaleSwitch"] = tailscale_result
    return after


async def preload_datasource_config(db: AsyncSession) -> None:
    """启动时从 sys_config 预载数据源配置到 settings 内存。

    应用场景：FastAPI lifespan startup 时调用，确保 SignalR 订阅器等
    组件读取 settings 时获取的是 sys_config 中的运行时配置（真相源），
    而不是 .env 中的初始默认值（可能为空）。

    与 ``update_datasource_config`` 的区别：
    - preload 只读取不写入，不触发 Tailscale 切换，不写审计日志
    - update 是 UI 主动修改时调用，会触发副作用（Tailscale 切换 + 审计）
    - preload 失败不应阻塞启动，调用方应 try/except 兜底
    """
    # 预载必须取原始 Token（mask_token=False），否则 settings 会被写入打码值
    config = await get_datasource_config(db, mask_token=False)
    for field, attr in _SETTINGS_ATTR_MAP.items():
        value = config.get(field)
        if value is not None:
            setattr(settings, attr, value)


async def test_history_api_connection(
    url: str | None,
    token: str | None,
    timeout: float,
    tag_code: str | None = None,
) -> dict:
    """测试外部历史数据 API 连通性（用真实 tag 发最小查询请求）。

    Args:
        url: 历史 API 地址
        token: Bearer token（可选）
        timeout: 超时秒数
        tag_code: 真实 tag 位号。若提供则用此 tag 测试数据查询链路；
            若不提供则回退到假 tag（仅测试 HTTP 连通性，不验证数据查询）。
    """
    if not url:
        return {"success": False, "latencyMs": None, "message": "未配置历史数据 API 地址"}

    now = datetime.now(UTC).replace(tzinfo=None)
    start = now
    one_sec_ago = now - timedelta(seconds=1)

    # 优先使用真实 tag 验证数据查询链路；无 tag 时回退到假 tag（仅测 HTTP 连通性）
    test_tag = tag_code or "__CONNECTIVITY_TEST__"
    is_real_tag = tag_code is not None

    payload = {
        "tagCodes": [test_tag],
        "startTime": one_sec_ago.isoformat(),
        "endTime": now.isoformat(),
        "sampleInterval": 1,
    }
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=payload, headers=headers)
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        if resp.status_code == 200:
            # 检查响应体是否包含有效数据结构
            try:
                body = resp.json()
                code = body.get("code")
                if code != 200 and code != "200":
                    return {
                        "success": False,
                        "latencyMs": latency,
                        "message": f"业务码异常: code={code}, msg={body.get('message', '')}",
                    }
            except Exception:
                pass
            label = f"tag={test_tag}" if is_real_tag else "假tag（仅测连通性）"
            return {
                "success": True,
                "latencyMs": latency,
                "message": f"历史数据 API 连接成功（{label}, {latency}ms）",
            }
        return {
            "success": False,
            "latencyMs": latency,
            "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
    except httpx.TimeoutException:
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        label = f"tag={test_tag}" if is_real_tag else "假tag"
        return {
            "success": False,
            "latencyMs": latency,
            "message": f"请求超时（{label}, {latency}ms）— 远程 API 数据查询无响应",
        }
    except Exception as exc:
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
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
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        return {"success": True, "latencyMs": latency, "message": "SignalR Hub 连接成功"}
    except Exception as exc:
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        return {"success": False, "latencyMs": latency, "message": f"连接失败: {exc}"}


__all__ = [
    "get_datasource_config",
    "preload_datasource_config",
    "test_history_api_connection",
    "test_signalr_hub_connection",
    "update_datasource_config",
]
