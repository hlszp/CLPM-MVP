"""Celery Beat 条件注册 — 模块热插拔 P1。

在 ``@beat_init.connect`` 信号中从 DB 读取 ``enabled_modules``，按模块启用状态
移除已禁用模块的 beat 条目。基础任务（kpi-calc、data-link-check、alert-patrol 等）
不受影响。

**为什么用 beat_init 信号**：各任务模块在 import 期注册 beat_schedule，此时 DB
不可用（无法读 sys_config）。beat_init 在 Beat 启动完成、所有调度条目加载完毕后
触发，是条件化的唯一正确时机。

条件化条目：
- ``diagnosis-scheduled-daily``（diagnosis_schedule.py，每日 01:10）
- ``diagnosis-scheduled-weekly``（diagnosis_schedule.py，每周日 02:10）
- ``diagnosis-evidence-cleanup``（diagnosis_maintenance.py，每日 03:40）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery.signals import beat_init

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

#: 模块 key → 该模块注册的 beat 条目名称列表
_MODULE_BEAT_ENTRIES: dict[str, list[str]] = {
    "diagnosis": [
        "diagnosis-scheduled-daily",
        "diagnosis-scheduled-weekly",
        "diagnosis-evidence-cleanup",
    ],
}


async def _load_enabled_modules() -> set[str]:
    """从 sys_config 读取已启用模块集合（复用 core.modules 的规范化逻辑）。"""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.core.modules import _normalize
    from app.models.sys_config import SysConfig

    async with AsyncSessionLocal() as db:
        row = await db.execute(select(SysConfig.value).where(SysConfig.key == "enabled_modules"))
        raw = row.scalar_one_or_none()
    return _normalize(raw)


def _apply_module_conditions(enabled: set[str]) -> None:
    """根据启用模块集合，从 beat_schedule 中移除禁用模块的条目。"""
    schedule: dict[str, Any] = dict(celery_app.conf.beat_schedule or {})
    changed = False
    for module_key, entry_names in _MODULE_BEAT_ENTRIES.items():
        if module_key in enabled:
            continue
        for name in entry_names:
            if name in schedule:
                schedule.pop(name, None)
                logger.info("beat_schedule: 模块 %s 已禁用，移除调度条目 %s", module_key, name)
                changed = True
    if changed:
        celery_app.conf.beat_schedule = schedule


def _reload_module_conditions() -> None:
    """从 DB 加载模块状态并应用到 beat_schedule（同步包装）。"""
    try:
        enabled = asyncio.run(_load_enabled_modules())
    except Exception as exc:  # noqa: BLE001
        logger.warning("beat_registry: 从 DB 读取 enabled_modules 失败，保留全部调度: %s", exc)
        return
    _apply_module_conditions(enabled)
    logger.info("beat_registry: 模块条件化完成，已启用模块=%s", ", ".join(sorted(enabled)))


@beat_init.connect
def _on_beat_init_apply_modules(sender=None, **kwargs: object) -> None:
    """Beat 启动时根据模块启用状态条件移除调度条目。"""
    _reload_module_conditions()
