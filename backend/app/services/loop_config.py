"""Loop configuration services (重构方案 v1.2).

对齐 GB/T 44693.2-2024 的 3 项配置 CRUD 服务：
- 投用定义 CRUD（MODE 值到控制模式的映射）
- 回路类型权重 CRUD（附表1，4 种回路类型）
- 回路级别权重 CRUD（附表2，3 个级别）

所有写操作均记录审计日志。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.loop_config import LoopLevelWeight, LoopModeMapping, LoopTypeWeight

logger = logging.getLogger(__name__)

# Redis 缓存键
LOOP_TYPE_WEIGHT_CACHE_KEY = "clpm:loop_type_weight"
LOOP_LEVEL_WEIGHT_CACHE_KEY = "clpm:loop_level_weight"
LOOP_MODE_MAPPING_CACHE_KEY_TEMPLATE = "clpm:loop_mode_mapping:{loop_id}"

# 工艺类型 → 评分类型 默认映射（对齐国标 GB/T 44693.2-2024 附表1）
# 工艺类型（LoopLedger.loop_type）→ 评分类型（LoopTypeWeight.loop_type）
_LOOP_TYPE_TO_SCORE_TYPE: dict[str, str] = {
    "TEMPERATURE": "STABLE",  # 温度控制 → 稳定型
    "PRESSURE": "STABLE",     # 压力控制 → 稳定型
    "LEVEL": "SLOW",          # 液位控制 → 慢速型
    "ANALYSIS": "SLOW",       # 成分分析 → 慢速型
    "FLOW": "FAST",           # 流量控制 → 快速型
    "SPEED": "FAST",          # 速度控制 → 快速型
    "OTHER": "LOGIC",         # 其他 → 逻辑型
}


def infer_score_type(loop_type: str | None) -> str:
    """根据回路工艺类型推断评分类型（STABLE/SLOW/FAST/LOGIC）。

    用于评分算法 v2 查询 loop_type_weight。
    后续可扩展为 LoopLedger.score_type 字段直接配置。

    Args:
        loop_type: LoopLedger.loop_type（TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER）

    Returns:
        评分类型：STABLE/SLOW/FAST/LOGIC（默认 LOGIC）
    """
    if not loop_type:
        return "LOGIC"
    return _LOOP_TYPE_TO_SCORE_TYPE.get(loop_type, "LOGIC")


async def get_loop_type_weights_map(db: AsyncSession) -> dict[str, dict]:
    """批量查询全部回路类型权重，返回 {score_type: {weight_a, weight_f, weight_s}} 映射。

    用于评分算法 v2 批量获取权重，避免逐回路查询。
    """
    result = await db.execute(select(LoopTypeWeight))
    weights = result.scalars().all()
    return {
        w.loop_type: {
            "weight_a": w.weight_a,
            "weight_f": w.weight_f,
            "weight_s": w.weight_s,
        }
        for w in weights
    }


async def get_loop_level_weights_map(db: AsyncSession) -> dict[int, Decimal]:
    """批量查询全部回路级别权重，返回 {level: weight} 映射。

    用于节点级聚合 v2 批量获取权重。
    """
    result = await db.execute(select(LoopLevelWeight))
    weights = result.scalars().all()
    return {w.level: w.weight for w in weights}


# ---------------------------------------------------------------------------
# 审计日志辅助
# ---------------------------------------------------------------------------


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志。"""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# SVC-01: 投用定义 CRUD（LoopModeMapping）
# ---------------------------------------------------------------------------


async def list_mode_mappings(db: AsyncSession, loop_id: str) -> list[dict]:
    """获取指定回路的投用定义列表。"""
    result = await db.execute(
        select(LoopModeMapping)
        .where(LoopModeMapping.loop_id == loop_id)
        .order_by(LoopModeMapping.mode_value.asc())
    )
    mappings = result.scalars().all()
    return [_mode_mapping_to_dict(m) for m in mappings]


