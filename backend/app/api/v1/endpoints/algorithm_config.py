"""指标算法参数配置接口（P0-B 配置化基础设施）.

提供指标算法参数（3 指标 × 4 控制类型）的查询/更新。

存储在 ``algorithm_parameter`` 表中，保存后立即刷新进程内缓存，
计算器热路径通过 ``get_algorithm_params()`` 读取，不查库。

路由清单：
- GET  /api/v1/configs/algorithm-params           — 获取全部算法参数合并视图
- GET  /api/v1/configs/algorithm-params/{metricCode} — 获取指定指标的算法参数
- PUT  /api/v1/configs/algorithm-params/{metricCode} — 更新指定指标的算法参数（仅 ADMIN）

P2-02 起本 PUT 作为**兼容写入通道**纳入统一配置发布：
- 可选 ``expectedRevision`` 乐观锁（不传保持旧行为；失配 409 携带最新版本）；
- 写入经 ``config_publish.note_legacy_sync`` 推进统一 revision 并入发布账本
  （LEGACY_SYNC）+ SysAuditLog 审计（修复 P2-01 遗留 before_value=None，
  before/after/原因/操作者/影响范围/回退版本全量入审计）；
- commit 后广播 ``CONFIG_REVISION_CHANNEL`` 加速通知（跨进程一致性 CFG-03），
  并同步本进程运行时缓存；
- 重置默认（resetControlTypes）响应 message 附"重置低层后高层仍生效"的解释
  （P2-01 遗留收口：metric_config.threshold / TEMPLATE / NODE / LOOP 层的剩余覆盖）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.algorithm_parameter import AlgorithmParameter
from app.models.audit import SysAuditLog
from app.models.config_publish import ConfigOverride
from app.models.metric import MetricConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    AlgorithmParamsControlItem,
    AlgorithmParamsMetricGroup,
    AlgorithmParamsSaveRequest,
    AlgorithmParamsSchema,
)
from app.services import algorithm_config as algo_config_service
from app.services import config_publish as config_publish_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/algorithm-params", tags=["algorithm-config"])

#: 指标代码 → 中文名映射（页面标题「中文（english_code）」格式）
#: P2-01：与 _DEFAULTS/PARAM_META 注册表 8 指标全集对齐（补 stability_rate/saturation_rate）
_METRIC_NAMES = {
    "oscillation_rate": "振荡率",
    "fast_rate": "快速率",
    "accuracy_rate": "准确率",
    "settling_time": "稳态时间",
    "effective_auto_rate": "有效自控率",
    "output_trip_index": "输出行程指数",
    "stability_rate": "稳定率",
    "saturation_rate": "饱和率",
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
    """更新指定指标的算法参数（仅 ADMIN；DEC-03 配置发布为 ADMIN 职责）.

    部分覆盖：仅更新传入的控制类型和参数键，未传的保持原值。
    P2-02 兼容通道语义：
    - ``expectedRevision`` 可选乐观锁（失配 409 + 最新版本）；
    - 同一事务内推进统一 revision（LEGACY_SYNC 发布账本）+ 审计
      （before/after/原因/操作者/影响范围/回退版本全量）；
    - commit 后广播 CONFIG_REVISION_CHANNEL + 刷新本进程运行时缓存；
    - resetControlTypes 重置后 message 附更高层剩余覆盖解释。
    """
    if metric_code not in algo_config_service._DEFAULTS:
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"未知指标代码: {metric_code}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # P2-02：expectedRevision 乐观锁前置校验（旧前端不传保持原行为）
    current_revision = await config_publish_service.get_current_revision(db)
    if body.expectedRevision is not None and body.expectedRevision != current_revision:
        raise BizError(
            code="ERR_CONFIG_REVISION_CONFLICT",
            message=(
                f"配置版本冲突：expectedRevision={body.expectedRevision}，"
                f"当前最新 revision={current_revision}（并发发布已被他人提交，请以最新版本重试）"
            ),
            status_code=status.HTTP_409_CONFLICT,
            data={"currentRevision": current_revision},
        )

    now = _now_naive()

    # P2-02：记录变更前状态（修复 P2-01 遗留 before_value=None）——
    # 覆盖本次请求涉及的全部控制类型（reset + items）
    touched_cts: set[str] = set(body.resetControlTypes)
    touched_cts.update(item.controlType for item in body.items)
    before_by_ct: dict[str, dict | None] = {}
    for ct in sorted(touched_cts):
        row = (
            await db.execute(
                select(AlgorithmParameter).where(
                    AlgorithmParameter.metric_code == metric_code,
                    AlgorithmParameter.control_type == ct,
                )
            )
        ).scalar_one_or_none()
        before_by_ct[ct] = dict(row.params) if row and row.params else None

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

        # 查询现有记录（先查再校验：组合约束需在合并后视图上求值）
        existing_result = await db.execute(
            select(AlgorithmParameter).where(
                AlgorithmParameter.metric_code == metric_code,
                AlgorithmParameter.control_type == ct,
            )
        )
        existing = existing_result.scalar_one_or_none()

        # 整改 F1 + P2-01 CFG-05：服务端键白名单/类型/有限数/值域/组合约束校验
        # （防越界值写入 JSONB 直供计算管线）。base=该控制类型保存前的
        # Layer1+2 有效参数，拦截"单次合法、合并后档位颠倒"的写入。
        base = {
            **algo_config_service.get_default_params(metric_code, ct),
            **(dict(existing.params) if existing and existing.params else {}),
        }
        errors = algo_config_service.validate_metric_params(metric_code, params, base=base)
        if errors:
            raise BizError(
                code="ERR_PARAM_INVALID",
                message="；".join(errors),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

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

    # 变更后状态（本次涉及的控制类型）
    after_by_ct: dict[str, dict] = {ct: {} for ct in before_by_ct}
    for ct in sorted(touched_cts):
        row = (
            await db.execute(
                select(AlgorithmParameter).where(
                    AlgorithmParameter.metric_code == metric_code,
                    AlgorithmParameter.control_type == ct,
                )
            )
        ).scalar_one_or_none()
        after_by_ct[ct] = dict(row.params) if row and row.params else {}

    # 审计日志（P2-02：before_value 补真实变更前状态）
    audit = SysAuditLog(
        operator=user.username,
        operation_type="ALGORITHM_PARAMS_UPDATE",
        target_type="algorithm_parameter",
        target_id=metric_code,
        before_value=json.dumps(before_by_ct, ensure_ascii=False, default=str, sort_keys=True),
        after_value=str(body.model_dump(by_alias=True)),
        operated_at=now,
    )
    db.add(audit)

    # P2-02：兼容通道发布登记（同一事务推进统一 revision + 发布账本 + 审计）
    new_revision = await config_publish_service.note_legacy_sync(
        db,
        metric_code=metric_code,
        before=before_by_ct,
        after=after_by_ct,
        reason=body.reason or "算法参数更新（algorithm-params 兼容通道）",
        operator=user.username,
        expected_revision=body.expectedRevision,
        control_types=sorted(touched_cts),
    )

    try:
        await db.commit()
    except IntegrityError:
        # P2-02：并发发布抢先提交同一 revision（唯一约束兜底）
        await db.rollback()
        latest = await config_publish_service.get_current_revision(db)
        raise BizError(
            code="ERR_CONFIG_REVISION_CONFLICT",
            message=(
                f"配置版本冲突：并发发布抢先提交了同一 revision（最新 revision={latest}），"
                "请以最新版本重试"
            ),
            status_code=status.HTTP_409_CONFLICT,
            data={"currentRevision": latest},
        ) from None
    except Exception:
        await db.rollback()
        logger.exception("更新算法参数配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    # 刷新运行时缓存 + 广播加速通知（CFG-03：跨进程一致性）
    await config_publish_service.sync_runtime_cache(db)
    await config_publish_service.broadcast_config_revision(new_revision, source="algorithm-params")

    logger.info(
        "算法参数已更新: metric=%s, control_types=%s, operator=%s, revision=%s",
        metric_code,
        [i.controlType for i in body.items],
        user.username,
        new_revision,
    )

    # P2-01 遗留收口：重置默认后，更高层（metric_config.threshold 与
    # TEMPLATE/NODE/LOOP 覆盖层）仍生效的键给出可解释说明
    message = "算法参数已更新"
    if body.resetControlTypes:
        explanation = await _explain_reset_for_global_channel(db, metric_code, before_by_ct)
        if explanation:
            message = f"算法参数已更新；重置的控制类型中以下键仍被更高层覆盖: {explanation}"
        else:
            message = "算法参数已更新；重置的控制类型已回落算法默认（无更高层覆盖）"

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
        message=message,
    )


async def _explain_reset_for_global_channel(
    db: AsyncSession, metric_code: str, before_by_ct: dict[str, dict | None]
) -> list[str]:
    """重置全局层（algorithm_parameter）后的高层剩余覆盖解释.

    逐键列出被重置层原值中仍被更高层遮盖的键：
    - metric_config.threshold（DEFAULT 层内的全局指标级覆盖，旧三层链顶层）；
    - config_override TEMPLATE/NODE/LOOP 层（统一作用域链更高层，任意作用域）。
    """
    explanations: list[str] = []
    threshold = (
        await db.execute(
            select(MetricConfig.threshold).where(
                MetricConfig.metric_code == metric_code,
                MetricConfig.threshold.is_not(None),
            )
        )
    ).scalar_one_or_none()
    threshold_map = dict(threshold) if isinstance(threshold, dict) else {}

    override_rows = (
        (
            await db.execute(
                select(ConfigOverride).where(
                    ConfigOverride.metric_code == metric_code,
                    ConfigOverride.is_enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    for ct, before_params in before_by_ct.items():
        if not before_params:
            continue
        for key in before_params:
            if key in threshold_map:
                explanations.append(
                    f"[{ct}] {key}={threshold_map[key]}（metric_config.threshold 全局指标级覆盖）"
                )
            for row in override_rows:
                if row.params and key in row.params:
                    explanations.append(
                        f"[{ct}] {key}={row.params[key]}（{row.layer} 层 {row.scope_id} 覆盖）"
                    )
    return explanations


__all__ = ["router"]
