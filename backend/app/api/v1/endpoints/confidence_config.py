"""数据可信度阈值管理接口.

提供 5 级数据可信度阈值（A/B/C/D/E）的查询与更新。

可信度阈值存储在 ``sys_config`` 表中（JSON 序列化）：
- ``confidence_thresholds.current`` — 当前可信度阈值配置

算法默认阈值（对齐算法说明 §3.7.2）：
    - A 级 (≥0.95)  绿色 #52c41a  数据充分
    - B 级 (≥0.80)  蓝色 #1890ff  数据较充分
    - C 级 (≥0.60)  黄色 #faad14  数据一般
    - D 级 (≥0.20)  橙色 #fa8c16  数据不足
    - E 级 (<0.20)  红色 #f5222d  可信度不足（INCONCLUSIVE）

路由清单：
- GET  /api/v1/configs/confidence-thresholds — 获取当前可信度阈值
- POST /api/v1/configs/confidence-thresholds — 更新可信度阈值（含严格递减校验）
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
_KEY_DESC = "5 级数据可信度阈值配置（JSON）"

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
    """构建算法默认可信度阈值."""
    items = [ConfidenceThresholdItem(**t) for t in DEFAULT_CONFIDENCE_THRESHOLDS]
    return ConfidenceThresholdSchema(
        thresholds=items,
        updatedAt=None,
        updatedBy=None,
    )


async def _load_current_thresholds(db: AsyncSession) -> ConfidenceThresholdSchema:
    """加载当前生效的可信度阈值.

    若 sys_config 中不存在，返回算法默认阈值（不写入数据库）。
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
    """更新 5 级数据可信度阈值（仅 ADMIN）.

    校验规则：
    - 必须为 5 级（level 1-5）
    - 等级名称必须为 A/B/C/D/E
    - 各等级 minRate 在 [0, 1] 范围内
    - 等级阈值严格递减：level N 的 minRate > level N+1 的 minRate
    - level 5 的 minRate 必须为 0
    """
    _validate_thresholds(body.thresholds)

    current = await _load_current_thresholds(db)
    before_snapshot = current.model_dump_json()

    new_thresholds = ConfidenceThresholdSchema(
        thresholds=body.thresholds,
        updatedAt=_now_iso(),
        updatedBy=user.username,
    )

    await _set_config_value(
        db,
        _KEY_CURRENT,
        new_thresholds.model_dump_json(),
        _KEY_DESC,
        user.username,
    )

    await _write_audit(
        db=db,
        operator=user.username,
        operation_type="CONFIDENCE_THRESHOLD_UPDATE",
        target_type="sys_config",
        target_id=_KEY_CURRENT,
        before_value=before_snapshot,
        after_value=new_thresholds.model_dump_json(),
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("更新可信度阈值事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    logger.info(
        "可信度阈值已更新: levels=%s, operator=%s",
        [t.level for t in new_thresholds.thresholds],
        user.username,
    )

    # 更新运行时阈值缓存（当前进程立即生效）+ Redis pub/sub 广播（其他进程同步）
    # 可信度统一 Phase 3（P3-2 / D4）：多进程阈值同步
    from app.services.confidence_evaluator import (
        ConfidenceEvaluator,
        broadcast_thresholds,
    )

    threshold_map: dict[str, float] = {}
    for item in new_thresholds.thresholds:
        threshold_map[item.name] = item.minRate
    ConfidenceEvaluator.set_thresholds(threshold_map)

    # 广播给所有 Celery worker / uvicorn 进程的订阅线程
    try:
        await broadcast_thresholds(threshold_map, source=f"api:{user.username}")
    except Exception as exc:  # noqa: BLE001
        # 广播失败不阻塞响应（当前进程已更新，其他进程下次重启预载时会加载）
        logger.warning("阈值更新广播失败（其他进程将在重启时预载）: %s", exc)

    return success(data=new_thresholds.model_dump(), message="可信度阈值已更新")


__all__ = ["router"]