async def replace_mode_mappings(
    db: AsyncSession,
    loop_id: str,
    operator: str,
    mappings: list[dict],
) -> list[dict]:
    """全量替换指定回路的投用定义。

    采用"先删后建"策略，保证幂等性。
    每条 mapping 格式：{"modeValue": 1, "modeLabel": "AUTO", "isAuto": true, "isEffective": true}

    校验：
    - modeValue 必须为整数
    - modeLabel 必须在 {AUTO, CAS, REMOTE, APC, MANUAL} 中
    - 同一回路内 modeValue 不重复

    Raises:
        BizError: ERR_MODE_MAPPING_DUPLICATE / ERR_MODE_MAPPING_INVALID
    """
    # 校验输入
    seen_values: set[int] = set()
    valid_labels = {"AUTO", "CAS", "REMOTE", "APC", "MANUAL"}
    for m in mappings:
        mv = m.get("modeValue")
        label = m.get("modeLabel")
        if not isinstance(mv, int) or mv < 0:
            raise BizError(
                code="ERR_MODE_MAPPING_INVALID",
                message=f"MODE 值无效: {mv}（必须为非负整数）",
                status_code=422,
            )
        if label not in valid_labels:
            raise BizError(
                code="ERR_MODE_MAPPING_INVALID",
                message=f"控制模式无效: {label}（允许: {valid_labels}）",
                status_code=422,
            )
        if mv in seen_values:
            raise BizError(
                code="ERR_MODE_MAPPING_DUPLICATE",
                message=f"MODE 值重复: {mv}",
                status_code=422,
            )
        seen_values.add(mv)

    # 查询旧数据用于审计
    old_result = await db.execute(
        select(LoopModeMapping).where(LoopModeMapping.loop_id == loop_id)
    )
    old_mappings = old_result.scalars().all()
    before_json = json.dumps(
        [_mode_mapping_to_dict(m) for m in old_mappings],
        ensure_ascii=False,
        default=str,
    )

    # 先删后建
    await db.execute(
        delete(LoopModeMapping).where(LoopModeMapping.loop_id == loop_id)
    )

    new_records: list[LoopModeMapping] = []
    for m in mappings:
        record = LoopModeMapping(
            id=str(uuid4()),
            loop_id=loop_id,
            mode_value=m["modeValue"],
            mode_label=m["modeLabel"],
            is_auto=bool(m.get("isAuto", False)),
            is_effective=bool(m.get("isEffective", False)),
        )
        db.add(record)
        new_records.append(record)

    after_json = json.dumps(
        [_mode_mapping_to_dict(m) for m in new_records],
        ensure_ascii=False,
        default=str,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="MODE_MAPPING_REPLACE",
        target_type="loop_mode_mapping",
        target_id=loop_id,
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    logger.info(
        "[投用定义] 回路 %s 已更新 %d 条映射（操作人: %s）",
        loop_id, len(new_records), operator,
    )

    return [_mode_mapping_to_dict(m) for m in new_records]


async def get_auto_mode_values(db: AsyncSession, loop_id: str) -> set[int]:
    """获取指定回路中"算自动控制"的 MODE 值集合。

    用于实时自控率计算，替代硬编码 {1,2,3}。
    若回路无配置，返回默认 {1, 2, 3}（向后兼容）。
    """
    result = await db.execute(
        select(LoopModeMapping.mode_value)
        .where(LoopModeMapping.loop_id == loop_id, LoopModeMapping.is_auto.is_(True))
    )
    values = {row.mode_value for row in result.all()}
    if not values:
        # 无配置时回退到默认值（向后兼容）
        return {1, 2, 3}
    return values


async def get_effective_mode_values(db: AsyncSession, loop_id: str) -> set[int]:
    """获取指定回路中"算有效自动"的 MODE 值集合。

    用于有效自控率计算。
    若回路无配置，返回默认 {1, 2, 3}（向后兼容）。
    """
    result = await db.execute(
        select(LoopModeMapping.mode_value)
        .where(LoopModeMapping.loop_id == loop_id, LoopModeMapping.is_effective.is_(True))
    )
    values = {row.mode_value for row in result.all()}
    if not values:
        return {1, 2, 3}
    return values


# ---------------------------------------------------------------------------
# SVC-02: 回路类型权重 CRUD（LoopTypeWeight）
# ---------------------------------------------------------------------------


async def list_loop_type_weights(db: AsyncSession) -> list[dict]:
    """获取全部回路类型权重配置。"""
    result = await db.execute(
        select(LoopTypeWeight).order_by(LoopTypeWeight.loop_type.asc())
    )
    weights = result.scalars().all()
    return [_type_weight_to_dict(w) for w in weights]


async def get_loop_type_weight(
    db: AsyncSession, loop_type: str
) -> dict | None:
    """获取指定类型的权重配置。"""
    result = await db.execute(
        select(LoopTypeWeight).where(LoopTypeWeight.loop_type == loop_type)
    )
    w = result.scalar_one_or_none()
    return _type_weight_to_dict(w) if w else None


async def update_loop_type_weight(
    db: AsyncSession,
    loop_type: str,
    operator: str,
    *,
    type_name: str | None = None,
    weight_a: Decimal | None = None,
    weight_f: Decimal | None = None,
    weight_s: Decimal | None = None,
    description: str | None = None,
) -> dict:
    """更新回路类型权重配置。

    校验：
    - 类型必须存在（ERR_LOOP_TYPE_NOT_FOUND）
    - weight_a + weight_f + weight_s 应为 1.0（允许 ±0.01 误差）

    Raises:
        BizError: ERR_LOOP_TYPE_NOT_FOUND / ERR_WEIGHT_SUM_INVALID
    """
    result = await db.execute(
        select(LoopTypeWeight).where(LoopTypeWeight.loop_type == loop_type)
    )
    w = result.scalar_one_or_none()
    if w is None:
        raise BizError(
            code="ERR_LOOP_TYPE_NOT_FOUND",
            message=f"回路类型不存在: {loop_type}",
            status_code=404,
        )

    before = _type_weight_to_dict(w)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if type_name is not None:
        w.type_name = type_name
    if weight_a is not None:
        w.weight_a = weight_a
    if weight_f is not None:
        w.weight_f = weight_f
    if weight_s is not None:
        w.weight_s = weight_s
    if description is not None:
        w.description = description

    w.updated_by = operator
    w.updated_at = datetime.now(UTC).replace(tzinfo=None)

    # 权重和校验：a + f + s 应为 1.0
    total = float(w.weight_a) + float(w.weight_f) + float(w.weight_s)
    if abs(total - 1.0) > 0.01:
        raise BizError(
            code="ERR_WEIGHT_SUM_INVALID",
            message=f"权重总和必须为 1.0，当前为 {total:.2f}",
            status_code=422,
        )

    after = _type_weight_to_dict(w)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_TYPE_WEIGHT_UPDATE",
        target_type="loop_type_weight",
        target_id=str(w.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    logger.info(
        "[类型权重] %s 已更新（a=%s, f=%s, s=%s, 操作人: %s）",
        loop_type, w.weight_a, w.weight_f, w.weight_s, operator,
    )

    return after


# ---------------------------------------------------------------------------
# SVC-03: 回路级别权重 CRUD（LoopLevelWeight）
# ---------------------------------------------------------------------------


async def list_loop_level_weights(db: AsyncSession) -> list[dict]:
    """获取全部回路级别权重配置。"""
    result = await db.execute(
        select(LoopLevelWeight).order_by(LoopLevelWeight.level.asc())
    )
    weights = result.scalars().all()
    return [_level_weight_to_dict(w) for w in weights]


async def get_loop_level_weight(
    db: AsyncSession, level: int
) -> dict | None:
    """获取指定级别的权重配置。"""
    result = await db.execute(
        select(LoopLevelWeight).where(LoopLevelWeight.level == level)
    )
    w = result.scalar_one_or_none()
    return _level_weight_to_dict(w) if w else None


async def update_loop_level_weight(
    db: AsyncSession,
    level: int,
    operator: str,
    *,
    level_name: str | None = None,
    weight: Decimal | None = None,
    description: str | None = None,
) -> dict:
    """更新回路级别权重配置。

    校验：
    - 级别必须存在（ERR_LOOP_LEVEL_NOT_FOUND）
    - weight 必须 > 0

    Raises:
        BizError: ERR_LOOP_LEVEL_NOT_FOUND / ERR_WEIGHT_INVALID
    """
    result = await db.execute(
        select(LoopLevelWeight).where(LoopLevelWeight.level == level)
    )
    w = result.scalar_one_or_none()
    if w is None:
        raise BizError(
            code="ERR_LOOP_LEVEL_NOT_FOUND",
            message=f"回路级别不存在: {level}",
            status_code=404,
        )

    before = _level_weight_to_dict(w)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if level_name is not None:
        w.level_name = level_name
    if weight is not None:
        if weight <= 0:
            raise BizError(
                code="ERR_WEIGHT_INVALID",
                message=f"权重必须大于 0，当前为 {weight}",
                status_code=422,
            )
        w.weight = weight
    if description is not None:
        w.description = description

    w.updated_by = operator
    w.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _level_weight_to_dict(w)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="LOOP_LEVEL_WEIGHT_UPDATE",
        target_type="loop_level_weight",
        target_id=str(w.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    logger.info(
        "[级别权重] %d 级已更新（weight=%s, 操作人: %s）",
        level, w.weight, operator,
    )

    return after


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------


def _mode_mapping_to_dict(m: LoopModeMapping) -> dict:
    return {
        "id": str(m.id),
        "loopId": str(m.loop_id),
        "modeValue": m.mode_value,
        "modeLabel": m.mode_label,
        "isAuto": bool(m.is_auto),
        "isEffective": bool(m.is_effective),
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }


def _type_weight_to_dict(w: LoopTypeWeight) -> dict:
    return {
        "id": str(w.id),
        "loopType": w.loop_type,
        "typeName": w.type_name,
        "weightA": float(w.weight_a),
        "weightF": float(w.weight_f),
        "weightS": float(w.weight_s),
        "description": w.description,
        "updatedBy": w.updated_by,
        "updatedAt": w.updated_at.isoformat() if w.updated_at else None,
    }


def _level_weight_to_dict(w: LoopLevelWeight) -> dict:
    return {
        "id": str(w.id),
        "level": w.level,
        "levelName": w.level_name,
        "weight": float(w.weight),
        "description": w.description,
        "updatedBy": w.updated_by,
        "updatedAt": w.updated_at.isoformat() if w.updated_at else None,
    }


__all__ = [
    # 映射辅助
    "infer_score_type",
    "get_loop_type_weights_map",
    "get_loop_level_weights_map",
    # 投用定义
    "list_mode_mappings",
    "replace_mode_mappings",
    "get_auto_mode_values",
    "get_effective_mode_values",
    # 类型权重
    "list_loop_type_weights",
    "get_loop_type_weight",
    "update_loop_type_weight",
    # 级别权重
    "list_loop_level_weights",
    "get_loop_level_weight",
    "update_loop_level_weight",
]
