"""指标定义管理接口（指标配置-指标定义 Tab：CRUD + 版本化）.

提供 13 项内置 KPI 指标定义（12 项 3+1+8 体系 + 综合评分）与自定义指标
定义的增删改查能力，全部变更自动生成新版本并立即生效。

指标定义存储在 ``sys_config`` 表中（JSON 序列化，与权重模板同模式）：
- ``metric_definition.current`` — 当前生效定义列表（含 version 字段）
- ``metric_definition.history`` — 历史版本列表（含生效/失效时间）

内置指标（isBuiltin=True）：
- 代码 / 类别 / 公式锁定，仅允许编辑名称 / 说明 / 单位 / 启停
- 不可删除（KPI 计算引擎硬依赖，删除将导致评估链路失败）

自定义指标（isBuiltin=False，category=CUSTOM）：
- 仅作为登记项（展示 / 文档化管理），不参与 KPI 计算引擎
- 可增删改

路由清单：
- GET    /api/v1/configs/metric-definitions                 — 获取当前指标定义列表
- POST   /api/v1/configs/metric-definitions                 — 新增自定义指标定义
- PUT    /api/v1/configs/metric-definitions/{metric_code}   — 更新指标定义
- DELETE /api/v1/configs/metric-definitions/{metric_code}   — 删除自定义指标定义
- GET    /api/v1/configs/metric-definitions/history         — 版本历史
- POST   /api/v1/configs/metric-definitions/{version}/rollback — 回滚到指定版本

设计依据：GB/T 44693.2-2024 + 3+1+8 指标体系（v6.1 修订）
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

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    MetricDefinitionCreateRequest,
    MetricDefinitionItem,
    MetricDefinitionListSchema,
    MetricDefinitionUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/metric-definitions", tags=["metric-definition"])

# ---------------------------------------------------------------------------
# sys_config 键常量
# ---------------------------------------------------------------------------

_KEY_CURRENT = "metric_definition.current"
_KEY_HISTORY = "metric_definition.history"
_KEY_DESC_CURRENT = "指标定义当前生效版本（JSON 数组）"
_KEY_DESC_HISTORY = "指标定义历史版本列表（JSON 数组）"

# ---------------------------------------------------------------------------
# 内置指标定义注册表（13 项：综合评分 + 3 核心 + 1 投用 + 8 辅助诊断）
# ---------------------------------------------------------------------------

_BUILTIN_DEFINITIONS: list[dict[str, Any]] = [
    # --- COMPOSITE 综合评分（1 项）---
    {
        "metricCode": "comprehensive_score",
        "metricName": "综合评分",
        "category": "COMPOSITE",
        "formula": "P = (A·a + F·f + S·s) / (a + f + s) × R",
        "description": (
            "回路综合性能评分。A/F/S 为核心质量指标（准确率/快速率/稳定率），"
            "a/f/s 为对应权重（权重总和 100），R 为有效自控率（折扣因子，非加权项）。"
            "对齐 GB/T 44693.2-2024 §6.4.1。"
        ),
        "unit": None,
        "sortOrder": 0,
    },
    # --- CORE 核心质量（3 项）---
    {
        "metricCode": "accuracy_rate",
        "metricName": "准确率",
        "category": "CORE",
        "formula": "max(0, (1 - mean_abs_error / e_max)) × 100",
        "description": (
            "衡量 PV 与 SP 的偏离程度。mean_abs_error 为评估窗内 |PV-SP| 均值，"
            "e_max 为工艺允许最大偏差。对齐 GB/T 44693.2-2024 §6.4.2。"
        ),
        "unit": None,
        "sortOrder": 10,
    },
    {
        "metricCode": "fast_rate",
        "metricName": "快速率",
        "category": "CORE",
        "formula": "ideal_settling_time / actual_settling_time × 100",
        "description": (
            "衡量回路响应速度。理想稳态时间与实际稳态时间之比，"
            "基于 ARMA 模型辨识 + Green 函数法计算。对齐 GB/T 44693.2-2024 §6.4.3。"
        ),
        "unit": None,
        "sortOrder": 11,
    },
    {
        "metricCode": "steady_rate",
        "metricName": "稳定率",
        "category": "CORE",
        "formula": "max(0, (1 - osc_rate - k×std_norm) / (1 - osc_rate)) × 100",
        "description": (
            "衡量回路在稳态下的波动程度。结合振荡率与标准化标准差综合评定。"
            "对齐 GB/T 44693.2-2024 §6.4.4。"
        ),
        "unit": None,
        "sortOrder": 12,
    },
    # --- COMMISSIONING 投用（1 项）---
    {
        "metricCode": "effective_auto_rate",
        "metricName": "有效自控率",
        "category": "COMMISSIONING",
        "formula": "count(auto AND op NOT saturated AND pv_quality=Good) / count(*) × 100",
        "description": (
            "综合考量自动模式、输出未饱和、PV 质量良好三个条件同时满足的占比。"
            "作为综合评分的折扣因子 R，非加权项。对齐 GB/T 44693.2-2024 §6.4.5。"
        ),
        "unit": None,
        "sortOrder": 20,
    },
    # --- AUXILIARY_DIAGNOSTIC 辅助诊断（8 项）---
    {
        "metricCode": "good_value_rate",
        "metricName": "好值率",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "count(pv_quality=Good) / count(*) × 100",
        "description": (
            "PV 质量码为 Good 的数据点占比。支持 TDengine schema（1=Good）"
            "和 OPC DA（192=Good）两种质量码体系。"
        ),
        "unit": None,
        "sortOrder": 30,
    },
    {
        "metricCode": "auto_mode_rate",
        "metricName": "自控率",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "count(mode IN (Auto, Cascade, Remote)) / count(*) × 100",
        "description": (
            "回路处于自动模式（Auto/Cascade/Remote）的时长占比。"
            "投用定义可按回路单独配置（loop_mode_mapping）。"
        ),
        "unit": None,
        "sortOrder": 31,
    },
    {
        "metricCode": "oscillation_rate",
        "metricName": "振荡率",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "min(S_A, S_B) × 100",
        "description": (
            "基于 IAE 零交叉相似性法检测振荡。S_A、S_B 分别为归一化后的 IAE "
            "积分特征值。对齐 GB/T 44693.2-2024 §6.4.6。"
        ),
        "unit": None,
        "sortOrder": 32,
    },
    {
        "metricCode": "saturation_rate",
        "metricName": "饱和率",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "saturated_duration / total_duration × 100",
        "description": (
            "OP 输出处于饱和区间（高限或低限）的时长占比。饱和判定阈值可配置，默认 ±2% 量程范围。"
        ),
        "unit": None,
        "sortOrder": 33,
    },
    {
        "metricCode": "settling_time",
        "metricName": "稳态时间",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "arma_green_function_settling_time",
        "description": (
            "基于 ARMA 模型辨识与 Green 函数法计算的回路实际稳态时间（秒）。"
            "需输入阶跃响应或扰动恢复数据段。"
        ),
        "unit": "s",
        "sortOrder": 34,
    },
    {
        "metricCode": "ideal_settling_time",
        "metricName": "理想稳态时间",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "α × (τ + θ) 或按控制类型默认值",
        "description": (
            "按控制类型（稳定型/慢速型/快速型/逻辑型）的理想稳态时间。"
            "α 为系数，τ 为时间常数，θ 为纯滞后时间。"
        ),
        "unit": "s",
        "sortOrder": 35,
    },
    {
        "metricCode": "stiction_index",
        "metricName": "粘滞指数",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "cross_correlation_based_stiction_detection",
        "description": (
            "基于互相关分析的阀门粘滞检测指数。值域 [0, 1]，>0.5 提示存在粘滞。"
            "对齐 Choudhury-Horch-Shah 方法。"
        ),
        "unit": None,
        "sortOrder": 36,
    },
    {
        "metricCode": "output_trip_index",
        "metricName": "输出行程指数",
        "category": "AUXILIARY_DIAGNOSTIC",
        "formula": "std(op_diff) / range",
        "description": (
            "OP 输出变化量的标准差与量程之比，衡量阀门动作频繁程度。"
            "值过大提示可能存在整定不当或噪声干扰。"
        ),
        "unit": None,
        "sortOrder": 37,
    },
]

_BUILTIN_CODES = {d["metricCode"] for d in _BUILTIN_DEFINITIONS}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


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


def _build_default_definitions() -> MetricDefinitionListSchema:
    """构建内置默认指标定义列表（version=1，首次访问时落库）."""
    items = [MetricDefinitionItem(isBuiltin=True, **d) for d in _BUILTIN_DEFINITIONS]
    return MetricDefinitionListSchema(
        version=1,
        items=items,
        updatedAt=_now_iso(),
        updatedBy="system",
    )


async def _load_current(db: AsyncSession) -> MetricDefinitionListSchema:
    """加载当前生效的指标定义列表.

    若 sys_config 中不存在（首次访问），返回内置默认列表并落库。
    """
    raw = await _get_config_value(db, _KEY_CURRENT)
    if not raw:
        default = _build_default_definitions()
        await _set_config_value(
            db,
            _KEY_CURRENT,
            default.model_dump_json(),
            _KEY_DESC_CURRENT,
            "system",
        )
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("初始化指标定义默认版本失败")
        return default
    try:
        data = json.loads(raw)
        return MetricDefinitionListSchema.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("指标定义当前版本解析失败，回退内置默认: %s", exc)
        return _build_default_definitions()


async def _load_history(db: AsyncSession) -> list[dict[str, Any]]:
    """加载历史版本列表."""
    raw = await _get_config_value(db, _KEY_HISTORY)
    if not raw:
        return []
    try:
        history = json.loads(raw)
        return history if isinstance(history, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("指标定义历史版本解析失败，返回空列表")
        return []


async def _save_version(
    db: AsyncSession,
    items: list[MetricDefinitionItem],
    operator: str,
    remark: str | None = None,
) -> MetricDefinitionListSchema:
    """保存指标定义为新版本并写入历史.

    版本记录含生效时间（effectiveAt）与失效时间（expiresAt）：
    归档旧版本时补充 expiresAt，新版本 effectiveAt=now、expiresAt=None。
    """
    current = await _load_current(db)
    before_snapshot = current.model_dump_json()

    new_version = current.version + 1
    now = _now_iso()
    new_list = MetricDefinitionListSchema(
        version=new_version,
        items=items,
        updatedAt=now,
        updatedBy=operator,
    )

    # 归档当前版本到历史（补充失效时间）
    history = await _load_history(db)
    history.append(
        {
            "version": current.version,
            "items": [i.model_dump() for i in current.items],
            "updatedAt": current.updatedAt,
            "updatedBy": current.updatedBy,
            "remark": remark or f"保存版本 {new_version} 前的快照",
            "isCurrent": False,
            "effectiveAt": current.updatedAt,
            "expiresAt": now,
        }
    )

    await _set_config_value(
        db, _KEY_CURRENT, new_list.model_dump_json(), _KEY_DESC_CURRENT, operator
    )
    await _set_config_value(
        db,
        _KEY_HISTORY,
        json.dumps(history, ensure_ascii=False),
        _KEY_DESC_HISTORY,
        operator,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="METRIC_DEFINITION_SAVE",
        target_type="sys_config",
        target_id=_KEY_CURRENT,
        before_value=before_snapshot,
        after_value=new_list.model_dump_json(),
    )

    return new_list


def _find_item(items: list[MetricDefinitionItem], metric_code: str) -> MetricDefinitionItem | None:
    return next((i for i in items if i.metricCode == metric_code), None)


async def _commit_or_rollback(db: AsyncSession, action: str) -> None:
    """提交事务，失败回滚并抛 BizError."""
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("指标定义 %s 事务提交失败", action)
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None


# ---------------------------------------------------------------------------
# GET /configs/metric-definitions — 获取当前指标定义列表
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[MetricDefinitionListSchema])
async def get_metric_definitions(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取当前生效的指标定义列表（内置 13 项 + 自定义）."""
    definitions = await _load_current(db)
    return success(data=definitions.model_dump())


