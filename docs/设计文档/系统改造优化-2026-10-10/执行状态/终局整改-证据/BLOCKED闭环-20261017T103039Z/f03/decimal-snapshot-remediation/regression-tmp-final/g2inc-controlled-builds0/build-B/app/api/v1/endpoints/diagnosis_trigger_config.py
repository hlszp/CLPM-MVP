"""诊断触发条件配置接口.

提供诊断触发条件（评分阈值 / 并发数 / 最少点数 / 体检轨开关）的查询与更新。

存储方式参照 outlier-params：sys_config 表 JSON 序列化，
key = ``diagnosis_trigger.current``（结构见 ``app.services.diagnosis_trigger_config``）。

保存后立即生效：刷新进程内缓存，diagnosis_engine 热路径经
``get_trigger_config()`` 读取，不查库。

路由清单：
- GET  /api/v1/configs/diagnosis-trigger — 获取当前配置
- PUT  /api/v1/configs/diagnosis-trigger — 更新配置（仅 ADMIN）
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import DiagnosisTriggerSaveRequest, DiagnosisTriggerSchema
from app.services import diagnosis_trigger_config as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/diagnosis-trigger", tags=["diagnosis-trigger-config"])


# ---------------------------------------------------------------------------
# GET /configs/diagnosis-trigger — 获取当前配置
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[DiagnosisTriggerSchema])
async def get_diagnosis_trigger(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取诊断触发条件配置.

    若未配置过，返回默认值（score_threshold=60, concurrency=5,
    min_data_points=32, checkup_enabled=true）。
    """
    stored = await svc.load_stored_config(db)
    if stored is None:
        return success(data=svc.get_trigger_config().model_dump(by_alias=True))
    # 从 DB 读取最新值并构建 schema（不依赖进程内缓存状态）
    svc.apply_runtime(stored)
    return success(data=svc.get_trigger_config().model_dump(by_alias=True))


# ---------------------------------------------------------------------------
# PUT /configs/diagnosis-trigger — 更新配置
# ---------------------------------------------------------------------------


@router.put("", response_model=ApiResponse[DiagnosisTriggerSchema])
async def update_diagnosis_trigger(
    req: DiagnosisTriggerSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新诊断触发条件配置（仅 ADMIN）.

    保存后立即刷新进程内缓存，diagnosis_engine 热路径即刻生效。
    同时写入审计日志（operation_type=DIAGNOSIS_TRIGGER_UPDATE）。
    """
    before_raw = await svc.get_config_value(db, svc.SYS_CONFIG_KEY)
    payload = svc.build_stored_payload(req, user.username)
    payload_json = json.dumps(payload, ensure_ascii=False)
    await svc.set_config_value(
        db, svc.SYS_CONFIG_KEY, payload_json, svc.SYS_CONFIG_DESC, user.username
    )
    await svc.write_audit(
        db,
        user.username,
        "DIAGNOSIS_TRIGGER_UPDATE",
        "sys_config",
        svc.SYS_CONFIG_KEY,
        before_value=before_raw,
        after_value=payload_json,
    )
    await db.commit()
    # 刷新进程内缓存（保存后立即生效）
    svc.apply_runtime(payload)
    logger.info(
        "诊断触发条件已更新: score_threshold=%s concurrency=%s "
        "min_data_points=%s checkup_enabled=%s (operator=%s)",
        req.score_threshold,
        req.concurrency,
        req.min_data_points,
        req.checkup_enabled,
        user.username,
    )
    return success(data=svc.get_trigger_config().model_dump(by_alias=True))
