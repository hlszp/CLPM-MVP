"""过程模型版本迁移服务（V62-P3-005）.

实施"一次性回填 → 影子读比对 → 切换读取 → 停止旧参数新写"四步迁移策略，
将 ``tuning_record.model_params`` 的唯一写所有者迁移到 ``process_model_version``。

迁移策略（v6.2 方案 §10）：
1. **一次性回填**：为每个有 ``model_params`` 且未关联版本的 ``tuning_record``
   创建对应的 ``process_model_version`` CANDIDATE，回填 FK；
2. **影子读比对**：双源读取 ``tuning_record.model_params`` 与
   ``process_model_version.model_params``，断言一致；
3. **切换读取**：读路径优先从 ``process_model_version`` 读取，FK 为空时回退旧字段；
4. **停止旧参数新写**：新辨识结果写入 ``process_model_version``（CANDIDATE），
   ``tuning_record.model_params`` 不再接收新写，仅保留遗留快照只读兼容。

事务约定：本服务不自行 commit/rollback，由调用方控制事务边界。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.process_model_version import ProcessModelVersion
from app.models.tuning import TuningRecord
from app.services.process_model_version import create_candidate_version

logger = logging.getLogger(__name__)


async def backfill_model_versions_from_tuning_records(
    db: AsyncSession,
    *,
    batch_size: int = 500,
) -> dict[str, int]:
    """一次性回填：为遗留 tuning_record 创建 process_model_version CANDIDATE.

    遍历所有 ``model_params IS NOT NULL`` 且 ``process_model_version_id IS NULL``
    的 ``tuning_record``，为每条记录创建一个 ``process_model_version`` CANDIDATE
    （携带相同 model_type / model_params / identify_method / 可信度等元数据），
    并回填 ``tuning_record.process_model_version_id`` 外键。

    幂等：已关联版本的记录跳过，重复执行无副作用。

    Returns:
        {"backfilled": N, "skipped": M, "total_scanned": T}
    """
    total_scanned = 0
    backfilled = 0
    skipped = 0

    # 分批扫描未关联版本且有 model_params 的遗留记录
    while True:
        result = await db.execute(
            select(TuningRecord)
            .where(
                TuningRecord.model_params.isnot(None),
                TuningRecord.process_model_version_id.is_(None),
            )
            .order_by(TuningRecord.created_at.asc())
            .limit(batch_size)
        )
        records = list(result.scalars().all())
        if not records:
            break

        total_scanned += len(records)
        for record in records:
            # 双保险：model_params 必须是 dict
            if not isinstance(record.model_params, dict):
                skipped += 1
                continue

            version = await create_candidate_version(
                db,
                loop_id=str(record.loop_id),
                model_type=str(record.model_type),
                model_params=dict(record.model_params),
                identify_method=record.identify_method,
                algorithm_version=None,
                theta_source=_extract_theta_source(record.confidence_reason),
                sampling_period=None,
                data_window_start=record.time_window_start,
                data_window_end=record.time_window_end,
                data_hash=None,
                condition_summary=None,
                metrics=_build_metrics_snapshot(record),
                residual_test=_build_residual_test_snapshot(record),
                uncertainty=None,
                physical_feasibility=None,
                confidence_level=record.confidence_level,
                confidence_reason=record.confidence_reason,
                created_by=record.created_by,
            )
            record.process_model_version_id = str(version.id)
            backfilled += 1

        await db.flush()

    logger.info(
        "backfill_model_versions_from_tuning_records: scanned=%d backfilled=%d skipped=%d",
        total_scanned,
        backfilled,
        skipped,
    )
    return {
        "backfilled": backfilled,
        "skipped": skipped,
        "total_scanned": total_scanned,
    }


def _extract_theta_source(confidence_reason: str | None) -> str | None:
    """从 confidence_reason 中提取 theta_source 标记（HEURISTIC_2TS 等）."""
    if not confidence_reason:
        return None
    upper = confidence_reason.upper()
    if "THETA_SOURCE=HEURISTIC_2TS" in upper:
        return "HEURISTIC_2TS"
    if "THETA_SOURCE=EXPLICIT" in upper:
        return "EXPLICIT"
    if "THETA_SOURCE=SEARCHED" in upper:
        return "SEARCHED"
    return None


def _build_metrics_snapshot(record: TuningRecord) -> dict[str, Any] | None:
    """从 tuning_record 提取验证指标快照."""
    if record.fitting_score is None and record.excitation_score is None:
        return None
    snapshot: dict[str, Any] = {}
    if record.fitting_score is not None:
        snapshot["fitting_score"] = float(record.fitting_score)
    if record.excitation_score is not None:
        snapshot["excitation_score"] = float(record.excitation_score)
    return snapshot or None


def _build_residual_test_snapshot(record: TuningRecord) -> dict[str, Any] | None:
    """从 tuning_record 提取残差检验快照."""
    if record.residual_test_passed is None:
        return None
    return {"passed": bool(record.residual_test_passed)}


async def shadow_read_compare(
    db: AsyncSession,
    record: TuningRecord,
) -> dict[str, Any]:
    """影子读比对：双源读取 model_params，返回比对结果.

    用于迁移验证阶段：确认回填后 ``tuning_record.model_params`` 与
    ``process_model_version.model_params`` 一致。

    Returns:
        {
            "record_id": str,
            "version_id": str | None,
            "old_params": dict | None,      # tuning_record.model_params
            "new_params": dict | None,      # process_model_version.model_params
            "match": bool,                  # 是否一致
            "mismatch_keys": list[str],     # 不一致字段
        }
    """
    old_params = record.model_params
    version_id = record.process_model_version_id

    new_params: dict[str, Any] | None = None
    if version_id is not None:
        result = await db.execute(
            select(ProcessModelVersion.model_params).where(ProcessModelVersion.id == version_id)
        )
        new_params = result.scalar_one_or_none()

    mismatch_keys: list[str] = []
    if old_params is None and new_params is None:
        match = True
    elif old_params is None or new_params is None:
        match = False
        mismatch_keys = ["__presence__"]
    else:
        match, mismatch_keys = _compare_params(old_params, new_params)

    return {
        "record_id": str(record.id),
        "version_id": str(version_id) if version_id else None,
        "old_params": old_params,
        "new_params": new_params,
        "match": match,
        "mismatch_keys": mismatch_keys,
    }


def _compare_params(old: dict[str, Any], new: dict[str, Any]) -> tuple[bool, list[str]]:
    """比较两组 model_params，返回 (是否一致, 不一致字段列表).

    数值字段用 ``math.isclose`` 容忍浮点误差。
    """
    import math

    all_keys = set(old.keys()) | set(new.keys())
    mismatch: list[str] = []
    for key in all_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val is None and new_val is None:
            continue
        if old_val is None or new_val is None:
            mismatch.append(key)
            continue
        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
            if not math.isclose(float(old_val), float(new_val), rel_tol=1e-9, abs_tol=1e-12):
                mismatch.append(key)
        elif old_val != new_val:
            mismatch.append(key)
    return len(mismatch) == 0, mismatch


async def shadow_read_batch_verify(
    db: AsyncSession,
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """批量影子读比对验证：扫描所有已关联版本的 tuning_record，返回不一致列表.

    用于回填后的全量验证。

    Returns:
        {
            "total_verified": int,
            "matched": int,
            "mismatched": int,
            "mismatches": list[dict],   # 不一致详情（前 100 条）
        }
    """
    total = 0
    matched = 0
    mismatched = 0
    mismatches: list[dict[str, Any]] = []
    max_mismatch_samples = 100

    while True:
        result = await db.execute(
            select(TuningRecord)
            .where(
                TuningRecord.process_model_version_id.isnot(None),
                TuningRecord.model_params.isnot(None),
            )
            .order_by(TuningRecord.created_at.asc())
            .limit(batch_size)
            .offset(total)
        )
        records = list(result.scalars().all())
        if not records:
            break

        for record in records:
            total += 1
            comparison = await shadow_read_compare(db, record)
            if comparison["match"]:
                matched += 1
            else:
                mismatched += 1
                if len(mismatches) < max_mismatch_samples:
                    mismatches.append(comparison)

    logger.info(
        "shadow_read_batch_verify: total=%d matched=%d mismatched=%d",
        total,
        matched,
        mismatched,
    )
    return {
        "total_verified": total,
        "matched": matched,
        "mismatched": mismatched,
        "mismatches": mismatches,
    }


async def get_effective_model_params(
    db: AsyncSession, record: TuningRecord
) -> dict[str, Any] | None:
    """读路径切换：优先从 process_model_version 读取 model_params.

    迁移后的读路径入口：
    - 若 ``record.process_model_version_id`` 非空，从 ``process_model_version`` 读取；
    - 否则回退到 ``record.model_params``（遗留兼容）。

    这是 P3-005 步骤 3"切换读取"的核心：调用方不再直接访问
    ``record.model_params``，而是通过本函数获取生效参数。
    """
    version_id = record.process_model_version_id
    if version_id is not None:
        result = await db.execute(
            select(ProcessModelVersion.model_params).where(ProcessModelVersion.id == version_id)
        )
        params = result.scalar_one_or_none()
        if params is not None:
            return dict(params)
        # 版本被删除（SET NULL 外键可能未级联）：回退旧字段并告警
        logger.warning(
            "get_effective_model_params: version %s 不存在，回退到 record.model_params",
            version_id,
        )
    # 遗留路径：直接读 tuning_record.model_params
    if isinstance(record.model_params, dict):
        return dict(record.model_params)
    return None


async def count_records_without_version(db: AsyncSession) -> int:
    """统计未关联版本且有 model_params 的遗留记录数（回填进度监控）."""
    result = await db.execute(
        select(func.count())
        .select_from(TuningRecord)
        .where(
            TuningRecord.model_params.isnot(None),
            TuningRecord.process_model_version_id.is_(None),
        )
    )
    return int(result.scalar() or 0)


__all__ = [
    "backfill_model_versions_from_tuning_records",
    "shadow_read_compare",
    "shadow_read_batch_verify",
    "get_effective_model_params",
    "count_records_without_version",
]
