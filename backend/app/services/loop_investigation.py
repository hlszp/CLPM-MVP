"""回路核查最小实体服务层（P6-05 后续分片一 E1，契约 01a §2.1）.

行为红线落点：

- 登录主体只来自 ``get_current_user``——所有函数以 ``user_id``/``role``
  形参接收服务端已认证主体，**不存在自由 userId 入参的 HTTP 通道**；
  分派身份只接受稳定 ``sys_user.id``（请求体无"按姓名分派"通道）；
- 阶段机 ``TODO → IN_PROGRESS ⇄ WAITING → CLOSED``（终态）：非法迁移
  一律 422（ERR_INVESTIGATION_STAGE_INVALID）；``WAITING`` 三要素
  （等待原因/复查条件/等待责任）进入必填、离开清空，DB CHECK 兜底；
- 结束结果 4 类；``HANDED_OFF`` 只能经 ``handoff_investigation`` 双记录
  原子路径达成（close 直传 HANDED_OFF → 422）；``CANCELLED`` 必须留
  取消理由——**取消 ≠ 回路正常**，不触碰任何回路/工单状态；
- 去重：``(loop_id, source_kind, source_id, problem_key)`` 在未结束记录
  上唯一（部分唯一索引硬保证）——同来源同问题再创建命中去重返回既有
  记录（``deduped=true``），并发首建由唯一索引裁决；已结束（含
  CANCELLED）不阻塞同问题再现新建；
- 全部写操作：``expectedRevision`` 行级 CAS（过期 409 携当前版本/阶段/
  负责人）+ ``last_request_id`` 幂等重放（``replayed=true`` 不推进版本）；
- 移交：同事务内源记录 CLOSED/HANDED_OFF（保存目标回链）+ 目标记录
  TODO 建立且接续责任真实落位（owner=接手人稳定 id）；接手人不存在/
  停用/为自己 → 400，事务回滚无双记录半成品；目标记录复制来源身份与
  问题身份（问题延续），不复制任何执行/验证状态；
- 撤权不泄露：读/写均经 ``ensure_loop_in_scope`` 通用 403 拒绝，不回显
  对象详情；旧姓名（owner_name 快照）只作显示，读方以 ``sys_user``
  当前姓名为准，稳定身份缺失时派生"待确认责任"（不猜测同名关联）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.loop_investigation import (
    INVESTIGATION_OUTCOMES,
    INVESTIGATION_STAGES,
    LoopInvestigation,
)
from app.models.sys_user import SysUser
from app.services.authz_scope import ScopeContext, ensure_loop_in_scope

# ---------------------------------------------------------------------------
# 白名单与常量
# ---------------------------------------------------------------------------

#: 来源身份类型白名单（HANDOFF 不对客户端开放——移交目标复制原始来源身份）
SOURCE_KIND_MANUAL = "MANUAL"
SOURCE_KIND_ALERT_EVENT = "ALERT_EVENT"
SOURCE_KIND_DIAGNOSIS_RUN = "DIAGNOSIS_RUN"
SOURCE_KIND_ASSESSMENT = "ASSESSMENT"
ALLOWED_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        SOURCE_KIND_MANUAL,
        SOURCE_KIND_ALERT_EVENT,
        SOURCE_KIND_DIAGNOSIS_RUN,
        SOURCE_KIND_ASSESSMENT,
    }
)

#: 结束目标回链类型白名单（loop_investigation 仅服务端移交路径写入）
CLOSE_TARGET_KIND_INVESTIGATION = "loop_investigation"
ALLOWED_CLOSE_TARGET_KINDS: frozenset[str] = frozenset(
    {"handling_order", "diagnosis_run", "loop_action_item"}
)

#: close 端点可直传的结束结果（HANDED_OFF 只能经 /handoff 双记录路径）
CLOSEABLE_OUTCOMES: frozenset[str] = frozenset({"NO_FURTHER_ACTION", "ANSWERED", "CANCELLED"})

ROLE_ADMIN = "ADMIN"


def _now() -> datetime:
    """服务端时间（naive UTC，与全库时间列写入口径一致）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _conflict_409(row: LoopInvestigation, message: str) -> BizError:
    return BizError(
        code="ERR_REVISION_CONFLICT",
        message=message,
        status_code=status.HTTP_409_CONFLICT,
        data={
            "investigationId": str(row.id),
            "currentRevision": int(row.revision),
            "stage": str(row.stage),
            "ownerId": str(row.owner_id) if row.owner_id is not None else None,
        },
    )


