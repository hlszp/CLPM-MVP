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


#: Beat 运行中的 Scheduler 引用（beat_init 时绑定，供 pub/sub 监听线程复用）。
#: 普通属性赋值，非 asyncio 原语——不违反"禁止模块级 asyncio.Lock/Semaphore/Event"。
_live_scheduler: Any = None


def bind_live_scheduler(sender: Any = None) -> Any:
    """记录运行中的 Beat Scheduler 引用（beat_init 时调用）。"""
    global _live_scheduler
    scheduler = getattr(sender, "scheduler", None)
    if scheduler is not None:
        _live_scheduler = scheduler
    return _live_scheduler


def live_beat_schedule(sender: Any = None) -> tuple[dict[str, Any], Any]:
    """返回**真正生效**的调度表及其所属 Scheduler（整改 G28）。

    关键事实：Celery 的 Service.start() 先构造 Scheduler（setup_schedule 把
    app.conf.beat_schedule 合并进 self.schedule），**之后**才发送 beat_init 信号；
    tick() 只读 self.schedule。因此只在 beat_init 里改
    celery_app.conf.beat_schedule 对运行中的 Beat 完全无效——模块热插拔、
    周期覆盖、pub/sub 热重载三处同时静默失效，而既有测试恰好只断言 conf
    字典，所以永远绿灯、运维却以为配置已生效。

    Returns:
        (schedule, scheduler)：有运行中调度器时就地修改其 schedule 即生效
        （改完调用 sync_live_schedule 落盘）；无调度器（导入期/单测）时退回
        conf.beat_schedule，保持既有语义。
    """
    scheduler = getattr(sender, "scheduler", None) or _live_scheduler
    sched = getattr(scheduler, "schedule", None)
    if isinstance(sched, dict):
        return sched, scheduler
    return celery_app.conf.beat_schedule, None


def sync_live_schedule(scheduler: Any) -> None:
    """就地修改 live schedule 后落盘（PersistentScheduler 需要）。"""
    if scheduler is None:
        return
    try:
        scheduler.sync()
    except Exception:  # noqa: BLE001
        logger.warning(
            "beat_registry: scheduler.sync() 失败，本次变更将在下个周期落盘",
            exc_info=True,
        )


def _apply_module_conditions(enabled: set[str], sender: Any = None) -> None:
    """根据启用模块集合，从 beat_schedule 中移除禁用模块的条目。"""
    # 整改 G28：改**运行中的** scheduler.schedule（就地），而不是 conf 的副本
    schedule, scheduler = live_beat_schedule(sender)
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
        if scheduler is None:
            celery_app.conf.beat_schedule = schedule
        else:
            sync_live_schedule(scheduler)


def _reload_module_conditions(sender: Any = None) -> None:
    """从 DB 加载模块状态并应用到 beat_schedule（同步包装）。"""
    try:
        enabled = asyncio.run(_load_enabled_modules())
    except Exception as exc:  # noqa: BLE001
        logger.warning("beat_registry: 从 DB 读取 enabled_modules 失败，保留全部调度: %s", exc)
        return
    _apply_module_conditions(enabled, sender=sender)
    logger.info("beat_registry: 模块条件化完成，已启用模块=%s", ", ".join(sorted(enabled)))


@beat_init.connect
def _on_beat_init_apply_modules(sender=None, **kwargs: object) -> None:
    """Beat 启动时根据模块启用状态条件移除调度条目。

    整改 G28：必须先绑定运行中的 Scheduler，再条件化——否则改的是 conf 副本，
    运行中的 Beat 完全感知不到。
    """
    bind_live_scheduler(sender)
    _reload_module_conditions(sender=sender)
