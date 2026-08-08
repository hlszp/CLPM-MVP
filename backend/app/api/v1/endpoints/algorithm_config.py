"""指标算法参数配置接口（P0-B 配置化基础设施）.

提供指标算法参数（3 指标 × 4 控制类型）的查询/更新。

存储在 ``algorithm_parameter`` 表中，保存后立即刷新进程内缓存，
计算器热路径通过 ``get_algorithm_params()`` 读取，不查库。

路由清单：
- GET  /api/v1/configs/algorithm-params           — 获取全部算法参数合并视图
- GET  /api/v1/configs/algorithm-params/{metricCode} — 获取指定指标的算法参数
- PUT  /api/v1/configs/algorithm-params/{metricCode} — 更新指定指标的算法参数（仅 ADMIN）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.algorithm_parameter import AlgorithmParameter
from app.models.audit import SysAuditLog
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    AlgorithmParamsControlItem,
    AlgorithmParamsMetricGroup,
    AlgorithmParamsSaveRequest,
    AlgorithmParamsSchema,
)
from app.services import algorithm_config as algo_config_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/algorithm-params", tags=["algorithm-config"])

#: 指标代码 → 中文名映射
_METRIC_NAMES = {
    "oscillation_rate": "振荡率",
    "fast_rate": "快速率",
    "accuracy_rate": "准确率",
}


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# GET /configs/algorithm-params — 全部算法参数合并视图
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[AlgorithmParamsSchema])
async def get_all_algorithm_params(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取全部指标算法参数配置的合并视图.

    返回 3 个指标 × 4 控制类型的参数生效值（算法默认 + algorithm_parameter 表覆盖
    + metric_config.threshold 覆盖），含每项是否被覆盖标记。
    """
    view = algo_config_service.build_merged_view()

    metrics: list[AlgorithmParamsMetricGroup] = []
    for metric_code, ct_map in view.items():
        items = [
            AlgorithmParamsControlItem(
                controlType=ct,
                params=ct_data["params"],
                defaults=ct_data["defaults"],
                overridden=ct_data["overridden"],
            )
            for ct, ct_data in ct_map.items()
        ]
        metrics.append(
            AlgorithmParamsMetricGroup(
                metricCode=metric_code,
                metricName=_METRIC_NAMES.get(metric_code, metric_code),
                items=items,
                paramMeta=algo_config_service.build_param_meta(metric_code),
            )
        )

    # 查询最近更新时间
    latest_result = await db.execute(
        select(AlgorithmParameter.updated_at, AlgorithmParameter.updated_by)
        .order_by(AlgorithmParameter.updated_at.desc())
        .limit(1)
    )
    latest = latest_result.first()

    schema = AlgorithmParamsSchema(
        metrics=metrics,
        updatedAt=latest.updated_at.isoformat() if latest and latest.updated_at else None,
        updatedBy=latest.updated_by if latest else None,
    )
    return success(data=schema.model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# GET /configs/algorithm-params/{metricCode} — 单个指标算法参数
# ---------------------------------------------------------------------------


@router.get("/{metric_code}", response_model=ApiResponse[AlgorithmParamsMetricGroup])
async def get_metric_algorithm_params(
    metric_code: str,
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取指定指标的算法参数（4 控制类型）."""
    view = algo_config_service.build_merged_view()
    ct_map = view.get(metric_code)
    if ct_map is None:
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"未知指标代码: {metric_code}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    items = [
        AlgorithmParamsControlItem(
            controlType=ct,
            params=ct_data["params"],
            defaults=ct_data["defaults"],
            overridden=ct_data["overridden"],
        )
        for ct, ct_data in ct_map.items()
    ]
    group = AlgorithmParamsMetricGroup(
        metricCode=metric_code,
        metricName=_METRIC_NAMES.get(metric_code, metric_code),
        items=items,
        paramMeta=algo_config_service.build_param_meta(metric_code),
    )
    return success(data=group.model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# PUT /configs/algorithm-params/{metricCode} — 更新算法参数
# ---------------------------------------------------------------------------


@router.put("/{metric_code}", response_model=ApiResponse[AlgorithmParamsMetricGroup])
async def save_metric_algorithm_params(
    metric_code: str,
    body: AlgorithmParamsSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新指定指标的算法参数（仅 ADMIN）.

    部分覆盖：仅更新传入的控制类型和参数键，未传的保持原值。
    保存后写审计日志并刷新进程内缓存（立即生效，无需重启）。
    """
    if metric_code not in algo_config_service._DEFAULTS:
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"未知指标代码: {metric_code}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    now = _now_naive()

    # 整改 F6：重置默认——将指定控制类型的覆盖清空（params={}，合并视图回落算法默认）
    for ct in body.resetControlTypes:
        existing_result = await db.execute(
            select(AlgorithmParameter).where(
                AlgorithmParameter.metric_code == metric_code,
                AlgorithmParameter.control_type == ct,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            existing.params = {}
            existing.updated_by = user.username
            existing.updated_at = now
            existing.version += 1
        else:
            db.add(
                AlgorithmParameter(
                    metric_code=metric_code,
                    control_type=ct,
                    params={},
                    description=f"{_METRIC_NAMES.get(metric_code, metric_code)} 算法参数",
                    is_enabled=True,
                    updated_by=user.username,
                    updated_at=now,
                    version=1,
                )
            )

    # 逐控制类型 UPSERT
    for item in body.items:
        ct = item.controlType
        params = item.params
        if not params:
            continue

        # 整改 F1：服务端键白名单 + 值域校验（防越界值写入 JSONB 直供计算管线）
        errors = algo_config_service.validate_metric_params(metric_code, params)
        if errors:
            raise BizError(
                code="ERR_PARAM_INVALID",
                message="；".join(errors),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # 查询现有记录
        existing_result = await db.execute(
            select(AlgorithmParameter).where(
                AlgorithmParameter.metric_code == metric_code,
                AlgorithmParameter.control_type == ct,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            # 合并参数（部分覆盖）
            merged_params = dict(existing.params or {})
            merged_params.update(params)
            existing.params = merged_params
            existing.updated_by = user.username
            existing.updated_at = now
            existing.version += 1
        else:
            # 新建记录
            new_record = AlgorithmParameter(
                metric_code=metric_code,
                control_type=ct,
                params=params,
                description=f"{_METRIC_NAMES.get(metric_code, metric_code)} 算法参数",
                is_enabled=True,
                updated_by=user.username,
                updated_at=now,
                version=1,
            )
            db.add(new_record)

    # 审计日志
    audit = SysAuditLog(
        operator=user.username,
        operation_type="ALGORITHM_PARAMS_UPDATE",
        target_type="algorithm_parameter",
        target_id=metric_code,
        before_value=None,
        after_value=str(body.model_dump(by_alias=True)),
        operated_at=now,
    )
    db.add(audit)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("更新算法参数配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    # 刷新运行时缓存
    table_overrides = await algo_config_service.load_stored_config(db)
    metric_thresholds = await algo_config_service.load_metric_thresholds(db)
    algo_config_service.apply_runtime(table_overrides, metric_thresholds)

    logger.info(
        "算法参数已更新: metric=%s, control_types=%s, operator=%s",
        metric_code,
        [i.controlType for i in body.items],
        user.username,
    )

    # 返回更新后的合并视图
    view = algo_config_service.build_merged_view()
    ct_map = view.get(metric_code, {})
    items = [
        AlgorithmParamsControlItem(
            controlType=ct,
            params=ct_data["params"],
            defaults=ct_data["defaults"],
            overridden=ct_data["overridden"],
        )
        for ct, ct_data in ct_map.items()
    ]
    group = AlgorithmParamsMetricGroup(
        metricCode=metric_code,
        metricName=_METRIC_NAMES.get(metric_code, metric_code),
        items=items,
        paramMeta=algo_config_service.build_param_meta(metric_code),
    )
    return success(
        data=group.model_dump(by_alias=True),
        message="算法参数已更新",
    )


__all__ = ["router"]
