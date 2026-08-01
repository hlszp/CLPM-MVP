"""过程模型版本服务（V62-P3-004 并发一致性 + P3-006 引用基础）.

提供 ``process_model_version`` 的生命周期管理：
- ``create_candidate_version``: 从辨识结果创建 CANDIDATE（分配 version 号）
- ``publish_model_version``: CANDIDATE → CURRENT（原子退役旧 CURRENT）
- ``retire_model_version``: CURRENT → RETIRED
- ``get_current_version``: 查询回路当前 CURRENT

并发一致性设计（P3-004）：
- 同一回路至多一个 CURRENT，由两层防护：
  1. 服务层 ``SELECT ... FOR UPDATE`` 锁定同回路版本行，串行化并发 publish；
  2. 数据库部分唯一索引 ``uk_process_model_version_current`` 作为最后防线，
     即使绕过服务层也拒绝双 CURRENT。
- ``publish_model_version`` 在同一事务内：旧 CURRENT → RETIRED + 新 CANDIDATE → CURRENT，
  避免"中间态无 CURRENT"或"双 CURRENT"。

事务约定：本服务不自行 commit/rollback，由调用方（endpoint/task）控制事务边界。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.process_model_version import ProcessModelVersion

logger = logging.getLogger(__name__)


def _now_naive() -> datetime:
    """naive UTC datetime（DB 存储口径，与其他模型一致）."""
    return datetime.now(UTC).replace(tzinfo=None)


async def create_candidate_version(
    db: AsyncSession,
    *,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any] | None,
    identify_method: str | None = None,
    algorithm_version: str | None = None,
    theta_source: str | None = None,
    sampling_period: float | None = None,
    data_window_start: datetime | None = None,
    data_window_end: datetime | None = None,
    data_hash: str | None = None,
    condition_summary: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    residual_test: dict[str, Any] | None = None,
    uncertainty: dict[str, Any] | None = None,
    physical_feasibility: dict[str, Any] | None = None,
    confidence_level: str | None = None,
    confidence_reason: str | None = None,
    created_by: str | None = None,
) -> ProcessModelVersion:
    """从辨识结果创建 CANDIDATE 版本（P3-006 引用基础）.

    version 号分配：锁定同回路所有版本行后取 MAX+1。
    新建版本默认 status=CANDIDATE，不自动发布为 CURRENT（需人工审批）。

    Returns:
        新建的 ProcessModelVersion（status=CANDIDATE）
    """
    # 串行化 version 号分配：锁定同回路所有版本行，防止并发创建跳号或重号
    # PostgreSQL 不允许 FOR UPDATE 与聚合函数同用，先锁行再在 Python 取 max
    lock_result = await db.execute(
        select(ProcessModelVersion.version)
        .where(ProcessModelVersion.loop_id == loop_id)
        .with_for_update()
    )
    existing_versions = [int(v) for v in lock_result.scalars().all()]
    next_version = (max(existing_versions) if existing_versions else 0) + 1

    version = ProcessModelVersion(
        id=str(uuid4()),
        loop_id=loop_id,
        version=next_version,
        status="CANDIDATE",
        model_type=model_type,
        model_params=model_params,
        identify_method=identify_method,
        algorithm_version=algorithm_version,
        theta_source=theta_source,
        sampling_period=sampling_period,
        data_window_start=data_window_start,
        data_window_end=data_window_end,
        data_hash=data_hash,
        condition_summary=condition_summary,
        metrics=metrics,
        residual_test=residual_test,
        uncertainty=uncertainty,
        physical_feasibility=physical_feasibility,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        created_by=created_by,
    )
    db.add(version)
    await db.flush()  # 触发 id 生成，不 commit
    return version


async def get_current_version(db: AsyncSession, loop_id: str) -> ProcessModelVersion | None:
    """查询回路当前 CURRENT 模型版本（至多一个，由部分唯一索引保证）."""
    result = await db.execute(
        select(ProcessModelVersion)
        .where(
            ProcessModelVersion.loop_id == loop_id,
            ProcessModelVersion.status == "CURRENT",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def publish_model_version(
    db: AsyncSession,
    *,
    version_id: str,
    published_by: str,
) -> ProcessModelVersion:
    """将 CANDIDATE 发布为 CURRENT（V62-P3-004 并发一致性核心）.

    原子操作（同一事务内）：
    1. ``SELECT ... FOR UPDATE`` 锁定同回路所有版本行，串行化并发 publish；
    2. 校验目标版本存在且为 CANDIDATE；
    3. 同回路旧 CURRENT → RETIRED（retired_reason/at/by + supersedes 链回填）；
    4. 目标 CANDIDATE → CURRENT（published_by/at 回填）。

    并发安全：
    - 服务层 FOR UPDATE 锁串行化：并发 publish 同回路的两个 CANDIDATE 时，
      第二个会等待第一个事务提交后才拿到锁，此时旧 CURRENT 已退役、
      目标已是 CURRENT，第二个会因"目标不再是 CANDIDATE"报错。
    - 数据库部分唯一索引 ``uk_process_model_version_current`` 作为最后防线：
      即使绕过服务层，双 CURRENT 写入也会被唯一约束拒绝（IntegrityError）。

    Raises:
        BizError: ERR_MODEL_VERSION_NOT_FOUND / ERR_MODEL_VERSION_NOT_CANDIDATE
    """
    # 先取目标版本（不带锁，仅读取 loop_id 与状态）
    target_result = await db.execute(
        select(ProcessModelVersion).where(ProcessModelVersion.id == version_id)
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        raise BizError(
            code="ERR_MODEL_VERSION_NOT_FOUND",
            message="模型版本不存在",
            status_code=404,
        )
    loop_id = str(target.loop_id)

    # 锁定同回路所有版本行，串行化并发 publish（P3-004 核心）
    # FOR UPDATE 使并发 publish 同回路的第二个事务等待第一个提交
    await db.execute(
        select(ProcessModelVersion).where(ProcessModelVersion.loop_id == loop_id).with_for_update()
    )

    # 重新加载目标（锁后状态可能已被并发事务改变）
    await db.refresh(target)
    if target.status != "CANDIDATE":
        raise BizError(
            code="ERR_MODEL_VERSION_NOT_CANDIDATE",
            message=(
                f"模型版本 v{target.version} 状态为 {target.status}，仅 CANDIDATE 可发布为 CURRENT"
            ),
            status_code=409,
        )

    # 退役同回路旧 CURRENT（至多一个，由部分唯一索引保证）
    old_current = await get_current_version(db, loop_id)
    old_version_id: str | None = None
    if old_current is not None:
        old_version_id = str(old_current.id)
        old_current.status = "RETIRED"
        old_current.retired_reason = f"superseded by v{target.version}"
        old_current.retired_at = _now_naive()
        old_current.retired_by = published_by

    # 发布目标为 CURRENT
    target.status = "CURRENT"
    target.published_by = published_by
    target.published_at = _now_naive()
    if old_version_id is not None:
        target.supersedes_version_id = old_version_id

    logger.info(
        "publish_model_version: loop=%s v%s -> CURRENT (superseded v=%s, by=%s)",
        loop_id,
        target.version,
        old_version_id,
        published_by,
    )
    return target


async def retire_model_version(
    db: AsyncSession,
    *,
    version_id: str,
    reason: str,
    retired_by: str,
) -> ProcessModelVersion:
    """将 CURRENT/RETIRED（手动退役当前版本）.

    与 publish 的区别：publish 是"用新版本替代旧 CURRENT"，retire 是"直接退役
    当前 CURRENT 不设新版本"（回路进入无 CURRENT 状态）。

    Raises:
        BizError: ERR_MODEL_VERSION_NOT_FOUND / ERR_MODEL_VERSION_ALREADY_RETIRED
    """
    # 锁定同回路版本行
    target_result = await db.execute(
        select(ProcessModelVersion).where(ProcessModelVersion.id == version_id).with_for_update()
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        raise BizError(
            code="ERR_MODEL_VERSION_NOT_FOUND",
            message="模型版本不存在",
            status_code=404,
        )
    if target.status == "RETIRED":
        raise BizError(
            code="ERR_MODEL_VERSION_ALREADY_RETIRED",
            message=f"模型版本 v{target.version} 已退役",
            status_code=409,
        )
    target.status = "RETIRED"
    target.retired_reason = reason
    target.retired_at = _now_naive()
    target.retired_by = retired_by
    return target


__all__ = [
    "create_candidate_version",
    "get_current_version",
    "publish_model_version",
    "retire_model_version",
]
