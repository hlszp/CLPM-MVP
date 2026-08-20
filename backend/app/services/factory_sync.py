"""AAS 工厂模型同步（工厂配置页）。

参考 AAS-erm 的 ErmSync 模式实现（防腐层 + 全量 upsert）：
- 同步配置存 ``sys_config``（键 ``factory_sync.setting``，密码 Base64，
  空值表示保留原密码）；同步日志存 ``factory_sync.logs``（JSON 数组，
  保留最近 50 条）。
- 连接协议（ABP 动态 API）：
  - 登录：``POST {BaseUrl}{AuthApiPath}``，body ``{userNameOrEmailAddress,
    password}``，响应 ``result.accessToken``；
  - 区域节点：``GET {BaseUrl}{NodesApiPath}?SkipCount=&MaxResultCount=``
    分页循环拉全量（ABP PagedResultDto）。
- 同步语义（主数据源 = AAS）：
  - 按 ``source_node_id`` upsert：存在则更新名称并恢复软删（本实现无软删，
    直接更新名称/类型/父级）；不存在则新建；
  - 层级映射：AAS depth 0 → FACTORY、1 → AREA、≥2 → UNIT（CLPM 三层）；
  - 父先子后：按 depth 升序处理，父节点通过 source_node_id 映射；
  - 本地独立节点（source_node_id 为空）不受同步影响。
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import SysAuditLog
from app.models.plant_node import PlantNode
from app.models.sys_config import SysConfig

logger = logging.getLogger(__name__)

_KEY_SETTING = "factory_sync.setting"
_KEY_LOGS = "factory_sync.logs"

_DEFAULT_SETTING: dict[str, Any] = {
    "baseUrl": "http://192.168.100.2:81",
    "authApiPath": "/api/TokenAuth/Authenticate",
    "nodesApiPath": "/api/services/v1/AreaNode/GetAllPagedAndSorted",
    "userName": "admin",
    "password": "",
    "isEnabled": False,
    "pageBatchSize": 500,
    "lastSyncAt": None,
    "lastSyncStatus": None,
    "lastSyncSummary": None,
}

_MAX_LOGS = 50
_MAX_EMPTY_GUARD = 200
_TIMEOUT_LOGIN = 30.0
_TIMEOUT_FETCH = 60.0


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------


async def _get_config(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def _set_config(
    db: AsyncSession, key: str, value: str, description: str, operator: str
) -> None:
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    if cfg is None:
        cfg = SysConfig(
            key=key,
            value=value,
            description=description,
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = value
        cfg.description = description
        cfg.updated_by = operator
        cfg.updated_at = now


def _mask_setting(setting: dict[str, Any]) -> dict[str, Any]:
    """配置脱敏：密码不回传，仅回传是否已配置。"""
    masked = dict(setting)
    masked["hasPassword"] = bool(setting.get("password"))
    masked.pop("password", None)
    return masked


async def get_sync_setting(db: AsyncSession) -> dict[str, Any]:
    raw = await _get_config(db, _KEY_SETTING)
    if not raw:
        return _mask_setting(_DEFAULT_SETTING)
    try:
        data = json.loads(raw)
        merged = {**_DEFAULT_SETTING, **data}
        # 存储为 Base64
        if merged.get("password"):
            try:
                merged["password"] = base64.b64decode(merged["password"]).decode()
            except Exception:  # noqa: BLE001
                merged["password"] = ""
        return _mask_setting(merged)
    except (json.JSONDecodeError, TypeError):
        return _mask_setting(_DEFAULT_SETTING)


async def get_raw_sync_setting(db: AsyncSession) -> dict[str, Any]:
    """读取含明文密码的完整配置（仅内部同步/测试连接使用）。"""
    raw = await _get_config(db, _KEY_SETTING)
    if not raw:
        return dict(_DEFAULT_SETTING)
    try:
        data = json.loads(raw)
        merged = {**_DEFAULT_SETTING, **data}
        if merged.get("password"):
            try:
                merged["password"] = base64.b64decode(merged["password"]).decode()
            except Exception:  # noqa: BLE001
                merged["password"] = ""
        return merged
    except (json.JSONDecodeError, TypeError):
        return dict(_DEFAULT_SETTING)


async def save_sync_setting(
    db: AsyncSession,
    operator: str,
    *,
    base_url: str,
    auth_api_path: str,
    nodes_api_path: str,
    user_name: str,
    password: str | None,
    is_enabled: bool,
    page_batch_size: int = 500,
) -> dict[str, Any]:
    """保存同步配置（运行时生效；password 为空表示保留原密码）。"""
    current = await get_raw_sync_setting(db)
    plain_password = password if password else current.get("password", "")

    setting = {
        "baseUrl": base_url.rstrip("/"),
        "authApiPath": auth_api_path or _DEFAULT_SETTING["authApiPath"],
        "nodesApiPath": nodes_api_path or _DEFAULT_SETTING["nodesApiPath"],
        "userName": user_name,
        "password": (base64.b64encode(plain_password.encode()).decode() if plain_password else ""),
        "isEnabled": is_enabled,
        "pageBatchSize": max(1, min(page_batch_size or 500, 2000)),
        "lastSyncAt": current.get("lastSyncAt"),
        "lastSyncStatus": current.get("lastSyncStatus"),
        "lastSyncSummary": current.get("lastSyncSummary"),
    }
    await _set_config(
        db,
        _KEY_SETTING,
        json.dumps(setting, ensure_ascii=False),
        "工厂模型 AAS 同步配置（JSON，密码 Base64）",
        operator,
    )
    await db.commit()
    return _mask_setting(setting)


# ---------------------------------------------------------------------------
# AAS HTTP 适配（防腐层）
# ---------------------------------------------------------------------------


async def _login_aas(setting: dict[str, Any]) -> str:
    """登录 AAS 获取 Bearer Token；失败抛 RuntimeError。"""
    url = setting["baseUrl"] + _trim_slash(setting["authApiPath"])
    body = {
        "userNameOrEmailAddress": setting.get("userName", ""),
        "password": setting.get("password", ""),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_LOGIN) as client:
        resp = await client.post(url, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"AAS 登录 HTTP {resp.status_code}：{_brief(resp.text)}")
        data = resp.json()
    token = (data.get("result") or {}).get("accessToken")
    if not token:
        error = (data.get("error") or {}).get("message") or "AAS 登录响应中无 accessToken"
        raise RuntimeError(error)
    return token


async def _fetch_nodes_all(setting: dict[str, Any], token: str) -> list[dict[str, Any]]:
    """分页循环拉取 AAS 全量区域节点（ABP PagedResultDto）。"""
    base_url = setting["baseUrl"] + _trim_slash(setting["nodesApiPath"])
    batch = int(setting.get("pageBatchSize") or 500)
    all_items: list[dict[str, Any]] = []
    skip = 0
    async with httpx.AsyncClient(timeout=_TIMEOUT_FETCH) as client:
        for _guard in range(_MAX_EMPTY_GUARD):
            separator = "&" if "?" in base_url else "?"
            page_url = f"{base_url}{separator}SkipCount={skip}&MaxResultCount={batch}"
            resp = await client.get(page_url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                raise RuntimeError(f"AAS 节点接口 HTTP {resp.status_code}：{_brief(resp.text)}")
            result = resp.json().get("result") or {}
            items = result.get("items") or []
            total = result.get("totalCount") or 0
            if not items:
                break
            all_items.extend(items)
            skip += len(items)
            if total > 0 and skip >= total:
                break
    return all_items


def _trim_slash(path: str) -> str:
    if not path:
        return path
    return path if path.startswith("/") else f"/{path}"


def _brief(text: str, limit: int = 200) -> str:
    return text[:limit] + "…" if len(text) > limit else text


# ---------------------------------------------------------------------------
# 同步日志
# ---------------------------------------------------------------------------


async def _append_sync_log(
    db: AsyncSession,
    *,
    status: str,
    nodes_total: int,
    created: int,
    updated: int,
    duration_ms: int,
    operator: str,
    error_message: str | None = None,
) -> None:
    raw = await _get_config(db, _KEY_LOGS)
    logs: list[Any] = []
    if raw:
        try:
            parsed = json.loads(raw)
            logs = parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            logs = []
    logs.append(
        {
            "id": str(uuid4()),
            "syncType": "nodes",
            "startTime": _now_iso(),
            "durationMs": duration_ms,
            "status": status,
            "nodesTotal": nodes_total,
            "nodesCreated": created,
            "nodesUpdated": updated,
            "trigger": "manual",
            "operatorName": operator,
            "errorMessage": (error_message or "")[:2000] or None,
        }
    )
    logs = logs[-_MAX_LOGS:]
    await _set_config(
        db,
        _KEY_LOGS,
        json.dumps(logs, ensure_ascii=False),
        "工厂模型同步日志（JSON 数组，保留最近 50 条）",
        operator,
    )


async def get_sync_logs(db: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    raw = await _get_config(db, _KEY_LOGS)
    if not raw:
        return []
    try:
        logs = json.loads(raw)
        return list(reversed(logs[-limit:])) if isinstance(logs, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# 连接测试 / 全量同步
# ---------------------------------------------------------------------------


async def test_connection(setting: dict[str, Any]) -> dict[str, Any]:
    """用配置账号登录 AAS 验证连通性（不写库）。"""
    start = datetime.now(UTC)
    try:
        await _login_aas(setting)
        latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
        return {"success": True, "latencyMs": latency, "message": "连接成功，账号认证通过"}
    except Exception as exc:  # noqa: BLE001
        latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
        return {"success": False, "latencyMs": latency, "message": str(exc)}


async def sync_factory_model(db: AsyncSession, operator: str) -> dict[str, Any]:
    """全量同步 AAS 工厂模型（AreaNode → plant_node，按 source_node_id upsert）。

    Returns:
        {status: success|failed, nodesTotal, created, updated, message}
    """
    setting = await get_raw_sync_setting(db)
    start = datetime.now(UTC)
    status = "success"
    error_message: str | None = None
    nodes_total = created = updated = 0

    try:
        if not setting.get("isEnabled"):
            raise RuntimeError("同步未启用（请在同步设置中开启）")

        token = await _login_aas(setting)
        aas_nodes = await _fetch_nodes_all(setting, token)
        nodes_total = len(aas_nodes)

        # 计算 depth（parentId 链；环保护 20 层，父链断裂按 depth=0）
        by_id = {n.get("id"): n for n in aas_nodes if n.get("id") is not None}

        def depth_of(node: dict[str, Any]) -> int:
            d = 0
            cur = node
            visited: set[int] = set()
            while cur.get("parentId") is not None:
                pid = cur["parentId"]
                if pid in visited or pid not in by_id:
                    break
                visited.add(pid)
                cur = by_id[pid]
                d += 1
                if d > 20:
                    break
            return d

        # 存量同步节点索引（source_node_id → PlantNode）
        result = await db.execute(select(PlantNode).where(PlantNode.source_node_id.isnot(None)))
        existing_by_source = {n.source_node_id: n for n in result.scalars().all()}

        # 按 depth 升序处理（父先子后）
        ordered = sorted(
            (n for n in aas_nodes if n.get("id") is not None),
            key=lambda n: (depth_of(n), n["id"]),
        )

        # source → 本地 id 映射（本次已建 + 存量）
        source_to_local: dict[int, str] = {src: node.id for src, node in existing_by_source.items()}

        for node in ordered:
            aas_id = int(node["id"])
            display_name = (node.get("displayName") or "").strip()
            sn = (node.get("sn") or "").strip()
            name = sn or display_name or f"AAS-{aas_id}"
            depth = depth_of(node)
            node_type = "FACTORY" if depth == 0 else ("AREA" if depth == 1 else "UNIT")

            # 父节点映射：AAS parentId → 本地节点；无父/映射缺失挂根
            parent_local_id: str | None = None
            aas_parent = node.get("parentId")
            if aas_parent is not None and int(aas_parent) in source_to_local:
                parent_local_id = source_to_local[int(aas_parent)]

            existing = existing_by_source.get(aas_id)
            if existing is not None:
                # 更新（主数据语义：名称/类型/父级以 AAS 为准）
                existing.name = name[:100]
                existing.type = node_type
                existing.parent_id = parent_local_id
                existing.updated_at = _now_naive()
                updated += 1
                source_to_local[aas_id] = existing.id
            else:
                new_node = PlantNode(
                    id=str(uuid4()),
                    name=name[:100],
                    type=node_type,
                    parent_id=parent_local_id,
                    is_kpi_enabled=False,
                    source_node_id=aas_id,
                )
                db.add(new_node)
                created += 1
                source_to_local[aas_id] = new_node.id

        # 审计
        db.add(
            SysAuditLog(
                id=str(uuid4()),
                operator=operator,
                operation_type="FACTORY_MODEL_SYNC",
                target_type="plant_node",
                target_id="sync",
                before_value=None,
                after_value=json.dumps(
                    {"nodesTotal": nodes_total, "created": created, "updated": updated},
                    ensure_ascii=False,
                ),
                operated_at=_now_naive(),
            )
        )
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error_message = str(exc)
        logger.warning("工厂模型同步失败: %s", exc, exc_info=True)

    duration_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)

    # 回写最近同步状态 + 日志
    try:
        raw = await _get_config(db, _KEY_SETTING)
        if raw:
            data = json.loads(raw)
            data["lastSyncAt"] = _now_iso()
            data["lastSyncStatus"] = status
            data["lastSyncSummary"] = (
                f"共 {nodes_total} 节点：新增 {created}，更新 {updated}"
                if status == "success"
                else (error_message or "同步失败")
            )
            await _set_config(
                db,
                _KEY_SETTING,
                json.dumps(data, ensure_ascii=False),
                "工厂模型 AAS 同步配置（JSON，密码 Base64）",
                operator,
            )
        await _append_sync_log(
            db,
            status=status,
            nodes_total=nodes_total,
            created=created,
            updated=updated,
            duration_ms=duration_ms,
            operator=operator,
            error_message=error_message,
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
        logger.exception("工厂模型同步日志写入失败")

    message = (
        f"同步完成：共 {nodes_total} 节点，新增 {created}，更新 {updated}"
        if status == "success"
        else f"同步失败：{error_message}"
    )
    return {
        "status": status,
        "nodesTotal": nodes_total,
        "created": created,
        "updated": updated,
        "durationMs": duration_ms,
        "message": message,
    }
