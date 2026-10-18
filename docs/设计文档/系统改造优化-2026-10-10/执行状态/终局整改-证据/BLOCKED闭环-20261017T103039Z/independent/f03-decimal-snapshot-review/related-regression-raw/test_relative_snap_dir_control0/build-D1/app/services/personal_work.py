"""统一工作查询服务层（P6-05 后续分片二 E2，契约 01a §2.1/§3 + 09§4.2/4.3）.

``GET /me/work`` 跨来源统一工作查询（**纯查询**：不新增/修改任何表）：

- 四 workKind：
  - ``ASSIGNED`` 分配给我——``loop_investigation.owner_id``（未结束）+
    ``handling_order.handler_id``（进行中状态）。稳定责任只用 ``sys_user.id``
    列；action_item 侧的"建议审核责任"当前**无稳定列**（reviewed_by 等旧
    姓名字段按契约不得冒充稳定分派身份，不猜测归给同名用户），已转工单的
    建议经 ``suggestion_ids`` 稳定链并入工单工作项证据（同一事项不重复
    累加）；独立审核责任待"稳定审核/验证责任"分片落列后接入；
  - ``SYSTEM_SUGGESTION`` 系统建议——``loop_action_item`` 中
    source=SYSTEM 且 status=PENDING（待审核池，责任范围内未认领）；
  - ``FOLLOW`` 我的关注——``user_loop_follow.is_followed``；
  - ``RESUME`` 最近继续——``user_work_resume``（当前代际、未删除）。
- 分区互斥（先判等待 → 再判今日计划 → 其余现在可行动）：仅 ASSIGNED
  参与分区；``WAITING``=核查 WAITING 阶段；``TODAY``=计划日期=工作日期
  （服务端 UTC 日期）；``FUTURE``/``CLOSED`` 不进默认视图，仅经筛选查询；
  工作日期只用于计划/期限，未完工作不按"今天有更新"筛掉。
- 排序=有效优先级+期限+稳定 ID：``_rank``（逾期/SLA  breach > SLA 预警/
  重开/高优先级建议 > 常规分配/建议 > 关注 > 最近继续）→ dueAt 升序
  （NULL 最后）→ workKind → workId；计数与分页同规则；keyset cursor
  稳定分页（游标含全排序键，不随插入漂移）。
- 诚实化：任一来源失败 → ``sourceStatuses[src]=ERROR``、``total=None``
  （**不填伪造 0**）、``readStatus=PARTIAL``，健康来源数据仍可见；全部
  来源失败 → ``readStatus=ERROR``；撤权（scope 收窄）对象保留但不回显
  名称/状态/证据——仅从 items 剔除并计入 ``inaccessibleCount``
  （readStatus=PARTIAL，同 personal_workspace 惯例）；权限每次查询经
  SQL 级 scope 过滤 + ``loops_in_scope`` 批校验重新校验。
- 空待办 ≠ 健康：响应不输出任何健康断言。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger
from app.models.loop_action_item import LoopActionItem
from app.models.loop_investigation import LoopInvestigation
from app.models.personal_workspace import UserLoopFollow, UserWorkResume
from app.models.plant_node import PlantNode
from app.models.sys_user import SysUser
from app.schemas.personal_work import (
    PARTITION_ACTIONABLE,
    PARTITION_CLOSED,
    PARTITION_FUTURE,
    PARTITION_TODAY,
    PARTITION_WAITING,
    PARTITIONS,
    READ_STATUS_COMPLETE,
    READ_STATUS_ERROR,
    READ_STATUS_PARTIAL,
    SOURCE_STATUS_ERROR,
    SOURCE_STATUS_OK,
    WORK_KIND_ASSIGNED,
    WORK_KIND_FOLLOW,
    WORK_KIND_RESUME,
    WORK_KIND_SYSTEM_SUGGESTION,
    WORK_KINDS,
)
from app.services.authz_scope import ScopeContext, loops_in_scope
from app.services.personal_workspace import (
    RESOURCE_KIND_LOOP,
    get_resume_generation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 统一来源名（sourceStatuses 键）
SOURCE_INVESTIGATION = "investigation"
SOURCE_HANDLING_ORDER = "handling_order"
SOURCE_ACTION_ITEM = "action_item"
SOURCE_FOLLOW = "follow"
SOURCE_RESUME = "resume"

#: workKind → 参与聚合的来源（ASSIGNED 双来源共用同一视图）
_WORK_KIND_SOURCES: dict[str, tuple[str, ...]] = {
    WORK_KIND_ASSIGNED: (SOURCE_INVESTIGATION, SOURCE_HANDLING_ORDER),
    WORK_KIND_SYSTEM_SUGGESTION: (SOURCE_ACTION_ITEM,),
    WORK_KIND_FOLLOW: (SOURCE_FOLLOW,),
    WORK_KIND_RESUME: (SOURCE_RESUME,),
}

#: 工单进行中状态（分配给我的执行/验证责任）；CLOSED/CANCELLED 仅经筛选查询
_ORDER_ACTIVE_STATUSES = ("PENDING", "EXECUTING", "VERIFYING", "REOPENED")
_ORDER_CLOSED_STATUSES = ("CLOSED", "CANCELLED")

#: 有效优先级档位（越小越靠前；priorityReasons 逐项给出业务理由）
_RANK_OVERDUE = 0  # 逾期 / SLA breach
_RANK_WARN = 1  # SLA 预警 / 重开 / 高优先级建议
_RANK_NORMAL = 2  # 常规分配工作 / 常规系统建议
_RANK_FOLLOW = 3  # 我的关注（不承担工作责任）
_RANK_RESUME = 4  # 最近继续（不自动变成待办）

_MAX_EVIDENCE_REFS = 20


def _now() -> datetime:
    """服务端时间（naive UTC，与全库时间列写入口径一致）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _filter_invalid_400(message: str) -> BizError:
    return BizError(code="ERR_WORK_FILTER_INVALID", message=message, status_code=400)


