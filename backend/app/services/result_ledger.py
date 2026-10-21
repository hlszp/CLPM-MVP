"""结果账本（calculation_result_record）读写服务（P1-05）.

契约：《01-系统改造优化方案》§3.1 + 《P0-02-数值语义冻结-v2》§2（DEC-10 已裁决）。

- 追加不可变 record：同唯一键 ``(logicalRunId, objectKind, objectId, tsStart,
  tsEnd)`` 已存在时**复用**既有 record（同 logicalRunId 重试幂等），不重复建。
- logicalRunId 推导：
  - Celery 任务链路用 celery task id（Celery retry 复用同一 task id → 重试幂等）；
  - 自定义评估用 task_id + 窗口（同任务重跑幂等，新任务=新逻辑运行）；
  - 无上下文时按 (source, source_task_id, 窗口) 确定性推导；
  - 节点聚合按**内容寻址**（聚合输入与输出 canonical payload 哈希）——
    同输入重跑幂等复用，输入变化（如下游回路 record 更新）即新版本。
- 旧表 ID 经 ``calculation_result_legacy_map`` 兼容读。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.timeparse import to_naive_utc
from app.models.calculation_result import (
    LEGACY_TABLE_HOURLY,
    LEGACY_TABLE_LATEST,
    LEGACY_TABLE_NODE_HOURLY,
    CalculationResultAttempt,
    CalculationResultLegacyMap,
    CalculationResultRecord,
)

# 存量归档行 algorithmVersion/configRevision 未知时的显式标记（不伪补版本）
LEGACY_UNVERIFIABLE = "LEGACY_UNVERIFIABLE"
# P1 期尚未接入配置版本体系（P2-02 落地）时的诚实占位；新写入优先传内容摘要
CONFIG_REVISION_UNVERSIONED = "UNVERSIONED"

OBJECT_KIND_LOOP = "LOOP"
OBJECT_KIND_NODE = "NODE"

RECORD_STATUS_COMPLETED = "COMPLETED"
RECORD_STATUS_FAILED = "FAILED"

UQ_CONSTRAINT = "uq_calc_result_record_run_object_window"

# 兼容读遍历顺序：先记录本身，再按归档映射逐表查
LEGACY_TABLES: tuple[str, ...] = (
    LEGACY_TABLE_HOURLY,
    LEGACY_TABLE_LATEST,
    LEGACY_TABLE_NODE_HOURLY,
)

# 固定 namespace（uuid5 确定性推导用）
_RUN_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "clpm:calculation-result-ledger")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# canonical JSON / 摘要
# ---------------------------------------------------------------------------


def canonical_value(value: Any) -> Any:
    """把业务值转为 JSON 安全结构（Decimal→float，datetime→ISO，递归）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime) or isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): canonical_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_value(v) for v in value]
    return str(value)


