"""回路适用性阈值配置接口（IA 优化 P2：L0~L4 预诊断）.

提供 7 项适用性判定阈值（L1/L2/L3 分层）的查询与更新，
存储在 ``sys_config`` 表中，键前缀 ``fitness.``（与 loop_fitness.py
``_DEFAULT_THRESHOLDS`` 逐键对齐）。

保存后立即生效：loop_fitness.compute_fitness 从 sys_config 逐键读取，
无需刷新缓存，下次 KPI 计算任务自然使用最新值。

路由清单：
- GET  /api/v1/configs/fitness-thresholds — 获取合并视图（默认值 + 覆盖标记）
- PUT  /api/v1/configs/fitness-thresholds — 更新覆盖或一键重置默认（仅 ADMIN）
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    FitnessThresholdItem,
    FitnessThresholdSaveRequest,
    FitnessThresholdSchema,
)
from app.services.loop_fitness import (
    _DEFAULT_THRESHOLDS,
    FITNESS_CONFIG_PREFIX,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/fitness-thresholds", tags=["fitness-config"])


# ---------------------------------------------------------------------------
# 元数据：7 项阈值的展示信息（与 _DEFAULT_THRESHOLDS 的 key 一一对应）
# 省略前缀 FITNESS_CONFIG_PREFIX = "fitness."
# ---------------------------------------------------------------------------

_META: list[dict] = [
    # L1
    {
        "key": "manual_dominant_pct",
        "label": "手动主导阈值",
        "description": "手动模式时间占比（%）高于该阈值 → MANUAL_DOMINANT（归为 L1 仅可监视）",
        "level": "L1",
        "tag": "MANUAL_DOMINANT",
        "default": 80.0,
        "min": 0.0,
        "max": 100.0,
        "unit": "%",
    },
    {
        "key": "low_auto_rate_pct",
        "label": "低自控率阈值",
        "description": (
            "自控率（AUTO/CAS/REMOTE/APC 合计）（%）低于该阈值 → LOW_AUTO_RATE（归为 L1 仅可监视）"
        ),
        "level": "L1",
        "tag": "LOW_AUTO_RATE",
        "default": 20.0,
        "min": 0.0,
        "max": 100.0,
        "unit": "%",
    },
    # L2
    {
        "key": "op_saturated_band_pct",
        "label": "OP饱和限位带宽",
        "description": (
            "OP 距离量程上下限的占比范围（%）内即视为饱和样本，"
            "结合 OP 饱和时间占比判定 OP_SATURATED"
        ),
        "level": "L2",
        "tag": "OP_SATURATED",
        "default": 2.0,
        "min": 0.1,
        "max": 20.0,
        "unit": "%",
    },
    {
        "key": "op_saturated_time_pct",
        "label": "OP饱和时间占比阈值",
        "description": "饱和样本占比（%）高于该阈值 → OP_SATURATED（归为 L2 条件异常）",
        "level": "L2",
        "tag": "OP_SATURATED",
        "default": 30.0,
        "min": 0.0,
        "max": 100.0,
        "unit": "%",
    },
    {
        "key": "sp_pv_deviation_pct",
        "label": "SP-PV偏离幅度",
        "description": (
            "|SP-PV| 相对 PV 量程的占比（%）大于该值即视为偏离样本，"
            "结合偏离时间占比判定 SP_PV_DEVIATION"
        ),
        "level": "L2",
        "tag": "SP_PV_DEVIATION",
        "default": 10.0,
        "min": 0.1,
        "max": 100.0,
        "unit": "%",
    },
    {
        "key": "sp_pv_deviation_time_pct",
        "label": "SP-PV偏离时间占比阈值",
        "description": "偏离样本占比（%）高于该阈值 → SP_PV_DEVIATION（归为 L2 条件异常）",
        "level": "L2",
        "tag": "SP_PV_DEVIATION",
        "default": 30.0,
        "min": 0.0,
        "max": 100.0,
        "unit": "%",
    },
    # L3
    {
        "key": "no_excitation_op_range_pct",
        "label": "无激励OP变化范围",
        "description": (
            "OP 变化幅度（max-min）占量程比例（%）低于该值 → NO_EXCITATION（归为 L3 待激励）"
        ),
        "level": "L3",
        "tag": "NO_EXCITATION",
        "default": 2.0,
        "min": 0.1,
        "max": 50.0,
        "unit": "%",
    },
    {
        "key": "weak_response_min_gain",
        "label": "弱响应最小增益",
        "description": (
            "PV 变化量 / OP 变化量（归一化后）低于该值 → WEAK_RESPONSE"
            "（归为 L3 待激励，整定前需人工确认激励充分）"
        ),
        "level": "L3",
        "tag": "WEAK_RESPONSE",
        "default": 0.05,
        "min": 0.0,
        "max": 1.0,
        "unit": "无量纲",
    },
]

# 与 loop_fitness._DEFAULT_THRESHOLDS 双向对齐检查（启动时即抛错，避免静默错漏）
meta_keys = {FITNESS_CONFIG_PREFIX + m["key"] for m in _META}
assert meta_keys == set(_DEFAULT_THRESHOLDS.keys()), (
    "fitness_threshold_config._META 与 loop_fitness._DEFAULT_THRESHOLDS 键不匹配："
    f"meta_keys={sorted(meta_keys)}, "
    f"default_keys={sorted(_DEFAULT_THRESHOLDS.keys())}"
)

_META_KEYS = {m["key"]: m for m in _META}


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# sys_config 读写辅助
# ---------------------------------------------------------------------------


async def _load_stored_map(db: AsyncSession) -> dict[str, tuple[str, datetime | None, str | None]]:
    """读取所有 fitness. 前缀 sys_config 行.

    Returns:
        {key_without_prefix: (value_str, updated_at, updated_by)}
    """
    prefix = FITNESS_CONFIG_PREFIX
    stmt = select(SysConfig).where(SysConfig.key.like(f"{prefix}%"))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    out: dict[str, tuple[str, datetime | None, str | None]] = {}
    for row in rows:
        short = row.key[len(prefix) :]
        out[short] = (row.value, row.updated_at, row.updated_by)
    return out


async def _upsert_config(
    db: AsyncSession,
    short_key: str,
    value: str,
    operator: str,
) -> None:
    full_key = f"{FITNESS_CONFIG_PREFIX}{short_key}"
    meta = _META_KEYS.get(short_key)
    description: str | None = None
    if meta is not None:
        description = f"适用性阈值：{meta['label']}（{meta['tag']}，{meta['level']}）"
    stmt = select(SysConfig).where(SysConfig.key == full_key)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    if cfg is None:
        cfg = SysConfig(
            key=full_key,
            value=value,
            description=description,
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = value
        if description:
            cfg.description = description
        cfg.updated_by = operator
        cfg.updated_at = now


async def _delete_config(db: AsyncSession, short_key: str) -> None:
    """重置默认：删除 sys_config 行，让 compute_fitness 回落 DEFAULT."""
    full_key = f"{FITNESS_CONFIG_PREFIX}{short_key}"
    stmt = select(SysConfig).where(SysConfig.key == full_key)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if cfg is not None:
        await db.delete(cfg)


def _validate_value(short_key: str, value: float) -> None:
    meta = _META_KEYS.get(short_key)
    if meta is None:
        raise BizError(
            code="ERR_FITNESS_THRESHOLD_UNKNOWN_KEY",
            message=f"未知适用性阈值键: {short_key}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    lo = meta["min"]
    hi = meta["max"]
    if value < lo or value > hi:
        raise BizError(
            code="ERR_FITNESS_THRESHOLD_OUT_OF_RANGE",
            message=(
                f"适用性阈值 {meta['label']}({short_key})={value} 超出有效范围 "
                f"[{lo}, {hi}]（{meta['unit']}）"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )


async def _write_audit(
    db: AsyncSession,
    operator: str,
    before_raw: dict[str, str],
    after_raw: dict[str, str],
    remark: str | None = None,
) -> None:
    # P4 修复（2026-08-22）：SysAuditLog 无 remark 列，直接传 remark= 会抛
    # TypeError → 500"服务异常"。备注并入 after_value 载荷（对齐其他配置
    # 模块把 remark 存 JSON 的做法）。
    after_payload: dict[str, str] = dict(after_raw)
    if remark:
        after_payload["_remark"] = remark
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="FITNESS_THRESHOLD_UPDATE",
        target_type="sys_config:fitness.*",
        target_id=str(uuid4()),
        before_value=json.dumps(before_raw, ensure_ascii=False),
        after_value=json.dumps(after_payload, ensure_ascii=False),
        operated_at=_now_naive(),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# 合并视图构建
# ---------------------------------------------------------------------------


def _build_view(
    stored_map: dict[str, tuple[str, datetime | None, str | None]],
) -> FitnessThresholdSchema:
    items: list[FitnessThresholdItem] = []
    latest_updated_at: datetime | None = None
    latest_updated_by: str | None = None
    for meta in _META:
        key = meta["key"]
        default_value = float(meta["default"])
        stored = stored_map.get(key)
        if stored is not None:
            value_str, upd_at, upd_by = stored
            try:
                value = float(value_str)
            except (TypeError, ValueError):
                logger.warning(
                    "fitness 阈值 %s 非法存储值 '%s'，回落默认 %s",
                    key,
                    value_str,
                    default_value,
                )
                value = default_value
                upd_at = None
                upd_by = None
            if upd_at is not None and (latest_updated_at is None or upd_at > latest_updated_at):
                latest_updated_at = upd_at
                latest_updated_by = upd_by
        else:
            value = default_value
        items.append(
            FitnessThresholdItem(
                key=key,
                label=meta["label"],
                description=meta["description"],
                level=meta["level"],  # type: ignore[arg-type]
                tag=meta["tag"],
                value=value,
                defaultValue=default_value,
                minValue=float(meta["min"]),
                maxValue=float(meta["max"]),
                unit=meta["unit"],
            )
        )
    updated_at_iso = (
        latest_updated_at.replace(tzinfo=UTC).isoformat() if latest_updated_at is not None else None
    )
    return FitnessThresholdSchema(
        items=items,
        updatedAt=updated_at_iso,
        updatedBy=latest_updated_by,
    )


# ---------------------------------------------------------------------------
# GET /configs/fitness-thresholds
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[FitnessThresholdSchema])
async def get_fitness_thresholds(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取适用性判定阈值的合并视图（默认值 + sys_config 覆盖）.

    返回 7 项阈值，按 L1→L2→L3 排列；从未保存过的键使用默认值。
    """
    stored = await _load_stored_map(db)
    view = _build_view(stored)
    return success(data=view.model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# PUT /configs/fitness-thresholds
# ---------------------------------------------------------------------------


@router.put("", response_model=ApiResponse[FitnessThresholdSchema])
async def save_fitness_thresholds(
    body: FitnessThresholdSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新适用性阈值（仅 ADMIN）.

    - resetAll=True：删除全部 7 个 ``fitness.*`` 行，立即回落默认值。
    - resetAll=False：按 body.items 逐条覆盖（仅校验范围内的键，范围外 400）；
      未列出的键保持原值（或默认）。
    """
    before_stored = await _load_stored_map(db)
    before_snapshot = {k: v[0] for k, v in before_stored.items()}

    if body.resetAll:
        for meta in _META:
            await _delete_config(db, meta["key"])
    else:
        if not body.items:
            raise BizError(
                code="ERR_FITNESS_THRESHOLD_EMPTY",
                message="适用性阈值保存请求 items 为空，且 resetAll=False",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        # 先整体校验，再批量写入，避免半写
        normalized: dict[str, float] = {}
        for item in body.items:
            if item.key in normalized:
                raise BizError(
                    code="ERR_FITNESS_THRESHOLD_DUPLICATE_KEY",
                    message=f"适用性阈值保存请求中键 {item.key} 重复",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            _validate_value(item.key, item.value)
            normalized[item.key] = item.value
        for short_key, val in normalized.items():
            await _upsert_config(db, short_key, repr(float(val)), user.username)

    after_stored = await _load_stored_map(db)
    after_snapshot = {k: v[0] for k, v in after_stored.items()}

    await _write_audit(
        db=db,
        operator=user.username,
        before_raw=before_snapshot,
        after_raw=after_snapshot,
        remark=body.remark or ("重置为默认值" if body.resetAll else None),
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("适用性阈值保存事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    stored = await _load_stored_map(db)
    view = _build_view(stored)
    return success(data=view.model_dump(by_alias=True))
