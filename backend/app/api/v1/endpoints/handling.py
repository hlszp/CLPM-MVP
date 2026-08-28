"""处置模块 API（v2.0 双实体：处置建议 + 处置工单）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §4 状态机 / §6 API 定义
端点清单（前缀 /handling）：
建议侧（§6.1，loop_action_item 审核对象）：
- GET  /suggestions                建议清单（分页/筛选/状态分组排序 + convertedOrderNo）
- POST /suggestions                手动新增建议（run_id 置空，source=MANUAL）
- POST /suggestions/{id}/accept    接受（PENDING → ACCEPTED）
- POST /suggestions/{id}/reject    驳回（PENDING → REJECTED，rejectedReason 必填）
- POST /suggestions/{id}/ignore    忽略（PENDING → IGNORED，ignoreReason 必填）
- POST /suggestions/convert        转工单（多建议合一单，order_no=HD-YYYYMMDD-NNN）
工单侧（§6.2，handling_order 执行对象）：
- GET  /orders                     工单清单（分页/筛选/状态分组排序）
- GET  /orders/export              工单 CSV 导出（筛选同 /orders，上限 5000 行）
- GET  /orders/{id}                工单详情（+ 来源建议摘要数组）
- POST /orders                     手动新建工单（source=MANUAL）
- POST /orders/{id}/start          开工（PENDING/REOPENED → EXECUTING）
- POST /orders/{id}/feedback       执行反馈（EXECUTING 追加 feedback_log，状态不变）
- POST /orders/{id}/submit         提交验证（EXECUTING → VERIFYING，TUNING 必填 pidAfter）
- POST /orders/{id}/verify         验证结论（VERIFYING → CLOSED/REOPENED，服务端固化 KPI）
- POST /orders/{id}/cancel         作废（PENDING → CANCELLED，cancelReason 必填）
- POST /orders/{id}/kpi-comparison KPI 前后对比预览（VERIFYING，不落库）
聚合（§6.3）：
- GET  /loops                      档案聚合（双实体口径）
- GET  /statistics                 统计（工单维度 + 建议驳回率）

状态机后端强校验：所有非法迁移返回 ERR_STATE 400（§4 流转表为唯一合法迁移）。
CONVERTED / REJECTED / IGNORED / CLOSED / CANCELLED 为终态，不可重开（§1.3 裁决）。
所有写端点 commit 后必须 await db.refresh(row) 再序列化（updated_at 服务端计算，
commit 后过期属性懒加载会 500，v1.x 已踩坑）。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.api.v1.endpoints.diagnosis_v2 import _CATEGORY_LABELS
from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.modules import is_module_enabled
from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import ACTION_TYPES, HandlingOrder
from app.models.loop import LoopLedger
from app.models.loop_action_item import LoopActionItem
from app.models.metric import KpiSnapshotHourly
from app.models.sys_user import SysUser
from app.models.tuning import TuningRecord
from app.schemas.common import ApiResponse, success
from app.services.handling_stats import (
    ACTION_TYPE_LABELS as _ACTION_TYPE_LABELS,
)
from app.services.handling_stats import (
    _build_loop_agg_sql,
    _load_subtree_unit_ids,
    _load_unit_paths,
)
from app.services.handling_stats import (
    build_handling_statistics as _collect_handling_statistics,
)

router = APIRouter(prefix="/handling", tags=["handling"])

#: 允许建议审核与工单流转的角色（§7）
_HANDLING_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN")

# 处置统计聚合逻辑已下沉 app/services/handling_stats.py（报告模块优化 P0-2，
# 与 /reports/handling-statistics 共用单一实现），此处回导保持原引用点不变。

#: 建议状态中文名（§4.1，4 态）
_SUGGESTION_STATUS_LABELS = {
    "PENDING": "待审核",
    "ACCEPTED": "已接受",
    "CONVERTED": "已转工单",
    "REJECTED": "已驳回",
    "IGNORED": "已忽略",
}

#: 工单状态中文名（§4.2，6 态）
_ORDER_STATUS_LABELS = {
    "PENDING": "待执行",
    "EXECUTING": "执行中",
    "VERIFYING": "验证中",
    "CLOSED": "已闭环",
    "REOPENED": "重开",
    "CANCELLED": "已作废",
}

#: KPI 验证窗口时长（§4.3：前后各 24h）
_KPI_WINDOW = timedelta(hours=24)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime | None) -> str | None:
    """naive UTC → ISO + Z（前端补 Z 转本地，同诊断模块口径）。"""
    return dt.isoformat() + "Z" if dt else None


def _err_param(message: str) -> BizError:
    return BizError(code="ERR_PARAM", message=message, status_code=400)


def _ensure_uuid(value: str, field: str) -> None:
    """路径 id UUID 格式防御：畸形串直接 ERR_PARAM 400（否则 PG UUID 列比较抛 500，
    同 loops.list_loops / diagnosis_v2 既有口径）。"""
    try:
        UUID(value)
    except (AttributeError, ValueError):
        raise _err_param(f"{field} 格式非法（应为 UUID）") from None


def _err_state(status: str, action: str, labels: dict[str, str]) -> BizError:
    return BizError(
        code="ERR_STATE",
        message=f"当前状态不允许{action}：{labels.get(status, status)}（{status}）",
        status_code=400,
    )


def _err_suggestion_state(sug: LoopActionItem, action: str) -> BizError:
    return _err_state(sug.status, action, _SUGGESTION_STATUS_LABELS)


def _err_order_state(order: HandlingOrder, action: str) -> BizError:
    return _err_state(order.status, action, _ORDER_STATUS_LABELS)


# ---------------------------------------------------------------------------
# 请求体（业务必填/枚举校验在端点内手工执行，保证 ERR_PARAM 400 口径，
# 不走 pydantic Field 约束的 422 通道）
# ---------------------------------------------------------------------------


class CreateSuggestionBody(BaseModel):
    """手动新增建议请求体（§6.1：loopId/content 必填）。"""

    loopId: str | None = None
    content: str | None = Field(None, max_length=2000)
    basis: str | None = Field(None, max_length=500)
    priority: int | None = Field(None, ge=1, le=5)


class RejectBody(BaseModel):
    """驳回请求体（§6.1：rejectedReason 必填）。"""

    rejectedReason: str | None = Field(None, max_length=200)


class IgnoreBody(BaseModel):
    """忽略请求体（§6.1：ignoreReason 必填）。"""

    ignoreReason: str | None = Field(None, max_length=200)


class ConvertBody(BaseModel):
    """转工单请求体（§6.1：suggestionIds ≥1 且全为 ACCEPTED 且同回路）。"""

    suggestionIds: list[str] | None = None
    actionType: str | None = None
    plannedAt: datetime | None = None
    handler: str | None = Field(None, max_length=64)
    title: str | None = Field(None, max_length=200)


class CreateOrderBody(BaseModel):
    """手动新建工单请求体（§6.2：loopId/actionType 必填，title 缺省取 content 前 50 字）。"""

    loopId: str | None = None
    actionType: str | None = None
    title: str | None = Field(None, max_length=200)
    content: str | None = Field(None, max_length=2000)
    plannedAt: datetime | None = None
    handler: str | None = Field(None, max_length=64)
    actionDetail: dict[str, Any] | None = None


class OrderStartBody(BaseModel):
    """开工请求体（§6.2：handler 缺省=当前登录用户；TUNING 可带 pidBefore）。"""

    handler: str | None = Field(None, max_length=64)
    actionDetail: dict[str, Any] | None = None
    pidBefore: dict[str, Any] | None = None


class FeedbackBody(BaseModel):
    """执行反馈请求体（§6.2：content 必填）。"""

    content: str | None = Field(None, max_length=1000)


class SubmitBody(BaseModel):
    """提交验证请求体（§6.2：actionDetail 必填，按类型校验子字段）。"""

    actionDetail: dict[str, Any] | None = None


class VerifyBody(BaseModel):
    """验证结论请求体（§6.2：verifyResult 必填 EFFECTIVE/INEFFECTIVE）。"""

    verifyResult: str | None = None
    verifyNote: str | None = Field(None, max_length=500)
    verifyRunId: str | None = None


class CancelBody(BaseModel):
    """作废请求体（§6.2：cancelReason 必填）。"""

    cancelReason: str | None = Field(None, max_length=200)


# ---------------------------------------------------------------------------
# 序列化与公共查询
# ---------------------------------------------------------------------------


def _suggestion_to_dict(row: LoopActionItem) -> dict[str, Any]:
    """建议本体序列化（审核流转端点响应；convertedOrderNo 由清单/详情 join 提供）。"""
    return {
        "id": row.id,
        "runId": row.run_id,
        "loopId": row.loop_id,
        "source": row.source,
        "category": row.category,
        "categoryLabel": _CATEGORY_LABELS.get(row.category) if row.category else None,
        "content": row.content,
        "basis": row.basis,
        "priority": row.priority,
        "status": row.status,
        "statusLabel": _SUGGESTION_STATUS_LABELS.get(row.status, row.status),
        "suggestedBy": row.suggested_by,
        "suggestedAt": _iso(row.suggested_at),
        "reviewedBy": row.reviewed_by,
        "reviewedAt": _iso(row.reviewed_at),
        "rejectedReason": row.rejected_reason,
        "convertedOrderId": row.converted_order_id,
        "ignoreReason": row.ignore_reason,
        "updatedAt": _iso(row.updated_at),
    }


def _order_to_dict(row: HandlingOrder) -> dict[str, Any]:
    """工单本体序列化（流转端点响应，全字段）。"""
    return {
        "id": row.id,
        "orderNo": row.order_no,
        "loopId": row.loop_id,
        "source": row.source,
        "suggestionIds": row.suggestion_ids or [],
        "title": row.title,
        "actionType": row.action_type,
        "actionTypeLabel": _ACTION_TYPE_LABELS.get(row.action_type, row.action_type),
        "actionDetail": row.action_detail,
        "plannedAt": _iso(row.planned_at),
        "plannedBy": row.planned_by,
        "handler": row.handler,
        "startedAt": _iso(row.started_at),
        "feedbackLog": row.feedback_log or [],
        "submittedAt": _iso(row.submitted_at),
        "verifyRunId": row.verify_run_id,
        "verifyResult": row.verify_result,
        "verifyResultLabel": (
            {"EFFECTIVE": "有效", "INEFFECTIVE": "无效"}.get(row.verify_result)
            if row.verify_result
            else None
        ),
        "verifyNote": row.verify_note,
        "verifiedBy": row.verified_by,
        "verifiedAt": _iso(row.verified_at),
        "kpiBefore": row.kpi_before,
        "kpiAfter": row.kpi_after,
        "tuningRecordId": row.tuning_record_id,
        "cancelReason": row.cancel_reason,
        "status": row.status,
        "statusLabel": _ORDER_STATUS_LABELS.get(row.status, row.status),
        "updatedAt": _iso(row.updated_at),
    }


async def _get_suggestion_or_404(db: AsyncSession, suggestion_id: str) -> LoopActionItem:
    _ensure_uuid(suggestion_id, "suggestionId")
    row = (
        await db.execute(select(LoopActionItem).where(LoopActionItem.id == suggestion_id))
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code="ERR_NOT_FOUND", message=f"处置建议不存在: {suggestion_id}", status_code=404
        )
    return row


async def _get_order_or_404(db: AsyncSession, order_id: str) -> HandlingOrder:
    _ensure_uuid(order_id, "orderId")
    row = (
        await db.execute(select(HandlingOrder).where(HandlingOrder.id == order_id))
    ).scalar_one_or_none()
    if row is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"处置工单不存在: {order_id}", status_code=404)
    return row


def _kpi_summary(snap: KpiSnapshotHourly | None) -> dict[str, Any] | None:
    """KPI 快照摘要（§4.3：score + 六率 + 可信度 + 窗口；无快照侧为 None）。"""

    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    if snap is None:
        return None
    return {
        "score": _f(snap.score),
        "goodValueRate": _f(snap.good_value_rate),
        "effectiveAutoRate": _f(snap.effective_auto_rate),
        "steadyRate": _f(snap.steady_rate),
        "accuracyRate": _f(snap.accuracy_rate),
        "fastRate": _f(snap.fast_rate),
        "oscillationRate": _f(snap.oscillation_rate),
        "saturationRate": _f(snap.saturation_rate),
        "confidenceLevel": snap.confidence_level,
        "tsStart": _iso(snap.ts_start),
        "tsEnd": _iso(snap.ts_end),
    }


async def _latest_snapshot_in_window(
    db: AsyncSession, loop_id: str, win_start: datetime, win_end: datetime
) -> KpiSnapshotHourly | None:
    """窗口内最新一条有 score 的 kpi_snapshot_hourly 记录（§4.3 快照来源）。"""
    return (
        await db.execute(
            select(KpiSnapshotHourly)
            .where(
                KpiSnapshotHourly.loop_id == loop_id,
                KpiSnapshotHourly.score.is_not(None),
                KpiSnapshotHourly.ts_start >= win_start,
                KpiSnapshotHourly.ts_start <= win_end,
            )
            .order_by(KpiSnapshotHourly.ts_start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _pull_kpi_windows(
    db: AsyncSession, order: HandlingOrder
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """拉取 KPI 前后窗口快照摘要（§4.3 v2.0：前窗以 started_at 为界，后窗以 submitted_at 为界）。

    - kpi_before：[started_at − 24h, started_at]（开工前最后基线，原 handled_at 口径平移）
    - kpi_after：[submitted_at, submitted_at + 24h]（提交验证后线上效果）
    两侧窗口隔离：处置执行期数据不参与对比；窗口不足/无快照侧为 None。
    """
    before = after = None
    if order.started_at:
        snap = await _latest_snapshot_in_window(
            db, order.loop_id, order.started_at - _KPI_WINDOW, order.started_at
        )
        before = _kpi_summary(snap)
    if order.submitted_at:
        snap = await _latest_snapshot_in_window(
            db, order.loop_id, order.submitted_at, order.submitted_at + _KPI_WINDOW
        )
        after = _kpi_summary(snap)
    return before, after


async def _writeback_tuning_record(db: AsyncSession, order: HandlingOrder, status: str) -> None:
    """整定记录状态回写（09 设计方案 §5.4）：仅 TUNING 类且已关联整定记录的工单。

    模块热插拔：整定模块禁用时跳过回写（软依赖，handling 不硬依赖 tuning）。
    """
    if order.action_type != "TUNING" or not order.tuning_record_id:
        return
    if not is_module_enabled("tuning"):
        return
    rec = await db.get(TuningRecord, order.tuning_record_id)
    if rec is not None:
        rec.status = status


def _validate_action_detail(action_type: str | None, detail: dict[str, Any]) -> None:
    """submit 按类型校验必填子字段（§5.2：TUNING 必填 pidAfter；其余仅要求非空对象）。"""
    if not detail:
        raise _err_param("actionDetail 必填且为非空对象")
    if action_type == "TUNING":
        pid_after = detail.get("pidAfter")
        if not isinstance(pid_after, dict) or not pid_after:
            raise _err_param("TUNING 类型提交验证时 actionDetail.pidAfter 必填（非空对象）")


# ---------------------------------------------------------------------------
# 工单编号生成（§3.3：HD-YYYYMMDD-NNN 按日重置；COUNT+1，唯一冲突重试一次）
# ---------------------------------------------------------------------------


async def _next_order_no(db: AsyncSession, bump: int = 0) -> str:
    prefix = f"HD-{_utcnow_naive().strftime('%Y%m%d')}"
    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM handling_order WHERE order_no LIKE :prefix"),
            {"prefix": f"{prefix}-%"},
        )
    ).scalar() or 0
    return f"{prefix}-{int(count) + 1 + bump:03d}"


async def _get_loop_or_400(db: AsyncSession, loop_id: str) -> None:
    """回路存在性校验（手动新增建议/新建工单）。"""
    found = (
        await db.execute(select(LoopLedger.id).where(LoopLedger.id == loop_id))
    ).scalar_one_or_none()
    if found is None:
        raise _err_param(f"回路不存在: {loop_id}")


# ---------------------------------------------------------------------------
# 清单公共 helper（plant_node 树回溯 / naive UTC / 筛选拼装）
# ---------------------------------------------------------------------------


def _to_naive_utc(dt: datetime) -> datetime:
    """aware datetime（前端 ISO 带 Z/+08:00）→ naive UTC（同诊断模块口径）。"""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


# ===========================================================================
# 建议侧（§6.1）
# ===========================================================================

#: 建议清单排序的状态分组优先级（§6.1：PENDING→ACCEPTED→其他）
_SUGGESTION_STATUS_RANK_SQL = (
    "CASE ai.status WHEN 'PENDING' THEN 0 WHEN 'ACCEPTED' THEN 1 ELSE 2 END"
)

#: 工单清单排序的状态分组优先级（§6.2：PENDING→REOPENED→EXECUTING→VERIFYING→其他）
_ORDER_STATUS_RANK_SQL = (
    "CASE ho.status WHEN 'PENDING' THEN 0 WHEN 'REOPENED' THEN 1 "
    "WHEN 'EXECUTING' THEN 2 WHEN 'VERIFYING' THEN 3 ELSE 4 END"
)


def _suggestion_list_row_to_dict(r: Any, unit_paths: dict[str, str]) -> dict[str, Any]:
    """建议清单行序列化（§6.1 返回行字段 + convertedOrderNo）。"""
    return {
        "id": str(r.id),
        "runId": str(r.run_id) if r.run_id else None,
        "loopId": str(r.loop_id),
        "loopTagName": r.loop_tag_name,
        "loopDescription": r.loop_description,
        "importanceLevel": int(r.importance_level) if r.importance_level else None,
        "unitId": str(r.unit_id) if r.unit_id else None,
        "unitPath": unit_paths.get(str(r.unit_id)) if r.unit_id else None,
        "source": r.source,
        "category": r.category,
        "categoryLabel": _CATEGORY_LABELS.get(r.category) if r.category else None,
        "content": r.content,
        "basis": r.basis,
        "priority": r.priority,
        "status": r.status,
        "statusLabel": _SUGGESTION_STATUS_LABELS.get(r.status, r.status),
        "suggestedBy": r.suggested_by,
        "suggestedAt": _iso(r.suggested_at),
        "reviewedBy": r.reviewed_by,
        "reviewedAt": _iso(r.reviewed_at),
        "rejectedReason": r.rejected_reason,
        "convertedOrderId": str(r.converted_order_id) if r.converted_order_id else None,
        "convertedOrderNo": r.converted_order_no,
        "ignoreReason": r.ignore_reason,
        "updatedAt": _iso(r.updated_at),
    }


def _build_suggestion_filters(params: dict[str, Any], args: dict[str, Any]) -> str:
    """建议清单 WHERE 子句（仅追加出现的条件，参数走 named params）。"""
    conds = ["1=1"]
    if args.get("statuses"):
        conds.append("ai.status = ANY(CAST(:statuses AS text[]))")
        params["statuses"] = args["statuses"]
    if args.get("source"):
        conds.append("ai.source = :source")
        params["source"] = args["source"]
    if args.get("loop_id"):
        conds.append("ai.loop_id = :loop_id")
        params["loop_id"] = args["loop_id"]
    if args.get("importance_level"):
        conds.append("ll.importance_level = :importance_level")
        params["importance_level"] = args["importance_level"]
    if args.get("keyword"):
        conds.append("(ll.tag_name ILIKE :kw OR ai.content ILIKE :kw)")
        params["kw"] = f"%{args['keyword']}%"
    if args.get("start"):
        conds.append("ai.suggested_at >= :start_ts")
        params["start_ts"] = args["start"]
    if args.get("end"):
        conds.append("ai.suggested_at <= :end_ts")
        params["end_ts"] = args["end"]
    if args.get("unit_ids") is not None:
        conds.append("ll.unit_id = ANY(CAST(:unit_ids AS uuid[]))")
        params["unit_ids"] = args["unit_ids"]
    return " AND ".join(conds)


@router.get("/suggestions", response_model=ApiResponse[dict])
async def list_suggestions(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    status: str | None = Query(None, description="状态多值，逗号分隔"),
    source: str | None = Query(None, pattern="^(SYSTEM|MANUAL)$"),
    plantNodeId: str | None = Query(None, description="装置节点（递归下钻到单元）"),
    loopId: str | None = Query(None),
    importanceLevel: int | None = Query(None, ge=1, le=3),
    keyword: str | None = Query(None, description="回路位号/建议内容模糊"),
    startTime: datetime | None = Query(None, description="建议时间起（ISO）"),
    endTime: datetime | None = Query(None, description="建议时间止（ISO）"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
) -> dict:
    """建议清单（分页）。排序：状态分组（PENDING→ACCEPTED→其他）+ suggested_at DESC（§6.1）。"""
    args: dict[str, Any] = {
        "statuses": [s for s in (status or "").split(",") if s] or None,
        "source": source,
        "loop_id": loopId,
        "importance_level": importanceLevel,
        "keyword": keyword,
        "start": _to_naive_utc(startTime) if startTime else None,
        "end": _to_naive_utc(endTime) if endTime else None,
    }
    if plantNodeId is not None:
        args["unit_ids"] = await _load_subtree_unit_ids(db, plantNodeId)

    params: dict[str, Any] = {}
    where = _build_suggestion_filters(params, args)

    total = (
        await db.execute(
            text(
                f"SELECT COUNT(*) FROM loop_action_item ai "
                f"JOIN loop_ledger ll ON ll.id = ai.loop_id WHERE {where}"
            ),
            params,
        )
    ).scalar() or 0

    rows = list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT ai.*, ll.tag_name AS loop_tag_name,
                           ll.description AS loop_description,
                           ll.importance_level, ll.unit_id,
                           ho.order_no AS converted_order_no
                    FROM loop_action_item ai
                    JOIN loop_ledger ll ON ll.id = ai.loop_id
                    LEFT JOIN handling_order ho ON ho.id = ai.converted_order_id
                    WHERE {where}
                    ORDER BY {_SUGGESTION_STATUS_RANK_SQL}, ai.suggested_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {**params, "limit": pageSize, "offset": (page - 1) * pageSize},
            )
        ).all()
    )
    unit_paths = await _load_unit_paths(db)
    return success(
        {
            "items": [_suggestion_list_row_to_dict(r, unit_paths) for r in rows],
            "total": int(total),
            "page": page,
            "pageSize": pageSize,
        }
    )


@router.post("/suggestions", response_model=ApiResponse[dict])
async def create_suggestion(
    body: CreateSuggestionBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """手动新增建议（§6.1：run_id 置空、source=MANUAL、建议人=当前登录用户）。"""
    if not body.loopId:
        raise _err_param("loopId 必填")
    if not body.content or not body.content.strip():
        raise _err_param("content 必填")
    await _get_loop_or_400(db, body.loopId)

    row = LoopActionItem(
        id=str(uuid4()),
        run_id=None,
        loop_id=body.loopId,
        source="MANUAL",
        category=None,
        content=body.content.strip(),
        basis=body.basis,
        priority=body.priority,
        status="PENDING",
        suggested_by=user.username,
        suggested_at=_utcnow_naive(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return success(_suggestion_to_dict(row))


@router.post("/suggestions/{suggestion_id}/accept", response_model=ApiResponse[dict])
async def accept_suggestion(
    suggestion_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """接受（PENDING → ACCEPTED；记录审核人/时间，§4.1 #1）。"""
    sug = await _get_suggestion_or_404(db, suggestion_id)
    if sug.status != "PENDING":
        raise _err_suggestion_state(sug, "接受")
    sug.status = "ACCEPTED"
    sug.reviewed_by = user.username
    sug.reviewed_at = _utcnow_naive()
    await db.commit()
    await db.refresh(sug)
    return success(_suggestion_to_dict(sug))


