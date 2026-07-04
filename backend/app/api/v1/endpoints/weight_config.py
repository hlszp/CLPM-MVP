"""权重模板管理接口 (FDS v5.1 §5.2.2 / DDS v4.1 / UIUX v5.3 ②).

提供 4 类控制类型（STABLE/SLOW/FAST/LOGIC）的 6 指标权重模板管理，
支持版本化保存、历史查询、回滚与恢复国标默认值。

权重模板存储在 ``sys_config`` 表中（JSON 序列化）：
- ``weight_template.current`` — 当前生效模板（含 version 字段）
- ``weight_template.history`` — 历史版本列表（JSON 数组）

国标默认权重（对齐 GB/T 44693.2-2024 附录 C / 算法 v2.1 §4.10.3）：
    - STABLE: a=20, f=30, s=50  —— 温度、压力控制
    - SLOW:   a=30, f=10, s=60  —— 缓慢调节回路
    - FAST:   a=20, f=50, s=30  —— 副回路、流量控制
    - LOGIC:  a=0,  f=40, s=60  —— 防回流、防超温

有效自控率（effective_auto_rate）为折扣因子 R，不参与权重和校验。
3 项核心指标权重（accuracyRate + fastRate + steadyRate）总和须为 100。

路由清单：
- GET  /api/v1/configs/weight-templates                  — 获取当前权重模板
- POST /api/v1/configs/weight-templates                  — 保存为新版本
- GET  /api/v1/configs/weight-templates/history          — 版本历史
- POST /api/v1/configs/weight-templates/{version}/rollback — 回滚到指定版本
- POST /api/v1/configs/weight-templates/restore-defaults  — 恢复国标默认值

设计依据：FDS v5.1 §5.2.2, DDS v4.1, 算法 v2.1 §4.10.3, UIUX v5.3 ②
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
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
    WeightTemplateItem,
    WeightTemplateSaveRequest,
    WeightTemplateSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/weight-templates", tags=["weight-config"])

# ---------------------------------------------------------------------------
# sys_config 键常量
# ---------------------------------------------------------------------------

_KEY_CURRENT = "weight_template.current"
_KEY_HISTORY = "weight_template.history"
_KEY_DESC_CURRENT = "权重模板当前生效版本（JSON）"
_KEY_DESC_HISTORY = "权重模板历史版本列表（JSON 数组）"

# ---------------------------------------------------------------------------
# 国标默认权重（对齐 GB/T 44693.2-2024 附录 C / 算法 v2.1 §4.10.3）
# ---------------------------------------------------------------------------

DEFAULT_WEIGHT_TEMPLATES: list[dict[str, Any]] = [
    {
        "controlType": "STABLE",
        "autoModeRate": 0,
        "steadyRate": 50,
        "accuracyRate": 20,
        "fastRate": 30,
        "oscillationRate": 0,
        "saturationRate": 0,
    },
    {
        "controlType": "SLOW",
        "autoModeRate": 0,
        "steadyRate": 60,
        "accuracyRate": 30,
        "fastRate": 10,
        "oscillationRate": 0,
        "saturationRate": 0,
    },
    {
        "controlType": "FAST",
        "autoModeRate": 0,
        "steadyRate": 30,
        "accuracyRate": 20,
        "fastRate": 50,
        "oscillationRate": 0,
        "saturationRate": 0,
    },
    {
        "controlType": "LOGIC",
        "autoModeRate": 0,
        "steadyRate": 60,
        "accuracyRate": 0,
        "fastRate": 40,
        "oscillationRate": 0,
        "saturationRate": 0,
    },
]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


def _validate_core_weight_sum(templates: list[WeightTemplateItem]) -> None:
    """校验 3 项核心指标权重总和为 100.

    核心指标：accuracyRate + fastRate + steadyRate = 100
    辅助指标（autoModeRate/oscillationRate/saturationRate）不参与校验。
    """
    for tpl in templates:
        core_sum = tpl.accuracyRate + tpl.fastRate + tpl.steadyRate
        if core_sum != 100:
            raise BizError(
                code="ERR_WEIGHT_SUM_INVALID",
                message=(
                    f"控制类型 {tpl.controlType} 的核心指标权重总和必须为 100，"
                    f"当前为 {core_sum}（accuracy={tpl.accuracyRate}, "
                    f"fast={tpl.fastRate}, steady={tpl.steadyRate}）"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )


async def _get_config_value(db: AsyncSession, key: str) -> str | None:
    """读取 sys_config 表中某个 key 的值."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def _set_config_value(
    db: AsyncSession,
    key: str,
    value: str,
    description: str | None,
    operator: str,
) -> None:
    """写入 sys_config 表（upsert，不提交）."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    if cfg is None:
        cfg = SysConfig(
            key=key,
            value=value,
            description=description,
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = value
        cfg.description = description or cfg.description
        cfg.updated_by = operator
        cfg.updated_at = now


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志."""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=_now_naive(),
    )
    db.add(log)


