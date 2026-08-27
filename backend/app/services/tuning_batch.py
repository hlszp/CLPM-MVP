"""tuning_batch 独立查询 service（追溯矩阵 docs/MVP设计/13 §6.2 GAP-2a）.

为 GET /tuning/batches（列表）与 GET /tuning/batches/{id}（详情）提供组装逻辑：
- 列表：分页 + status/时间窗（按 created_at）筛选，摘要含记录数与前置工单阻塞状态
- 详情：批次全字段 + 关联 tuning_record 列表（tuning_batch_records N:M）
  + scatters_before/after（JSONB 原样返回）+ 前置 handling_order 摘要

阻塞判定复用 workbench_tuning 的 B-06 口径（resolve_batch_status：
前置任一 PENDING/EXECUTING/VERIFYING → BLOCKED；终态不重算），
前置工单与记录统计批量查询（_query_prereq_orders/_query_batch_record_stats）
避免 N+1。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.tuning import TuningRecord
from app.models.tuning_batch import TuningBatch, TuningBatchRecords
from app.services.workbench_tuning import (
    _query_batch_record_stats,
    _query_prereq_orders,
    resolve_batch_status,
)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _resolve_prereqs(
    prereq_order_ids: list | None,
    prereq_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """prereq_order_ids → 前置工单摘要列表（不存在的工单按已闭合占位，同 A-04 口径）。"""
    orders: list[dict[str, Any]] = []
    for oid in prereq_order_ids or []:
        key = str(oid)
        o = prereq_map.get(key)
        if o is None:
            orders.append(
                {
                    "orderId": key,
                    "orderNo": key[:8],
                    "title": None,
                    "status": None,
                    "closed": True,
                }
            )
            continue
        orders.append(
            {
                "orderId": key,
                "orderNo": o.get("order_no"),
                "title": o.get("title"),
                "status": o.get("status"),
                "closed": o.get("status") in ("CLOSED", "CANCELLED"),
            }
        )
    return orders


def _batch_summary(
    batch: TuningBatch,
    prereq_orders: list[dict[str, Any]],
    record_count: int,
) -> dict[str, Any]:
    """批次摘要（camelCase；status 为 B-06 动态阻塞判定后的有效状态）。"""
    stored = batch.status or "PENDING"
    eff_status, eff_reason = resolve_batch_status(
        stored,
        [{"order_no": o.get("orderNo"), "status": o.get("status")} for o in prereq_orders],
    )
    return {
        "id": batch.id,
        "batchNo": batch.batch_no,
        "title": batch.title,
        "scopeType": batch.scope_type,
        "scopeId": batch.scope_id,
        "status": eff_status,
        "storedStatus": stored,
        "blocked": eff_status == "BLOCKED",
        "blockReason": eff_reason or batch.block_reason,
        "recordCount": record_count,
        "createdAt": _iso(batch.created_at),
    }


async def list_tuning_batches(
    db: AsyncSession,
    *,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """整定批次列表（分页 + status/created_at 时间窗筛选）。

    status 精确匹配库存状态（B-06 动态阻塞仅影响展示态，不作为过滤口径）；
    start_time/end_time 按 created_at 闭区间过滤（naive UTC）。
    """
    query = select(TuningBatch)
    count_query = select(func.count()).select_from(TuningBatch)
    if status:
        query = query.where(TuningBatch.status == status)
        count_query = count_query.where(TuningBatch.status == status)
    if start_time:
        query = query.where(TuningBatch.created_at >= start_time)
        count_query = count_query.where(TuningBatch.created_at >= start_time)
    if end_time:
        query = query.where(TuningBatch.created_at <= end_time)
        count_query = count_query.where(TuningBatch.created_at <= end_time)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(TuningBatch.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    batches = list(result.scalars().all())

    # 批量查询：记录统计 + 前置工单（避免 N+1）
    record_stats = await _query_batch_record_stats(db, [int(b.id) for b in batches])
    prereq_ids = sorted(
        {str(oid) for b in batches for oid in (b.prereq_order_ids or [])},
    )
    prereq_map = await _query_prereq_orders(db, prereq_ids)

    items = [
        _batch_summary(
            b,
            _resolve_prereqs(b.prereq_order_ids, prereq_map),
            int((record_stats.get(int(b.id)) or {}).get("loop_count") or 0),
        )
        for b in batches
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_tuning_batch_detail(db: AsyncSession, batch_id: int) -> dict[str, Any]:
    """整定批次详情：全字段 + N:M 关联记录 + 前置工单摘要 + scatters 原样返回。"""
    result = await db.execute(select(TuningBatch).where(TuningBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if batch is None:
        raise BizError(
            code="ERR_TUNING_BATCH_NOT_FOUND",
            message="整定批次不存在",
            status_code=404,
        )

    # 关联整定记录（tuning_batch_records N:M，按 sort_order 排序，附回路位号）
    records_result = await db.execute(
        select(TuningRecord, TuningBatchRecords.sort_order, LoopLedger.tag_name)
        .join(TuningBatchRecords, TuningBatchRecords.tuning_record_id == TuningRecord.id)
        .outerjoin(LoopLedger, TuningRecord.loop_id == LoopLedger.id)
        .where(TuningBatchRecords.batch_id == batch_id)
        .order_by(TuningBatchRecords.sort_order)
    )
    records: list[dict[str, Any]] = []
    for record, sort_order, tag_name in records_result.all():
        records.append(
            {
                "recordId": str(record.id),
                "sortOrder": sort_order,
                "loopId": str(record.loop_id),
                "tagName": tag_name,
                "modelType": record.model_type,
                "algorithm": record.algorithm,
                "status": record.status,
                "fittingScore": (float(record.fitting_score) if record.fitting_score else None),
                "createdBy": record.created_by,
                "createdAt": _iso(record.created_at),
            }
        )

    prereq_map = await _query_prereq_orders(
        db, [str(oid) for oid in (batch.prereq_order_ids or [])]
    )
    prereq_orders = _resolve_prereqs(batch.prereq_order_ids, prereq_map)
    stored = batch.status or "PENDING"
    eff_status, eff_reason = resolve_batch_status(
        stored,
        [{"order_no": o.get("orderNo"), "status": o.get("status")} for o in prereq_orders],
    )

    return {
        "id": batch.id,
        "batchNo": batch.batch_no,
        "title": batch.title,
        "scopeType": batch.scope_type,
        "scopeId": batch.scope_id,
        "status": eff_status,
        "storedStatus": stored,
        "blocked": eff_status == "BLOCKED",
        "blockReason": eff_reason or batch.block_reason,
        "prereqOrderIds": [str(oid) for oid in (batch.prereq_order_ids or [])],
        "prereqOrders": prereq_orders,
        "scattersBefore": batch.scatters_before,
        "scattersAfter": batch.scatters_after,
        "ownerId": batch.owner_id,
        "expectedStartAt": _iso(batch.expected_start_at),
        "actualStartAt": _iso(batch.actual_start_at),
        "completedAt": _iso(batch.completed_at),
        "createdAt": _iso(batch.created_at),
        "recordCount": len(records),
        "records": records,
    }
