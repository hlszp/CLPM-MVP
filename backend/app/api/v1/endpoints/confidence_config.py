"""数据可信度阈值管理接口.

提供 5 级数据可信度阈值（A/B/C/D/E）的查询与更新，保存自动生成新版本
并归档历史，支持版本历史查询与回滚。

可信度阈值存储在 ``sys_config`` 表中（JSON 序列化）：
- ``confidence_thresholds.current`` — 当前可信度阈值配置（含 version 字段）
- ``confidence_thresholds.history`` — 历史版本列表（含生效/失效时间）

算法默认阈值（对齐算法说明 §3.7.2）：
    - A 级 (≥0.95)  绿色 #52c41a  数据充分
    - B 级 (≥0.80)  蓝色 #1890ff  数据较充分
    - C 级 (≥0.60)  黄色 #faad14  数据一般
    - D 级 (≥0.20)  橙色 #fa8c16  数据不足
    - E 级 (<0.20)  红色 #f5222d  可信度不足（INCONCLUSIVE）

路由清单：
- GET  /api/v1/configs/confidence-thresholds            — 获取当前可信度阈值
- POST /api/v1/configs/confidence-thresholds            — 更新可信度阈值（保存为新版本）
- GET  /api/v1/configs/confidence-thresholds/history    — 版本历史
- POST /api/v1/configs/confidence-thresholds/{version}/rollback — 回滚到指定版本
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
    ConfidenceThresholdItem,
    ConfidenceThresholdSaveRequest,
    ConfidenceThresholdSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/confidence-thresholds", tags=["confidence-config"])

# ---------------------------------------------------------------------------
# sys_config 键常量
# ---------------------------------------------------------------------------

_KEY_CURRENT = "confidence_thresholds.current"
_KEY_HISTORY = "confidence_thresholds.history"
_KEY_DESC = "5 级数据可信度阈值配置（JSON）"
_KEY_DESC_HISTORY = "可信度阈值历史版本列表（JSON 数组）"

# ---------------------------------------------------------------------------
# 算法默认可信度阈值（对齐算法说明 §3.7.2）
# ---------------------------------------------------------------------------

DEFAULT_CONFIDENCE_THRESHOLDS: list[dict] = [
    {
        "level": 1,
        "name": "A",
        "minRate": 0.95,
        "description": "数据充分",
        "color": "#52c41a",
    },
    {
        "level": 2,
        "name": "B",
        "minRate": 0.80,
        "description": "数据较充分",
        "color": "#1890ff",
    },
    {
        "level": 3,
        "name": "C",
        "minRate": 0.60,
        "description": "数据一般",
        "color": "#faad14",
    },
    {
        "level": 4,
        "name": "D",
        "minRate": 0.20,
        "description": "数据不足",
        "color": "#fa8c16",
    },
    {
        "level": 5,
        "name": "E",
        "minRate": 0.0,
        "description": "可信度不足（INCONCLUSIVE）",
        "color": "#f5222d",
    },
]

# 等级名称枚举（按 level 顺序）
_EXPECTED_LEVEL_NAMES = {
    1: "A",
    2: "B",
    3: "C",
    4: "D",
    5: "E",
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


def _validate_thresholds(thresholds: list[ConfidenceThresholdItem]) -> None:
    """校验可信度阈值的完整性与一致性.

    校验规则：
    1. 必须为 5 级（已由 Schema min_length=max_length=5 保证）
    2. level 必须为 1-5 且不重复
    3. 等级名称必须为 A/B/C/D/E
    4. 各等级 minRate 在 [0, 1] 范围内
    5. 等级递减方向：level 越小（1=A=最优），minRate 越大
       - level N 的 minRate > level N+1 的 minRate（严格递减）
    6. level 1 的 minRate <= 1.0（上限）
    7. level 5 的 minRate 必须为 0（最低下限）
    """
    sorted_by_level = sorted(thresholds, key=lambda t: t.level)

    levels = [t.level for t in sorted_by_level]
    if levels != [1, 2, 3, 4, 5]:
        raise BizError(
            code="ERR_CONFIDENCE_LEVELS_INVALID",
            message=f"可信度阈值必须包含 level 1-5 五个等级，当前为 {levels}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    for t in sorted_by_level:
        expected_name = _EXPECTED_LEVEL_NAMES.get(t.level)
        if t.name != expected_name:
            raise BizError(
                code="ERR_CONFIDENCE_NAME_MISMATCH",
                message=(f"等级 {t.level} 的名称必须为 {expected_name}，当前为 {t.name}"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # 校验严格递减（level N 的 minRate > level N+1 的 minRate）
    for i in range(len(sorted_by_level) - 1):
        current = sorted_by_level[i]
        next_item = sorted_by_level[i + 1]
        if current.minRate <= next_item.minRate:
            raise BizError(
                code="ERR_CONFIDENCE_NOT_DESCENDING",
                message=(
                    f"等级 {current.level}（{current.name}）的 minRate({current.minRate}) "
                    f"必须大于等级 {next_item.level}（{next_item.name}）的 "
                    f"minRate({next_item.minRate})，确保阈值严格递减"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # 校验 level 1 的 minRate <= 1.0
    if sorted_by_level[0].minRate > 1.0 + 1e-6:
        raise BizError(
            code="ERR_CONFIDENCE_TOP_BOUND",
            message=(f"等级 1（A）的 minRate 不能超过 1.0，当前为 {sorted_by_level[0].minRate}"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 校验 level 5 的 minRate = 0
    if abs(sorted_by_level[-1].minRate) > 1e-6:
        raise BizError(
            code="ERR_CONFIDENCE_BOTTOM_BOUND",
            message=(f"等级 5（E）的 minRate 必须为 0，当前为 {sorted_by_level[-1].minRate}"),
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


def _build_default_thresholds() -> ConfidenceThresholdSchema:
    """构建算法默认可信度阈值（version=0 表示算法规范默认）."""
    items = [ConfidenceThresholdItem(**t) for t in DEFAULT_CONFIDENCE_THRESHOLDS]
    return ConfidenceThresholdSchema(
        version=0,
        thresholds=items,
        updatedAt=None,
        updatedBy=None,
    )


async def _load_current_thresholds(db: AsyncSession) -> ConfidenceThresholdSchema:
    """加载当前生效的可信度阈值.

    若 sys_config 中不存在，返回算法默认阈值（不写入数据库）。
    存量数据无 version 字段时按 version=0 处理（算法默认语义）。
    """
    raw = await _get_config_value(db, _KEY_CURRENT)
    if not raw:
        return _build_default_thresholds()
    try:
        data = json.loads(raw)
        return ConfidenceThresholdSchema.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("可信度阈值解析失败，回退算法默认: %s", exc)
        return _build_default_thresholds()


async def _load_history(db: AsyncSession) -> list[dict]:
    """加载历史版本列表."""
    raw = await _get_config_value(db, _KEY_HISTORY)
    if not raw:
        return []
    try:
        history = json.loads(raw)
        return history if isinstance(history, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.warning("可信度阈值历史版本解析失败，返回空列表")
        return []


async def _save_version(
    db: AsyncSession,
    thresholds: list[ConfidenceThresholdItem],
    operator: str,
    remark: str | None = None,
) -> ConfidenceThresholdSchema:
    """保存可信度阈值为新版本并归档历史（含生效/失效时间）."""
    current = await _load_current_thresholds(db)
    before_snapshot = current.model_dump_json()

    new_version = current.version + 1 if current.version > 0 else 1
    now = _now_iso()
    new_thresholds = ConfidenceThresholdSchema(
        version=new_version,
        thresholds=thresholds,
        updatedAt=now,
        updatedBy=operator,
    )

    history = await _load_history(db)
    history.append(
        {
            "version": current.version,
            "thresholds": [t.model_dump() for t in current.thresholds],
            "updatedAt": current.updatedAt,
            "updatedBy": current.updatedBy,
            "remark": remark or f"保存版本 {new_version} 前的快照",
            "isCurrent": False,
            "effectiveAt": current.updatedAt,
            "expiresAt": now,
        }
    )

    await _set_config_value(db, _KEY_CURRENT, new_thresholds.model_dump_json(), _KEY_DESC, operator)
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
        operation_type="CONFIDENCE_THRESHOLD_UPDATE",
        target_type="sys_config",
        target_id=_KEY_CURRENT,
        before_value=before_snapshot,
        after_value=new_thresholds.model_dump_json(),
    )

    return new_thresholds


async def _commit_or_rollback(db: AsyncSession, action: str) -> None:
    """提交事务，失败回滚并抛 BizError."""
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("可信度阈值 %s 事务提交失败", action)
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None


async def _apply_runtime_thresholds(thresholds: list[ConfidenceThresholdItem], source: str) -> None:
    """更新运行时阈值缓存（当前进程立即生效）+ Redis pub/sub 广播（其他进程同步）.

    可信度统一 Phase 3（P3-2 / D4）：多进程阈值同步。
    """
    from app.services.confidence_evaluator import (
        ConfidenceEvaluator,
        broadcast_thresholds,
    )

    threshold_map: dict[str, float] = {}
    for item in thresholds:
        threshold_map[item.name] = item.minRate
    ConfidenceEvaluator.set_thresholds(threshold_map)

    # 广播给所有 Celery worker / uvicorn 进程的订阅线程
    try:
        await broadcast_thresholds(threshold_map, source=source)
    except Exception as exc:  # noqa: BLE001
        # 广播失败不阻塞响应（当前进程已更新，其他进程下次重启预载时会加载）
        logger.warning("阈值更新广播失败（其他进程将在重启时预载）: %s", exc)


# ---------------------------------------------------------------------------
# GET /configs/confidence-thresholds — 获取当前可信度阈值
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[ConfidenceThresholdSchema])
async def get_confidence_thresholds(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取当前生效的 5 级数据可信度阈值.

    若未配置过，返回算法默认阈值。
    """
    thresholds = await _load_current_thresholds(db)
    return success(data=thresholds.model_dump())