# ---------------------------------------------------------------------------
# 筛选校验（白名单；非法一律 400，先于 DB 访问）
# ---------------------------------------------------------------------------


def validate_work_kind(kind: str) -> str:
    """workKind 白名单校验（归一化大写；未知值 400）."""
    normalized = kind.strip().upper()
    if normalized not in WORK_KINDS:
        raise _filter_invalid_400(f"不支持的 workKind: {kind}（允许: {list(WORK_KINDS)}）")
    return normalized


def validate_partition(partition: str) -> str:
    """partition 白名单校验（归一化大写；未知值 400）."""
    normalized = partition.strip().upper()
    if normalized not in PARTITIONS:
        raise _filter_invalid_400(f"不支持的 partition: {partition}（允许: {list(PARTITIONS)}）")
    return normalized


# ---------------------------------------------------------------------------
# 稳定分页游标（keyset：含全排序键，插入新行不漂移）
# ---------------------------------------------------------------------------


def _due_key(due_at: datetime | None) -> tuple[int, str]:
    """期限排序键（NULL 最后）."""
    return (0, due_at.isoformat()) if due_at is not None else (1, "")


def _sort_key(item: dict[str, Any]) -> tuple[int, tuple[int, str], str, str]:
    """统一排序键：有效优先级 → 期限 → workKind → 稳定 ID."""
    return (
        int(item["_rank"]),
        _due_key(item["due_at"]),
        str(item["work_kind"]),
        str(item["work_id"]),
    )


