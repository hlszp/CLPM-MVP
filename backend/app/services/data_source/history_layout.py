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


# ---------------------------------------------------------------------------
# 写入布局 与 读取路由 一致性自检（2026-09-25）
# ---------------------------------------------------------------------------
# 生产排查暴露的问题：写入侧（sys_config: history.storage_mode）与读取路由
# （history_layout_manifest）是两套独立开关，且此前没有任何 UI/API/脚本能
# 修改它们（set_storage_mode / set_layout 都是无调用方的死代码）。二者一旦
# 不一致，表现就是"实时数据在采、库里却没有 / 趋势图全空"，而日志里一句
# 提示都没有。本自检把结论显性化，供启动自检、/datasource/health 与运维使用。
SELFCHECK_OK = "ok"
SELFCHECK_WARNING = "warning"
SELFCHECK_ERROR = "error"


async def get_layout_selfcheck(db: Any) -> dict[str, Any]:
    """自检写入侧布局与读取路由是否一致（只读，无副作用）。

    返回字段：
    - writeMode：写入侧布局（sys_config 真相源）
    - readLayout：读取路由（manifest 解析；无段恒 legacy）
    - writesWideTable / writesPointTable：该写入侧实际写哪张表
    - manifest：当前生效的最新 manifest 段（便于运维核对）
    - consistent：读写是否自洽；severity：ok / warning / error
    - diagnosis：一句话结论 + 修复方向（可直接打日志或上屏）
    """
    write_mode = await get_storage_mode(db)
    writes_wide, writes_point = writeback_enabled_for(write_mode)

    read_layout = "legacy"
    manifest: dict[str, Any] | None = None
    try:
        from datetime import UTC, datetime

        from app.models.point_history import HistoryLayoutManifest
        from app.services.data_source.point_history_metadata import resolve_layout

        # 传一个不存在的 loop_id：loop 精确段必然落空，解析结果即 global 兜底段，
        # 也就是最坏情况下前端趋势会走的那条读路径。
        read_layout = await resolve_layout(db, loop_id="__layout_selfcheck__", at=datetime.now(UTC))
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
    except Exception as exc:  # noqa: BLE001 - 自检本身失败也必须显性告警
        logger.warning("布局自检失败（无法判定读写是否一致）: %s", exc)
        return {
            "writeMode": write_mode,
            "readLayout": None,
            "writesWideTable": writes_wide,
            "writesPointTable": writes_point,
            "manifest": None,
            "consistent": None,
            "severity": SELFCHECK_WARNING,
            "diagnosis": f"读取侧布局解析失败，无法判定读写是否一致：{exc}",
        }

    reads_point = read_layout == "point"
    reads_wide = not reads_point
    if write_mode == "shadow":
        # 双写：点表与宽表同时有数据，读哪张都对
        consistent = True
    else:
        consistent = (writes_wide and reads_wide) or (writes_point and reads_point)

    if consistent:
        severity = SELFCHECK_OK
        diagnosis = f"写入侧 {write_mode}、读取侧 {read_layout}，读写口径一致。"
    else:
        severity = SELFCHECK_ERROR
        if writes_point and not writes_wide and reads_wide:
            diagnosis = (
                f"写入侧已切点表（storage_mode={write_mode}，宽表停写），但读取路由仍为 "
                f"{read_layout}（history_layout_manifest 无生效段）→ 趋势图与评估会读到空的宽表。"
                "修复二选一：① 登记 global 布局段 layout=point 完成切读；"
                "② storage_mode 退回 shadow 恢复双写。"
            )
        else:
            diagnosis = (
                f"读取路由为 {read_layout}（按点表读），但写入侧 {write_mode} 只写宽表 → "
                "点表无数据，趋势图会全空。修复：把 storage_mode 设为 point 或 shadow。"
            )

    return {
        "writeMode": write_mode,
        "readLayout": read_layout,
        "writesWideTable": writes_wide,
        "writesPointTable": writes_point,
        "manifest": manifest,
        "consistent": consistent,
        "severity": severity,
        "diagnosis": diagnosis,
    }


def writeback_trap_hint(mode: str, realtime_writeback_enabled: bool) -> str | None:
    """识别「回写开关开着、写入器却没启动」的语义陷阱（返回提示语或 None）。

    背景：datasource.realtime_writeback_enabled 的 UI 文案是"实时数据写回本地
    TDengine 宽表"，但它不控制测点子表写入；真正决定 PointHistoryWriter 是否
    启动的是 history.storage_mode。生产上因此出现"明明配了实时回写，为什么
    没有落库"。命中该组合时返回可直读的提示，由调用方打 WARNING。
    """
    if realtime_writeback_enabled and mode == "legacy":
        return (
            "实时回写开关为开，但 history.storage_mode=legacy → PointHistoryWriter 不会启动，"
            "实时数据仅进 Redis 与宽表，不会写入点表 st_point_data_v1。"
            "如需点表落库，请把 storage_mode 设为 shadow（双写）或 point（只写点表）。"
        )
    return None
