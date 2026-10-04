"""三性分离维度口径配置接口（2026-10-03 R5 裁决）.

「一套原子判定，三个维度视图」：7 项适用性阈值（/configs/fitness-thresholds）
产出原子 tags，本接口配置各 tag 对 可评估性/可诊断性/可整定性 三个维度的
降级档位映射，存储在 sys_config 单键 ``fitness.dimension_maps``（JSON）。

- DATA_INSUFFICIENT 恒为三维 L0（数据红线），不在可配置项中
- 映射值：L1/L2/L3 或 null（该 tag 不影响该维度）；未列出维度回退默认映射
- 默认映射与现行三模块消费行为完全等价（零行为变化的等价迁移）
- 保存后立即生效：compute_fitness 每次计算从 sys_config 读取

路由清单：
- GET  /api/v1/configs/fitness-dimension-maps — 合并视图（默认 + 覆盖 + 元数据）
- PUT  /api/v1/configs/fitness-dimension-maps — 保存覆盖或重置默认（仅 ADMIN）
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.services.loop_fitness import (
    _DEFAULT_DIMENSION_MAPS,
    DIMENSION_TAGS,
    DIMENSIONS,
    FITNESS_DIMENSION_MAPS_KEY,
    TAG_HUMAN_REASON,
    load_dimension_maps,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/fitness-dimension-maps", tags=["fitness-config"])

_VALID_LEVELS = ("L1", "L2", "L3")

_DIMENSION_META: list[dict] = [
    {
        "key": "assess",
        "label": "可评估性",
        "description": "数据充分 + 非手动主导即可评估；饱和/偏差/激励不影响「能不能评」",
    },
    {
        "key": "diagnose",
        "label": "可诊断性",
        "default-block": "L0 阻断、L1 阻断、L2 警告放行、L3 受限提示",
        "description": "现行诊断口径（2026-10-01 裁决）",
    },
    {
        "key": "tune",
        "label": "可整定性",
        "description": (
            "现行整定口径：L0/L1 阻断 ERR_TUNING_FITNESS_INSUFFICIENT、L2 警告、L3 前置激励提示"
        ),
    },
]


class DimensionMapsSaveRequest(BaseModel):
    """保存请求：maps 为 {维度: {tag: level|null}}；resetAll=true 重置默认."""

    maps: dict[str, dict[str, str | None]] = Field(default_factory=dict)
    remark: str | None = None
    resetAll: bool = False


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _validate_maps(maps: dict) -> None:
    for dim, tag_map in maps.items():
        if dim not in DIMENSIONS:
            raise BizError(
                code="ERR_FITNESS_DIMENSION_UNKNOWN",
                message=f"未知维度: {dim}（合法值 {list(DIMENSIONS)}）",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(tag_map, dict):
            raise BizError(
                code="ERR_FITNESS_DIMENSION_MAP_TYPE",
                message=f"维度 {dim} 的映射必须为对象（tag → 档位）",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        for tag, level in tag_map.items():
            if tag not in DIMENSION_TAGS:
                raise BizError(
                    code="ERR_FITNESS_DIMENSION_UNKNOWN_TAG",
                    message=(
                        f"维度 {dim} 中出现不可配置标签 {tag}"
                        f"（DATA_INSUFFICIENT 恒为 L0，不可配置；合法标签 {list(DIMENSION_TAGS)}）"
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if level is not None and level not in _VALID_LEVELS:
                raise BizError(
                    code="ERR_FITNESS_DIMENSION_BAD_LEVEL",
                    message=f"{dim}.{tag} 档位非法: {level}（合法值 L1/L2/L3 或 null=不影响）",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )


def _build_view(stored_raw: str | None) -> dict:
    """合并视图：默认映射 + 用户覆盖 + tag/维度元数据."""
    overrides: dict = {}
    if stored_raw:
        try:
            parsed = json.loads(stored_raw)
            if isinstance(parsed, dict):
                overrides = parsed
        except (TypeError, ValueError):
            logger.warning("维度映射存储值非法 JSON，忽略覆盖")
    effective = load_dimension_maps(
        {FITNESS_DIMENSION_MAPS_KEY: stored_raw} if stored_raw else None
    )

    dimensions: list[dict] = []
    for meta in _DIMENSION_META:
        dim = meta["key"]
        rows: list[dict] = []
        for tag in DIMENSION_TAGS:
            rows.append(
                {
                    "tag": tag,
                    "tagLabel": TAG_HUMAN_REASON.get(tag, tag),
                    "defaultValue": _DEFAULT_DIMENSION_MAPS[dim].get(tag),
                    "value": effective[dim].get(tag),
                    "isOverridden": tag in overrides.get(dim, {}),
                }
            )
        dimensions.append(
            {
                "key": dim,
                "label": meta["label"],
                "description": meta["description"],
                "tags": rows,
            }
        )
    return {
        "dimensions": dimensions,
        "note": (
            "档位语义：L1=不适用/阻断、L2=警告放行、L3=提示放行、null=不影响该维度；"
            "未命中任何降档 → L4 开放。DATA_INSUFFICIENT 恒为三维 L0。"
        ),
    }


async def _load_stored(db: AsyncSession) -> SysConfig | None:
    result = await db.execute(select(SysConfig).where(SysConfig.key == FITNESS_DIMENSION_MAPS_KEY))
    return result.scalar_one_or_none()


@router.get("", response_model=ApiResponse[dict])
async def get_fitness_dimension_maps(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取三性维度口径合并视图（默认映射 + sys_config 覆盖）."""
    cfg = await _load_stored(db)
    view = _build_view(cfg.value if cfg else None)
    if cfg is not None:
        view["updatedAt"] = (
            cfg.updated_at.replace(tzinfo=UTC).isoformat() if cfg.updated_at else None
        )
        view["updatedBy"] = cfg.updated_by
    return success(data=view)


