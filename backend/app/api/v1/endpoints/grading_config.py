"""定级阈值管理接口 (FDS v5.1 §5.2.4 / DDS v4.1 / UIUX v5.3 ⑥).

提供 5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR）的查询与更新，
保存自动生成新版本并归档历史，支持版本历史查询与回滚。

定级阈值存储在 ``sys_config`` 表中（JSON 序列化）：
- ``grading_thresholds.current`` — 当前定级阈值配置（含 version 字段）
- ``grading_thresholds.history`` — 历史版本列表（含生效/失效时间）

国标默认定级（对齐 GB/T 44693.2-2024 §6.3 / FDS v5.1 §5.2.4）：
    - 1 级 EXCELLENT (≥90)   绿色 #52c41a
    - 2 级 GOOD     (80-90)  蓝色 #1890ff
    - 3 级 FAIR     (60-80)  黄色 #faad14
    - 4 级 WARNING  (40-60)  橙色 #fa8c16
    - 5 级 POOR     (<40)    红色 #f5222d

路由清单：
- GET  /api/v1/configs/grading-thresholds            — 获取当前定级阈值
- POST /api/v1/configs/grading-thresholds            — 更新定级阈值（保存为新版本）
- GET  /api/v1/configs/grading-thresholds/history    — 版本历史
- POST /api/v1/configs/grading-thresholds/{version}/rollback — 回滚到指定版本

设计依据：FDS v5.1 §5.2.4, GB/T 44693.2-2024 §6.3, UIUX v5.3 ⑥
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
    GradingThresholdItem,
    GradingThresholdSaveRequest,
    GradingThresholdSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/configs/grading-thresholds", tags=["grading-config"])

# ---------------------------------------------------------------------------
# sys_config 键常量
# ---------------------------------------------------------------------------

_KEY_CURRENT = "grading_thresholds.current"
_KEY_HISTORY = "grading_thresholds.history"
_KEY_DESC = "5 级性能定级阈值配置（JSON）"
_KEY_DESC_HISTORY = "定级阈值历史版本列表（JSON 数组）"

# ---------------------------------------------------------------------------
# 国标默认定级阈值（对齐 GB/T 44693.2-2024 §6.3 / FDS v5.1 §5.2.4）
# ---------------------------------------------------------------------------

DEFAULT_GRADING_THRESHOLDS: list[dict] = [
    {
        "level": 1,
        "name": "EXCELLENT",
        "label": "优秀",
        "minScore": 90.0,
        "maxScore": 100.0,
        "color": "#52c41a",
    },
    {
        "level": 2,
        "name": "GOOD",
        "label": "良好",
        "minScore": 80.0,
        "maxScore": 90.0,
        "color": "#1890ff",
    },
    {
        "level": 3,
        "name": "FAIR",
        "label": "合格",
        "minScore": 60.0,
        "maxScore": 80.0,
        "color": "#faad14",
    },
    {
        "level": 4,
        "name": "WARNING",
        "label": "警告",
        "minScore": 40.0,
        "maxScore": 60.0,
        "color": "#fa8c16",
    },
    {
        "level": 5,
        "name": "POOR",
        "label": "不合格",
        "minScore": 0.0,
        "maxScore": 40.0,
        "color": "#f5222d",
    },
]

# 等级名称枚举（按 level 顺序）
_EXPECTED_LEVEL_NAMES = {
    1: "EXCELLENT",
    2: "GOOD",
    3: "FAIR",
    4: "WARNING",
    5: "POOR",
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


def _validate_thresholds(thresholds: list[GradingThresholdItem]) -> None:
    """校验定级阈值的完整性与一致性.

    校验规则：
    1. 必须为 5 级（已由 Schema min_length=max_length=5 保证）
    2. level 必须为 1-5 且不重复
    3. 等级名称必须与国标定义一致（EXCELLENT/GOOD/FAIR/WARNING/POOR）
    4. 各等级 minScore < maxScore
    5. 等级递增方向：level 越高（1=最优），minScore 越大
       - level N 的 minScore == level N+1 的 maxScore（严格递减区间）
    6. level 1 的 maxScore 必须为 100（满分上限）
    7. level 5 的 minScore 必须为 0（最低下限）
    """
    # 按 level 排序
    sorted_by_level = sorted(thresholds, key=lambda t: t.level)

    # 校验 level 完整性
    levels = [t.level for t in sorted_by_level]
    if levels != [1, 2, 3, 4, 5]:
        raise BizError(
            code="ERR_GRADING_LEVELS_INVALID",
            message=f"定级阈值必须包含 level 1-5 五个等级，当前为 {levels}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 校验等级名称
    for t in sorted_by_level:
        expected_name = _EXPECTED_LEVEL_NAMES.get(t.level)
        if t.name != expected_name:
            raise BizError(
                code="ERR_GRADING_NAME_MISMATCH",
                message=(f"等级 {t.level} 的名称必须为 {expected_name}，当前为 {t.name}"),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # 校验各等级 minScore < maxScore
    for t in sorted_by_level:
        if t.minScore >= t.maxScore:
            raise BizError(
                code="ERR_GRADING_RANGE_INVALID",
                message=(
                    f"等级 {t.level}（{t.name}）的 minScore({t.minScore}) "
                    f"必须小于 maxScore({t.maxScore})"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # 校验等级区间严格递减（level N 的 minScore == level N+1 的 maxScore）
    for i in range(len(sorted_by_level) - 1):
        current = sorted_by_level[i]
        next_item = sorted_by_level[i + 1]
        if abs(current.minScore - next_item.maxScore) > 1e-6:
            raise BizError(
                code="ERR_GRADING_NOT_CONTIGUOUS",
                message=(
                    f"等级 {current.level}（{current.name}）的 minScore({current.minScore}) "
                    f"必须等于等级 {next_item.level}（{next_item.name}）的 "
                    f"maxScore({next_item.maxScore})，确保区间连续"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    # 校验 level 1 的 maxScore = 100
    if abs(sorted_by_level[0].maxScore - 100.0) > 1e-6:
        raise BizError(
            code="ERR_GRADING_TOP_BOUND",
            message=(
                f"等级 1（EXCELLENT）的 maxScore 必须为 100，当前为 {sorted_by_level[0].maxScore}"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 校验 level 5 的 minScore = 0
    if abs(sorted_by_level[-1].minScore) > 1e-6:
        raise BizError(
            code="ERR_GRADING_BOTTOM_BOUND",
            message=(f"等级 5（POOR）的 minScore 必须为 0，当前为 {sorted_by_level[-1].minScore}"),
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


def _build_default_thresholds() -> GradingThresholdSchema:
    """构建国标默认定级阈值（version=0 表示国标默认）."""
    items = [GradingThresholdItem(**t) for t in DEFAULT_GRADING_THRESHOLDS]
    return GradingThresholdSchema(
        version=0,
        thresholds=items,
        updatedAt=None,
        updatedBy=None,
    )


async def _load_current_thresholds(db: AsyncSession) -> GradingThresholdSchema:
    """加载当前生效的定级阈值.

    若 sys_config 中不存在，返回国标默认阈值（不写入数据库）。
    存量数据无 version 字段时按 version=0 处理（国标默认语义）。
    """
    raw = await _get_config_value(db, _KEY_CURRENT)
    if not raw:
        return _build_default_thresholds()
    try:
        data = json.loads(raw)
        return GradingThresholdSchema.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("定级阈值解析失败，回退国标默认: %s", exc)
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
        logger.warning("定级阈值历史版本解析失败，返回空列表")
        return []


async def _save_version(
    db: AsyncSession,
    thresholds: list[GradingThresholdItem],
    operator: str,
    remark: str | None = None,
) -> GradingThresholdSchema:
    """保存定级阈值为新版本并归档历史（含生效/失效时间）."""
    current = await _load_current_thresholds(db)
    before_snapshot = current.model_dump_json()

    new_version = current.version + 1 if current.version > 0 else 1
    now = _now_iso()
    new_thresholds = GradingThresholdSchema(
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

    await _set_config_value(
        db, _KEY_CURRENT, new_thresholds.model_dump_json(), _KEY_DESC, operator
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
        operation_type="GRADING_THRESHOLD_UPDATE",
        target_type=f"sys_config:{_KEY_CURRENT}",
        target_id=str(uuid4()),
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
        logger.exception("定级阈值 %s 事务提交失败", action)
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None


# ---------------------------------------------------------------------------
# GET /configs/grading-thresholds — 获取当前定级阈值
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[GradingThresholdSchema])
async def get_grading_thresholds(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """获取当前生效的 5 级性能定级阈值.

    若未配置过，返回国标默认阈值。

    设计依据：FDS v5.1 §5.2.4, GB/T 44693.2-2024 §6.3
    """
    thresholds = await _load_current_thresholds(db)
    return success(data=thresholds.model_dump())


# ---------------------------------------------------------------------------
# POST /configs/grading-thresholds — 更新定级阈值
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[GradingThresholdSchema])
async def save_grading_thresholds(
    body: GradingThresholdSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新 5 级性能定级阈值（仅 ADMIN，保存为新版本并立即生效）.

    校验规则：
    - 必须为 5 级（level 1-5）
    - 等级名称必须与国标定义一致（EXCELLENT/GOOD/FAIR/WARNING/POOR）
    - 各等级 minScore < maxScore
    - 等级区间连续：level N 的 minScore == level N+1 的 maxScore
    - level 1 的 maxScore 必须为 100
    - level 5 的 minScore 必须为 0

    设计依据：FDS v5.1 §5.2.4, GB/T 44693.2-2024 §6.3
    """
    # 校验阈值完整性与一致性
    _validate_thresholds(body.thresholds)

    # 保存新版本（归档当前版本到历史）
    new_thresholds = await _save_version(
        db=db,
        thresholds=body.thresholds,
        operator=user.username,
        remark=body.remark,
    )
    await _commit_or_rollback(db, "保存")

    logger.info(
        "定级阈值已更新: version=%d, levels=%s, operator=%s",
        new_thresholds.version,
        [t.level for t in new_thresholds.thresholds],
        user.username,
    )
    return success(data=new_thresholds.model_dump(), message="定级阈值已保存为新版本")