@router.post("/suggestions/{suggestion_id}/reject", response_model=ApiResponse[dict])
async def reject_suggestion(
    suggestion_id: str,
    body: RejectBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """驳回（PENDING → REJECTED 终态；rejected_reason 必填，§4.1 #2）。"""
    sug = await _get_suggestion_or_404(db, suggestion_id)
    if sug.status != "PENDING":
        raise _err_suggestion_state(sug, "驳回")
    if not body.rejectedReason or not body.rejectedReason.strip():
        raise _err_param("rejectedReason 必填")
    sug.rejected_reason = body.rejectedReason.strip()
    sug.reviewed_by = user.username
    sug.reviewed_at = _utcnow_naive()
    sug.status = "REJECTED"
    await db.commit()
    await db.refresh(sug)
    return success(_suggestion_to_dict(sug))


@router.post("/suggestions/{suggestion_id}/ignore", response_model=ApiResponse[dict])
async def ignore_suggestion(
    suggestion_id: str,
    body: IgnoreBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """忽略（PENDING → IGNORED 终态；ignore_reason 必填，§4.1 #3）。"""
    sug = await _get_suggestion_or_404(db, suggestion_id)
    if sug.status != "PENDING":
        raise _err_suggestion_state(sug, "忽略")
    if not body.ignoreReason or not body.ignoreReason.strip():
        raise _err_param("ignoreReason 必填")
    sug.ignore_reason = body.ignoreReason.strip()
    sug.status = "IGNORED"
    await db.commit()
    await db.refresh(sug)
    return success(_suggestion_to_dict(sug))


@router.post("/suggestions/convert", response_model=ApiResponse[dict])
async def convert_suggestions(
    body: ConvertBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """转工单（§4.1 #4：ACCEPTED → CONVERTED，多建议合一单并各自回链）。

    守卫：suggestionIds ≥1 且无重复、全部 ACCEPTED、同一 loop_id；
    actionType 必填 8 类；order_no 按 §3.3 当日序号生成，唯一冲突重试一次。
    """
    ids = body.suggestionIds or []
    if not ids:
        raise _err_param("suggestionIds 必填（至少 1 条建议）")
    if len(set(ids)) != len(ids):
        raise _err_param("suggestionIds 存在重复项")
    if not body.actionType or body.actionType not in ACTION_TYPES:
        raise _err_param(f"actionType 必填且为 8 类枚举（合法值: {', '.join(ACTION_TYPES)}）")

    rows = list(
        (await db.execute(select(LoopActionItem).where(LoopActionItem.id.in_(ids)))).scalars().all()
    )
    if len(rows) != len(ids):
        raise _err_param("suggestionIds 中存在无效建议")
    not_accepted = [r.id for r in rows if r.status != "ACCEPTED"]
    if not_accepted:
        raise _err_param(f"仅已接受（ACCEPTED）建议可转工单，当前含非接受状态: {not_accepted}")
    loop_ids = {r.loop_id for r in rows}
    if len(loop_ids) != 1:
        raise _err_param("跨回路建议不能合并转工单（须同一回路）")

    loop_id = rows[0].loop_id
    title = (body.title or rows[0].content[:50]).strip() or "处置工单"
    planned_at = _to_naive_utc(body.plannedAt) if body.plannedAt else None
    suggestion_ids_json = [str(r.id) for r in rows]

    order: HandlingOrder | None = None
    for attempt in (0, 1):
        try:
            order = HandlingOrder(
                id=str(uuid4()),
                order_no=await _next_order_no(db, bump=attempt),
                loop_id=loop_id,
                source="DIAGNOSIS",
                suggestion_ids=suggestion_ids_json,
                title=title[:200],
                action_type=body.actionType,
                planned_at=planned_at,
                planned_by=user.username,
                handler=body.handler,
                status="PENDING",
            )
            db.add(order)
            for r in rows:
                r.status = "CONVERTED"
                r.converted_order_id = order.id
            await db.commit()
            break
        except IntegrityError:
            # order_no 并发冲突：唯一约束兜底，序号+1 重试一次（§3.3）
            await db.rollback()
            if attempt == 1:
                raise
            rows = list(
                (await db.execute(select(LoopActionItem).where(LoopActionItem.id.in_(ids))))
                .scalars()
                .all()
            )
    assert order is not None  # 重试耗尽时上方已 raise
    await db.refresh(order)
    return success(_order_to_dict(order))


# ===========================================================================
# 工单侧（§6.2）
# ===========================================================================


def _order_list_row_to_dict(r: Any, unit_paths: dict[str, str]) -> dict[str, Any]:
    """工单清单行序列化（§6.2 返回行字段；反馈只给计数，全量在详情）。"""
    feedback_log = r.feedback_log or []
    return {
        "id": str(r.id),
        "orderNo": r.order_no,
        "loopId": str(r.loop_id),
        "loopTagName": r.loop_tag_name,
        "loopDescription": r.loop_description,
        "importanceLevel": int(r.importance_level) if r.importance_level else None,
        "unitId": str(r.unit_id) if r.unit_id else None,
        "unitPath": unit_paths.get(str(r.unit_id)) if r.unit_id else None,
        "source": r.source,
        "suggestionIds": r.suggestion_ids or [],
        "title": r.title,
        "actionType": r.action_type,
        "actionTypeLabel": _ACTION_TYPE_LABELS.get(r.action_type, r.action_type),
        "plannedAt": _iso(r.planned_at),
        "plannedBy": r.planned_by,
        "handler": r.handler,
        "startedAt": _iso(r.started_at),
        "feedbackCount": len(feedback_log),
        "submittedAt": _iso(r.submitted_at),
        "verifyResult": r.verify_result,
        "verifyResultLabel": (
            {"EFFECTIVE": "有效", "INEFFECTIVE": "无效"}.get(r.verify_result)
            if r.verify_result
            else None
        ),
        "verifiedBy": r.verified_by,
        "verifiedAt": _iso(r.verified_at),
        "cancelReason": r.cancel_reason,
        "status": r.status,
        "statusLabel": _ORDER_STATUS_LABELS.get(r.status, r.status),
        "updatedAt": _iso(r.updated_at),
    }


def _build_order_filters(params: dict[str, Any], args: dict[str, Any]) -> str:
    """工单清单 WHERE 子句（仅追加出现的条件，参数走 named params）。"""
    conds = ["1=1"]
    if args.get("status"):
        conds.append("ho.status = :status")
        params["status"] = args["status"]
    if args.get("action_type"):
        conds.append("ho.action_type = :action_type")
        params["action_type"] = args["action_type"]
    if args.get("source"):
        conds.append("ho.source = :source")
        params["source"] = args["source"]
    if args.get("loop_id"):
        conds.append("ho.loop_id = :loop_id")
        params["loop_id"] = args["loop_id"]
    if args.get("handler"):
        conds.append("ho.handler ILIKE :handler")
        params["handler"] = f"%{args['handler']}%"
    if args.get("keyword"):
        conds.append("(ho.order_no ILIKE :kw OR ll.tag_name ILIKE :kw OR ho.title ILIKE :kw)")
        params["kw"] = f"%{args['keyword']}%"
    if args.get("planned_before"):
        conds.append("ho.planned_at <= :planned_before")
        params["planned_before"] = args["planned_before"]
    if args.get("planned_after"):
        conds.append("ho.planned_at >= :planned_after")
        params["planned_after"] = args["planned_after"]
    if args.get("created_before"):
        conds.append("ho.created_at <= :created_before")
        params["created_before"] = args["created_before"]
    if args.get("created_after"):
        conds.append("ho.created_at >= :created_after")
        params["created_after"] = args["created_after"]
    if args.get("verified_before"):
        conds.append("ho.verified_at <= :verified_before")
        params["verified_before"] = args["verified_before"]
    if args.get("verified_after"):
        conds.append("ho.verified_at >= :verified_after")
        params["verified_after"] = args["verified_after"]
    if args.get("unit_ids") is not None:
        conds.append("ll.unit_id = ANY(CAST(:unit_ids AS uuid[]))")
        params["unit_ids"] = args["unit_ids"]
    return " AND ".join(conds)


@router.get("/orders", response_model=ApiResponse[dict])
async def list_orders(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    status: str | None = Query(None, description="工单状态（单值）"),
    actionType: str | None = Query(None),
    source: str | None = Query(None, pattern="^(DIAGNOSIS|MANUAL)$"),
    plantNodeId: str | None = Query(None, description="装置节点（递归下钻到单元）"),
    loopId: str | None = Query(None),
    handler: str | None = Query(None, description="处置人模糊"),
    keyword: str | None = Query(None, description="处置编号/回路位号/标题模糊"),
    plannedBefore: datetime | None = Query(None, description="计划时间止（ISO）"),
    plannedAfter: datetime | None = Query(None, description="计划时间起（ISO）"),
    createdBefore: datetime | None = Query(None, description="创建时间止（ISO，按 created_at）"),
    createdAfter: datetime | None = Query(None, description="创建时间起（ISO，按 created_at）"),
    verifiedBefore: datetime | None = Query(None, description="验证时间止（ISO，按 verified_at）"),
    verifiedAfter: datetime | None = Query(None, description="验证时间起（ISO，按 verified_at）"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
) -> dict:
    """工单清单（分页）。排序：状态分组（PENDING→REOPENED→EXECUTING→VERIFYING→其他）
    + updated_at DESC（§6.2）。

    时间窗口筛选：plannedBefore/plannedAfter 按 planned_at；createdBefore/createdAfter
    按 created_at；verifiedBefore/verifiedAfter 按 verified_at；均为闭区间。"""
    args: dict[str, Any] = {
        "status": status,
        "action_type": actionType,
        "source": source,
        "loop_id": loopId,
        "handler": handler,
        "keyword": keyword,
        "planned_before": _to_naive_utc(plannedBefore) if plannedBefore else None,
        "planned_after": _to_naive_utc(plannedAfter) if plannedAfter else None,
        "created_before": _to_naive_utc(createdBefore) if createdBefore else None,
        "created_after": _to_naive_utc(createdAfter) if createdAfter else None,
        "verified_before": _to_naive_utc(verifiedBefore) if verifiedBefore else None,
        "verified_after": _to_naive_utc(verifiedAfter) if verifiedAfter else None,
    }
    if plantNodeId is not None:
        args["unit_ids"] = await _load_subtree_unit_ids(db, plantNodeId)

    params: dict[str, Any] = {}
    where = _build_order_filters(params, args)

    total = (
        await db.execute(
            text(
                f"SELECT COUNT(*) FROM handling_order ho "
                f"JOIN loop_ledger ll ON ll.id = ho.loop_id WHERE {where}"
            ),
            params,
        )
    ).scalar() or 0

    rows = list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT ho.*, ll.tag_name AS loop_tag_name,
                           ll.description AS loop_description,
                           ll.importance_level, ll.unit_id
                    FROM handling_order ho
                    JOIN loop_ledger ll ON ll.id = ho.loop_id
                    WHERE {where}
                    ORDER BY {_ORDER_STATUS_RANK_SQL}, ho.updated_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {**params, "limit": pageSize, "offset": (page - 1) * pageSize},
            )
        ).all()
    )
    unit_paths = await _load_unit_paths(db)
    return success(
        {
            "items": [_order_list_row_to_dict(r, unit_paths) for r in rows],
            "total": int(total),
            "page": page,
            "pageSize": pageSize,
        }
    )


#: 工单来源中文名（CSV 导出展示口径）
_ORDER_SOURCE_LABELS = {
    "DIAGNOSIS": "诊断",
    "MANUAL": "手动",
}

#: 工单 CSV 导出行数上限（GAP-4：与诊断 /diagnosis/export 同口径）
_ORDER_EXPORT_LIMIT = 5000


def _fmt_csv_ts(iso: str | None) -> str:
    """ISO+Z → 可读时间（CSV 展示口径，空值落空串）。"""
    return iso[:19].replace("T", " ") if iso else ""


@router.get("/orders/export")
async def export_handling_orders(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    status: str | None = Query(None, description="工单状态（单值）"),
    actionType: str | None = Query(None),
    source: str | None = Query(None, pattern="^(DIAGNOSIS|MANUAL)$"),
    plantNodeId: str | None = Query(None, description="装置节点（递归下钻到单元）"),
    loopId: str | None = Query(None),
    handler: str | None = Query(None, description="处置人模糊"),
    keyword: str | None = Query(None, description="处置编号/回路位号/标题模糊"),
    plannedBefore: datetime | None = Query(None, description="计划时间止（ISO）"),
    plannedAfter: datetime | None = Query(None, description="计划时间起（ISO）"),
    createdBefore: datetime | None = Query(None, description="创建时间止（ISO，按 created_at）"),
    createdAfter: datetime | None = Query(None, description="创建时间起（ISO，按 created_at）"),
    verifiedBefore: datetime | None = Query(None, description="验证时间止（ISO，按 verified_at）"),
    verifiedAfter: datetime | None = Query(None, description="验证时间起（ISO，按 verified_at）"),
) -> StreamingResponse:
    """工单 CSV 导出（GAP-4）：筛选参数与 GET /orders 完全一致，上限 5000 行。

    排序同清单口径（状态分组 + updated_at DESC）；表头为字段中文名，
    UTF-8 with BOM 便于 Excel 直接打开（同诊断模块导出模式）。
    """
    args: dict[str, Any] = {
        "status": status,
        "action_type": actionType,
        "source": source,
        "loop_id": loopId,
        "handler": handler,
        "keyword": keyword,
        "planned_before": _to_naive_utc(plannedBefore) if plannedBefore else None,
        "planned_after": _to_naive_utc(plannedAfter) if plannedAfter else None,
        "created_before": _to_naive_utc(createdBefore) if createdBefore else None,
        "created_after": _to_naive_utc(createdAfter) if createdAfter else None,
        "verified_before": _to_naive_utc(verifiedBefore) if verifiedBefore else None,
        "verified_after": _to_naive_utc(verifiedAfter) if verifiedAfter else None,
    }
    if plantNodeId is not None:
        args["unit_ids"] = await _load_subtree_unit_ids(db, plantNodeId)

    params: dict[str, Any] = {}
    where = _build_order_filters(params, args)

    rows = list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT ho.*, ll.tag_name AS loop_tag_name,
                           ll.description AS loop_description,
                           ll.importance_level, ll.unit_id
                    FROM handling_order ho
                    JOIN loop_ledger ll ON ll.id = ho.loop_id
                    WHERE {where}
                    ORDER BY {_ORDER_STATUS_RANK_SQL}, ho.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {**params, "limit": _ORDER_EXPORT_LIMIT},
            )
        ).all()
    )
    unit_paths = await _load_unit_paths(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "处置编号",
            "回路",
            "装置",
            "标题",
            "处置类型",
            "来源",
            "处置人",
            "计划时间",
            "状态",
            "创建时间",
            "开工时间",
            "提交验证时间",
            "验证结论",
            "验证人",
            "验证时间",
            "最近更新",
        ]
    )
    for r in rows:
        row = _order_list_row_to_dict(r, unit_paths)
        writer.writerow(
            [
                row["orderNo"],
                row["loopTagName"] or "",
                row["unitPath"] or "",
                row["title"] or "",
                row["actionTypeLabel"] or "",
                _ORDER_SOURCE_LABELS.get(row["source"] or "", row["source"] or ""),
                row["handler"] or "",
                _fmt_csv_ts(row["plannedAt"]),
                row["statusLabel"] or "",
                _fmt_csv_ts(_iso(r.created_at)),
                _fmt_csv_ts(row["startedAt"]),
                _fmt_csv_ts(row["submittedAt"]),
                row["verifyResultLabel"] or "",
                row["verifiedBy"] or "",
                _fmt_csv_ts(row["verifiedAt"]),
                _fmt_csv_ts(row["updatedAt"]),
            ]
        )
    # UTF-8 BOM 头与正文分块流式返回（Excel 直接打开中文不乱码）
    return StreamingResponse(
        iter([b"\xef\xbb\xbf", buf.getvalue().encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=handling_orders.csv"},
    )


async def _load_order_suggestions(db: AsyncSession, order: HandlingOrder) -> list[dict[str, Any]]:
    """来源建议摘要（§6.2 详情：suggestion_ids 解析）。"""
    ids = order.suggestion_ids or []
    if not ids:
        return []
    rows = list(
        (
            await db.execute(
                select(LoopActionItem).where(LoopActionItem.id.in_([str(i) for i in ids]))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "content": r.content,
            "status": r.status,
            "statusLabel": _SUGGESTION_STATUS_LABELS.get(r.status, r.status),
            "category": r.category,
            "categoryLabel": _CATEGORY_LABELS.get(r.category) if r.category else None,
            "suggestedBy": r.suggested_by,
            "suggestedAt": _iso(r.suggested_at),
        }
        for r in rows
    ]


@router.get("/orders/{order_id}", response_model=ApiResponse[dict])
async def get_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """工单详情（§6.2：清单行 + action_detail + feedback_log + kpi 固化 + 来源建议摘要）。"""
    order = await _get_order_or_404(db, order_id)
    loop = (
        await db.execute(select(LoopLedger).where(LoopLedger.id == order.loop_id))
    ).scalar_one_or_none()
    unit_paths = await _load_unit_paths(db)
    data = _order_to_dict(order)
    if loop is not None:
        data.update(
            {
                "loopTagName": loop.tag_name,
                "loopDescription": loop.description,
                "importanceLevel": int(loop.importance_level) if loop.importance_level else None,
                "unitId": str(loop.unit_id) if loop.unit_id else None,
                "unitPath": unit_paths.get(str(loop.unit_id)) if loop.unit_id else None,
            }
        )
    data["suggestions"] = await _load_order_suggestions(db, order)
    return success(data)


@router.post("/orders", response_model=ApiResponse[dict])
async def create_order(
    body: CreateOrderBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """手动新建工单（§6.2：source=MANUAL；loopId/actionType 必填，title 缺省 content 前 50 字）。"""
    if not body.loopId:
        raise _err_param("loopId 必填")
    if not body.actionType or body.actionType not in ACTION_TYPES:
        raise _err_param(f"actionType 必填且为 8 类枚举（合法值: {', '.join(ACTION_TYPES)}）")
    await _get_loop_or_400(db, body.loopId)
    title = (body.title or (body.content[:50] if body.content else "") or "").strip()
    if not title:
        raise _err_param("title 必填（缺省取 content 前 50 字，两者均空时需显式填写）")

    planned_at = _to_naive_utc(body.plannedAt) if body.plannedAt else None
    order: HandlingOrder | None = None
    for attempt in (0, 1):
        try:
            order = HandlingOrder(
                id=str(uuid4()),
                order_no=await _next_order_no(db, bump=attempt),
                loop_id=body.loopId,
                source="MANUAL",
                suggestion_ids=None,
                title=title[:200],
                action_type=body.actionType,
                action_detail=body.actionDetail or None,
                planned_at=planned_at,
                planned_by=user.username,
                handler=body.handler,
                status="PENDING",
            )
            db.add(order)
            await db.commit()
            break
        except IntegrityError:
            # order_no 并发冲突：唯一约束兜底，序号+1 重试一次（§3.3）
            await db.rollback()
            if attempt == 1:
                raise
    assert order is not None  # 重试耗尽时上方已 raise
    await db.refresh(order)
    return success(_order_to_dict(order))


@router.post("/orders/{order_id}/start", response_model=ApiResponse[dict])
async def start_order(
    order_id: str,
    body: OrderStartBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """开工（PENDING/REOPENED → EXECUTING，§4.2 #1/#7）。

    守卫：handler 手工填写（缺省=当前登录用户）；TUNING 可带 pidBefore 并入
    action_detail；REOPENED 再开工时清空上一轮验证字段，待新轮回。
    """
    order = await _get_order_or_404(db, order_id)
    if order.status not in ("PENDING", "REOPENED"):
        raise _err_order_state(order, "开工")

    detail: dict[str, Any] = dict(order.action_detail or {})
    if body.actionDetail:
        detail.update(body.actionDetail)
    if body.pidBefore is not None:
        # TUNING 类型开工时回填调整前 P/I/D（§6.2）
        detail["pidBefore"] = body.pidBefore
    order.action_detail = detail or None
    order.handler = body.handler or user.username
    order.started_at = _utcnow_naive()
    if order.status == "REOPENED":
        # 新一轮处置：清空上一轮验证字段
        order.submitted_at = None
        order.verify_run_id = None
        order.verify_result = None
        order.verify_note = None
        order.verified_by = None
        order.verified_at = None
        order.kpi_before = None
        order.kpi_after = None
    order.status = "EXECUTING"
    await db.commit()
    # commit 后 updated_at（onupdate=func.now() 服务端计算值）已过期，
    # refresh 重新加载后再序列化，避免懒加载触发新查询 500
    await db.refresh(order)
    return success(_order_to_dict(order))


@router.post("/orders/{order_id}/feedback", response_model=ApiResponse[dict])
async def feedback_order(
    order_id: str,
    body: FeedbackBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """执行反馈（EXECUTING 自环，§4.2 #3：content 必填，追加 feedback_log，状态不变）。"""
    order = await _get_order_or_404(db, order_id)
    if order.status != "EXECUTING":
        raise _err_order_state(order, "执行反馈")
    if not body.content or not body.content.strip():
        raise _err_param("content 必填")
    log = [dict(entry) for entry in (order.feedback_log or [])]
    log.append({"at": _iso(_utcnow_naive()), "by": user.username, "content": body.content.strip()})
    order.feedback_log = log
    await db.commit()
    await db.refresh(order)
    return success(_order_to_dict(order))


@router.post("/orders/{order_id}/submit", response_model=ApiResponse[dict])
async def submit_order(
    order_id: str,
    body: SubmitBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """提交验证（EXECUTING → VERIFYING，§4.2 #4）。

    守卫：action_detail 必填（TUNING 必填 pidAfter）；与开工时已填详情合并
    （保留 pidBefore 等先填字段），提交键覆盖同名键。
    """
    order = await _get_order_or_404(db, order_id)
    if order.status != "EXECUTING":
        raise _err_order_state(order, "提交验证")
    _validate_action_detail(order.action_type, body.actionDetail or {})

    merged: dict[str, Any] = dict(order.action_detail or {})
    merged.update(body.actionDetail or {})
    order.action_detail = merged
    order.submitted_at = _utcnow_naive()
    order.status = "VERIFYING"
    # 09 设计方案 §5.4：TUNING 类提交验证 → 回写整定记录 APPLIED（同事务提交）
    await _writeback_tuning_record(db, order, "APPLIED")
    await db.commit()
    await db.refresh(order)
    return success(_order_to_dict(order))


@router.post("/orders/{order_id}/verify", response_model=ApiResponse[dict])
async def verify_order(
    order_id: str,
    body: VerifyBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """验证结论（VERIFYING → CLOSED/REOPENED，§4.2 #5/#6）。

    服务端此刻固化 kpi_before/kpi_after（前窗 started_at 口径，§4.3 v2.0），
    再落 verify 字段；验证人=当前登录用户。
    """
    order = await _get_order_or_404(db, order_id)
    if order.status != "VERIFYING":
        raise _err_order_state(order, "验证结论")
    if body.verifyResult not in ("EFFECTIVE", "INEFFECTIVE"):
        raise _err_param("verifyResult 必填且为 EFFECTIVE / INEFFECTIVE")
    if body.verifyRunId is not None:
        run = (
            await db.execute(select(DiagnosisRun.id).where(DiagnosisRun.id == body.verifyRunId))
        ).scalar_one_or_none()
        if run is None:
            raise BizError(
                code="ERR_PARAM",
                message=f"复诊诊断记录不存在: {body.verifyRunId}",
                status_code=400,
            )

    kpi_before, kpi_after = await _pull_kpi_windows(db, order)
    order.kpi_before = kpi_before
    order.kpi_after = kpi_after
    order.verify_result = body.verifyResult
    order.verify_note = body.verifyNote
    order.verify_run_id = body.verifyRunId
    order.verified_by = user.username
    order.verified_at = _utcnow_naive()
    order.status = "CLOSED" if body.verifyResult == "EFFECTIVE" else "REOPENED"
    # 09 设计方案 §5.4：TUNING 类验证有效 → VERIFIED；无效重开 → 回退 SIMULATED
    await _writeback_tuning_record(
        db, order, "VERIFIED" if body.verifyResult == "EFFECTIVE" else "SIMULATED"
    )
    await db.commit()
    await db.refresh(order)
    return success(_order_to_dict(order))


@router.post("/orders/{order_id}/cancel", response_model=ApiResponse[dict])
async def cancel_order(
    order_id: str,
    body: CancelBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """作废（PENDING → CANCELLED 终态，§4.2 #2；cancel_reason 必填）。"""
    order = await _get_order_or_404(db, order_id)
    if order.status != "PENDING":
        raise _err_order_state(order, "作废")
    if not body.cancelReason or not body.cancelReason.strip():
        raise _err_param("cancelReason 必填")
    order.cancel_reason = body.cancelReason.strip()
    order.status = "CANCELLED"
    await db.commit()
    await db.refresh(order)
    return success(_order_to_dict(order))


@router.post("/orders/{order_id}/kpi-comparison", response_model=ApiResponse[dict])
async def order_kpi_comparison(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """KPI 前后对比预览（VERIFYING 阶段实时拉取，不落库；verify 时才固化）。"""
    order = await _get_order_or_404(db, order_id)
    if order.status != "VERIFYING":
        raise _err_order_state(order, "KPI 对比预览")
    kpi_before, kpi_after = await _pull_kpi_windows(db, order)
    return success(
        {
            "id": order.id,
            "orderNo": order.order_no,
            "loopId": order.loop_id,
            "kpiBefore": kpi_before,
            "kpiAfter": kpi_after,
            "window": {
                "beforeStart": _iso(order.started_at - _KPI_WINDOW) if order.started_at else None,
                "beforeEnd": _iso(order.started_at),
                "afterStart": _iso(order.submitted_at),
                "afterEnd": _iso(order.submitted_at + _KPI_WINDOW) if order.submitted_at else None,
            },
        }
    )


# ===========================================================================
# 档案聚合与统计（§6.3，双实体口径）
# ===========================================================================

# 双实体聚合 SQL 已下沉 app/services/handling_stats.py（P0-2 单一实现）


#: KPI 改善筛选（§6.3：按最近闭环 KPI delta 情况筛回路，工单口径）
_KPI_DELTA_FILTERS = {
    "improved": "agg.last_closed_kpi_delta > 0",
    "degraded": "agg.last_closed_kpi_delta < 0",
    "closed": "agg.ho_closed > 0",
    "unclosed": "COALESCE(agg.ho_closed, 0) = 0",
}

#: 状态分布筛选列映射（建议 5 态 su_* + 工单 6 态 ho_*；同名状态二者任一命中）
_STATUS_FILTER_COLUMNS = {
    "PENDING": ("su_pending", "ho_pending"),
    "ACCEPTED": ("su_accepted",),
    "CONVERTED": ("su_converted",),
    "REJECTED": ("su_rejected",),
    "IGNORED": ("su_ignored",),
    "EXECUTING": ("ho_executing",),
    "VERIFYING": ("ho_verifying",),
    "CLOSED": ("ho_closed",),
    "REOPENED": ("ho_reopened",),
    "CANCELLED": ("ho_cancelled",),
}


def _loop_agg_row_to_dict(r: Any, unit_paths: dict[str, str]) -> dict[str, Any]:
    """回路聚合行序列化（camelCase，双实体口径）。"""

    def _n(v: Any) -> int:
        return int(v or 0)

    verified = _n(r.ho_verified)
    closed = _n(r.ho_closed)
    delta = r.last_closed_kpi_delta
    return {
        "loopId": str(r.loop_id),
        "loopTagName": r.loop_tag_name,
        "loopDescription": r.loop_description,
        "importanceLevel": int(r.importance_level) if r.importance_level else None,
        "unitPath": unit_paths.get(str(r.unit_id)) if r.unit_id else None,
        "suggestionCounts": {
            "pending": _n(r.su_pending),
            "accepted": _n(r.su_accepted),
            "converted": _n(r.su_converted),
            "rejected": _n(r.su_rejected),
            "ignored": _n(r.su_ignored),
        },
        "suggestionTotal": _n(r.suggestion_total),
        "orderCounts": {
            "pending": _n(r.ho_pending),
            "executing": _n(r.ho_executing),
            "verifying": _n(r.ho_verifying),
            "closed": closed,
            "reopened": _n(r.ho_reopened),
            "cancelled": _n(r.ho_cancelled),
        },
        "orderTotal": _n(r.order_total),
        "closeRate": round(closed / verified, 4) if verified else None,
        "lastClosedKpiDelta": round(float(delta), 2) if delta is not None else None,
        "lastSuggestedAt": _iso(r.last_suggested_at),
        "lastHandledAt": _iso(r.last_handled_at),
        "lastHandledBy": r.last_handled_by,
    }


@router.get("/loops", response_model=ApiResponse[dict])
async def list_handling_loops(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    plantNodeId: str | None = Query(None, description="装置节点（递归下钻到单元）"),
    importanceLevel: int | None = Query(None, ge=1, le=3),
    keyword: str | None = Query(None, description="回路位号/名称模糊"),
    status: str | None = Query(
        None, description="状态分布筛选（多值逗号分隔；建议/工单该状态计数>0 即命中）"
    ),
    kpiDelta: str | None = Query(
        None,
        pattern="^(improved|degraded|closed|unclosed)$",
        description="KPI 改善筛选：improved 改善/degraded 恶化/closed 有闭环/unclosed 无闭环",
    ),
    activeOnly: bool = Query(False, description="仅看有在途（待审核/已接受建议或非终态工单 >0）"),
    sort: str = Query("recent", pattern="^(recent|reopened)$"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
) -> dict:
    """按回路聚合（档案页主查询，§6.3 双实体口径）。

    - 聚合：建议五态分布 + 工单六态分布 + 闭环率 + 最近闭环 KPI delta
      + 最近建议/处置时间与处置人
    - 筛选：plantNodeId 递归 / 等级 / 位号模糊 / 状态分布 / KPI 改善 / 仅看在途
    - 排序：recent=最近活动（建议/工单较新者）倒序（默认）；
      reopened=工单重开次数倒序（问题回路 Top）
    """
    statuses = [s for s in (status or "").split(",") if s]
    for s in statuses:
        if s not in _STATUS_FILTER_COLUMNS:
            raise _err_param(f"status 非法: {s}（合法值: {', '.join(_STATUS_FILTER_COLUMNS)}）")

    # 外层可见别名为内层子查询别名 base（聚合 SQL 已透出 unit_id/
    # importance_level 等列），过滤条件必须引用 base 而非子查询内部的 ll
    where: list[str] = ["1=1"]
    params: dict[str, Any] = {}
    if importanceLevel:
        where.append("base.importance_level = :importance_level")
        params["importance_level"] = importanceLevel
    if keyword:
        where.append("(base.loop_tag_name ILIKE :kw OR base.loop_description ILIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if plantNodeId is not None:
        subtree_ids = await _load_subtree_unit_ids(db, plantNodeId)
        where.append("base.unit_id = ANY(CAST(:unit_ids AS uuid[]))")
        params["unit_ids"] = subtree_ids

    # plantNodeId/importanceLevel 过滤下推到内层聚合（loop_id IN 子查询），
    # 避免建议/工单全表 GROUP BY 后再外层过滤；外层 where 保留同条件作冗余防护
    loop_scope: list[str] = []
    if importanceLevel:
        loop_scope.append("importance_level = :importance_level")
    if plantNodeId is not None:
        loop_scope.append("unit_id = ANY(CAST(:unit_ids AS uuid[]))")
    loop_filter = (
        f"loop_id IN (SELECT id FROM loop_ledger WHERE {' AND '.join(loop_scope)})"
        if loop_scope
        else ""
    )

    agg_inner = (
        f"SELECT * FROM ({_build_loop_agg_sql(loop_filter)}) base "
        f"WHERE {' AND '.join(where)} "
        f"AND (COALESCE(base.suggestion_total, 0) + COALESCE(base.order_total, 0)) > 0"
    )

    # 聚合后筛选（HAVING 语义，作用于外层 WHERE；单状态的多列命中为 OR 组，
    # 多状态之间 AND 连接——OR 组必须加括号，避免 AND 优先级改变语义）
    post: list[str] = []
    for s in statuses:
        cols = _STATUS_FILTER_COLUMNS[s]
        post.append("(" + " OR ".join(f"COALESCE(agg.{c}, 0) > 0" for c in cols) + ")")
    if kpiDelta:
        post.append(_KPI_DELTA_FILTERS[kpiDelta])
    if activeOnly:
        post.append(
            "(COALESCE(agg.su_pending, 0) + COALESCE(agg.su_accepted, 0) "
            "+ COALESCE(agg.ho_pending, 0) + COALESCE(agg.ho_executing, 0) "
            "+ COALESCE(agg.ho_verifying, 0) + COALESCE(agg.ho_reopened, 0)) > 0"
        )
    post_where = f" WHERE {' AND '.join(post)}" if post else ""

    order = (
        "agg.ho_reopened DESC NULLS LAST, agg.ho_ineffective DESC NULLS LAST, "
        "agg.order_total DESC NULLS LAST"
        if sort == "reopened"
        else (
            "GREATEST(COALESCE(agg.last_suggested_at, 'epoch'::timestamp), "
            "COALESCE(agg.last_order_at, 'epoch'::timestamp)) DESC"
        )
    )

    # COUNT 与分页合并：窗口函数 COUNT(*) OVER() 随分页结果带出总数，
    # 消除原先整轮重复聚合的 COUNT 查询
    rows = list(
        (
            await db.execute(
                text(
                    f"SELECT *, COUNT(*) OVER() AS _total FROM ({agg_inner}) agg{post_where} "
                    f"ORDER BY {order} LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": pageSize, "offset": (page - 1) * pageSize},
            )
        ).all()
    )
    if rows:
        total = rows[0]._total or 0
    elif page == 1:
        total = 0
    else:
        # 页码超出末页时窗口函数无行可携带总数，补一次 COUNT（仅该冷路径）
        total = (
            await db.execute(text(f"SELECT COUNT(*) FROM ({agg_inner}) agg{post_where}"), params)
        ).scalar() or 0
    unit_paths = await _load_unit_paths(db)
    return success(
        {
            "items": [_loop_agg_row_to_dict(r, unit_paths) for r in rows],
            "total": int(total),
            "page": page,
            "pageSize": pageSize,
        }
    )


@router.get("/statistics", response_model=ApiResponse[dict])
async def get_handling_statistics(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    months: int = Query(6, ge=1, le=12, description="月度趋势窗口（月数）"),
) -> dict:
    """处置统计页数据（§6.3，工单维度 + 建议驳回率）。

    聚合逻辑已下沉 app/services/handling_stats.build_handling_statistics
    （报告模块优化 P0-2，与 /reports/handling-statistics 共用单一实现；
    本端点不传时间窗/装置过滤，行为契约不变）：

    - summary：本月（北京时间月界）闭环数 / 闭环率 / 平均处置时长（创建→验证闭环）/
      无效重开率 / 平均 KPI 改善分 / 驳回率（建议侧 REJECTED/已审核）/
      平均排程周期（工单创建→开工均值）；无数据时相关项为 null（空态显 —）
    - monthly：近 N 月（北京时间月界，按 verified_at 归月）闭环数与闭环率，空月补零
    - byType / byUnit / topLoops：类型分布、装置闭环分布、重开次数 Top 10（工单口径）
    """
    data = await _collect_handling_statistics(db, months=months)
    return success(data)


__all__ = ["router"]
