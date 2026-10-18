"""可恢复的工程属性批次：复用不可变 sys_audit_log，不新增数据库结构。

快照与业务变更同事务提交；恢复只重放服务端快照，不接收客户端 before 值。
行锁和完整状态令牌共同阻止覆盖后来编辑；每行确定性恢复审计保证重试幂等。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.dcs_model import DcsModel
from app.models.loop import LoopLedger
from app.models.sys_user import SysUser
from app.services.authz_scope import (
    ScopeContext,
    domain_guard_enabled,
    ensure_loops_in_scope,
    lock_guard_for_share,
    resolve_scope,
)
from app.services.loop_batch import batch_update_loops

FIELDS = (
    "control_action_direction",
    "op_output_lower_limit",
    "op_output_upper_limit",
    "dcs_model_id",
    "engineering_template_id",
    "engineering_template_version",
)
SNAPSHOT_OPERATION = "LOOP_BATCH_SNAPSHOT"
RESTORE_OPERATION = "LOOP_BATCH_RESTORE"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _state(loop: LoopLedger) -> dict:
    state = {field: getattr(loop, field) for field in FIELDS}
    for field in ("dcs_model_id", "engineering_template_id"):
        if state[field] is not None:
            state[field] = str(state[field])
    return state


def _token(loop: LoopLedger) -> str:
    # updated_at 也参与令牌：先改为 B 再改回 A（ABA）仍须重新预览。
    updated_at = loop.updated_at.isoformat() if loop.updated_at else None
    return hashlib.sha256(
        _json({"state": _state(loop), "updatedAt": updated_at}).encode()
    ).hexdigest()


def _request(loop_ids: list[str], updates: dict, tokens: dict[str, str]) -> dict:
    if not loop_ids or len(loop_ids) > 200 or len(set(loop_ids)) != len(loop_ids):
        raise BizError("ERR_BATCH_INVALID_FIELD", "工程快照批次须为 1~200 个不重复回路", 422)
    if not updates or set(updates) - set(FIELDS):
        raise BizError("ERR_BATCH_INVALID_FIELD", "工程快照批次仅支持工程属性字段", 422)
    updates = dict(updates)
    if "engineering_template_id" in updates:
        # 选择 follow 或解除关联必须清除原 pin，不能继承另一模板的版本。
        updates.setdefault("engineering_template_version", None)
        if updates["engineering_template_id"] is None and updates["engineering_template_version"]:
            raise BizError("ERR_BATCH_INVALID_FIELD", "解除模板关联时不能保留固定版本", 422)
    return {"loopIds": loop_ids, "updates": updates, "expectedStateTokens": tokens}


async def _rows(db: AsyncSession, loop_ids: list[str], *, lock: bool) -> dict[str, LoopLedger]:
    stmt = select(LoopLedger).where(LoopLedger.id.in_(loop_ids)).order_by(LoopLedger.id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    rows = (await db.execute(stmt)).scalars().all()
    return {str(row.id): row for row in rows}


async def _lock_write_scope(
    db: AsyncSession, operator_id: str | None, scope: ScopeContext | None
) -> ScopeContext | None:
    """复用现有授权发布栅栏：撤权/范围变更不能插入复核与本次提交之间。"""
    if operator_id is None or scope is None or not domain_guard_enabled():
        return scope
    from app.services.authz_runtime import get_role_codes

    revision = await lock_guard_for_share(db)
    user = (await db.execute(select(SysUser).where(SysUser.id == operator_id))).scalar_one_or_none()
    if user is None or not user.is_active or user.role == "SPONSOR":
        raise BizError("ERR_PERMISSION_DENIED", "当前主体已失去批量维护权限", 403)
    granted = await get_role_codes(db, user.role)
    if not any(code in granted for code in ("*", "loop:*", "loop:batch-config")):
        raise BizError("ERR_PERMISSION_DENIED", "当前主体已失去批量维护权限", 403)
    return await resolve_scope(db, operator_id, user.role, revision=revision)


async def _snapshot(
    db: AsyncSession, snapshot_id: str, operator: str, operator_id: str | None = None
) -> tuple[SysAuditLog, dict]:
    log = await db.get(SysAuditLog, snapshot_id)
    # 不泄露其他操作者批次的存在性；有批量权限仍不能恢复他人的动作。
    if log is None or log.operation_type != SNAPSHOT_OPERATION:
        raise BizError("ERR_BATCH_SNAPSHOT_NOT_FOUND", "快照不存在或不属于当前操作者", 404)
    payload = json.loads(log.before_value)
    if payload.get("ownerId") != (operator_id or operator):
        raise BizError("ERR_BATCH_SNAPSHOT_NOT_FOUND", "快照不存在或不属于当前操作者", 404)
    if payload.get("version") != 1:
        raise BizError("ERR_BATCH_SNAPSHOT_VERSION", "快照格式不支持，禁止猜测恢复", 409)
    return log, payload


async def run_snapshot_batch(
    db: AsyncSession,
    loop_ids: list[str],
    updates: dict,
    operator: str,
    *,
    operation_id: str,
    dry_run: bool,
    expected_state_tokens: dict[str, str] | None = None,
    scope: ScopeContext | None = None,
    operator_id: str | None = None,
) -> dict:
    """工程批量预览/执行；每个 chunk 一个稳定 operationId，失败重试不能重新生成。"""
    request = _request(loop_ids, updates, expected_state_tokens or {})
    if not dry_run:
        scope = await _lock_write_scope(db, operator_id, scope)
    if scope is not None:
        await ensure_loops_in_scope(db, scope, loop_ids)
    updates = request["updates"]
    fingerprint = hashlib.sha256(_json(request).encode()).hexdigest()
    if not dry_run:
        # 对同一幂等 ID 串行化，含不同回路列表并发复用 ID 的情况。
        key = int.from_bytes(hashlib.sha256(operation_id.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        existing = await db.get(SysAuditLog, operation_id)
        if existing is not None:
            log, payload = await _snapshot(db, operation_id, operator, operator_id)
            if payload["fingerprint"] != fingerprint:
                raise BizError(
                    "ERR_BATCH_OPERATION_REUSED", "operationId 已用于不同请求，请重新预览", 409
                )
            return {**json.loads(log.after_value), "replayed": True}

    loops = await _rows(db, loop_ids, lock=not dry_run)
    if scope is not None and any(not scope.node_in_scope(row.unit_id) for row in loops.values()):
        raise BizError("ERR_SCOPE_FORBIDDEN", "无权访问该数据范围外的对象", 403)
    before = {lid: {"state": _state(row), "token": _token(row)} for lid, row in loops.items()}
    conflicts = []
    eligible = []
    for lid in loop_ids:
        row = loops.get(lid)
        if (
            row is not None
            and not dry_run
            and request["expectedStateTokens"].get(lid) != _token(row)
        ):
            conflicts.append(
                {
                    "loopId": lid,
                    "tagName": row.tag_name,
                    "status": "CONFLICT",
                    "changes": [],
                    "error": "预览后回路已变更或缺少状态令牌，请重新预览",
                }
            )
        else:
            eligible.append(lid)
    result = {"affected": 0, "dryRun": dry_run, "results": []}
    if eligible:
        result = await batch_update_loops(
            db,
            eligible,
            updates,
            operator,
            dry_run=dry_run,
            commit=False,
            allow_null_engineering=True,
        )
    result["results"].extend(conflicts)
    result["results"].sort(key=lambda item: loop_ids.index(item["loopId"]))
    for item in result["results"]:
        if item["loopId"] in before:
            item["beforeState"] = before[item["loopId"]]["state"]
            item["stateToken"] = before[item["loopId"]]["token"]
    result.update(snapshotId=None, replayed=False)
    if dry_run:
        return result

    changed = {item["loopId"] for item in result["results"] if item["status"] == "OK"}
    now = datetime.now(UTC).replace(tzinfo=None)
    for lid in changed:
        loops[lid].updated_at = now
    await db.flush()
    entries = [
        {
            "loopId": lid,
            "tagName": loops[lid].tag_name,
            "before": before[lid]["state"],
            "after": _state(loops[lid]),
            "afterToken": _token(loops[lid]),
        }
        for lid in loop_ids
        if lid in changed
    ]
    result["snapshotId"] = operation_id
    db.add(
        SysAuditLog(
            id=operation_id,
            operator=operator,
            operation_type=SNAPSHOT_OPERATION,
            target_type="loop_batch_snapshot",
            target_id=operator_id or operation_id,
            before_value=_json(
                {
                    "version": 1,
                    "ownerId": operator_id or operator,
                    "fingerprint": fingerprint,
                    "request": request,
                    "entries": entries,
                }
            ),
            after_value=_json(result),
            operated_at=now,
        )
    )
    await db.commit()
    return result


async def list_batch_snapshots(
    db: AsyncSession, operator: str, *, offset: int, limit: int, operator_id: str | None = None
) -> dict:
    where = (
        SysAuditLog.operation_type == SNAPSHOT_OPERATION,
        SysAuditLog.target_id == operator_id if operator_id else SysAuditLog.operator == operator,
    )
    total = (
        await db.execute(select(func.count()).select_from(SysAuditLog).where(*where))
    ).scalar_one()
    logs = (
        (
            await db.execute(
                select(SysAuditLog)
                .where(*where)
                .order_by(SysAuditLog.operated_at.desc(), SysAuditLog.id)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "snapshotId": log.id,
                "operator": log.operator,
                "createdAt": log.operated_at.isoformat(),
                "affected": json.loads(log.after_value)["affected"],
            }
            for log in logs
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


async def get_batch_snapshot(
    db: AsyncSession,
    snapshot_id: str,
    operator: str,
    *,
    offset: int,
    limit: int,
    scope: ScopeContext | None = None,
    operator_id: str | None = None,
) -> dict:
    log, payload = await _snapshot(db, snapshot_id, operator, operator_id)
    entries = payload["entries"]
    if scope is not None:
        await ensure_loops_in_scope(db, scope, [entry["loopId"] for entry in entries])
    return {
        "snapshotId": snapshot_id,
        "operator": log.operator,
        "createdAt": log.operated_at.isoformat(),
        "items": entries[offset : offset + limit],
        "total": len(entries),
        "offset": offset,
        "limit": limit,
        "expiresAt": None,
        "retentionNote": "依赖审计日志保留；日志缺失时明确拒绝恢复",
    }


async def _validate_restore(db: AsyncSession, state: dict, loop_id: str) -> None:
    if state["dcs_model_id"] is not None:
        if await db.get(DcsModel, state["dcs_model_id"]) is None:
            raise BizError("ERR_DCS_MODEL_NOT_FOUND", "原 DCS 型号已删除，不能恢复", 409)
    if state["engineering_template_id"] is not None:
        from app.services.engineering_template import validate_loop_template_ref

        await validate_loop_template_ref(
            db,
            template_id=state["engineering_template_id"],
            version=state["engineering_template_version"],
        )
    from app.models.loop import LoopTagMapping
    from app.models.tag import TagRegistry
    from app.services.loop import _validate_op_output_limits

    tag = (
        await db.execute(
            select(TagRegistry.range_min, TagRegistry.range_max)
            .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopTagMapping.loop_id == loop_id, LoopTagMapping.tag_role == "OP")
        )
    ).first()
    _validate_op_output_limits(
        lower=state["op_output_lower_limit"],
        upper=state["op_output_upper_limit"],
        op_range_min=float(tag[0]) if tag and tag[0] is not None else None,
        op_range_max=float(tag[1]) if tag and tag[1] is not None else None,
    )


async def restore_batch_snapshot(
    db: AsyncSession,
    snapshot_id: str,
    operator: str,
    *,
    reason: str,
    dry_run: bool,
    loop_ids: list[str] | None = None,
    scope: ScopeContext | None = None,
    operator_id: str | None = None,
) -> dict:
    """逐回路比较原动作后的完整状态/时间，再恢复原状态（含 NULL 和各自 pin）。"""
    _, payload = await _snapshot(db, snapshot_id, operator, operator_id)
    entries = {entry["loopId"]: entry for entry in payload["entries"]}
    selected = list(entries) if loop_ids is None else loop_ids
    if len(selected) > 200 or len(set(selected)) != len(selected) or set(selected) - set(entries):
        raise BizError("ERR_BATCH_RESTORE_SCOPE", "恢复范围必须属于该快照且不超过 200 条", 422)
    if not dry_run:
        scope = await _lock_write_scope(db, operator_id, scope)
    if scope is not None:
        await ensure_loops_in_scope(db, scope, selected)
    rows = await _rows(db, selected, lock=not dry_run)
    if scope is not None and any(not scope.node_in_scope(row.unit_id) for row in rows.values()):
        raise BizError("ERR_SCOPE_FORBIDDEN", "无权访问该数据范围外的对象", 403)
    results = []
    affected = 0
    for lid in selected:
        entry = entries[lid]
        item = {
            "loopId": lid,
            "tagName": entry["tagName"],
            "status": "OK",
            "changes": [],
            "error": None,
        }
        row = rows.get(lid)
        if row is None:
            item.update(status="NOT_FOUND", error="回路已删除，无法恢复")
            results.append(item)
            continue
        restore_id = str(uuid5(NAMESPACE_URL, f"clpm:loop-batch-restore:{snapshot_id}:{lid}"))
        previous = await db.get(SysAuditLog, restore_id)
        if previous is not None:
            restored = json.loads(previous.after_value)
            if restored.get("ownerId") == (operator_id or operator) and restored["token"] == _token(
                row
            ):
                item["status"] = "ALREADY_RESTORED"
            else:
                item.update(status="CONFLICT", error="该回路恢复后又被编辑，禁止覆盖")
        elif _token(row) != entry["afterToken"]:
            item.update(status="CONFLICT", error="原动作后回路已被编辑，请人工核对；未覆盖当前值")
        else:
            try:
                await _validate_restore(db, entry["before"], lid)
            except BizError as exc:
                item.update(status="FAIL", error=exc.message)
            if item["status"] == "OK":
                item["changes"] = [
                    {
                        "field": field,
                        "before": entry["after"][field],
                        "after": entry["before"][field],
                    }
                    for field in FIELDS
                    if entry["after"][field] != entry["before"][field]
                ]
                affected += 1
                if not dry_run:
                    current = _state(row)
                    for field in FIELDS:
                        setattr(row, field, entry["before"][field])
                    row.updated_by = operator
                    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
                    db.add(
                        SysAuditLog(
                            id=restore_id,
                            operator=operator,
                            operation_type=RESTORE_OPERATION,
                            target_type="loop_ledger",
                            target_id=lid,
                            before_value=_json(
                                {"snapshotId": snapshot_id, "reason": reason, "state": current}
                            ),
                            after_value=_json(
                                {
                                    "state": _state(row),
                                    "token": _token(row),
                                    "ownerId": operator_id or operator,
                                }
                            ),
                            operated_at=row.updated_at,
                        )
                    )
        results.append(item)
    if not dry_run:
        await db.commit()
    return {"snapshotId": snapshot_id, "affected": affected, "dryRun": dry_run, "results": results}