# ---------------------------------------------------------------------------
# POST /configs/metric-definitions — 新增自定义指标定义
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[MetricDefinitionListSchema])
async def create_metric_definition(
    body: MetricDefinitionCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """新增自定义指标定义（仅 ADMIN，保存后自动生成新版本并生效）.

    自定义指标仅作为登记项（不参与 KPI 计算引擎）。
    """
    current = await _load_current(db)

    if _find_item(current.items, body.metricCode) is not None:
        raise BizError(
            code="ERR_METRIC_CODE_EXISTS",
            message=f"指标代码 {body.metricCode} 已存在",
            status_code=status.HTTP_409_CONFLICT,
        )

    max_order = max((i.sortOrder for i in current.items), default=0)
    new_item = MetricDefinitionItem(
        metricCode=body.metricCode,
        metricName=body.metricName,
        category="CUSTOM",
        formula=body.formula,
        description=body.description,
        unit=body.unit,
        isBuiltin=False,
        isEnabled=True,
        sortOrder=max_order + 1,
        updatedAt=_now_iso(),
        updatedBy=user.username,
    )
    new_items = [*current.items, new_item]

    result = await _save_version(
        db=db,
        items=new_items,
        operator=user.username,
        remark=f"新增自定义指标 {body.metricCode}",
    )
    await _commit_or_rollback(db, "新增")
    logger.info(
        "自定义指标已新增: %s, version=%d, operator=%s",
        body.metricCode,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message="指标定义已新增")


# ---------------------------------------------------------------------------
# PUT /configs/metric-definitions/{metric_code} — 更新指标定义
# ---------------------------------------------------------------------------


@router.put("/{metric_code}", response_model=ApiResponse[MetricDefinitionListSchema])
async def update_metric_definition(
    metric_code: str,
    body: MetricDefinitionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新指标定义（仅 ADMIN，保存后自动生成新版本并生效）.

    内置指标：仅允许更新名称/说明/单位/启停（代码/类别/公式锁定）；
    自定义指标： additionally 允许更新公式。
    """
    current = await _load_current(db)
    target = _find_item(current.items, metric_code)
    if target is None:
        raise BizError(
            code="ERR_METRIC_NOT_FOUND",
            message=f"指标 {metric_code} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    new_items = []
    for item in current.items:
        if item.metricCode != metric_code:
            new_items.append(item)
            continue
        updated = item.model_copy(
            update={
                "metricName": body.metricName or item.metricName,
                "description": (
                    body.description if body.description is not None else item.description
                ),
                "unit": body.unit if body.unit is not None else item.unit,
                "isEnabled": (body.isEnabled if body.isEnabled is not None else item.isEnabled),
                # 公式：内置锁定，自定义可改
                "formula": (
                    body.formula
                    if not item.isBuiltin and body.formula is not None
                    else item.formula
                ),
                "updatedAt": _now_iso(),
                "updatedBy": user.username,
            }
        )
        new_items.append(updated)

    result = await _save_version(
        db=db,
        items=new_items,
        operator=user.username,
        remark=f"更新指标 {metric_code}",
    )
    await _commit_or_rollback(db, "更新")
    logger.info(
        "指标定义已更新: %s, version=%d, operator=%s",
        metric_code,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message="指标定义已更新")


# ---------------------------------------------------------------------------
# DELETE /configs/metric-definitions/{metric_code} — 删除自定义指标定义
# ---------------------------------------------------------------------------


@router.delete("/{metric_code}", response_model=ApiResponse[MetricDefinitionListSchema])
async def delete_metric_definition(
    metric_code: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除自定义指标定义（仅 ADMIN；内置指标不可删除）."""
    current = await _load_current(db)
    target = _find_item(current.items, metric_code)
    if target is None:
        raise BizError(
            code="ERR_METRIC_NOT_FOUND",
            message=f"指标 {metric_code} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if target.isBuiltin or metric_code in _BUILTIN_CODES:
        raise BizError(
            code="ERR_METRIC_BUILTIN_LOCKED",
            message=(
                f"内置指标 {metric_code} 为 KPI 计算引擎依赖项，不可删除；可停用或编辑名称/说明"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    new_items = [i for i in current.items if i.metricCode != metric_code]
    result = await _save_version(
        db=db,
        items=new_items,
        operator=user.username,
        remark=f"删除自定义指标 {metric_code}",
    )
    await _commit_or_rollback(db, "删除")
    logger.info(
        "自定义指标已删除: %s, version=%d, operator=%s",
        metric_code,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message="指标定义已删除")


# ---------------------------------------------------------------------------
# GET /configs/metric-definitions/history — 版本历史
# ---------------------------------------------------------------------------


@router.get("/history", response_model=ApiResponse[dict])
async def get_metric_definition_history(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询指标定义版本历史（仅 ADMIN）.

    返回全部版本（含当前版本），每条含生效时间与失效时间。
    """
    current = await _load_current(db)
    history = await _load_history(db)

    for item in history:
        item["isCurrent"] = False

    current_item = {
        "version": current.version,
        "items": [i.model_dump() for i in current.items],
        "updatedAt": current.updatedAt,
        "updatedBy": current.updatedBy,
        "remark": "当前生效版本",
        "isCurrent": True,
        "effectiveAt": current.updatedAt,
        "expiresAt": None,
    }

    all_items = [current_item] + sorted(history, key=lambda x: x.get("version", 0), reverse=True)
    return success(data={"items": all_items, "currentVersion": current.version})


# ---------------------------------------------------------------------------
# POST /configs/metric-definitions/{version}/rollback — 回滚到指定版本
# ---------------------------------------------------------------------------


@router.post("/{version}/rollback", response_model=ApiResponse[MetricDefinitionListSchema])
async def rollback_metric_definition(
    version: int,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回滚到指定历史版本（仅 ADMIN，回滚生成新版本号保留追溯链）."""
    if version < 1:
        raise BizError(
            code="ERR_INVALID_VERSION",
            message="版本号必须为正整数",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    history = await _load_history(db)
    target = next((h for h in history if h.get("version") == version), None)
    if target is None:
        raise BizError(
            code="ERR_VERSION_NOT_FOUND",
            message=f"历史版本 {version} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    rollback_items = [MetricDefinitionItem.model_validate(i) for i in target.get("items", [])]
    if not rollback_items:
        raise BizError(
            code="ERR_VERSION_EMPTY",
            message=f"历史版本 {version} 的定义数据为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = await _save_version(
        db=db,
        items=rollback_items,
        operator=user.username,
        remark=f"回滚自版本 {version}",
    )
    await _commit_or_rollback(db, "回滚")
    logger.info(
        "指标定义已回滚: from_version=%d, to_new_version=%d, operator=%s",
        version,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message=f"已回滚到版本 {version}")


__all__ = ["router"]
