"""批量配置接口 (IDS v3.2 §2.8/§2.9).

提供指标配置与诊断配置的批量读写能力，便于前端配置界面一次性加载/保存
全部指标配置（3+1+8 三段式结构）与全部 8 类诊断标签配置。批量保存时
后端事务化处理，任一项校验失败则全部回滚。

路由清单：
- GET /api/v1/configs/metrics     — 批量获取指标配置
- PUT /api/v1/configs/metrics     — 批量更新指标配置（事务性）
- GET /api/v1/configs/diagnosis   — 批量获取诊断配置
- PUT /api/v1/configs/diagnosis   — 批量更新诊断配置（事务性）
- POST /api/v1/configs/diagnosis  — 新增诊断配置（2026-08-19 诊断配置页 CRUD）
- DELETE /api/v1/configs/diagnosis/{diag_id} — 删除诊断配置（写审计）

设计依据：IDS §2.8.1/§2.8.2/§2.9.1/§2.9.2 + 诊断配置页 CRUD 扩展
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
from app.core.modules import require_module
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisConfig
from app.models.metric import MetricConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.config import (
    DiagnosisConfigBatchResponse,
    DiagnosisConfigBatchUpdateRequest,
    DiagnosisConfigCreateRequest,
    DiagnosisConfigItem,
    DiagnosisConfigUpdateItem,
    MetricConfigUpdateItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs", tags=["configs"])

# 诊断配置版本化 sys_config 键（快照模式：每次 CRUD 自动归档全量快照）
_KEY_DIAG_VERSION = "diagnosis_config.version"
_KEY_DIAG_VERSION_DESC = "诊断配置当前版本快照（JSON，含 version + items）"
_KEY_DIAG_HISTORY = "diagnosis_config.history"
_KEY_DIAG_HISTORY_DESC = "诊断配置历史版本快照列表（JSON 数组，含生效/失效时间）"

#: P2-01 CFG-02：诊断配置字段级元数据（区分注册表只读说明与真实可调字段）。
#: algorithmType/calcMethod/params 可存但活执行引擎（diagnosis_v2）不消费——
#: 标注 readOnly，不再暗示可存生效；threshold/isEnabled 为真实可调字段。
#: 前端（W3 批准后）据此渲染只读态；后端 PUT/POST 仍兼容接收存量字段。
DIAGNOSIS_FIELD_META: dict[str, dict[str, Any]] = {
    "algorithmType": {
        "label": "算法类型",
        "readOnly": True,
        "consumedByLiveEngine": False,
        "note": "v2 诊断执行引擎不消费该字段；仅作注册表只读说明保留（CFG-02）",
    },
    "calcMethod": {
        "label": "计算方法",
        "readOnly": True,
        "consumedByLiveEngine": False,
        "note": "v2 诊断执行引擎不消费该字段；仅作注册表只读说明保留（CFG-02）",
    },
    "params": {
        "label": "算法参数",
        "readOnly": True,
        "consumedByLiveEngine": False,
        "note": "v2 诊断执行引擎不消费该字段；仅作注册表只读说明保留（CFG-02）",
    },
    "threshold": {
        "label": "阈值",
        "readOnly": False,
        "consumedByLiveEngine": True,
        "note": "全局默认层，真实可调（层级覆盖：全局默认 < 模板 < 装置 < 回路）",
    },
    "isEnabled": {
        "label": "启用",
        "readOnly": False,
        "consumedByLiveEngine": True,
        "note": None,
    },
    "diagName": {
        "label": "名称",
        "readOnly": False,
        "consumedByLiveEngine": True,
        "note": None,
    },
}


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    from datetime import UTC as _UTC

    return datetime.now(_UTC).isoformat()


async def _get_sys_config(db: AsyncSession, key: str) -> str | None:
    from app.models.sys_config import SysConfig

    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def _set_sys_config(
    db: AsyncSession,
    key: str,
    value: str,
    description: str,
    operator: str,
) -> None:
    from app.models.sys_config import SysConfig

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
        cfg.description = description
        cfg.updated_by = operator
        cfg.updated_at = now


async def _load_diag_version(db: AsyncSession) -> dict:
    """读取诊断配置当前版本快照（无则返回 version=0 空快照）."""
    import json as _json

    raw = await _get_sys_config(db, _KEY_DIAG_VERSION)
    if not raw:
        return {"version": 0, "items": [], "updatedAt": None, "updatedBy": None}
    try:
        data = _json.loads(raw)
        return data if isinstance(data, dict) else {"version": 0, "items": []}
    except (ValueError, TypeError):
        return {"version": 0, "items": []}


async def _load_diag_history(db: AsyncSession) -> list:
    import json as _json

    raw = await _get_sys_config(db, _KEY_DIAG_HISTORY)
    if not raw:
        return []
    try:
        history = _json.loads(raw)
        return history if isinstance(history, list) else []
    except (ValueError, TypeError):
        return []


async def _snapshot_diagnosis_version(
    db: AsyncSession,
    operator: str,
    remark: str | None = None,
) -> int:
    """将当前诊断配置全量归档为新版本（在 CRUD 事务内、commit 前调用）.

    需先 flush 使同事务内的增删改对 SELECT 可见。
    返回新版本号。
    """
    import json as _json

    await db.flush()
    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.diag_code.asc()))
    items = [_diagnosis_to_response_dict(c) for c in result.scalars().all()]

    current = await _load_diag_version(db)
    new_version = current.get("version", 0) + 1
    now = _now_iso()

    # 归档旧版本到历史（补充失效时间）
    history = await _load_diag_history(db)
    history.append(
        {
            "version": current.get("version", 0),
            "items": current.get("items", []),
            "updatedAt": current.get("updatedAt"),
            "updatedBy": current.get("updatedBy"),
            "remark": remark or f"保存版本 {new_version} 前的快照",
            "isCurrent": False,
            "effectiveAt": current.get("updatedAt"),
            "expiresAt": now,
        }
    )

    await _set_sys_config(
        db,
        _KEY_DIAG_VERSION,
        _json.dumps(
            {
                "version": new_version,
                "items": items,
                "updatedAt": now,
                "updatedBy": operator,
            },
            ensure_ascii=False,
            default=str,
        ),
        _KEY_DIAG_VERSION_DESC,
        operator,
    )
    await _set_sys_config(
        db,
        _KEY_DIAG_HISTORY,
        _json.dumps(history, ensure_ascii=False, default=str),
        _KEY_DIAG_HISTORY_DESC,
        operator,
    )
    return new_version


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
    """将 MetricConfig ORM 转为响应字典（含 category/isDiscountFactor）。

    v5.3 P3-T10：formula 字段已标注废弃（算法已固化在代码中），
    Schema 层通过 deprecated=True 在 OpenAPI 文档中体现。
    """
    category = _metric_category(c.metric_code)
    is_discount = c.metric_code == _COMMISSIONING_METRIC_CODE
    return {
        "metricId": str(c.id),
        "metricKey": c.metric_code,
        "metricName": c.metric_name,
        "category": category,
        "isDiscountFactor": is_discount if is_discount else None,
        # v5.3 P3-T10：formula 已废弃，保留返回值用于历史追溯
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
# §2.9.1 GET /configs/diagnosis — 批量获取诊断配置
# ---------------------------------------------------------------------------


@router.get("/diagnosis", response_model=ApiResponse[DiagnosisConfigBatchResponse])
async def batch_get_diagnosis_configs(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    _module: None = Depends(require_module("diagnosis")),
) -> dict:
    """批量获取诊断配置（全部 8 类诊断标签）.

    设计依据：IDS §2.9.1
    """
    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.diag_code.asc()))
    configs = list(result.scalars().all())

    items = [DiagnosisConfigItem.model_validate(_diagnosis_to_response_dict(c)) for c in configs]

    # P2-01 CFG-02：随读路径下发字段级元数据（只读说明 vs 真实可调）
    resp = DiagnosisConfigBatchResponse(items=items, methodMeta=DIAGNOSIS_FIELD_META)
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# §2.9.2 PUT /configs/diagnosis — 批量更新诊断配置（事务性）
# ---------------------------------------------------------------------------


@router.put("/diagnosis", response_model=ApiResponse[DiagnosisConfigBatchResponse])
async def batch_update_diagnosis_configs(
    body: DiagnosisConfigBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
    _module: None = Depends(require_module("diagnosis")),
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
        # 版本快照（同事务内归档，原子生效）
        new_version = await _snapshot_diagnosis_version(
            db, user.username, remark=f"批量更新 {len(body.items)} 项诊断配置"
        )
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
        "批量更新诊断配置成功: updated=%d, version=%d, operator=%s",
        len(body.items),
        new_version,
        user.username,
    )
    return success(data=resp.model_dump(), message="批量更新成功（已生成新版本）")


# ---------------------------------------------------------------------------
# §2.9.3 POST /configs/diagnosis — 新增诊断配置（2026-08-19 诊断配置页 CRUD）
# ---------------------------------------------------------------------------


@router.post("/diagnosis", response_model=ApiResponse[DiagnosisConfigBatchResponse])
async def create_diagnosis_config(
    body: DiagnosisConfigCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
    _module: None = Depends(require_module("diagnosis")),
) -> dict:
    """新增单条诊断配置（diag_code 唯一，重复返回 409）.

    设计依据：诊断配置页 CRUD 扩展（IDS §2.9.3）
    """
    import json

    item = body.item

    # Poka-Yoke：diag_code 必须属于 8 类诊断标签枚举。
    # DiagnosisConfigItem.label 为 Literal 枚举，枚举外的 diag_code 会
    # 使批量 GET 的 model_validate 整体 500（一条脏数据毒化整个列表）。
    from app.schemas.config import DiagnosisLabel

    if item.diagKey not in DiagnosisLabel.__args__:
        raise BizError(
            code="ERR_INVALID_DIAG_CODE",
            message=f"诊断代码必须为 8 类标签之一: {list(DiagnosisLabel.__args__)}",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    existing = (
        await db.execute(select(DiagnosisConfig).where(DiagnosisConfig.diag_code == item.diagKey))
    ).scalar_one_or_none()
    if existing is not None:
        raise BizError(
            code="ERR_DIAG_CODE_DUPLICATED",
            message=f"诊断代码 {item.diagKey} 已存在",
            status_code=status.HTTP_409_CONFLICT,
        )

    config = DiagnosisConfig(
        id=str(uuid4()),
        diag_code=item.diagKey,
        diag_name=item.diagName,
        algorithm_type=item.algorithmType,
        calc_method=item.calcMethod,
        params=item.params,
        threshold=item.threshold,
        is_enabled=item.isEnabled,
        version=1,
        updated_by=user.username,
        updated_at=_now_naive(),
    )
    db.add(config)

    after_snapshot = _diagnosis_to_response_dict(config)
    await _write_audit(
        db=db,
        operator=user.username,
        operation_type="DIAG_CONFIG_CREATE",
        target_type="diagnosis_config",
        target_id=str(config.id),
        before_value=None,
        after_value=json.dumps(after_snapshot, ensure_ascii=False, default=str),
    )

    try:
        # 版本快照（同事务内归档，原子生效）
        new_version = await _snapshot_diagnosis_version(
            db, user.username, remark=f"新增诊断配置 {item.diagKey}"
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("新增诊断配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.diag_code.asc()))
    configs = list(result.scalars().all())
    items = [DiagnosisConfigItem.model_validate(_diagnosis_to_response_dict(c)) for c in configs]

    logger.info(
        "新增诊断配置成功: diag_code=%s, version=%d, operator=%s",
        item.diagKey,
        new_version,
        user.username,
    )
    return success(
        data=DiagnosisConfigBatchResponse(items=items).model_dump(),
        message="新增成功（已生成新版本）",
    )


# ---------------------------------------------------------------------------
# §2.9.4 DELETE /configs/diagnosis/{diag_id} — 删除诊断配置（2026-08-19 CRUD）
# ---------------------------------------------------------------------------


@router.delete("/diagnosis/{diag_id}", response_model=ApiResponse[dict])
async def delete_diagnosis_config(
    diag_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
    _module: None = Depends(require_module("diagnosis")),
) -> dict:
    """删除单条诊断配置（写审计日志）.

    设计依据：诊断配置页 CRUD 扩展（IDS §2.9.4）
    """
    import json

    config = (
        await db.execute(select(DiagnosisConfig).where(DiagnosisConfig.id == diag_id))
    ).scalar_one_or_none()
    if config is None:
        raise BizError(
            code="ERR_DIAG_CONFIG_NOT_FOUND",
            message="诊断配置不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    before_snapshot = _diagnosis_to_response_dict(config)
    await db.delete(config)

    await _write_audit(
        db=db,
        operator=user.username,
        operation_type="DIAG_CONFIG_DELETE",
        target_type="diagnosis_config",
        target_id=str(config.id),
        before_value=json.dumps(before_snapshot, ensure_ascii=False, default=str),
        after_value=None,
    )

    try:
        # 版本快照（同事务内归档，原子生效）
        new_version = await _snapshot_diagnosis_version(
            db, user.username, remark=f"删除诊断配置 {config.diag_code}"
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("删除诊断配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "删除诊断配置成功: diag_code=%s, version=%d, operator=%s",
        config.diag_code,
        new_version,
        user.username,
    )
    return success(data={"deletedDiagId": diag_id}, message="删除成功（已生成新版本）")


# ---------------------------------------------------------------------------
# GET /configs/diagnosis/history — 诊断配置版本历史（快照模式）
# ---------------------------------------------------------------------------


@router.get("/diagnosis/history", response_model=ApiResponse[dict])
async def get_diagnosis_config_history(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
    _module: None = Depends(require_module("diagnosis")),
) -> dict:
    """查询诊断配置版本历史（仅 ADMIN，快照含生效/失效时间）."""
    current = await _load_diag_version(db)
    history = await _load_diag_history(db)

    for item in history:
        item["isCurrent"] = False

    current_item = {
        "version": current.get("version", 0),
        "items": current.get("items", []),
        "updatedAt": current.get("updatedAt"),
        "updatedBy": current.get("updatedBy"),
        "remark": "当前生效版本" if current.get("version", 0) > 0 else "初始版本",
        "isCurrent": True,
        "effectiveAt": current.get("updatedAt"),
        "expiresAt": None,
    }

    all_items = [current_item] + sorted(history, key=lambda x: x.get("version", 0), reverse=True)
    return success(data={"items": all_items, "currentVersion": current.get("version", 0)})


# ---------------------------------------------------------------------------
# POST /configs/diagnosis/{version}/rollback — 回滚诊断配置到指定版本
# ---------------------------------------------------------------------------


@router.post("/diagnosis/{version}/rollback", response_model=ApiResponse[dict])
async def rollback_diagnosis_config(
    version: int,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
    _module: None = Depends(require_module("diagnosis")),
) -> dict:
    """回滚诊断配置到指定历史版本（仅 ADMIN）.

    将快照中的全部配置项同步回 DiagnosisConfig 表（按 diag_code upsert，
    快照中不存在的行删除），回滚本身生成新版本号保留追溯链。
    """
    import json as _json

    if version < 1:
        raise BizError(
            code="ERR_INVALID_VERSION",
            message="版本号必须为正整数",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    history = await _load_diag_history(db)
    target = next((h for h in history if h.get("version") == version), None)
    if target is None:
        raise BizError(
            code="ERR_VERSION_NOT_FOUND",
            message=f"历史版本 {version} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    target_items = target.get("items", [])
    if not isinstance(target_items, list):
        target_items = []

    # 同步快照到表：按 diagKey upsert，多余行删除
    result = await db.execute(select(DiagnosisConfig))
    existing_map = {c.diag_code: c for c in result.scalars().all()}

    target_codes = set()
    for item in target_items:
        diag_code = item.get("diagKey")
        if not diag_code:
            continue
        target_codes.add(diag_code)
        existing = existing_map.get(diag_code)
        if existing is not None:
            existing.diag_name = item.get("diagName") or existing.diag_name
            existing.algorithm_type = item.get("algorithmType") or existing.algorithm_type
            existing.calc_method = item.get("calcMethod") or existing.calc_method
            existing.params = item.get("params") or existing.params
            existing.threshold = item.get("threshold") or existing.threshold
            existing.is_enabled = (
                item.get("isEnabled") if item.get("isEnabled") is not None else existing.is_enabled
            )
            existing.updated_by = user.username
            existing.updated_at = _now_naive()
            existing.version = (existing.version or 1) + 1
        else:
            db.add(
                DiagnosisConfig(
                    id=str(uuid4()),
                    diag_code=diag_code,
                    diag_name=item.get("diagName") or diag_code,
                    algorithm_type=item.get("algorithmType") or "",
                    calc_method=item.get("calcMethod") or "",
                    params=item.get("params") or {},
                    threshold=item.get("threshold") or {},
                    is_enabled=(
                        item.get("isEnabled") if item.get("isEnabled") is not None else True
                    ),
                    version=1,
                    updated_by=user.username,
                    updated_at=_now_naive(),
                )
            )

    # 删除快照中不存在的行
    for code, cfg in existing_map.items():
        if code not in target_codes:
            await db.delete(cfg)

    # 审计 + 版本快照
    await _write_audit(
        db=db,
        operator=user.username,
        operation_type="DIAG_CONFIG_ROLLBACK",
        target_type="sys_config",
        target_id=_KEY_DIAG_VERSION,
        before_value=None,
        after_value=_json.dumps(target_items, ensure_ascii=False, default=str),
    )
    new_version = await _snapshot_diagnosis_version(
        db, user.username, remark=f"回滚自版本 {version}"
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("回滚诊断配置事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "诊断配置已回滚: from_version=%d, to_new_version=%d, operator=%s",
        version,
        new_version,
        user.username,
    )
    return success(data={"version": new_version}, message=f"已回滚到版本 {version}")


__all__ = ["router"]