def canonical_json(value: Any) -> str:
    """确定性 JSON 串（键排序），用于内容摘要与 payload 落库前规整。"""
    return json.dumps(
        canonical_value(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def content_digest(value: Any) -> str:
    """canonical JSON 的 SHA256 摘要（16 位十六进制）。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def config_revision_digest(parts: dict) -> str:
    """实际消费配置（类型权重、fitness 阈值等）的内容摘要作为 configRevision。

    P1 期没有全局配置 revision（P2-02 落地），用实际参与计算的配置内容
    摘要保证：配置内容变 → record 的 configRevision 变，可区分、不伪补。
    """
    return f"digest:{content_digest(parts)}"


# ---------------------------------------------------------------------------
# logicalRunId 推导
# ---------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    return to_naive_utc(ts).isoformat()


def derive_loop_logical_run_id(
    ts_start: datetime,
    *,
    celery_task_id: str | None = None,
    custom_task_id: str | None = None,
    source: str | None = None,
    source_task_id: str | None = None,
) -> str:
    """回路评估 logicalRunId（确定性 uuid5）。

    优先级：celery task id（retry 复用 → 幂等）> 自定义 task_id+窗口 >
    (source, source_task_id, 窗口)。显式重评走不同 celery 任务/任务记录
    → 自然得到新 logicalRunId，旧 record 不被覆盖。
    """
    if celery_task_id:
        # 窗口进推导：一个 Celery 任务（如回填窗口批）可跨多个窗口，
        # 每窗口仍是独立逻辑运行（唯一键含窗口，同任务重试按窗幂等）
        return str(uuid.uuid5(_RUN_NAMESPACE, f"celery:{celery_task_id}:{_iso(ts_start)}"))
    if custom_task_id:
        return str(uuid.uuid5(_RUN_NAMESPACE, f"custom:{custom_task_id}:{_iso(ts_start)}"))
    return str(
        uuid.uuid5(
            _RUN_NAMESPACE,
            f"std:{source or '-'}:{source_task_id or '-'}:{_iso(ts_start)}",
        )
    )


def derive_node_logical_run_id(
    plant_node_id: str,
    ts_start: datetime,
    payload: dict,
) -> str:
    """节点聚合 logicalRunId（内容寻址）。

    节点聚合是回路 record 的确定性派生：输入（参与聚合的 loop recordIds
    与各字段）+ 输出值完全一致 → 复用同一 record（重跑/重试幂等）；
    任何变化（如回路 record 换版本后重聚合、实时自控率漂移）→ 新版本
    record，与投影切换语义一致。
    """
    return str(
        uuid.uuid5(
            _RUN_NAMESPACE,
            f"node:{plant_node_id}:{_iso(ts_start)}:{content_digest(payload)}",
        )
    )


# ---------------------------------------------------------------------------
# 追加（幂等）
# ---------------------------------------------------------------------------


def _record_key_filter(
    logical_run_id: str,
    object_kind: str,
    object_id: str,
    ts_start: datetime,
    ts_end: datetime,
):
    return (
        CalculationResultRecord.logical_run_id == str(logical_run_id),
        CalculationResultRecord.object_kind == object_kind,
        CalculationResultRecord.object_id == str(object_id),
        CalculationResultRecord.ts_start == to_naive_utc(ts_start),
        CalculationResultRecord.ts_end == to_naive_utc(ts_end),
    )


async def append_result_record(
    db,
    *,
    logical_run_id: str,
    object_kind: str,
    object_id: str,
    ts_start: datetime,
    ts_end: datetime,
    algorithm_version: str,
    config_revision: str,
    payload: dict,
    status: str = RECORD_STATUS_COMPLETED,
    source_record_id: str | None = None,
    dataset_snapshot_id: str | None = None,
) -> CalculationResultRecord | None:
    """追加不可变 record；同唯一键已存在时复用（同 logicalRunId 重试幂等）。

    与投影切换在同一事务（调用方同一 session 提交）。并发插入竞态由
    ``ON CONFLICT DO NOTHING`` 兜底：冲突后回读复用既有 record。

    P5-02（G2 遗留③）显式重评链：``source_record_id`` 由调用方经
    ``latest_failed_source_id`` 解析后显式传入（批/单回路入口先经
    ``loop_ids_with_failed_records`` 一次窗口级门查询判定是否存在 FAILED
    前身，常见空集路径零逐 record 查询——本函数自身不内联查找，保持
    P1-05 唯一幂等汇合点行为不变）。
    """
    key = _record_key_filter(logical_run_id, object_kind, object_id, ts_start, ts_end)

    # 新评估是热路径：直接追加，由唯一约束处理重试，省去逐回路前置查询。
    stmt = (
        pg_insert(CalculationResultRecord)
        .values(
            id=str(uuid.uuid4()),
            logical_run_id=str(logical_run_id),
            object_kind=object_kind,
            object_id=str(object_id),
            ts_start=to_naive_utc(ts_start),
            ts_end=to_naive_utc(ts_end),
            source_record_id=source_record_id,
            algorithm_version=algorithm_version,
            config_revision=config_revision,
            dataset_snapshot_id=dataset_snapshot_id,
            status=status,
            payload=canonical_value(payload),
        )
        .on_conflict_do_nothing(constraint=UQ_CONSTRAINT)
        .returning(CalculationResultRecord)
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        # 单独回读取得 READ COMMITTED 的新快照；不能合并成同一条 CTE，
        # 否则并发提交的胜者可能不在 INSERT 开始时的查询快照内。
        record = (
            await db.execute(select(CalculationResultRecord).where(*key))
        ).scalar_one_or_none()
    return record


async def loop_ids_with_failed_records(
    db,
    ts_start: datetime,
    ts_end: datetime,
    *,
    object_ids: list[str] | None = None,
    object_kind: str = OBJECT_KIND_LOOP,
) -> set[str]:
    """窗口级门查询：本窗存在 FAILED 终态 record 的对象 id 集合（P5-02）.

    批/单回路入口一次调用判定"是否存在 FAILED 前身"：空集（常见路径）→
    后续逐 record 落库完全跳过 sourceRecordId 解析（零额外查询）；命中
    对象才走 ``latest_failed_source_id`` 单条点查。失败按空集处理（链接
    链是审计增强，不阻断结果落库）。
    """
    try:
        cond = [
            CalculationResultRecord.object_kind == object_kind,
            CalculationResultRecord.ts_start == to_naive_utc(ts_start),
            CalculationResultRecord.ts_end == to_naive_utc(ts_end),
            CalculationResultRecord.status == RECORD_STATUS_FAILED,
        ]
        if object_ids is not None:
            cond.append(CalculationResultRecord.object_id.in_([str(x) for x in object_ids]))
        rows = (
            (await db.execute(select(CalculationResultRecord.object_id).where(*cond).distinct()))
            .scalars()
            .all()
        )
        return {str(r) for r in rows}
    except Exception:  # noqa: BLE001 — 门查询失败按空集（链接链不阻断主链）
        return set()


async def latest_failed_source_id(
    db,
    *,
    logical_run_id: str,
    object_kind: str,
    object_id: str,
    ts_start: datetime,
    ts_end: datetime,
) -> str | None:
    """查同 (对象, 窗口) 最近一条 FAILED 终态 record（不同 logicalRunId）.

    供 COMPLETED record 的显式重评链（sourceRecordId）；无 FAILED 前身时
    返回 None。命中 idx_calc_result_record_object_window 索引，单条点查。
    查询失败按 None 处理（链接链是审计增强，不阻断结果落库）。
    """
    try:
        row = (
            await db.execute(
                select(CalculationResultRecord.id)
                .where(
                    CalculationResultRecord.object_kind == object_kind,
                    CalculationResultRecord.object_id == str(object_id),
                    CalculationResultRecord.ts_start == to_naive_utc(ts_start),
                    CalculationResultRecord.ts_end == to_naive_utc(ts_end),
                    CalculationResultRecord.status == RECORD_STATUS_FAILED,
                    CalculationResultRecord.logical_run_id != str(logical_run_id),
                )
                .order_by(CalculationResultRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return str(row) if row is not None else None
    except Exception:  # noqa: BLE001 — 链接失败不阻断主链
        return None


async def append_failed_result_record(
    db,
    *,
    logical_run_id: str,
    object_kind: str,
    object_id: str,
    ts_start: datetime,
    ts_end: datetime,
    algorithm_version: str,
    config_revision: str,
    error: BaseException | str,
    worker_pid: int | str | None = None,
) -> CalculationResultRecord | None:
    """终末失败（第 3 次 attempt 仍失败）追加 ``status=FAILED`` 不可变终态 record.

    P0-02 冻结 v2 §2.2 重试终点（P5-02 落地）：

    - 仅在**终末尝试**调用（暂态失败只写 attempt 日志，不落终态）；
    - 同唯一键已有任何 record（此前尝试成功/已写 FAILED）→ 复用不重写
      （幂等，与 append_result_record 同口径）；
    - payload 含最终错误摘要（errorClass/errorMessage/workerPid），快照
      业务键 snapshotStatus=FAILED；
    - 失败证据不可变：后续同窗重算走新 logicalRunId 的 COMPLETED record
      并经 append_result_record 自动回链 sourceRecordId。
    """
    error_class = type(error).__name__ if isinstance(error, BaseException) else "Error"
    error_message = str(error)[:1800] or error_class
    return await append_result_record(
        db,
        logical_run_id=logical_run_id,
        object_kind=object_kind,
        object_id=object_id,
        ts_start=ts_start,
        ts_end=ts_end,
        algorithm_version=algorithm_version,
        config_revision=config_revision,
        status=RECORD_STATUS_FAILED,
        payload={
            "snapshotStatus": "FAILED",
            "error": {
                "errorClass": error_class,
                "errorMessage": error_message,
                "workerPid": str(worker_pid) if worker_pid is not None else None,
            },
        },
    )


# ---------------------------------------------------------------------------
# 计算尝试日志（P5-02，G2 遗留③；P0-02 冻结 v2 §2.2）
# ---------------------------------------------------------------------------


def derive_attempt_no(task_request) -> int:
    """Celery 任务请求 → attempt 序号（1=首跑；retry 复用同一 task id）."""
    return int(getattr(task_request, "retries", 0) or 0) + 1


def is_final_attempt(task_request, max_retries: int) -> bool:
    """当前尝试是否为终末尝试（再失败即耗尽重试，须落 FAILED 终态）."""
    return int(getattr(task_request, "retries", 0) or 0) >= max_retries


async def start_calculation_attempt(
    logical_run_id: str, attempt_no: int, worker_pid: int | str | None = None
) -> str | None:
    """开始一次计算尝试：插入 attempt 行（best-effort，独立短会话）.

    ``ON CONFLICT (logical_run_id, attempt_no) DO NOTHING``：Worker 重启/
    消息重投导致同 task id 同 attempt_no 再执行时不重复建行（幂等）。
    失败仅记日志——attempt 日志是可观测性增强，绝不阻断计算主链。
    """
    from datetime import UTC, datetime
    from uuid import uuid4 as _uuid4

    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    from app.core.db import AsyncSessionLocal

    if not logical_run_id:
        return None
    try:
        async with AsyncSessionLocal() as db:
            stmt = (
                _pg_insert(CalculationResultAttempt)
                .values(
                    id=str(_uuid4()),
                    logical_run_id=str(logical_run_id),
                    attempt_no=attempt_no,
                    worker_pid=str(worker_pid) if worker_pid is not None else None,
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                )
                .on_conflict_do_nothing(constraint="uq_calc_attempt_run_no")
            )
            await db.execute(stmt)
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "attempt 日志写入失败（忽略）: logical_run_id=%s attempt_no=%s",
            logical_run_id,
            attempt_no,
            exc_info=True,
        )
        return None
    return str(logical_run_id)


async def finish_calculation_attempt(
    logical_run_id: str,
    attempt_no: int,
    error: BaseException | None = None,
) -> None:
    """结束一次计算尝试：补 finished_at 与错误摘要（best-effort）.

    仅更新尚未结束的行（finished_at IS NULL）：重投递并发收尾不互相
    覆盖；成功完成时 error_class/error_message 保持 NULL。
    """
    from datetime import UTC, datetime

    from sqlalchemy import update as _update

    from app.core.db import AsyncSessionLocal

    if not logical_run_id:
        return
    error_class = type(error).__name__ if error is not None else None
    error_message = str(error)[:1800] if error is not None else None
    try:
        async with AsyncSessionLocal() as db:
            stmt = (
                _update(CalculationResultAttempt)
                .where(
                    CalculationResultAttempt.logical_run_id == str(logical_run_id),
                    CalculationResultAttempt.attempt_no == attempt_no,
                    CalculationResultAttempt.finished_at.is_(None),
                )
                .values(
                    finished_at=datetime.now(UTC).replace(tzinfo=None),
                    error_class=error_class,
                    error_message=error_message,
                )
            )
            await db.execute(stmt)
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "attempt 日志收尾失败（忽略）: logical_run_id=%s attempt_no=%s",
            logical_run_id,
            attempt_no,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# 读取（recordId 直读 + 旧 ID 映射兼容）
# ---------------------------------------------------------------------------


async def get_record_by_id(db, record_id: str) -> CalculationResultRecord | None:
    """按不可变 recordId 读取结果记录。"""
    result = await db.execute(
        select(CalculationResultRecord).where(CalculationResultRecord.id == str(record_id))
    )
    return result.scalar_one_or_none()


async def resolve_legacy_id(
    db,
    legacy_table: str,
    legacy_id: str,
) -> CalculationResultRecord | None:
    """旧投影表行 ID → 归档 record（经 calculation_result_legacy_map）。"""
    result = await db.execute(
        select(CalculationResultRecord)
        .join(
            CalculationResultLegacyMap,
            CalculationResultLegacyMap.record_id == CalculationResultRecord.id,
        )
        .where(
            CalculationResultLegacyMap.legacy_table == legacy_table,
            CalculationResultLegacyMap.legacy_id == str(legacy_id),
        )
    )
    return result.scalar_one_or_none()


async def find_record_any_id(
    db,
    any_id: str,
) -> tuple[CalculationResultRecord | None, str | None]:
    """按 recordId 或旧投影行 ID 读取（历史详情兼容入口）。

    Returns:
        (record, matched_via)；matched_via 为 "record" 或 "legacy:{table}"，
        未命中返回 (None, None)。
    """
    record = await get_record_by_id(db, any_id)
    if record is not None:
        return record, "record"
    for table in LEGACY_TABLES:
        record = await resolve_legacy_id(db, table, any_id)
        if record is not None:
            return record, f"legacy:{table}"
    return None, None


def record_to_dict(record: CalculationResultRecord) -> dict:
    """record ORM → API dict（payload 原样透出，含各指标分母/loopRecordIds）。"""
    return {
        "recordId": str(record.id),
        "logicalRunId": str(record.logical_run_id),
        "objectKind": record.object_kind,
        "objectId": str(record.object_id),
        "tsStart": record.ts_start.isoformat() if record.ts_start else None,
        "tsEnd": record.ts_end.isoformat() if record.ts_end else None,
        "sourceRecordId": str(record.source_record_id) if record.source_record_id else None,
        "algorithmVersion": record.algorithm_version,
        "configRevision": record.config_revision,
        "datasetSnapshotId": (
            str(record.dataset_snapshot_id) if record.dataset_snapshot_id else None
        ),
        "status": record.status,
        "payload": record.payload,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
    }


__all__ = [
    "CONFIG_REVISION_UNVERSIONED",
    "LEGACY_TABLES",
    "LEGACY_UNVERIFIABLE",
    "OBJECT_KIND_LOOP",
    "OBJECT_KIND_NODE",
    "RECORD_STATUS_COMPLETED",
    "RECORD_STATUS_FAILED",
    "append_failed_result_record",
    "append_result_record",
    "canonical_json",
    "canonical_value",
    "config_revision_digest",
    "content_digest",
    "derive_attempt_no",
    "derive_loop_logical_run_id",
    "derive_node_logical_run_id",
    "find_record_any_id",
    "finish_calculation_attempt",
    "get_record_by_id",
    "is_final_attempt",
    "latest_failed_source_id",
    "loop_ids_with_failed_records",
    "record_to_dict",
    "resolve_legacy_id",
    "start_calculation_attempt",
]
