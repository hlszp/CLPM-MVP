"""
本地历史布局配置（2026-09-25 宽表退役后收敛为单态 point）.

演进说明：
- 原为 legacy / shadow / point 三态，写入侧由 sys_config 键
  history.storage_mode 控制（legacy 只写宽表 / shadow 双写 / point 只写点表）；
- 宽表超级表（st_loop 前缀）于 2026-09-09 起停止接收历史导入，2026-09-25 起实时写入
  路径也已删除，宽表彻底退役：写入与读取的唯一形态都是测点点表
  st_point_data_v1（算法组装见 logical_wide_builder）；
- sys_config 键保留但仅作展示/审计（历史值不改变任何行为）；
- 读取路由 manifest（history_layout_manifest）机制保留，恒按 point 解析，
  为将来"按窗口切源"保留入口。
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
    """
    历史布局值 → (写宽表, 写点表)。

    宽表退役后只有 point 生效；legacy / shadow 的映射保留仅用于解释历史数据
    口径（例如"这段窗口当时写在哪张表"），不再驱动任何写入分支。
    """
    if mode == "legacy":
        return True, False
    if mode == "shadow":
        return True, True
    if mode == "point":
        return False, True
    raise ValueError(f"非法布局: {mode!r}")


# ---------------------------------------------------------------------------
# 落库形态自检（2026-09-25；宽表退役后收敛为单态）
# ---------------------------------------------------------------------------
# 历史背景：写入侧（sys_config: history.storage_mode）与读取路由
# （history_layout_manifest）曾是两套独立开关，二者不一致时表现为"实时数据在
# 采、库里却没有 / 趋势图全空"且日志无一行提示。宽表退役后写入与读取的唯一
# 形态都是测点点表 st_point_data_v1，本自检收敛为单态核对：回显 sys_config
# 历史值与当前 manifest 段，供 /datasource/health 与运维核对。
SELFCHECK_OK = "ok"
SELFCHECK_WARNING = "warning"
SELFCHECK_ERROR = "error"

#: 宽表退役后唯一写入/读取布局
POINT_LAYOUT = "point"


async def get_layout_selfcheck(db: Any) -> dict[str, Any]:
    """落库形态自检（只读，无副作用）。

    返回字段：
    - writeMode：sys_config 中的历史值（保留供展示/审计，不再影响行为）
    - readLayout：恒 point（读取路由已收敛为测点点表）
    - writesPointTable：恒 True（宽表退役后唯一落库形态）
    - manifest：当前生效的最新 manifest 段（供运维核对）
    - consistent / severity / diagnosis：形态结论（恒 ok；历史值仅在文案中提示）
    """
    raw_mode = await get_storage_mode(db)

    manifest: dict[str, Any] | None = None
    try:
        from app.models.point_history import HistoryLayoutManifest

        row = (
            (
                await db.execute(
                    select(HistoryLayoutManifest)
                    .where(HistoryLayoutManifest.is_active.is_(True))
                    .order_by(HistoryLayoutManifest.valid_from.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row is not None:
            manifest = {
                "scopeType": row.scope_type,
                "scopeId": row.scope_id,
                "layout": row.layout,
                "validFrom": row.valid_from.isoformat() if row.valid_from else None,
            }
    except Exception as exc:  # noqa: BLE001 - 清单读取失败不影响形态判定
        logger.warning("布局清单读取失败（不影响落库形态判定）: %s", exc)

    residual = (
        f"（sys_config 历史值 {raw_mode} 已不作数，可清理）" if raw_mode != POINT_LAYOUT else ""
    )
    return {
        "writeMode": raw_mode,
        "readLayout": POINT_LAYOUT,
        "writesPointTable": True,
        "manifest": manifest,
        "consistent": True,
        "severity": SELFCHECK_OK,
        "diagnosis": (
            "写入与读取均为测点点表 st_point_data_v1（宽表已退役），形态一致。" + residual
        ),
    }