def _build_default_template() -> WeightTemplateSchema:
    """构建国标默认权重模板（version=0 表示国标默认）."""
    items = [WeightTemplateItem(**tpl) for tpl in DEFAULT_WEIGHT_TEMPLATES]
    return WeightTemplateSchema(
        version=0,
        templates=items,
        updatedAt=None,
        updatedBy=None,
    )


async def _load_current_template(db: AsyncSession) -> WeightTemplateSchema:
    """加载当前生效的权重模板.

    若 sys_config 中不存在，返回国标默认模板（不写入数据库）。
    """
    raw = await _get_config_value(db, _KEY_CURRENT)
    if not raw:
        return _build_default_template()
    try:
        data = json.loads(raw)
        return WeightTemplateSchema.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("权重模板当前版本解析失败，回退国标默认: %s", exc)
        return _build_default_template()


async def _load_history(db: AsyncSession) -> list[dict[str, Any]]:
    """加载历史版本列表."""
    raw = await _get_config_value(db, _KEY_HISTORY)
    if not raw:
        return []
    try:
        history = json.loads(raw)
        return history if isinstance(history, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("权重模板历史版本解析失败，返回空列表")
        return []


async def _save_template_version(
    db: AsyncSession,
    templates: list[WeightTemplateItem],
    operator: str,
    remark: str | None = None,
) -> WeightTemplateSchema:
    """保存权重模板为新版本并写入历史.

    步骤：
    1. 加载当前模板作为 before_snapshot
    2. 计算新版本号（当前版本 + 1）
    3. 写入 history（追加当前版本到历史）
    4. 更新 current 为新版本
    5. 写入审计日志
    """
    # 加载当前模板
    current = await _load_current_template(db)
    before_snapshot = current.model_dump_json()

    # 计算新版本号（国标默认 version=0，首次保存为 1）
    new_version = current.version + 1 if current.version > 0 else 1

    # 构建新模板
    new_template = WeightTemplateSchema(
        version=new_version,
        templates=templates,
        updatedAt=_now_iso(),
        updatedBy=operator,
    )

    # 加载历史并追加当前版本
    history = await _load_history(db)
    history.append(
        {
            "version": current.version,
            "templates": [t.model_dump() for t in current.templates],
            "updatedAt": current.updatedAt,
            "updatedBy": current.updatedBy,
            "remark": remark or f"保存版本 {new_version} 前的快照",
            "isCurrent": False,
        }
    )

    # 写入 sys_config（不提交，由调用方提交）
    await _set_config_value(
        db,
        _KEY_CURRENT,
        new_template.model_dump_json(),
        _KEY_DESC_CURRENT,
        operator,
    )
    await _set_config_value(
        db,
        _KEY_HISTORY,
        json.dumps(history, ensure_ascii=False),
        _KEY_DESC_HISTORY,
        operator,
    )

    # 审计日志
    await _write_audit(
        db=db,
        operator=operator,
        operation_type="WEIGHT_TEMPLATE_SAVE",
        target_type="sys_config",
        target_id=_KEY_CURRENT,
        before_value=before_snapshot,
        after_value=new_template.model_dump_json(),
    )

    return new_template


# ---------------------------------------------------------------------------
# GET /configs/weight-templates — 获取当前权重模板
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[WeightTemplateSchema])
async def get_weight_templates(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取当前生效的权重模板（4 类控制类型的 6 指标权重）.

    若未配置过，返回国标默认权重（version=0）。

    设计依据：FDS v5.1 §5.2.2
    """
    template = await _load_current_template(db)
    return success(data=template.model_dump())


# ---------------------------------------------------------------------------
# POST /configs/weight-templates — 保存为新版本
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[WeightTemplateSchema])
async def save_weight_templates(
    body: WeightTemplateSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """保存权重模板为新版本（仅 ADMIN）.

    校验：
    - templates 数量 1-4（至少 1 类控制类型）
    - 每类控制类型的 3 项核心指标权重和须为 100
    - 保存后自动生成新版本号并归档当前版本到历史

    设计依据：FDS v5.1 §5.2.2
    """
    # 校验核心权重和
    _validate_core_weight_sum(body.templates)

    # 控制类型唯一性校验
    control_types = [t.controlType for t in body.templates]
    if len(set(control_types)) != len(control_types):
        raise BizError(
            code="ERR_DUPLICATE_CONTROL_TYPE",
            message="权重模板中存在重复的控制类型",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 保存新版本
    new_template = await _save_template_version(
        db=db,
        templates=body.templates,
        operator=user.username,
        remark=body.remark,
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("保存权重模板事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "权重模板已保存: version=%d, types=%s, operator=%s",
        new_template.version,
        [t.controlType for t in new_template.templates],
        user.username,
    )
    return success(data=new_template.model_dump(), message="权重模板已保存")


# ---------------------------------------------------------------------------
# GET /configs/weight-templates/history — 版本历史
# ---------------------------------------------------------------------------


@router.get("/history", response_model=ApiResponse[dict])
async def get_weight_template_history(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询权重模板版本历史（仅 ADMIN）.

    返回历史版本列表 + 当前版本标识。

    设计依据：FDS v5.1 §5.2.2
    """
    current = await _load_current_template(db)
    history = await _load_history(db)

    # 标记当前版本
    for item in history:
        item["isCurrent"] = item.get("version") == current.version

    # 当前版本也加入列表头部
    current_item = {
        "version": current.version,
        "templates": [t.model_dump() for t in current.templates],
        "updatedAt": current.updatedAt,
        "updatedBy": current.updatedBy,
        "remark": "当前生效版本" if current.version > 0 else "国标默认版本",
        "isCurrent": True,
    }

    # 按版本号倒序
    all_items = [current_item] + sorted(
        history, key=lambda x: x.get("version", 0), reverse=True
    )

    return success(data={"items": all_items, "currentVersion": current.version})


# ---------------------------------------------------------------------------
# POST /configs/weight-templates/{version}/rollback — 回滚到指定版本
# ---------------------------------------------------------------------------


@router.post("/{version}/rollback", response_model=ApiResponse[WeightTemplateSchema])
async def rollback_weight_template(
    version: int,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回滚到指定历史版本（仅 ADMIN）.

    将指定版本设为当前生效版本，并生成新版本号（避免版本号冲突）。

    设计依据：FDS v5.1 §5.2.2
    """
    if version < 0:
        raise BizError(
            code="ERR_INVALID_VERSION",
            message="版本号必须为非负整数",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # version=0 表示回滚到国标默认
    if version == 0:
        default_template = _build_default_template()
        # 保存国标默认为新版本
        result = await _save_template_version(
            db=db,
            templates=default_template.templates,
            operator=user.username,
            remark=f"回滚到国标默认值（源版本 0）",
        )
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("回滚权重模板事务提交失败")
            raise BizError(
                code="ERR_INTERNAL",
                message="事务提交失败，已回滚",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from None

        logger.info(
            "权重模板已回滚到国标默认: new_version=%d, operator=%s",
            result.version,
            user.username,
        )
        return success(data=result.model_dump(), message="已回滚到国标默认值")

    # 从历史中查找指定版本
    history = await _load_history(db)
    target = next((h for h in history if h.get("version") == version), None)
    if target is None:
        raise BizError(
            code="ERR_VERSION_NOT_FOUND",
            message=f"历史版本 {version} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 构建回滚后的模板
    rollback_items = [WeightTemplateItem(**t) for t in target.get("templates", [])]
    if not rollback_items:
        raise BizError(
            code="ERR_VERSION_EMPTY",
            message=f"历史版本 {version} 的模板数据为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 校验回滚版本的权重和
    _validate_core_weight_sum(rollback_items)

    # 保存为新版本（归档当前版本到历史）
    result = await _save_template_version(
        db=db,
        templates=rollback_items,
        operator=user.username,
        remark=f"回滚自版本 {version}",
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("回滚权重模板事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "权重模板已回滚: from_version=%d, to_new_version=%d, operator=%s",
        version,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message=f"已回滚到版本 {version}")


# ---------------------------------------------------------------------------
# POST /configs/weight-templates/restore-defaults — 恢复国标默认值
# ---------------------------------------------------------------------------


@router.post("/restore-defaults", response_model=ApiResponse[WeightTemplateSchema])
async def restore_weight_defaults(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """恢复权重模板为国标默认值（仅 ADMIN）.

    对齐 GB/T 44693.2-2024 附录 C 默认权重：
    - STABLE: a=20, f=30, s=50
    - SLOW:   a=30, f=10, s=60
    - FAST:   a=20, f=50, s=30
    - LOGIC:  a=0,  f=40, s=60

    恢复后生成新版本号并归档当前版本到历史。

    设计依据：FDS v5.1 §5.2.2, GB/T 44693.2-2024 附录 C
    """
    default_template = _build_default_template()
    result = await _save_template_version(
        db=db,
        templates=default_template.templates,
        operator=user.username,
        remark="恢复国标默认值",
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("恢复国标默认权重事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "权重模板已恢复国标默认值: new_version=%d, operator=%s",
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message="已恢复国标默认权重")


__all__ = ["router"]
