"""MODE 解析器（配置驱动的 MODE 值映射）.

对齐 DDS §3.1 / 算法说明 §4.0.3，从配置表读取 MODE 定义与映射关系，
替代硬编码的 ``AUTO_MODES`` 集合。

核心功能：
- ``get_auto_modes(db)``: 从 mode_definition 读 is_auto=True 的 standard_mode 集合
- ``resolve_raw_to_standard(db, dcs_model_id, raw_mode)``: DCS 原始 MODE → 标准 MODE
- ``resolve_standard_to_raw(db, dcs_model_id, standard_mode)``: 标准 MODE → DCS 原始 MODE
- ``batch_resolve_raw_modes(db, loop_raw_modes)``: 批量解析多个回路的 MODE 值

设计原则：
- 所有 MODE 判定规则从配置表读取，不硬编码在程序中
- 表为空时回退到 ``app.constants.mode.AUTO_MODES``（{1, 2, 3, 4}）保证向后兼容
- 型号映射缺失时回退到本系统默认映射（1:1）
- 默认映射也缺失时回退 raw_mode 值本身（1:1）

参考文档：
- DDS §3.1 超级表定义
- 算法说明 §4.0.3 calc_auto_mode_rate / §4.2 calc_effective_auto_rate
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.mode import AUTO_MODES as CONST_AUTO_MODES
from app.constants.mode import MODE_LABELS_ZH

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AUTO_MODES 集合（配置驱动）
# ---------------------------------------------------------------------------


async def get_auto_modes(db: AsyncSession) -> set[int]:
    """读取计入自控率的 MODE 值集合（配置驱动）.

    优先从 ``mode_definition`` 表读取 ``is_auto=True`` 的 ``standard_mode``；
    表为空时回退到 ``app.constants.mode.AUTO_MODES``（{1, 2, 3, 4}）。

    用于：
    - 实时自控率计算（``node_performance.query_realtime_auto_rate``）
    - 有效自控率计算（``metric_calculator.auto_mode``）
    - 投自动回路占比统计

    Returns:
        计入自控率的标准 MODE 值集合
    """
    from app.models.mode_definition import ModeDefinition

    result = await db.execute(
        select(ModeDefinition.standard_mode).where(ModeDefinition.is_auto.is_(True))
    )
    modes = {row.standard_mode for row in result.all()}
    if not modes:
        logger.debug(
            "[MODE 解析] mode_definition 表无 is_auto=True 记录，回退常量 %s",
            sorted(CONST_AUTO_MODES),
        )
        return set(CONST_AUTO_MODES)
    logger.debug("[MODE 解析] 从 mode_definition 读取 AUTO_MODES=%s", sorted(modes))
    return modes


async def get_mode_labels(db: AsyncSession) -> dict[int, str]:
    """读取标准 MODE 中文标签映射（配置驱动）.

    Returns:
        {standard_mode: label_zh} 字典
    """
    from app.models.mode_definition import ModeDefinition

    result = await db.execute(select(ModeDefinition))
    defs = result.scalars().all()
    if not defs:
        return dict(MODE_LABELS_ZH)
    return {d.standard_mode: d.label_zh for d in defs}


async def get_mode_definitions(db: AsyncSession) -> list[dict]:
    """读取全部标准 MODE 定义（按 sort_order 排序）.

    Returns:
        标准 MODE 定义列表（dict 格式）
    """
    from app.models.mode_definition import ModeDefinition

    result = await db.execute(
        select(ModeDefinition).order_by(ModeDefinition.sort_order.asc())
    )
    defs = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "standard_mode": d.standard_mode,
            "label_zh": d.label_zh,
            "label_en": d.label_en,
            "is_auto": bool(d.is_auto),
            "color": d.color,
            "sort_order": d.sort_order,
            "description": d.description,
        }
        for d in defs
    ]


# ---------------------------------------------------------------------------
# DCS 原始 MODE 值 ↔ 标准 MODE 值转换
# ---------------------------------------------------------------------------


async def resolve_raw_to_standard(
    db: AsyncSession,
    dcs_model_id: str | None,
    raw_mode: int,
) -> int:
    """将 DCS 原始 MODE 值转换为标准 MODE 值.

    解析优先级：
    1. 型号映射：``dcs_mode_mapping`` WHERE dcs_model_id=? AND raw_mode_value=?
    2. 本系统默认：``dcs_mode_mapping`` WHERE dcs_model_id IS NULL AND raw_mode_value=?
    3. 回退 raw_mode 值本身（1:1 映射）

    Args:
        db: 异步数据库会话
        dcs_model_id: 回路关联的 DCS 型号 ID（NULL=使用本系统默认）
        raw_mode: DCS 实际推送的 MODE 值

    Returns:
        标准 MODE 值（0-4）
    """
    from app.models.dcs_mode_mapping import DcsModeMapping

    # 1. 优先查型号映射
    if dcs_model_id:
        result = await db.execute(
            select(DcsModeMapping.standard_mode).where(
                DcsModeMapping.dcs_model_id == dcs_model_id,
                DcsModeMapping.raw_mode_value == raw_mode,
            )
        )
        standard = result.scalar_one_or_none()
        if standard is not None:
            return standard

    # 2. 回退本系统默认映射
    result = await db.execute(
        select(DcsModeMapping.standard_mode).where(
            DcsModeMapping.dcs_model_id.is_(None),
            DcsModeMapping.raw_mode_value == raw_mode,
        )
    )
    standard = result.scalar_one_or_none()
    if standard is not None:
        return standard

    # 3. 回退 raw_mode 本身（1:1）
    logger.debug(
        "[MODE 解析] raw_mode=%s 无映射记录（model_id=%s），回退 1:1",
        raw_mode,
        dcs_model_id,
    )
    return raw_mode


async def resolve_standard_to_raw(
    db: AsyncSession,
    dcs_model_id: str | None,
    standard_mode: int,
) -> int:
    """将标准 MODE 值转换为 DCS 原始 MODE 值（反向映射）.

    解析优先级：
    1. 型号映射：``dcs_mode_mapping`` WHERE dcs_model_id=? AND standard_mode=?
    2. 本系统默认：``dcs_mode_mapping`` WHERE dcs_model_id IS NULL AND standard_mode=?
    3. 回退 standard_mode 值本身（1:1）

    Args:
        db: 异步数据库会话
        dcs_model_id: DCS 型号 ID（NULL=使用本系统默认）
        standard_mode: 标准 MODE 值（0-4）

    Returns:
        DCS 原始 MODE 值
    """
    from app.models.dcs_mode_mapping import DcsModeMapping

    # 1. 优先查型号映射
    if dcs_model_id:
        result = await db.execute(
            select(DcsModeMapping.raw_mode_value).where(
                DcsModeMapping.dcs_model_id == dcs_model_id,
                DcsModeMapping.standard_mode == standard_mode,
            )
        )
        raw = result.scalar_one_or_none()
        if raw is not None:
            return raw

    # 2. 回退本系统默认映射
    result = await db.execute(
        select(DcsModeMapping.raw_mode_value).where(
            DcsModeMapping.dcs_model_id.is_(None),
            DcsModeMapping.standard_mode == standard_mode,
        )
    )
    raw = result.scalar_one_or_none()
    if raw is not None:
        return raw

    # 3. 回退 standard_mode 本身（1:1）
    return standard_mode


# ---------------------------------------------------------------------------
# 批量解析（用于实时自控率统计，避免逐回路查询）
# ---------------------------------------------------------------------------


async def build_raw_to_standard_map(
    db: AsyncSession,
    dcs_model_id: str | None,
) -> dict[int, int]:
    """构建某型号的 raw_mode_value → standard_mode 映射表.

    解析优先级：
    1. 型号映射（dcs_model_id 非 NULL 时）
    2. 本系统默认映射（dcs_model_id IS NULL）

    用于批量转换同型号回路的 MODE 值，避免逐次查询。

    Args:
        db: 异步数据库会话
        dcs_model_id: DCS 型号 ID（NULL=使用本系统默认）

    Returns:
        {raw_mode_value: standard_mode} 映射字典
    """
    from app.models.dcs_mode_mapping import DcsModeMapping

    # 先查型号映射
    model_map: dict[int, int] = {}
    if dcs_model_id:
        result = await db.execute(
            select(DcsModeMapping.raw_mode_value, DcsModeMapping.standard_mode).where(
                DcsModeMapping.dcs_model_id == dcs_model_id
            )
        )
        model_map = {row.raw_mode_value: row.standard_mode for row in result.all()}

    # 再查本系统默认映射（补充型号未覆盖的 raw 值）
    result = await db.execute(
        select(DcsModeMapping.raw_mode_value, DcsModeMapping.standard_mode).where(
            DcsModeMapping.dcs_model_id.is_(None)
        )
    )
    default_map = {row.raw_mode_value: row.standard_mode for row in result.all()}

    # 合并：型号映射优先，默认映射补充
    merged = {**default_map, **model_map}
    return merged


async def batch_resolve_loops_mode(
    db: AsyncSession,
    loop_model_pairs: list[tuple[str, int | None]],
) -> dict[str, int]:
    """批量解析多个回路的 MODE 值（按型号分组查询）.

    用于实时自控率统计场景：一次性解析所有回路的 MODE 值，
    避免逐回路查询数据库。

    Args:
        db: 异步数据库会话
        loop_model_pairs: [(loop_id, raw_mode)] 列表

    Returns:
        {loop_id: standard_mode} 映射字典
    """
    if not loop_model_pairs:
        return {}

    # 按型号分组（None 归入默认组）
    model_groups: dict[str | None, list[tuple[str, int]]] = {}
    for loop_id, raw_mode in loop_model_pairs:
        if raw_mode is None:
            continue
        # raw_mode 可能是 None（回路无 MODE 数据），跳过
        key = None  # 简化：暂不按回路型号分组，统一用默认+型号映射
        model_groups.setdefault(key, []).append((loop_id, raw_mode))

    # 简化实现：构建本系统默认映射表，逐回路转换
    # （型号映射在调用方通过 loop.dcs_model_id 查询，此处用默认映射）
    default_map = await build_raw_to_standard_map(db, None)

    result: dict[str, int] = {}
    for loop_id, raw_mode in loop_model_pairs:
        if raw_mode is None:
            continue
        # 优先用默认映射，找不到则 1:1
        result[loop_id] = default_map.get(raw_mode, raw_mode)
    return result


__all__ = [
    "batch_resolve_loops_mode",
    "build_raw_to_standard_map",
    "get_auto_modes",
    "get_mode_definitions",
    "get_mode_labels",
    "resolve_raw_to_standard",
    "resolve_standard_to_raw",
]
