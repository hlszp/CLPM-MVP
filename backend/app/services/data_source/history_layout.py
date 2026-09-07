"""本地历史布局配置（P1-5）：legacy / shadow / point 三态，默认 legacy.

设计依据：设计文档 §7。
- **配置真相源是 sys_config**（键 ``history.storage_mode``），启动设置
  （``settings.HISTORY_STORAGE_MODE``）仅兜底；未配置时恒 legacy——
  建表不等于切读；
- **写入开关与读取路由独立**：``storage_mode`` 控制**写入侧**（legacy 只写
  宽表 / shadow 双写 / point 只写点表）；**读取路由只认 manifest**
  （``history_layout_manifest``，由 point_history_metadata.resolve_layout
  解析，无 manifest 恒 legacy）。二者解耦避免"切写即空库被读"；
- 读侧布局切换只能通过显式登记 manifest 段完成（P4 迁移演练操作项）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.models.sys_config import SysConfig

logger = logging.getLogger(__name__)

#: sys_config 键（写入侧布局三态）
SYS_KEY_STORAGE_MODE = "history.storage_mode"

VALID_MODES = frozenset({"legacy", "shadow", "point"})


def get_storage_mode_from_rows(rows: dict[str, SysConfig]) -> str:
    """从已取的 sys_config 行解析写入侧布局（缺省/脏值回退 settings→legacy）."""
    row = rows.get(SYS_KEY_STORAGE_MODE)
    mode = getattr(row, "value", None) if row is not None else None
    if mode in VALID_MODES:
        return str(mode)
    fallback = getattr(settings, "HISTORY_STORAGE_MODE", "legacy")
    return fallback if fallback in VALID_MODES else "legacy"


def _settings_fallback() -> str:
    fallback = getattr(settings, "HISTORY_STORAGE_MODE", "legacy")
    return fallback if fallback in VALID_MODES else "legacy"


async def get_storage_mode(db: Any) -> str:
    """读 sys_config 的写入侧布局（一次主键查询；不命中走兜底链）."""
    row = (
        (await db.execute(select(SysConfig).where(SysConfig.key == SYS_KEY_STORAGE_MODE)))
        .scalars()
        .first()
    )
    if row is None:
        return _settings_fallback()
    return get_storage_mode_from_rows({SYS_KEY_STORAGE_MODE: row})


async def set_storage_mode(db: Any, mode: str) -> None:
    """写入布局配置（sys_config 真相源；非法值拒绝）."""
    if mode not in VALID_MODES:
        raise ValueError(f"非法 history.storage_mode: {mode!r}（合法: {sorted(VALID_MODES)}）")
    existing = (
        (await db.execute(select(SysConfig).where(SysConfig.key == SYS_KEY_STORAGE_MODE)))
        .scalars()
        .first()
    )
    if existing is not None:
        existing.value = mode
    else:
        db.add(
            SysConfig(
                key=SYS_KEY_STORAGE_MODE,
                value=mode,
                description="本地历史写入布局 legacy/shadow/point（读取路由由 manifest 决定）",
            )
        )
    await db.flush()
    logger.info("历史写入布局已设置: %s", mode)


def writeback_enabled_for(mode: str) -> tuple[bool, bool]:
    """布局 → (写宽表, 写点表)。

    - legacy：只写宽表（现状不动）；
    - shadow：双写（同一事件流，两路写入互不掩盖失败）；
    - point：只写点表（停止旧写须先确认回退覆盖，见设计 §7-5）。
    """
    if mode == "legacy":
        return True, False
    if mode == "shadow":
        return True, True
    if mode == "point":
        return False, True
    raise ValueError(f"非法布局: {mode!r}")
