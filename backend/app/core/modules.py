"""模块注册中心 — 热插拔启用/禁用（IA 优化 P1）。

模块启用状态持久化在 ``sys_config``（key=``enabled_modules``，JSON 数组）。
Workbench v2.0 新增 ``module_plugin`` 表承载 4 态状态机
（CORE/ENABLED/MAINTENANCE/UNINSTALLED），优先级高于 sys_config。

- 基础模块（``base=True``）不可禁用
- ``is_module_enabled()`` 供路由守卫、跨模块守卫调用
- ``get_module_status()`` 返回 4 态字面量（供工作台 BFF Tab 三色 dot）
- 状态在进程内缓存；``create_app()`` 路由注册时若缓存未初始化则同步从 DB 加载
  （失败回退默认全开，兼容已有部署）
- ``lifespan`` 启动时异步加载刷新缓存
- 启用/禁用需重启后端生效（Celery beat 调度变更需要重启）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: 8 个一级模块注册表（顺序与左侧导航一致）
MODULES: dict[str, dict[str, Any]] = {
    "monitor": {"name": "监控", "order": 1, "base": True, "deps": []},
    "assess": {"name": "评估", "order": 2, "base": True, "deps": []},
    "diagnosis": {"name": "诊断", "order": 3, "base": False, "deps": []},
    "tuning": {"name": "整定", "order": 4, "base": False, "deps": []},
    "handling": {"name": "处置", "order": 5, "base": False, "deps": ["diagnosis"]},
    "reports": {"name": "报告", "order": 6, "base": True, "deps": []},
    "config": {"name": "配置", "order": 7, "base": True, "deps": []},
    "system": {"name": "系统", "order": 8, "base": True, "deps": []},
}

_CONFIG_KEY = "enabled_modules"

#: 模块 4 态（Workbench v2.0，与 module_plugin.status CK 对齐）
MODULE_STATUSES = ("CORE", "ENABLED", "MAINTENANCE", "UNINSTALLED")
#: 视为"已启用"的状态（is_module_enabled 返回 True）
_ENABLED_STATUSES = frozenset({"CORE", "ENABLED"})

#: 进程内缓存：None=未加载，set=已启用模块 key 集合
_cache: set[str] | None = None
#: 4 态状态缓存：None=未加载，dict[str,str]=key→status
_status_cache: dict[str, str] | None = None


def _default_enabled() -> set[str]:
    """未配置时的默认值：所有模块启用（兼容已有部署，不破坏现有功能）。"""
    return set(MODULES.keys())


def _normalize(raw: str | None) -> set[str]:
    """将 sys_config 中的 JSON 数组字符串规范化为合法模块 key 集合。

    - 基础模块强制启用
    - 非法/未知 key 丢弃
    - 解析失败回退默认全开
    """
    if not raw:
        return _default_enabled()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("enabled_modules 配置解析失败，回退默认全开")
        return _default_enabled()
    if not isinstance(data, list):
        return _default_enabled()
    enabled = {k for k in data if isinstance(k, str) and k in MODULES}
    for key, meta in MODULES.items():
        if meta.get("base"):
            enabled.add(key)
    return enabled


def _load_from_module_plugin_sync() -> dict[str, str] | None:
    """同步从 module_plugin 表加载 4 态状态。

    返回 None 表示表不存在或查询失败（应回退 sys_config）。
    在临时事件循环中执行异步查询。
    """
    try:
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.module_plugin import ModulePlugin

        async def _read() -> dict[str, str]:
            async with AsyncSessionLocal() as db:
                rows = await db.execute(select(ModulePlugin.module_key, ModulePlugin.status))
                return dict(rows.all())

        return asyncio.run(_read())
    except RuntimeError:
        logger.debug("同步加载 module_plugin 时检测到运行中事件循环，回退 sys_config")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("module_plugin 表读取失败，回退 sys_config: %s", exc)
        return None


def _load_sync() -> set[str]:
    """同步从 DB 加载启用状态（仅供 create_app 路由注册期使用）。

    v2.0 优先查 module_plugin 表（4 态），失败/表空则回退 sys_config。
    在临时事件循环中执行异步查询；任何异常（DB 不可用/已有运行循环等）
    均回退默认全开，不阻塞应用启动。
    """
    global _status_cache
    # 优先尝试 module_plugin 表
    status_map = _load_from_module_plugin_sync()
    if status_map is not None and status_map:
        _status_cache = dict(status_map)
        enabled = {key for key, status in status_map.items() if status in _ENABLED_STATUSES}
        # base 模块强制启用
        for key, meta in MODULES.items():
            if meta.get("base"):
                enabled.add(key)
        return enabled
    # 回退 sys_config
    try:
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.sys_config import SysConfig

        async def _read() -> str | None:
            async with AsyncSessionLocal() as db:
                row = await db.execute(select(SysConfig.value).where(SysConfig.key == _CONFIG_KEY))
                return row.scalar_one_or_none()

        raw = asyncio.run(_read())
        return _normalize(raw)
    except RuntimeError:
        # 已有运行中的事件循环（如测试环境），无法 asyncio.run
        logger.debug("同步加载 enabled_modules 时检测到运行中事件循环，回退默认")
        return _default_enabled()
    except Exception as exc:  # noqa: BLE001
        logger.warning("同步加载 enabled_modules 失败，回退默认全开: %s", exc)
        return _default_enabled()


def is_module_enabled(key: str) -> bool:
    """判断模块是否启用（进程内缓存，未加载时同步从 DB 读取）。

    v2.0：若 _status_cache 已从 module_plugin 表加载，则按 4 态判断
    (CORE/ENABLED → True，其他 → False)；否则回退旧 _cache 集合判断。
    """
    global _cache
    if key not in MODULES:
        return False
    # 优先用 module_plugin 4 态缓存
    if _status_cache is not None:
        status = _status_cache.get(key)
        if status is None:
            # module_plugin 表无此 key，回退 base 模块默认启用
            return MODULES[key].get("base", False)
        return status in _ENABLED_STATUSES
    # 回退旧 sys_config 集合缓存
    if _cache is None:
        _cache = _load_sync()
    return key in _cache


def get_module_status(key: str) -> str:
    """返回模块 4 态字面量（CORE/ENABLED/MAINTENANCE/UNINSTALLED）。

    v2.0 新增，供工作台 BFF Tab 三色 dot / veil / 维护横幅。
    未加载时同步从 DB 读取；未知 key 返回 UNINSTALLED。
    """
    if key not in MODULES:
        return "UNINSTALLED"
    if _status_cache is None:
        _load_sync()
    if _status_cache is not None:
        return _status_cache.get(key, "CORE" if MODULES[key].get("base") else "ENABLED")
    # 回退：sys_config 判断
    return "ENABLED" if is_module_enabled(key) else "UNINSTALLED"


def get_enabled_modules() -> set[str]:
    """返回当前已启用模块 key 集合（副本）。"""
    global _cache
    if _cache is None:
        _cache = _load_sync()
    return set(_cache)


def list_modules() -> list[dict[str, Any]]:
    """列出所有模块及其状态（供管理 API/前端使用）。"""
    enabled = get_enabled_modules()
    result: list[dict[str, Any]] = []
    for key, meta in sorted(MODULES.items(), key=lambda kv: kv[1]["order"]):
        result.append(
            {
                "key": key,
                "name": meta["name"],
                "order": meta["order"],
                "base": meta["base"],
                "deps": list(meta.get("deps", [])),
                "enabled": key in enabled,
            }
        )
    return result


def set_cache(enabled: set[str]) -> None:
    """直接设置进程内缓存（供 lifespan 预载/测试/API 写入后刷新使用）。"""
    global _cache, _status_cache
    _cache = set(enabled)
    for key, meta in MODULES.items():
        if meta.get("base"):
            _cache.add(key)
    # 同步重建 _status_cache：enabled → ENABLED，未 enabled → UNINSTALLED
    # （仅用于兼容旧 set_cache 路径；真正 4 态由 module_plugin 表驱动）
    _status_cache = {key: ("ENABLED" if key in _cache else "UNINSTALLED") for key in MODULES}
    for key, meta in MODULES.items():
        if meta.get("base"):
            _status_cache[key] = "CORE"


def reset_cache() -> None:
    """重置缓存（仅供测试）。"""
    global _cache, _status_cache
    _cache = None
    _status_cache = None


def require_module(key: str):
    """FastAPI 依赖工厂：端点要求指定模块已启用，否则返回 404。

    用于基础模块路由（如 configs）中可选模块专属端点的守卫。
    未启用模块返回 404（而非 403），与前端路由过滤口径一致。
    """
    from fastapi import HTTPException

    def _dep() -> None:
        if not is_module_enabled(key):
            raise HTTPException(status_code=404, detail=f"模块「{MODULES[key]['name']}」未启用")

    return _dep


def validate_dependencies(
    enabled: set[str],
    *,
    stopping_on: str | None = None,
) -> list[str]:
    """校验依赖关系，返回冲突描述列表（空列表表示通过）。

    - 禁用 ``stopping_on`` 时：若有其他已启用模块依赖它则拒绝
    - 启用集合中：若某模块的依赖不在集合中则自动补全（调用方处理联动）
    """
    conflicts: list[str] = []
    if stopping_on is not None:
        for key, meta in MODULES.items():
            if stopping_on in meta.get("deps", []) and key in enabled:
                conflicts.append(
                    f"无法禁用「{MODULES[stopping_on]['name']}」："
                    f"「{meta['name']}」模块依赖它，请先禁用依赖模块"
                )
    return conflicts


async def load_enabled_modules(db) -> set[str]:
    """异步从 DB 加载并刷新缓存（lifespan 启动时调用）。

    v2.0 优先查 module_plugin 表（4 态），表空则回退 sys_config。
    """
    global _cache, _status_cache
    from sqlalchemy import select

    from app.models.module_plugin import ModulePlugin

    # 优先尝试 module_plugin 表
    try:
        rows = await db.execute(select(ModulePlugin.module_key, ModulePlugin.status))
        status_map = dict(rows.all())
        if status_map:
            _status_cache = dict(status_map)
            enabled = {key for key, status in status_map.items() if status in _ENABLED_STATUSES}
            for key, meta in MODULES.items():
                if meta.get("base"):
                    enabled.add(key)
            _cache = set(enabled)
            return enabled
    except Exception as exc:  # noqa: BLE001
        logger.debug("module_plugin 表读取失败，回退 sys_config: %s", exc)

    # 回退 sys_config
    from app.models.sys_config import SysConfig

    row = await db.execute(select(SysConfig).where(SysConfig.key == _CONFIG_KEY))
    cfg = row.scalar_one_or_none()
    enabled = _normalize(cfg.value if cfg else None)
    set_cache(enabled)
    return enabled


async def save_enabled_modules(db, enabled: set[str], operator: str | None = None) -> set[str]:
    """异步保存启用状态到 sys_config 并刷新缓存。返回规范化后的集合。

    自动补全依赖（启用 handling 时自动启用 diagnosis）；
    基础模块强制启用。
    """
    from sqlalchemy import select

    from app.models.sys_config import SysConfig

    normalized = set(enabled)
    for key, meta in MODULES.items():
        if meta.get("base"):
            normalized.add(key)
    # 自动补全依赖
    for key in list(normalized):
        for dep in MODULES[key].get("deps", []):
            normalized.add(dep)
    # 校验禁用冲突
    current = get_enabled_modules()
    for key in list(current - normalized):
        conflicts = validate_dependencies(normalized, stopping_on=key)
        if conflicts:
            raise ValueError("; ".join(conflicts))

    value = json.dumps(sorted(normalized), ensure_ascii=False)
    row = await db.execute(select(SysConfig).where(SysConfig.key == _CONFIG_KEY))
    cfg = row.scalar_one_or_none()
    if cfg:
        cfg.value = value
        cfg.updated_by = operator
    else:
        db.add(
            SysConfig(
                key=_CONFIG_KEY,
                value=value,
                updated_by=operator,
                description="已启用模块 key 列表（JSON 数组）",
            )
        )
    await db.commit()
    set_cache(normalized)
    return normalized