@router.put("", response_model=ApiResponse[dict])
async def save_fitness_dimension_maps(
    body: DimensionMapsSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """保存维度口径覆盖（仅 ADMIN；resetAll=true 删除配置回退默认）.

    保存的 maps 为**完整覆盖视图**（未列出的 tag 在该维度视为 null=不影响），
    与 GET 返回的合并视图一一对应，前端直接回传用户编辑结果即可。
    """
    before_cfg = await _load_stored(db)
    before_raw = before_cfg.value if before_cfg else None

    if body.resetAll:
        if before_cfg is not None:
            await db.delete(before_cfg)
        after_raw = None
    else:
        if not body.maps:
            raise BizError(
                code="ERR_FITNESS_DIMENSION_EMPTY",
                message="维度口径保存请求 maps 为空，且 resetAll=False",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        _validate_maps(body.maps)
        after_raw = json.dumps(body.maps, ensure_ascii=False)
        now = _now_naive()
        if before_cfg is None:
            db.add(
                SysConfig(
                    key=FITNESS_DIMENSION_MAPS_KEY,
                    value=after_raw,
                    description="三性分离维度口径映射（assess/diagnose/tune × tag → 档位）",
                    updated_by=user.username,
                    updated_at=now,
                )
            )
        else:
            before_cfg.value = after_raw
            before_cfg.updated_by = user.username
            before_cfg.updated_at = now

    db.add(
        SysAuditLog(
            id=str(uuid4()),
            operator=user.username,
            operation_type="FITNESS_DIMENSION_MAPS_UPDATE",
            target_type="sys_config:fitness.dimension_maps",
            target_id=str(uuid4()),
            before_value=before_raw or "{}",
            after_value=after_raw or "{}",
            operated_at=_now_naive(),
        )
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("维度口径保存事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    view = _build_view(after_raw)
    return success(data=view, message="维度口径已保存，下次 KPI 计算生效")
