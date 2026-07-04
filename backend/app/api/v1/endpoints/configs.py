"""批量配置接口 (IDS v3.2 §2.8/§2.9).

提供指标配置与诊断配置的批量读写能力，便于前端配置界面一次性加载/保存
全部指标配置（3+1+8 三段式结构）与全部 8 类诊断标签配置。批量保存时
后端事务化处理，任一项校验失败则全部回滚。

路由清单：
- GET /api/v1/configs/metrics     — 批量获取指标配置
- PUT /api/v1/configs/metrics     — 批量更新指标配置（事务性）
- GET /api/v1/configs/diagnosis   — 批量获取诊断配置
- PUT /api/v1/configs/diagnosis   — 批量更新诊断配置（事务性）

设计依据：IDS §2.8.1/§2.8.2/§2.9.1/§2.9.2
"""

from __future__ import annotations

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
from app.models.diagnosis import DiagnosisConfig
from app.models.metric import MetricConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    DiagnosisConfigBatchResponse,
    DiagnosisConfigBatchUpdateRequest,
    DiagnosisConfigItem,
    DiagnosisConfigUpdateItem,
    MetricConfigBatchResponse,
    MetricConfigBatchUpdateRequest,
    MetricConfigItem,
    MetricConfigUpdateItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs", tags=["configs"])

# v4.0 指标体系 3+1+8 结构（对齐 IDS §2.8）
# 3 核心指标（参与权重校验）
_CORE_METRIC_CODES: tuple[str, ...] = (
    "accuracy_rate",
    "fast_rate",
    "steady_rate",
)
# 1 投用指标（折扣因子）
_COMMISSIONING_METRIC_CODE = "effective_auto_rate"
# 8 辅助诊断指标
_AUXILIARY_METRIC_CODES: tuple[str, ...] = (
    "good_value_rate",
    "oscillation_rate",
    "saturation_rate",
    "stiction_index",
    "overaggressive_index",
    "overconservative_index",
    "disturbance_index",
    "quality_abnormal_rate",
)

# 各辅助诊断指标对应的算法版本（对齐 IDS §2.8.1）
_AUX_ALGORITHM_VERSIONS: dict[str, str] = {
    "good_value_rate": "KPI_CALC_v1.0",
    "oscillation_rate": "KPI_CALC_v1.0",
    "saturation_rate": "KPI_CALC_v1.0",
    "stiction_index": "STICTION_CH_v1.0",
    "overaggressive_index": "OVERAGGRESSIVE_PID_v1.0",
    "overconservative_index": "OVERCONSERVATIVE_PID_v1.0",
    "disturbance_index": "DISTURBANCE_SPEC_v1.0",
    "quality_abnormal_rate": "QUALITY_CHECK_v1.0",
}