# ---------------------------------------------------------------------------
# POST /configs/confidence-thresholds — 更新可信度阈值
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[ConfidenceThresholdSchema])
async def save_confidence_thresholds(
    body: ConfidenceThresholdSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新 5 级数据可信度阈值（仅 ADMIN，保存为新版本并立即生效）.

    校验规则：
    - 必须为 5 级（level 1-5）
    - 等级名称必须为 A/B/C/D/E
    - 各等级 minRate 在 [0, 1] 范围内
    - 等级阈值严格递减：level N 的 minRate > level N+1 的 minRate
    - level 5 的 minRate 必须为 0
    """
    _validate_thresholds(body.thresholds)

    new_thresholds = await _save_version(
        db=db,
        thresholds=body.thresholds,
        operator=user.username,
        remark=body.remark,
    )
    await _commit_or_rollback(db, "保存")

    logger.info(
        "可信度阈值已更新: version=%d, levels=%s, operator=%s",
        new_thresholds.version,
        [t.level for t in new_thresholds.thresholds],
        user.username,
    )

    # 更新运行时阈值缓存 + 广播其他进程
    await _apply_runtime_thresholds(new_thresholds.thresholds, source=f"api:{user.username}")

    return success(data=new_thresholds.model_dump(), message="可信度阈值已保存为新版本")


# ---------------------------------------------------------------------------
# GET /configs/confidence-thresholds/history — 版本历史
# ---------------------------------------------------------------------------


@router.get("/history", response_model=ApiResponse[dict])
async def get_confidence_threshold_history(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询可信度阈值版本历史（仅 ADMIN）.

    返回全部版本（含当前版本），每条含生效时间与失效时间。
    """
    current = await _load_current_thresholds(db)
    history = await _load_history(db)

    for item in history:
        item["isCurrent"] = False

    current_item = {
        "version": current.version,
        "thresholds": [t.model_dump() for t in current.thresholds],
        "updatedAt": current.updatedAt,
        "updatedBy": current.updatedBy,
        "remark": "当前生效版本" if current.version > 0 else "算法规范默认版本",
        "isCurrent": True,
        "effectiveAt": current.updatedAt,
        "expiresAt": None,
    }

    all_items = [current_item] + sorted(history, key=lambda x: x.get("version", 0), reverse=True)
    return success(data={"items": all_items, "currentVersion": current.version})


# ---------------------------------------------------------------------------
# POST /configs/confidence-thresholds/{version}/rollback — 回滚到指定版本
# ---------------------------------------------------------------------------


@router.post("/{version}/rollback", response_model=ApiResponse[ConfidenceThresholdSchema])
async def rollback_confidence_thresholds(
    version: int,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回滚到指定历史版本（仅 ADMIN，回滚生成新版本号保留追溯链）.

    version=0 表示回滚到算法规范默认值。
    """
    if version < 0:
        raise BizError(
            code="ERR_INVALID_VERSION",
            message="版本号必须为非负整数",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if version == 0:
        default = _build_default_thresholds()
        result = await _save_version(
            db=db,
            thresholds=default.thresholds,
            operator=user.username,
            remark="回滚到算法规范默认值（源版本 0）",
        )
        await _commit_or_rollback(db, "回滚")
        await _apply_runtime_thresholds(result.thresholds, source=f"api:{user.username}")
        logger.info(
            "可信度阈值已回滚到算法默认: new_version=%d, operator=%s",
            result.version,
            user.username,
        )
        return success(data=result.model_dump(), message="已回滚到算法规范默认值")

    history = await _load_history(db)
    target = next((h for h in history if h.get("version") == version), None)
    if target is None:
        raise BizError(
            code="ERR_VERSION_NOT_FOUND",
            message=f"历史版本 {version} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    rollback_items = [
        ConfidenceThresholdItem.model_validate(t) for t in target.get("thresholds", [])
    ]
    if not rollback_items:
        raise BizError(
            code="ERR_VERSION_EMPTY",
            message=f"历史版本 {version} 的阈值数据为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 校验回滚版本的阈值
    _validate_thresholds(rollback_items)

    result = await _save_version(
        db=db,
        thresholds=rollback_items,
        operator=user.username,
        remark=f"回滚自版本 {version}",
    )
    await _commit_or_rollback(db, "回滚")
    await _apply_runtime_thresholds(result.thresholds, source=f"api:{user.username}")

    logger.info(
        "可信度阈值已回滚: from_version=%d, to_new_version=%d, operator=%s",
        version,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message=f"已回滚到版本 {version}")


__all__ = ["router"]
