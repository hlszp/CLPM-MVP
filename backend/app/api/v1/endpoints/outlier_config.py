"""8 类异常值检测参数配置接口.

提供异常值检测阈值（5 控制类型 × 7 参数）与 8 类检测启停开关的查询/更新。

存储方式参照 confidence-thresholds：sys_config 表 JSON 序列化，
key = ``outlier_params.current``（结构见 ``app.services.preprocessing.outlier_params``）。

保存后立即生效：刷新 thresholds 模块进程内缓存（覆盖合并 + 检测开关），
Pipeline / DataPlanner / 诊断引擎的热路径经 ``get_threshold()`` /
``is_detector_enabled()`` 读取缓存，不查库。

路由清单：
- GET /api/v1/configs/outlier-params — 获取合并视图（默认值 + 覆盖标记）
- PUT /api/v1/configs/outlier-params — 更新参数覆盖与检测开关（仅 ADMIN）
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import OutlierParamsSaveRequest, OutlierParamsSchema
from app.services.preprocessing import outlier_params as outlier_params_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/outlier-params", tags=["outlier-config"])


# ---------------------------------------------------------------------------
# GET /configs/outlier-params — 获取合并视图
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[OutlierParamsSchema])
async def get_outlier_params(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取 8 类异常值检测参数配置的合并视图.

    返回 5 个控制类型参数的生效值（算法默认叠加 sys_config 覆盖，
    含每项是否被覆盖标记）与 8 类检测开关生效值（默认全部启用）。
    若未配置过，返回纯算法默认。
    """
    stored = await outlier_params_service.load_stored_config(db)
    view = outlier_params_service.build_merged_view(stored)
    return success(data=view.model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# PUT /configs/outlier-params — 更新参数覆盖与检测开关
# ---------------------------------------------------------------------------


@router.put("", response_model=ApiResponse[OutlierParamsSchema])
async def save_outlier_params(
    body: OutlierParamsSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新异常值检测参数覆盖与检测开关（仅 ADMIN）.

    校验规则（Schema 层）：
    - pct 类参数（frozenStdPct/jumpThresholdPct/spikeThresholdPct）∈ [0, 1]
    - 窗口点数（frozenWindowPoints）与连续有效最短段 ≥ 2
    - 噪声截止频率（noiseCutoffHz）> 0
    - 控制类型必须为 FC/PC/TC/LC/CC，开关键必须为 8 类检测键之一

    保存后写审计日志并刷新进程内缓存（立即生效，无需重启）。
    """
    before_raw = await outlier_params_service.get_config_value(
        db, outlier_params_service.SYS_CONFIG_KEY
    )

    stored_new = outlier_params_service.build_stored_payload(
        thresholds=body.thresholds,
        switches=body.switches,
        operator=user.username,
    )
    after_json = json.dumps(stored_new, ensure_ascii=False)

    await outlier_params_service.set_config_value(
        db,
        outlier_params_service.SYS_CONFIG_KEY,
        after_json,
        outlier_params_service.SYS_CONFIG_DESC,
        user.username,
    )

    await outlier_params_service.write_audit(
        db=db,
        operator=user.username,
        operation_type="OUTLIER_PARAMS_UPDATE",
        target_type="sys_config",
        target_id=outlier_params_service.SYS_CONFIG_KEY,
        before_value=before_raw,
        after_value=after_json,
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("更新异常值检测参数配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    # 刷新运行时进程内缓存（覆盖合并 + 检测开关，立即生效，无需重启）
    outlier_params_service.apply_runtime(stored_new)

    logger.info(
        "异常值检测参数已更新: overridden_types=%s, disabled=%s, operator=%s",
        sorted(stored_new["thresholds"].keys()),
        [k for k, v in stored_new["switches"].items() if not v] or "无",
        user.username,
    )

    view = outlier_params_service.build_merged_view(stored_new)
    return success(data=view.model_dump(by_alias=True), message="异常值检测参数已更新")


__all__ = ["router"]