# 各指标中文名（用于响应默认值）
_METRIC_NAMES: dict[str, str] = {
    "accuracy_rate": "准确率",
    "fast_rate": "快速率",
    "steady_rate": "稳定率",
    "effective_auto_rate": "有效自控率",
    "good_value_rate": "好值率",
    "oscillation_rate": "振荡率",
    "saturation_rate": "饱和率",
    "stiction_index": "粘滞指数",
    "overaggressive_index": "过激指数",
    "overconservative_index": "过保守指数",
    "disturbance_index": "外扰指数",
    "quality_abnormal_rate": "质量异常率",
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _metric_category(metric_code: str) -> str | None:
    """根据 metric_code 判定类别（CORE/COMMISSIONING/AUXILIARY_DIAGNOSTIC）."""
    if metric_code in _CORE_METRIC_CODES:
        return "CORE"
    if metric_code == _COMMISSIONING_METRIC_CODE:
        return "COMMISSIONING"
    if metric_code in _AUXILIARY_METRIC_CODES:
        return "AUXILIARY_DIAGNOSTIC"
    return None


def _metric_to_response_dict(c: MetricConfig) -> dict[str, Any]:
    """将 MetricConfig ORM 转为响应字典（含 category/isDiscountFactor）。"""
    category = _metric_category(c.metric_code)
    is_discount = c.metric_code == _COMMISSIONING_METRIC_CODE
    return {
        "metricId": str(c.id),
        "metricKey": c.metric_code,
        "metricName": c.metric_name,
        "category": category,
        "isDiscountFactor": is_discount if is_discount else None,
        "formula": c.formula,
        "weight": float(c.weight) if c.weight is not None else None,
        "threshold": c.threshold,
        "controlType": c.control_type,
        "isEnabled": bool(c.is_enabled) if c.is_enabled is not None else True,
        "description": None,
        "algorithmVersion": _AUX_ALGORITHM_VERSIONS.get(c.metric_code, "KPI_CALC_v1.0"),
        "updatedAt": c.updated_at.isoformat() if c.updated_at else None,
        "updatedBy": c.updated_by,
    }


def _diagnosis_to_response_dict(c: DiagnosisConfig) -> dict[str, Any]:
    """将 DiagnosisConfig ORM 转为响应字典."""
    return {
        "diagId": str(c.id),
        "diagKey": c.diag_code,
        "diagName": c.diag_name,
        "label": c.diag_code,  # diag_code 即为 label 枚举值
        "algorithmType": c.algorithm_type,
        "calcMethod": c.calc_method,
        "params": c.params,
        "threshold": c.threshold,
        "isEnabled": bool(c.is_enabled) if c.is_enabled is not None else True,
        "algorithmVersion": None,
        "updatedAt": c.updated_at.isoformat() if c.updated_at else None,
        "updatedBy": c.updated_by,
    }


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


async def _apply_metric_update(
    db: AsyncSession,
    config: MetricConfig,
    item: MetricConfigUpdateItem,
    operator: str,
) -> None:
    """将单个更新项应用到 ORM 对象（不提交事务）."""
    if item.formula is not None:
        config.formula = item.formula
    if item.threshold is not None:
        config.threshold = item.threshold
    if item.controlType is not None:
        config.control_type = item.controlType
    if item.isEnabled is not None:
        config.is_enabled = item.isEnabled
    if item.description is not None:
        # description 字段不存在于 MetricConfig，仅在响应中保留
        pass
    # weight 仅对核心指标生效；投用/辅助诊断指标的 weight 固定为 None
    if item.weight is not None and config.metric_code in _CORE_METRIC_CODES:
        config.weight = item.weight
    config.updated_by = operator
    config.updated_at = _now_naive()
    config.version = (config.version or 1) + 1


async def _apply_diagnosis_update(
    db: AsyncSession,
    config: DiagnosisConfig,
    item: DiagnosisConfigUpdateItem,
    operator: str,
) -> None:
    """将单个诊断配置更新项应用到 ORM 对象（不提交事务）."""
    if item.algorithmType is not None:
        config.algorithm_type = item.algorithmType
    if item.calcMethod is not None:
        config.calc_method = item.calcMethod
    if item.params is not None:
        config.params = item.params
    if item.threshold is not None:
        config.threshold = item.threshold
    if item.isEnabled is not None:
        config.is_enabled = item.isEnabled
    config.updated_by = operator
    config.updated_at = _now_naive()
    config.version = (config.version or 1) + 1


# ---------------------------------------------------------------------------
# §2.8.1 GET /configs/metrics — 批量获取指标配置
# ---------------------------------------------------------------------------


@router.get("/metrics", response_model=ApiResponse[MetricConfigBatchResponse])
async def batch_get_metric_configs(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """批量获取指标配置（3+1+8 三段式结构）.

    返回全部 12 项指标配置（3 核心 + 1 投用 + 8 辅助诊断），
    含 ``coreTotalWeight`` 与 ``coreWeightValid`` 标识核心权重校验状态。

    设计依据：IDS §2.8.1
    """
    result = await db.execute(select(MetricConfig))
    configs = list(result.scalars().all())

    core_items: list[MetricConfigItem] = []
    commissioning: MetricConfigItem | None = None
    aux_items: list[MetricConfigItem] = []

    for c in configs:
        item_dict = _metric_to_response_dict(c)
        item = MetricConfigItem.model_validate(item_dict)
        category = item.category
        if category == "CORE":
            core_items.append(item)
        elif category == "COMMISSIONING":
            commissioning = item
        elif category == "AUXILIARY_DIAGNOSTIC":
            aux_items.append(item)

    # 核心指标权重总和校验
    core_weights = [c.weight for c in core_items if c.weight is not None and c.isEnabled]
    core_total = sum(core_weights) if core_weights else 0.0
    core_valid = abs(core_total - 100.0) < 1e-6 if core_weights else True

    resp = MetricConfigBatchResponse(
        coreMetrics=core_items,
        commissioningMetric=commissioning,
        auxiliaryDiagnosticMetrics=aux_items,
        coreTotalWeight=round(core_total, 4),
        coreWeightValid=core_valid,
        structureVersion="3+1+8",
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# §2.8.2 PUT /configs/metrics — 批量更新指标配置（事务性）
# ---------------------------------------------------------------------------


@router.put("/metrics", response_model=ApiResponse[MetricConfigBatchResponse])
async def batch_update_metric_configs(
    body: MetricConfigBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量更新指标配置（事务性，任一项失败全部回滚）.

    权重校验仅针对 3 项核心指标（accuracy_rate/fast_rate/steady_rate），
    总和须为 100%，否则返回 ``ERR_METRIC_WEIGHT_SUM``。

    设计依据：IDS §2.8.2
    """
    # 收集所有更新项
    all_items: list[MetricConfigUpdateItem] = []
    if body.coreMetrics:
        all_items.extend(body.coreMetrics)
    if body.commissioningMetric is not None:
        all_items.append(body.commissioningMetric)
    if body.auxiliaryDiagnosticMetrics:
        all_items.extend(body.auxiliaryDiagnosticMetrics)

    if not all_items:
        raise BizError(
            code="ERR_VALIDATION",
            message="更新列表不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 一次性查询所有涉及的配置
    metric_ids = [item.metricId for item in all_items]
    result = await db.execute(select(MetricConfig).where(MetricConfig.id.in_(metric_ids)))
    config_map: dict[str, MetricConfig] = {str(c.id): c for c in result.scalars().all()}

    # 校验所有 metricId 都存在
    missing = [mid for mid in metric_ids if mid not in config_map]
    if missing:
        raise BizError(
            code="ERR_METRIC_NOT_FOUND",
            message=f"指标配置不存在: {missing[0]}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 应用所有更新（暂不提交）
    audit_logs: list[tuple[MetricConfig, MetricConfig]] = []
    for item in all_items:
        config = config_map[item.metricId]
        before_snapshot = _metric_to_response_dict(config)
        await _apply_metric_update(db, config, item, user.username)
        audit_logs.append((config, config))  # 用于审计
        # 写入审计日志
        after_snapshot = _metric_to_response_dict(config)
        import json

        await _write_audit(
            db=db,
            operator=user.username,
            operation_type="METRIC_CONFIG_BATCH_UPDATE",
            target_type="metric_config",
            target_id=str(config.id),
            before_value=json.dumps(before_snapshot, ensure_ascii=False, default=str),
            after_value=json.dumps(after_snapshot, ensure_ascii=False, default=str),
        )

    # 核心指标权重总和校验：本次更新后，启用的核心指标权重总和须为 100
    core_weights = [
        float(c.weight)
        for c in config_map.values()
        if c.metric_code in _CORE_METRIC_CODES and c.is_enabled and c.weight is not None
    ]
    if core_weights:
        total = sum(core_weights)
        if abs(total - 100.0) >= 1e-6:
            await db.rollback()
            raise BizError(
                code="ERR_METRIC_WEIGHT_SUM",
                message=(f"核心指标权重总和必须为 100，当前为 {total:.2f}；事务已回滚"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("批量更新指标配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    # 失效缓存
    try:
        from app.services.performance import _invalidate_metric_config_cache

        await _invalidate_metric_config_cache()
    except Exception:
        logger.warning("失效指标配置缓存失败", exc_info=True)

    # 重新查询返回完整结构
    result = await db.execute(select(MetricConfig))
    configs = list(result.scalars().all())
    core_items: list[MetricConfigItem] = []
    commissioning: MetricConfigItem | None = None
    aux_items: list[MetricConfigItem] = []
    for c in configs:
        item = MetricConfigItem.model_validate(_metric_to_response_dict(c))
        if item.category == "CORE":
            core_items.append(item)
        elif item.category == "COMMISSIONING":
            commissioning = item
        elif item.category == "AUXILIARY_DIAGNOSTIC":
            aux_items.append(item)

    core_weights_after = [c.weight for c in core_items if c.weight is not None and c.isEnabled]
    core_total = sum(core_weights_after) if core_weights_after else 0.0

    resp = MetricConfigBatchResponse(
        coreMetrics=core_items,
        commissioningMetric=commissioning,
        auxiliaryDiagnosticMetrics=aux_items,
        coreTotalWeight=round(core_total, 4),
        coreWeightValid=abs(core_total - 100.0) < 1e-6 if core_weights_after else True,
        structureVersion="3+1+8",
        updatedCount=len(all_items),
    )

    logger.info(
        "批量更新指标配置成功: updated=%d, operator=%s",
        len(all_items),
        user.username,
    )
    return success(data=resp.model_dump(), message="批量更新成功")


# ---------------------------------------------------------------------------
# §2.9.1 GET /configs/diagnosis — 批量获取诊断配置
# ---------------------------------------------------------------------------


@router.get("/diagnosis", response_model=ApiResponse[DiagnosisConfigBatchResponse])
async def batch_get_diagnosis_configs(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """批量获取诊断配置（全部 8 类诊断标签）.

    设计依据：IDS §2.9.1
    """
    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.diag_code.asc()))
    configs = list(result.scalars().all())

    items = [DiagnosisConfigItem.model_validate(_diagnosis_to_response_dict(c)) for c in configs]

    resp = DiagnosisConfigBatchResponse(items=items)
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# §2.9.2 PUT /configs/diagnosis — 批量更新诊断配置（事务性）
# ---------------------------------------------------------------------------


@router.put("/diagnosis", response_model=ApiResponse[DiagnosisConfigBatchResponse])
async def batch_update_diagnosis_configs(
    body: DiagnosisConfigBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量更新诊断配置（事务性，任一项失败全部回滚）.

    设计依据：IDS §2.9.2
    """
    if not body.items:
        raise BizError(
            code="ERR_VALIDATION",
            message="更新列表不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    diag_ids = [item.diagId for item in body.items]
    result = await db.execute(select(DiagnosisConfig).where(DiagnosisConfig.id.in_(diag_ids)))
    config_map: dict[str, DiagnosisConfig] = {str(c.id): c for c in result.scalars().all()}

    missing = [did for did in diag_ids if did not in config_map]
    if missing:
        raise BizError(
            code="ERR_DIAG_CONFIG_NOT_FOUND",
            message=f"诊断配置不存在: {missing[0]}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    import json

    # 应用所有更新（暂不提交）
    for item in body.items:
        config = config_map[item.diagId]
        before_snapshot = _diagnosis_to_response_dict(config)
        await _apply_diagnosis_update(db, config, item, user.username)
        after_snapshot = _diagnosis_to_response_dict(config)
        await _write_audit(
            db=db,
            operator=user.username,
            operation_type="DIAG_CONFIG_BATCH_UPDATE",
            target_type="diagnosis_config",
            target_id=str(config.id),
            before_value=json.dumps(before_snapshot, ensure_ascii=False, default=str),
            after_value=json.dumps(after_snapshot, ensure_ascii=False, default=str),
        )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("批量更新诊断配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    # 重新查询返回完整列表
    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.diag_code.asc()))
    configs = list(result.scalars().all())
    items = [DiagnosisConfigItem.model_validate(_diagnosis_to_response_dict(c)) for c in configs]

    resp = DiagnosisConfigBatchResponse(items=items, updatedCount=len(body.items))

    logger.info(
        "批量更新诊断配置成功: updated=%d, operator=%s",
        len(body.items),
        user.username,
    )
    return success(data=resp.model_dump(), message="批量更新成功")


__all__ = ["router"]