# ---------------------------------------------------------------------------
# GET /configs/grading-thresholds/history — 版本历史
# ---------------------------------------------------------------------------


@router.get("/history", response_model=ApiResponse[dict])
async def get_grading_threshold_history(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询定级阈值版本历史（仅 ADMIN）.

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
        "remark": "当前生效版本" if current.version > 0 else "国标默认版本",
        "isCurrent": True,
        "effectiveAt": current.updatedAt,
        "expiresAt": None,
    }

    all_items = [current_item] + sorted(
        history, key=lambda x: x.get("version", 0), reverse=True
    )
    return success(data={"items": all_items, "currentVersion": current.version})


# ---------------------------------------------------------------------------
# POST /configs/grading-thresholds/{version}/rollback — 回滚到指定版本
# ---------------------------------------------------------------------------


@router.post("/{version}/rollback", response_model=ApiResponse[GradingThresholdSchema])
async def rollback_grading_thresholds(
    version: int,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回滚到指定历史版本（仅 ADMIN，回滚生成新版本号保留追溯链）.

    version=0 表示回滚到国标默认值。
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
            remark="回滚到国标默认值（源版本 0）",
        )
        await _commit_or_rollback(db, "回滚")
        logger.info(
            "定级阈值已回滚到国标默认: new_version=%d, operator=%s",
            result.version,
            user.username,
        )
        return success(data=result.model_dump(), message="已回滚到国标默认值")

    history = await _load_history(db)
    target = next((h for h in history if h.get("version") == version), None)
    if target is None:
        raise BizError(
            code="ERR_VERSION_NOT_FOUND",
            message=f"历史版本 {version} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    rollback_items = [
        GradingThresholdItem.model_validate(t) for t in target.get("thresholds", [])
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

    logger.info(
        "定级阈值已回滚: from_version=%d, to_new_version=%d, operator=%s",
        version,
        result.version,
        user.username,
    )
    return success(data=result.model_dump(), message=f"已回滚到版本 {version}")


__all__ = ["router"]