def encode_cursor(item: dict[str, Any]) -> str:
    """由条目排序键生成不透明游标."""
    due_at = item["due_at"]
    payload = {
        "p": int(item["_rank"]),
        "d": due_at.isoformat() if isinstance(due_at, datetime) else None,
        "k": str(item["work_kind"]),
        "i": str(item["work_id"]),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[int, tuple[int, str], str, str]:
    """解析游标为排序键元组；非法游标一律 400（不静默回退首页）."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        rank = int(payload["p"])
        due = payload["d"]
        kind = str(payload["k"])
        work_id = str(payload["i"])
        if not work_id or kind not in WORK_KINDS:
            raise ValueError("cursor identity invalid")
        if due is not None:
            datetime.fromisoformat(str(due))  # 仅校验形态
        return (rank, _due_key(datetime.fromisoformat(str(due)) if due else None), kind, work_id)
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise BizError(
            code="ERR_WORK_CURSOR_INVALID",
            message="分页游标非法或已损坏",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc


# ---------------------------------------------------------------------------
# 分区判定（互斥：先判等待 → 再判今日计划 → 其余现在可行动）
# ---------------------------------------------------------------------------


def partition_of_investigation(stage: str) -> str:
    """核查分区：CLOSED（终态，仅筛选可见）> WAITING > ACTIONABLE."""
    if stage == "CLOSED":
        return PARTITION_CLOSED
    if stage == "WAITING":
        return PARTITION_WAITING
    return PARTITION_ACTIONABLE


def partition_of_order(order_status: str, planned_at: datetime | None, work_date: date) -> str:
    """工单分区：CLOSED > 今日计划 > 未来计划 > ACTIONABLE.

    工作日期只用于计划/期限：计划已过或没有计划的进行中工单进入"现在
    可行动"（不按"今天有更新"筛掉）；VERIFYING 仍是可行动（验证是人工
    动作，不是等待条件）。
    """
    if order_status in _ORDER_CLOSED_STATUSES:
        return PARTITION_CLOSED
    if planned_at is not None:
        planned_date = planned_at.date()
        if planned_date == work_date:
            return PARTITION_TODAY
        if planned_date > work_date:
            return PARTITION_FUTURE
    return PARTITION_ACTIONABLE


def _order_priority_reasons(
    order: HandlingOrder, *, now: datetime, work_date: date
) -> tuple[list[str], int]:
    """工单有效优先级理由（09§4.3：逾期/重开/SLA 等可解释理由，非新评分）."""
    reasons: list[str] = []
    overdue = order.sla_deadline_at is not None and order.sla_deadline_at < now
    if (
        order.status in _ORDER_ACTIVE_STATUSES
        and order.planned_at is not None
        and order.planned_at.date() < work_date
    ):
        overdue = True  # 计划日期已过且未完成
    if overdue:
        reasons.append("OVERDUE")
    if order.sla_stage == "BREACH":
        reasons.append("SLA_BREACH")
    elif order.sla_stage == "WARN":
        reasons.append("SLA_WARN")
    if int(order.reopen_count or 0) > 0 or order.status == "REOPENED":
        reasons.append("REOPENED")
    if overdue or order.sla_stage == "BREACH":
        return reasons, _RANK_OVERDUE
    if order.sla_stage == "WARN" or "REOPENED" in reasons:
        return reasons, _RANK_WARN
    return reasons, _RANK_NORMAL


# ---------------------------------------------------------------------------
# 条目装配辅助
# ---------------------------------------------------------------------------


_INVESTIGATION_REASON = {
    "MANUAL": "人工登记的核查，我负责",
    "ALERT_EVENT": "预警来源的核查，我负责",
    "DIAGNOSIS_RUN": "诊断来源的核查，我负责",
    "ASSESSMENT": "评估来源的核查，我负责",
}

_INVESTIGATION_NEXT_ACTION = {
    "TODO": ("claim_investigation", "认领核查"),
    "IN_PROGRESS": ("open_investigation", "继续核查"),
    "WAITING": ("review_wait_condition", "复查等待条件"),
    "CLOSED": ("open_investigation", "查看核查结论"),
}

_ORDER_REASON = {
    "PENDING": "我负责处置的工单（待执行）",
    "EXECUTING": "我负责处置的工单（执行中）",
    "VERIFYING": "我负责验证的工单（验证中）",
    "REOPENED": "我负责处置的工单（已重开）",
    "CLOSED": "我负责处置的工单（已闭环）",
    "CANCELLED": "我负责处置的工单（已作废）",
}

_ORDER_NEXT_ACTION = {
    "PENDING": ("start_handling_order", "开始处置"),
    "EXECUTING": ("submit_verification", "提交验证"),
    "VERIFYING": ("verify_handling_order", "执行验证"),
    "REOPENED": ("resume_handling_order", "重新处置"),
    "CLOSED": ("open_handling_order", "查看工单"),
    "CANCELLED": ("open_handling_order", "查看工单"),
}


def _loop_view(loop: LoopLedger, unit_name: str | None) -> dict[str, Any]:
    return {
        "loop_tag_name": str(loop.tag_name),
        "unit_id": str(loop.unit_id) if loop.unit_id is not None else None,
        "unit_name": unit_name,
    }


def _base_item(
    *,
    work_kind: str,
    work_id: str,
    loop: LoopLedger,
    unit_name: str | None,
    title: str,
    reason: str,
    source_kind: str,
    stage: str | None,
    partition: str | None,
    rank: int,
    next_action: tuple[str, str],
    updated_at: datetime | None,
    owner_id: str | None = None,
    owner_display_name: str | None = None,
    responsibility_pending: bool = False,
    planned_at: datetime | None = None,
    due_at: datetime | None = None,
    wait_reason: str | None = None,
    wait_review_condition: str | None = None,
    priority_reasons: list[str] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    revision: int | None = None,
    context: dict | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "work_kind": work_kind,
        "work_id": work_id,
        "loop_id": str(loop.id),
        "title": title,
        "reason": reason,
        "source_kind": source_kind,
        "owner_id": owner_id,
        "owner_display_name": owner_display_name,
        "responsibility_pending": responsibility_pending,
        "stage": stage,
        "partition": partition,
        "planned_at": planned_at,
        "due_at": due_at,
        "wait_reason": wait_reason,
        "wait_review_condition": wait_review_condition,
        "priority_reasons": priority_reasons or [],
        "evidence_refs": (evidence_refs or [])[:_MAX_EVIDENCE_REFS],
        "next_action": {"kind": next_action[0], "label": next_action[1]},
        "revision": revision,
        "context": context,
        "updated_at": updated_at,
        "_rank": rank,
    }
    item.update(_loop_view(loop, unit_name))
    return item


def _scoped_stmt(stmt: Any, ctx: ScopeContext) -> Any:
    """SQL 级数据范围过滤（unit_id 为 NULL 的回路仅 ALL 可见）."""
    if ctx.allowed_node_ids is not None:
        stmt = stmt.where(LoopLedger.unit_id.in_(ctx.allowed_node_ids))
    return stmt


async def _inaccessible_count(
    db: AsyncSession,
    ctx: ScopeContext,
    model: Any,
    *conditions: Any,
) -> int:
    """撤权不可访问计数（保留但不回显的对象；unit_id NULL 同样不可访问）."""
    if ctx.allowed_node_ids is None:
        return 0
    stmt = (
        select(func.count())
        .select_from(model)
        .join(LoopLedger, LoopLedger.id == model.loop_id)
        .where(
            *conditions,
            or_(LoopLedger.unit_id.is_(None), LoopLedger.unit_id.notin_(ctx.allowed_node_ids)),
        )
    )
    return int((await db.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# 来源一：分配给我的核查（loop_investigation.owner_id）
# ---------------------------------------------------------------------------


async def _fetch_investigations(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    partition_filter: str | None,
    my_display_name: str | None,
) -> dict[str, Any]:
    if partition_filter in (PARTITION_FUTURE,):
        return {"items": [], "inaccessible_count": 0}  # 核查无计划日期，无未来分区
    stage_cond = (
        LoopInvestigation.stage == "CLOSED"
        if partition_filter == PARTITION_CLOSED
        else LoopInvestigation.stage != "CLOSED"
    )
    stmt = (
        select(LoopInvestigation, LoopLedger, PlantNode.name)
        .join(LoopLedger, LoopLedger.id == LoopInvestigation.loop_id)
        .outerjoin(PlantNode, PlantNode.id == LoopLedger.unit_id)
        .where(LoopInvestigation.owner_id == user_id, stage_cond)
    )
    rows = (await db.execute(_scoped_stmt(stmt, ctx))).all()

    inaccessible = await _inaccessible_count(
        db, ctx, LoopInvestigation, LoopInvestigation.owner_id == user_id, stage_cond
    )

    items = []
    for inv, loop, unit_name in rows:
        stage = str(inv.stage)
        partition = partition_of_investigation(stage)
        source_kind = str(inv.source_kind)
        evidence_refs: list[dict[str, Any]] = []
        if source_kind != "MANUAL":
            evidence_refs.append({"kind": source_kind.lower(), "id": str(inv.source_id)})
        items.append(
            _base_item(
                work_kind=WORK_KIND_ASSIGNED,
                work_id=str(inv.id),
                loop=loop,
                unit_name=str(unit_name) if unit_name is not None else None,
                title=str(inv.title),
                reason=_INVESTIGATION_REASON.get(source_kind, "我负责的核查"),
                source_kind=SOURCE_INVESTIGATION,
                stage=stage,
                partition=partition,
                rank=_RANK_NORMAL,
                next_action=_INVESTIGATION_NEXT_ACTION.get(
                    stage, ("open_investigation", "打开核查")
                ),
                updated_at=inv.updated_at,
                owner_id=str(inv.owner_id) if inv.owner_id is not None else None,
                owner_display_name=my_display_name,
                wait_reason=inv.wait_reason,
                wait_review_condition=inv.wait_review_condition,
                evidence_refs=evidence_refs,
                revision=int(inv.revision),
            )
        )
    return {"items": items, "inaccessible_count": inaccessible}


# ---------------------------------------------------------------------------
# 来源二：分配给我的工单（handling_order.handler_id）
# ---------------------------------------------------------------------------


async def _fetch_handling_orders(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    partition_filter: str | None,
    my_display_name: str | None,
    now: datetime,
    work_date: date,
) -> dict[str, Any]:
    status_cond = (
        HandlingOrder.status.in_(_ORDER_CLOSED_STATUSES)
        if partition_filter == PARTITION_CLOSED
        else HandlingOrder.status.in_(_ORDER_ACTIVE_STATUSES)
    )
    stmt = (
        select(HandlingOrder, LoopLedger, PlantNode.name)
        .join(LoopLedger, LoopLedger.id == HandlingOrder.loop_id)
        .outerjoin(PlantNode, PlantNode.id == LoopLedger.unit_id)
        .where(HandlingOrder.handler_id == user_id, status_cond)
    )
    rows = (await db.execute(_scoped_stmt(stmt, ctx))).all()

    inaccessible = await _inaccessible_count(
        db, ctx, HandlingOrder, HandlingOrder.handler_id == user_id, status_cond
    )

    items = []
    for order, loop, unit_name in rows:
        order_status = str(order.status)
        partition = partition_of_order(order_status, order.planned_at, work_date)
        priority_reasons, rank = _order_priority_reasons(order, now=now, work_date=work_date)
        evidence_refs: list[dict[str, Any]] = []
        for sid in order.suggestion_ids or []:
            evidence_refs.append({"kind": "loop_action_item", "id": str(sid)})
        if order.verify_run_id is not None:
            evidence_refs.append({"kind": "diagnosis_run", "id": str(order.verify_run_id)})
        items.append(
            _base_item(
                work_kind=WORK_KIND_ASSIGNED,
                work_id=str(order.id),
                loop=loop,
                unit_name=str(unit_name) if unit_name is not None else None,
                title=str(order.title),
                reason=_ORDER_REASON.get(order_status, "我负责的工单"),
                source_kind=SOURCE_HANDLING_ORDER,
                stage=order_status,
                partition=partition,
                rank=rank,
                next_action=_ORDER_NEXT_ACTION.get(
                    order_status, ("open_handling_order", "查看工单")
                ),
                updated_at=order.updated_at,
                owner_id=str(order.handler_id) if order.handler_id is not None else None,
                owner_display_name=my_display_name,
                planned_at=order.planned_at,
                due_at=order.sla_deadline_at,
                priority_reasons=priority_reasons,
                evidence_refs=evidence_refs,
            )
        )
    return {"items": items, "inaccessible_count": inaccessible}


# ---------------------------------------------------------------------------
# 来源三：系统建议（loop_action_item，SYSTEM + PENDING 待审核池）
# ---------------------------------------------------------------------------


async def _fetch_action_items(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    partition_filter: str | None,
) -> dict[str, Any]:
    if partition_filter in (PARTITION_FUTURE, PARTITION_CLOSED, PARTITION_WAITING, PARTITION_TODAY):
        # 建议无等待/计划/结束分区语义（待审核立即可行动）
        return {"items": [], "inaccessible_count": 0}
    conditions = (LoopActionItem.source == "SYSTEM", LoopActionItem.status == "PENDING")
    stmt = (
        select(LoopActionItem, LoopLedger, PlantNode.name)
        .join(LoopLedger, LoopLedger.id == LoopActionItem.loop_id)
        .outerjoin(PlantNode, PlantNode.id == LoopLedger.unit_id)
        .where(*conditions)
    )
    rows = (await db.execute(_scoped_stmt(stmt, ctx))).all()

    inaccessible = await _inaccessible_count(db, ctx, LoopActionItem, *conditions)

    items = []
    for action, loop, unit_name in rows:
        high_priority = action.priority is not None and int(action.priority) == 1
        evidence_refs: list[dict[str, Any]] = []
        if action.run_id is not None:
            evidence_refs.append({"kind": "diagnosis_run", "id": str(action.run_id)})
        items.append(
            _base_item(
                work_kind=WORK_KIND_SYSTEM_SUGGESTION,
                work_id=str(action.id),
                loop=loop,
                unit_name=str(unit_name) if unit_name is not None else None,
                title=str(action.content),
                reason=(
                    str(action.basis) if action.basis else "系统按诊断结论生成的处置建议，待审核"
                ),
                source_kind=SOURCE_ACTION_ITEM,
                stage=str(action.status),
                partition=PARTITION_ACTIONABLE,
                rank=_RANK_WARN if high_priority else _RANK_NORMAL,
                next_action=("review_action_item", "审核建议"),
                updated_at=action.updated_at,
                priority_reasons=["HIGH_PRIORITY"] if high_priority else [],
                evidence_refs=evidence_refs,
            )
        )
    return {"items": items, "inaccessible_count": inaccessible}


# ---------------------------------------------------------------------------
# 来源四：我的关注（user_loop_follow.is_followed）
# ---------------------------------------------------------------------------


async def _fetch_follows(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    partition_filter: str | None,
) -> dict[str, Any]:
    if partition_filter in (PARTITION_FUTURE, PARTITION_CLOSED, PARTITION_WAITING, PARTITION_TODAY):
        return {"items": [], "inaccessible_count": 0}
    conditions = (UserLoopFollow.user_id == user_id, UserLoopFollow.is_followed.is_(True))
    stmt = (
        select(UserLoopFollow, LoopLedger, PlantNode.name)
        .join(LoopLedger, LoopLedger.id == UserLoopFollow.loop_id)
        .outerjoin(PlantNode, PlantNode.id == LoopLedger.unit_id)
        .where(*conditions)
    )
    rows = (await db.execute(_scoped_stmt(stmt, ctx))).all()

    inaccessible = await _inaccessible_count(db, ctx, UserLoopFollow, *conditions)

    items = []
    for follow, loop, unit_name in rows:
        items.append(
            _base_item(
                work_kind=WORK_KIND_FOLLOW,
                work_id=str(follow.loop_id),
                loop=loop,
                unit_name=str(unit_name) if unit_name is not None else None,
                title=str(loop.tag_name),
                reason="我关注的回路（关注不产生责任，不自动变成待办）",
                source_kind=SOURCE_FOLLOW,
                stage=None,
                partition=None,
                rank=_RANK_FOLLOW,
                next_action=("open_loop_workbench", "打开回路工作台"),
                updated_at=follow.updated_at,
                revision=int(follow.revision),
            )
        )
    return {"items": items, "inaccessible_count": inaccessible}


# ---------------------------------------------------------------------------
# 来源五：最近继续（user_work_resume，当前代际未删除）
# ---------------------------------------------------------------------------


def _resume_evidence_refs(context: dict | None) -> list[dict[str, Any]]:
    """从最近继续 context 提取证据引用（仅 {kind,id[,version]} 形态条目）."""
    if not isinstance(context, dict):
        return []
    raw = context.get("evidenceRefs")
    if not isinstance(raw, list):
        return []
    refs: list[dict[str, Any]] = []
    for entry in raw[:_MAX_EVIDENCE_REFS]:
        if not isinstance(entry, dict):
            continue
        kind, ref_id = entry.get("kind"), entry.get("id")
        if not isinstance(kind, str) or not kind or not isinstance(ref_id, str) or not ref_id:
            continue
        ref: dict[str, Any] = {"kind": kind, "id": ref_id}
        if isinstance(entry.get("version"), str):
            ref["version"] = entry["version"]
        refs.append(ref)
    return refs


async def _fetch_resumes(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    partition_filter: str | None,
) -> dict[str, Any]:
    if partition_filter in (PARTITION_FUTURE, PARTITION_CLOSED, PARTITION_WAITING, PARTITION_TODAY):
        return {"items": [], "inaccessible_count": 0}
    current_generation = await get_resume_generation(db, user_id)
    rows = (
        (
            await db.execute(
                select(UserWorkResume).where(
                    UserWorkResume.user_id == user_id,
                    UserWorkResume.deleted_at.is_(None),
                    UserWorkResume.generation == current_generation,
                    UserWorkResume.resource_kind == RESOURCE_KIND_LOOP,
                )
            )
        )
        .scalars()
        .all()
    )

    # 撤权批校验：范围内回路集合（每次查询重新校验）
    loop_ids = [str(r.resource_id) for r in rows]
    accessible_ids = await loops_in_scope(db, ctx, loop_ids) if loop_ids else set()
    kept = [r for r in rows if str(r.resource_id) in accessible_ids]
    inaccessible = len(rows) - len(kept)

    loop_info: dict[str, tuple[LoopLedger, str | None]] = {}
    if kept:
        info_rows = (
            await db.execute(
                select(LoopLedger, PlantNode.name)
                .outerjoin(PlantNode, PlantNode.id == LoopLedger.unit_id)
                .where(LoopLedger.id.in_([str(r.resource_id) for r in kept]))
            )
        ).all()
        loop_info = {
            str(loop.id): (loop, str(unit_name) if unit_name is not None else None)
            for loop, unit_name in info_rows
        }

    items = []
    for row in kept:
        info = loop_info.get(str(row.resource_id))
        if info is None:
            # 对象已删除：不可访问口径（不制造指向他处的条目）
            inaccessible += 1
            continue
        loop, unit_name = info
        context = row.context if isinstance(row.context, dict) else None
        items.append(
            _base_item(
                work_kind=WORK_KIND_RESUME,
                work_id=str(row.resource_id),
                loop=loop,
                unit_name=unit_name,
                title=str(loop.tag_name),
                reason="最近访问的回路，从原位置继续",
                source_kind=SOURCE_RESUME,
                stage=None,
                partition=None,
                rank=_RANK_RESUME,
                next_action=("resume_context", "回到上次位置"),
                updated_at=row.last_visited_at,
                evidence_refs=_resume_evidence_refs(context),
                revision=int(row.revision),
                context=context,
            )
        )
    return {"items": items, "inaccessible_count": inaccessible}


# ---------------------------------------------------------------------------
# 统一查询
# ---------------------------------------------------------------------------


async def _my_display_name(db: AsyncSession, user_id: str) -> str | None:
    """本人当前显示名（分配给我的条目负责人姓名以 sys_user 当前值为准）."""
    return (
        await db.execute(select(SysUser.display_name).where(SysUser.id == user_id))
    ).scalar_one_or_none()


async def query_my_work(
    db: AsyncSession,
    *,
    user_id: str,
    ctx: ScopeContext,
    work_kind: str | None = None,
    partition: str | None = None,
    cursor: str | None = None,
    page_size: int = 20,
) -> dict[str, Any]:
    """跨来源统一工作查询（登录主体唯一，无自由 userId）.

    单来源失败不阻塞其他来源：失败来源 ``sourceStatuses=ERROR``，
    ``total=None``（不填伪造 0），``readStatus=PARTIAL``；全部来源失败
    → ``readStatus=ERROR``。权限每次查询重新校验（SQL 级 scope 过滤 +
    ``loops_in_scope`` 批校验）。
    """
    if work_kind is not None:
        work_kind = validate_work_kind(work_kind)
    if partition is not None:
        partition = validate_partition(partition)
    cursor_key = decode_cursor(cursor) if cursor else None
    if page_size < 1 or page_size > 50:
        raise _filter_invalid_400("pageSize 须在 1..50 之间")

    now = _now()
    work_date = now.date()
    my_display_name = await _my_display_name(db, user_id)

    requested_sources: tuple[str, ...] = (
        _WORK_KIND_SOURCES[work_kind]
        if work_kind is not None
        else (
            SOURCE_INVESTIGATION,
            SOURCE_HANDLING_ORDER,
            SOURCE_ACTION_ITEM,
            SOURCE_FOLLOW,
            SOURCE_RESUME,
        )
    )

    fetchers = {
        SOURCE_INVESTIGATION: lambda: _fetch_investigations(
            db,
            user_id=user_id,
            ctx=ctx,
            partition_filter=partition,
            my_display_name=my_display_name,
        ),
        SOURCE_HANDLING_ORDER: lambda: _fetch_handling_orders(
            db,
            user_id=user_id,
            ctx=ctx,
            partition_filter=partition,
            my_display_name=my_display_name,
            now=now,
            work_date=work_date,
        ),
        SOURCE_ACTION_ITEM: lambda: _fetch_action_items(
            db, user_id=user_id, ctx=ctx, partition_filter=partition
        ),
        SOURCE_FOLLOW: lambda: _fetch_follows(
            db, user_id=user_id, ctx=ctx, partition_filter=partition
        ),
        SOURCE_RESUME: lambda: _fetch_resumes(
            db, user_id=user_id, ctx=ctx, partition_filter=partition
        ),
    }

    merged: list[dict[str, Any]] = []
    source_statuses: dict[str, dict[str, Any]] = {}
    per_source_totals: dict[str, int] = {}
    error_sources = 0
    inaccessible_total = 0

    for source_name in requested_sources:
        try:
            result = await fetchers[source_name]()
        except Exception as exc:  # noqa: BLE001 —— 单来源失败不阻塞其他来源
            logger.exception(
                "统一工作查询来源失败: source=%s user=%s %s", source_name, user_id, exc
            )
            source_statuses[source_name] = {
                "status": SOURCE_STATUS_ERROR,
                "total": None,
                "inaccessible_count": 0,
            }
            error_sources += 1
            continue
        items = result["items"]
        # 分区筛选：默认视图 = 等待 + 今日计划 + 现在可行动（未来/已结束仅筛选可见）
        default_partitions = (PARTITION_WAITING, PARTITION_TODAY, PARTITION_ACTIONABLE, None)
        if partition is not None:
            items = [i for i in items if i["partition"] == partition]
        else:
            items = [i for i in items if i["partition"] in default_partitions]
        merged.extend(items)
        per_source_totals[source_name] = len(items)
        inaccessible = int(result["inaccessible_count"])
        inaccessible_total += inaccessible
        source_statuses[source_name] = {
            "status": SOURCE_STATUS_OK,
            "total": len(items),
            "inaccessible_count": inaccessible,
        }

    # 统一排序（有效优先级 → 期限 → workKind → 稳定 ID）+ keyset 分页
    merged.sort(key=_sort_key)
    if cursor_key is not None:
        merged = [i for i in merged if _sort_key(i) > cursor_key]

    page = merged[: page_size + 1]
    has_more = len(page) > page_size
    page = page[:page_size]
    next_cursor = encode_cursor(page[-1]) if has_more and page else None

    # 计数与分页同规则：任一来源失败 → 计数未知（None，不填伪造 0）
    total: int | None
    if error_sources:
        total = None
    else:
        total = sum(per_source_totals.values())

    if error_sources == len(requested_sources):
        read_status = READ_STATUS_ERROR
    elif error_sources or inaccessible_total:
        read_status = READ_STATUS_PARTIAL
    else:
        read_status = READ_STATUS_COMPLETE

    for item in page:
        item.pop("_rank", None)

    return {
        "items": page,
        "total": total,
        "next_cursor": next_cursor,
        "read_at": now,
        "read_status": read_status,
        "source_statuses": source_statuses,
    }


__all__ = [
    "SOURCE_ACTION_ITEM",
    "SOURCE_FOLLOW",
    "SOURCE_HANDLING_ORDER",
    "SOURCE_INVESTIGATION",
    "SOURCE_RESUME",
    "decode_cursor",
    "encode_cursor",
    "partition_of_investigation",
    "partition_of_order",
    "query_my_work",
    "validate_partition",
    "validate_work_kind",
]