def _stage_invalid_422(message: str) -> BizError:
    return BizError(
        code="ERR_INVESTIGATION_STAGE_INVALID",
        message=message,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def _not_found_404() -> BizError:
    return BizError(
        code="ERR_INVESTIGATION_NOT_FOUND",
        message="核查记录不存在",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _forbidden_403(message: str = "仅核查负责人可执行该操作") -> BizError:
    return BizError(
        code="ERR_INVESTIGATION_FORBIDDEN",
        message=message,
        status_code=status.HTTP_403_FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# 入参校验
# ---------------------------------------------------------------------------


def validate_source_kind(kind: str) -> str:
    """来源身份类型白名单校验（未知类型一律 400；返回归一化大写值）."""
    normalized = kind.strip().upper()
    if normalized not in ALLOWED_SOURCE_KINDS:
        raise BizError(
            code="ERR_INVESTIGATION_SOURCE_INVALID",
            message=f"不支持的核查来源类型: {kind}",
            status_code=400,
        )
    return normalized


def _normalize_uuid(value: str, *, error: BizError) -> str:
    """UUID 校验 + 归一化（小写带连字符 PG 文本口径）.

    路径参数非法 → 404（防直撞 PG 类型报错且不泄露存在性）；请求体字段
    非法 → 400，由调用方经 ``error`` 指定。
    """
    try:
        return str(UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise error from None


def _loop_not_found_404() -> BizError:
    return BizError(
        code="ERR_LOOP_NOT_FOUND",
        message="回路不存在",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _validate_close_target(
    kind: str | None, target_id: str | None
) -> tuple[str | None, str | None]:
    """结束目标回链校验：类型白名单 + UUID 形态；kind/id 必须成对出现."""
    if kind is None and target_id is None:
        return None, None
    if kind is None or target_id is None:
        raise BizError(
            code="ERR_INVESTIGATION_TARGET_INVALID",
            message="目标回链 closeTargetKind 与 closeTargetId 必须成对提供",
            status_code=400,
        )
    normalized = kind.strip()
    if normalized not in ALLOWED_CLOSE_TARGET_KINDS:
        raise BizError(
            code="ERR_INVESTIGATION_TARGET_INVALID",
            message=f"不支持的目标回链类型: {kind}",
            status_code=400,
        )
    return normalized, _normalize_uuid(
        target_id,
        error=BizError(
            code="ERR_INVESTIGATION_TARGET_INVALID", message="目标回链 ID 格式非法", status_code=400
        ),
    )


# ---------------------------------------------------------------------------
# 视图装配（旧姓名只作显示；待确认责任派生）
# ---------------------------------------------------------------------------


async def _display_names(db: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    ids = [str(i) for i in user_ids if i]
    if not ids:
        return {}
    rows = await db.execute(select(SysUser.id, SysUser.display_name).where(SysUser.id.in_(ids)))
    return {str(r[0]): str(r[1]) for r in rows.all()}


def _view(row: LoopInvestigation, names: dict[str, str]) -> dict[str, Any]:
    owner_id = str(row.owner_id) if row.owner_id is not None else None
    if owner_id is not None:
        # 稳定身份在：姓名以 sys_user 当前显示名为准（改名即刷新）
        owner_display_name = names.get(owner_id, row.owner_name)
        responsibility_pending = False
    else:
        # 稳定身份缺失：回退分派时刻姓名快照（旧姓名只作显示），标待确认责任
        owner_display_name = row.owner_name
        responsibility_pending = row.owner_name is not None
    return {
        "id": str(row.id),
        "loop_id": str(row.loop_id),
        "source_kind": str(row.source_kind),
        "source_id": str(row.source_id),
        "problem_key": str(row.problem_key),
        "title": str(row.title),
        "detail": row.detail,
        "stage": str(row.stage),
        "outcome": row.outcome,
        "outcome_note": row.outcome_note,
        "owner_id": owner_id,
        "owner_display_name": owner_display_name,
        "responsibility_pending": responsibility_pending,
        "wait_reason": row.wait_reason,
        "wait_review_condition": row.wait_review_condition,
        "wait_responsible": row.wait_responsible,
        "close_target_kind": row.close_target_kind,
        "close_target_id": row.close_target_id,
        "handoff_source_id": (
            str(row.handoff_source_id) if row.handoff_source_id is not None else None
        ),
        "revision": int(row.revision),
        "created_by": str(row.created_by) if row.created_by is not None else None,
        "closed_by": str(row.closed_by) if row.closed_by is not None else None,
        "closed_at": row.closed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def _view_with_names(db: AsyncSession, row: LoopInvestigation) -> dict[str, Any]:
    ids = {str(row.owner_id)} if row.owner_id is not None else set()
    return _view(row, await _display_names(db, ids))


# ---------------------------------------------------------------------------
# 公共装载/鉴权
# ---------------------------------------------------------------------------


async def _ensure_loop_accessible(db: AsyncSession, ctx: ScopeContext, loop_id: str) -> None:
    """写入/读取前服务端校验：对象存在（404）→ 数据范围（403 通用拒绝）."""
    exists = (
        await db.execute(select(LoopLedger.id).where(LoopLedger.id == loop_id))
    ).scalar_one_or_none()
    if exists is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    await ensure_loop_in_scope(db, ctx, loop_id)


async def _lock_for_write(db: AsyncSession, investigation_id: str) -> LoopInvestigation:
    row = (
        await db.execute(
            select(LoopInvestigation)
            .where(LoopInvestigation.id == investigation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise _not_found_404()
    return row


def _check_revision(row: LoopInvestigation, expected_revision: int) -> None:
    if int(row.revision) != expected_revision:
        raise _conflict_409(row, "核查版本冲突，请刷新后重试")


def _check_owner(row: LoopInvestigation, user_id: str, role: str) -> None:
    """负责人或 ADMIN 可执行状态操作（未认领记录无负责人，先认领再操作）."""
    if role == ROLE_ADMIN:
        return
    if row.owner_id is None or str(row.owner_id) != user_id:
        raise _forbidden_403()


def _finish_close(row: LoopInvestigation, *, user_id: str, now: datetime) -> None:
    """进入 CLOSED 的公共收尾：清空 WAITING 三要素（DB CHECK 口径）."""
    row.stage = "CLOSED"
    row.wait_reason = None
    row.wait_review_condition = None
    row.wait_responsible = None
    row.closed_at = now
    row.closed_by = user_id


# ---------------------------------------------------------------------------
# 创建（来源+问题身份去重）
# ---------------------------------------------------------------------------


async def _find_by_request_id(
    db: AsyncSession,
    *,
    loop_id: str,
    source_kind: str,
    source_id: str,
    problem_key: str,
    request_id: str,
) -> LoopInvestigation | None:
    """同身份且由本请求创建的记录（最新一条；幂等重放判定用）."""
    return (
        (
            await db.execute(
                select(LoopInvestigation)
                .where(
                    LoopInvestigation.loop_id == loop_id,
                    LoopInvestigation.source_kind == source_kind,
                    LoopInvestigation.source_id == source_id,
                    LoopInvestigation.problem_key == problem_key,
                    LoopInvestigation.last_request_id == request_id,
                )
                .order_by(LoopInvestigation.created_at.desc(), LoopInvestigation.id.asc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _find_active_by_identity(
    db: AsyncSession,
    *,
    loop_id: str,
    source_kind: str,
    source_id: str,
    problem_key: str,
) -> LoopInvestigation | None:
    """同身份未结束记录（至多一条，部分唯一索引保证；去重判定用）."""
    return (
        (
            await db.execute(
                select(LoopInvestigation)
                .where(
                    LoopInvestigation.loop_id == loop_id,
                    LoopInvestigation.source_kind == source_kind,
                    LoopInvestigation.source_id == source_id,
                    LoopInvestigation.problem_key == problem_key,
                    LoopInvestigation.stage != "CLOSED",
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def create_investigation(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    loop_id: str,
    source_kind: str,
    source_id: str | None,
    problem_key: str,
    title: str,
    detail: str | None,
    request_id: str,
) -> dict[str, Any]:
    """登记核查（TODO/未认领）；按来源身份+问题身份去重.

    - 同一回路同一来源同一问题已存在**未结束**核查 → 返回既有记录
      （``deduped=true``，不新建）；已结束（含 CANCELLED）不阻塞同问题
      再现新建（取消≠问题解决）；
    - 并发首建由部分唯一索引裁决，败方重读后按去重返回；
    - 同 requestId 重试幂等返回（``replayed=true``）。
    """
    loop_id = _normalize_uuid(loop_id, error=_loop_not_found_404())
    source_kind = validate_source_kind(source_kind)
    if source_id is None or not source_id.strip():
        if source_kind != SOURCE_KIND_MANUAL:
            raise BizError(
                code="ERR_INVESTIGATION_SOURCE_INVALID",
                message="非 MANUAL 来源必须提供 sourceId",
                status_code=400,
            )
        source_id = f"manual:{user_id}"
    else:
        source_id = source_id.strip()
    problem_key = problem_key.strip()
    title = title.strip()
    if not problem_key or not title:
        raise BizError(
            code="ERR_INVESTIGATION_INPUT_INVALID",
            message="problemKey/title 不能为空",
            status_code=400,
        )
    await _ensure_loop_accessible(db, ctx, loop_id)

    # 同 requestId 重试：返回本请求已建记录（幂等优先于去重语义）
    replay_row = await _find_by_request_id(
        db,
        loop_id=loop_id,
        source_kind=source_kind,
        source_id=source_id,
        problem_key=problem_key,
        request_id=request_id,
    )
    if replay_row is not None:
        return {
            "item": await _view_with_names(db, replay_row),
            "deduped": False,
            "replayed": True,
        }
    # 同身份未结束记录：去重返回，不新建
    existing = await _find_active_by_identity(
        db,
        loop_id=loop_id,
        source_kind=source_kind,
        source_id=source_id,
        problem_key=problem_key,
    )
    if existing is not None:
        return {
            "item": await _view_with_names(db, existing),
            "deduped": True,
            "replayed": False,
        }

    now = _now()
    row = LoopInvestigation(
        id=str(uuid4()),
        loop_id=loop_id,
        source_kind=source_kind,
        source_id=source_id,
        problem_key=problem_key,
        title=title,
        detail=detail,
        stage="TODO",
        revision=1,
        last_request_id=request_id,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # 部分唯一索引裁决并发首建：败方重读，命中去重返回
        await db.rollback()
        current = await _find_active_by_identity(
            db,
            loop_id=loop_id,
            source_kind=source_kind,
            source_id=source_id,
            problem_key=problem_key,
        )
        if current is not None:
            return {
                "item": await _view_with_names(db, current),
                "deduped": current.last_request_id != request_id,
                "replayed": current.last_request_id == request_id,
            }
        raise
    return {"item": await _view_with_names(db, row), "deduped": False, "replayed": False}


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


async def list_loop_investigations(
    db: AsyncSession,
    *,
    ctx: ScopeContext,
    loop_id: str,
    stage: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """回路核查列表（stage 过滤/分页/稳定排序；越权 403 通用拒绝）."""
    loop_id = _normalize_uuid(loop_id, error=_loop_not_found_404())
    await _ensure_loop_accessible(db, ctx, loop_id)
    if stage is not None:
        stage = stage.strip().upper()
        if stage not in INVESTIGATION_STAGES:
            raise _stage_invalid_422(f"非法核查阶段: {stage}")

    stmt = select(LoopInvestigation).where(LoopInvestigation.loop_id == loop_id)
    if stage is not None:
        stmt = stmt.where(LoopInvestigation.stage == stage)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (
        (
            await db.execute(
                stmt.order_by(
                    LoopInvestigation.created_at.desc(),
                    LoopInvestigation.id.asc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    names = await _display_names(db, {str(r.owner_id) for r in rows if r.owner_id is not None})
    return {
        "items": [_view(r, names) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "read_at": _now(),
    }


async def get_investigation(
    db: AsyncSession,
    *,
    ctx: ScopeContext,
    investigation_id: str,
) -> dict[str, Any]:
    """核查详情（每次打开重新校验数据范围；越权 403 通用拒绝）."""
    investigation_id = _normalize_uuid(investigation_id, error=_not_found_404())
    row = (
        await db.execute(select(LoopInvestigation).where(LoopInvestigation.id == investigation_id))
    ).scalar_one_or_none()
    if row is None:
        raise _not_found_404()
    await ensure_loop_in_scope(db, ctx, str(row.loop_id))
    return await _view_with_names(db, row)


# ---------------------------------------------------------------------------
# 认领（TODO → IN_PROGRESS；并发 CAS）
# ---------------------------------------------------------------------------


async def claim_investigation(
    db: AsyncSession,
    *,
    user_id: str,
    role: str,
    ctx: ScopeContext,
    investigation_id: str,
    expected_revision: int,
    request_id: str,
) -> dict[str, Any]:
    """认领核查（并发认领由行锁+revision CAS 服务端裁决，败方 409）.

    未认领 TODO：任意范围内用户可认领；移交预留 TODO（owner 已落位）：
    仅接手人本人（或 ADMIN）可认领，其他人 403——接续责任不被截胡。
    """
    investigation_id = _normalize_uuid(investigation_id, error=_not_found_404())
    row = await _lock_for_write(db, investigation_id)
    await ensure_loop_in_scope(db, ctx, str(row.loop_id))
    if row.last_request_id == request_id:
        return {"item": await _view_with_names(db, row), "replayed": True}
    _check_revision(row, expected_revision)
    if row.stage != "TODO":
        raise _stage_invalid_422(f"仅待认领（TODO）核查可认领，当前阶段: {row.stage}")
    reserved_for_other = row.owner_id is not None and str(row.owner_id) != user_id
    if reserved_for_other and role != ROLE_ADMIN:
        raise _forbidden_403("核查已指定负责人（移交预留），仅接手人可认领")

    user_row = (await db.execute(select(SysUser).where(SysUser.id == user_id))).scalar_one_or_none()
    if user_row is None:
        raise _forbidden_403("认领用户不存在")
    now = _now()
    row.owner_id = user_id
    row.owner_name = str(user_row.display_name)
    row.stage = "IN_PROGRESS"
    row.revision = int(row.revision) + 1
    row.last_request_id = request_id
    row.updated_at = now
    await db.commit()
    return {"item": await _view_with_names(db, row), "replayed": False}


# ---------------------------------------------------------------------------
# 等待 / 恢复（IN_PROGRESS ⇄ WAITING）
# ---------------------------------------------------------------------------


async def wait_investigation(
    db: AsyncSession,
    *,
    user_id: str,
    role: str,
    ctx: ScopeContext,
    investigation_id: str,
    expected_revision: int,
    request_id: str,
    wait_reason: str,
    wait_review_condition: str,
    wait_responsible: str,
) -> dict[str, Any]:
    """进入等待（三要素必填；离开 WAITING 必须清空，DB CHECK 兜底）."""
    investigation_id = _normalize_uuid(investigation_id, error=_not_found_404())
    row = await _lock_for_write(db, investigation_id)
    await ensure_loop_in_scope(db, ctx, str(row.loop_id))
    if row.last_request_id == request_id:
        return {"item": await _view_with_names(db, row), "replayed": True}
    _check_revision(row, expected_revision)
    if row.stage != "IN_PROGRESS":
        raise _stage_invalid_422(f"仅核查中（IN_PROGRESS）可进入等待，当前阶段: {row.stage}")
    _check_owner(row, user_id, role)

    now = _now()
    row.stage = "WAITING"
    row.wait_reason = wait_reason.strip()
    row.wait_review_condition = wait_review_condition.strip()
    row.wait_responsible = wait_responsible.strip()
    row.revision = int(row.revision) + 1
    row.last_request_id = request_id
    row.updated_at = now
    await db.commit()
    return {"item": await _view_with_names(db, row), "replayed": False}


async def resume_investigation(
    db: AsyncSession,
    *,
    user_id: str,
    role: str,
    ctx: ScopeContext,
    investigation_id: str,
    expected_revision: int,
    request_id: str,
) -> dict[str, Any]:
    """恢复核查（清空 WAITING 三要素）."""
    investigation_id = _normalize_uuid(investigation_id, error=_not_found_404())
    row = await _lock_for_write(db, investigation_id)
    await ensure_loop_in_scope(db, ctx, str(row.loop_id))
    if row.last_request_id == request_id:
        return {"item": await _view_with_names(db, row), "replayed": True}
    _check_revision(row, expected_revision)
    if row.stage != "WAITING":
        raise _stage_invalid_422(f"仅等待中（WAITING）可恢复核查，当前阶段: {row.stage}")
    _check_owner(row, user_id, role)

    now = _now()
    row.stage = "IN_PROGRESS"
    row.wait_reason = None
    row.wait_review_condition = None
    row.wait_responsible = None
    row.revision = int(row.revision) + 1
    row.last_request_id = request_id
    row.updated_at = now
    await db.commit()
    return {"item": await _view_with_names(db, row), "replayed": False}


# ---------------------------------------------------------------------------
# 结束（→ CLOSED；取消 ≠ 回路正常）
# ---------------------------------------------------------------------------


async def close_investigation(
    db: AsyncSession,
    *,
    user_id: str,
    role: str,
    ctx: ScopeContext,
    investigation_id: str,
    expected_revision: int,
    request_id: str,
    outcome: str,
    note: str | None,
    close_target_kind: str | None,
    close_target_id: str | None,
) -> dict[str, Any]:
    """结束核查（NO_FURTHER_ACTION/ANSWERED/CANCELLED）.

    - HANDED_OFF 直传 422——移交必须走 /handoff 双记录原子路径；
    - CANCELLED 必填取消理由（取消≠回路正常，理由强制留痕）；取消不
      触碰回路/工单任何状态，结束结果仅属本核查记录；
    - 目标回链只存引用（白名单类型+UUID 形态），不复制目标执行/验证状态。
    """
    outcome = outcome.strip().upper()
    if outcome == "HANDED_OFF":
        raise _stage_invalid_422("移交结束必须经 /handoff 路径（双记录原子），不可直接关闭")
    if outcome not in CLOSEABLE_OUTCOMES:
        raise BizError(
            code="ERR_INVESTIGATION_OUTCOME_INVALID",
            message=f"非法结束结果: {outcome}（允许: {sorted(CLOSEABLE_OUTCOMES)}）",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if outcome == "CANCELLED" and (note is None or not note.strip()):
        raise BizError(
            code="ERR_INVESTIGATION_OUTCOME_INVALID",
            message="取消核查必须填写取消理由（取消不等于回路正常）",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    close_target_kind, close_target_id = _validate_close_target(close_target_kind, close_target_id)

    investigation_id = _normalize_uuid(investigation_id, error=_not_found_404())
    row = await _lock_for_write(db, investigation_id)
    await ensure_loop_in_scope(db, ctx, str(row.loop_id))
    if row.last_request_id == request_id:
        return {"item": await _view_with_names(db, row), "replayed": True}
    _check_revision(row, expected_revision)
    if row.stage == "CLOSED":
        raise _stage_invalid_422("核查已结束（CLOSED 为终态）")
    _check_owner(row, user_id, role)

    now = _now()
    _finish_close(row, user_id=user_id, now=now)
    row.outcome = outcome
    row.outcome_note = note.strip() if note else None
    row.close_target_kind = close_target_kind
    row.close_target_id = close_target_id
    row.revision = int(row.revision) + 1
    row.last_request_id = request_id
    row.updated_at = now
    await db.commit()
    return {"item": await _view_with_names(db, row), "replayed": False}


# ---------------------------------------------------------------------------
# 移交（HANDED_OFF 双记录原子）
# ---------------------------------------------------------------------------


async def handoff_investigation(
    db: AsyncSession,
    *,
    user_id: str,
    role: str,
    ctx: ScopeContext,
    investigation_id: str,
    expected_revision: int,
    request_id: str,
    new_owner_id: str,
    note: str | None,
) -> dict[str, Any]:
    """移交核查：源记录 CLOSED/HANDED_OFF + 目标记录 TODO 同事务原子建立.

    移交仅在目标记录与接续责任真实建立后成立：接手人必须是存在的启用
    用户（稳定 sys_user.id，拒绝按姓名移交），目标记录 owner 落位、
    ``handoff_source_id`` 回指源记录；任一校验失败整体回滚，源记录
    不留半成品。目标复制来源身份与问题身份（问题延续），不复制任何
    执行/验证状态。
    """
    new_owner_id = _normalize_uuid(
        new_owner_id,
        error=BizError(
            code="ERR_INVESTIGATION_TARGET_INVALID", message="接手人 ID 格式非法", status_code=400
        ),
    )
    investigation_id = _normalize_uuid(investigation_id, error=_not_found_404())
    row = await _lock_for_write(db, investigation_id)
    await ensure_loop_in_scope(db, ctx, str(row.loop_id))
    if row.last_request_id == request_id and row.stage == "CLOSED":
        target = (
            await db.execute(
                select(LoopInvestigation).where(LoopInvestigation.id == row.close_target_id)
            )
        ).scalar_one_or_none()
        names = await _display_names(
            db,
            {str(r.owner_id) for r in (row, target) if r is not None and r.owner_id is not None},
        )
        return {
            "source": _view(row, names),
            "target": _view(target, names) if target is not None else None,
            "replayed": True,
        }
    _check_revision(row, expected_revision)
    if row.stage not in ("IN_PROGRESS", "WAITING"):
        raise _stage_invalid_422(f"仅核查中/等待中可移交（先认领建立责任），当前阶段: {row.stage}")
    _check_owner(row, user_id, role)
    if row.owner_id is not None and new_owner_id == str(row.owner_id):
        raise BizError(
            code="ERR_INVESTIGATION_TARGET_INVALID",
            message="接手人与当前负责人相同，无需移交",
            status_code=400,
        )

    successor = (
        await db.execute(select(SysUser).where(SysUser.id == new_owner_id))
    ).scalar_one_or_none()
    if successor is None or not successor.is_active:
        raise BizError(
            code="ERR_INVESTIGATION_TARGET_INVALID",
            message="接手人不存在或已停用，移交未成立",
            status_code=400,
        )

    now = _now()
    target = LoopInvestigation(
        id=str(uuid4()),
        loop_id=str(row.loop_id),
        source_kind=str(row.source_kind),
        source_id=str(row.source_id),
        problem_key=str(row.problem_key),
        title=str(row.title),
        stage="TODO",
        owner_id=new_owner_id,
        owner_name=str(successor.display_name),
        handoff_source_id=str(row.id),
        revision=1,
        last_request_id=request_id,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(target)

    _finish_close(row, user_id=user_id, now=now)
    row.outcome = "HANDED_OFF"
    row.outcome_note = note.strip() if note else None
    row.close_target_kind = CLOSE_TARGET_KIND_INVESTIGATION
    row.close_target_id = str(target.id)
    row.revision = int(row.revision) + 1
    row.last_request_id = request_id
    row.updated_at = now

    # 单事务提交：双记录原子（任一方失败整体回滚，无半成品）
    await db.commit()
    names = await _display_names(db, {new_owner_id})
    return {
        "source": _view(row, names),
        "target": _view(target, names),
        "replayed": False,
    }


__all__ = [
    "ALLOWED_CLOSE_TARGET_KINDS",
    "ALLOWED_SOURCE_KINDS",
    "CLOSEABLE_OUTCOMES",
    "INVESTIGATION_OUTCOMES",
    "INVESTIGATION_STAGES",
    "claim_investigation",
    "close_investigation",
    "create_investigation",
    "get_investigation",
    "handoff_investigation",
    "list_loop_investigations",
    "resume_investigation",
    "validate_source_kind",
    "wait_investigation",
]
