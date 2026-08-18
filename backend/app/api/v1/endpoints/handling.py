"""处置模块 API（Phase 1 后端）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §4 状态机 / §6 API 定义
端点清单（前缀 /handling）：
- GET  /items                     处置清单（分页/筛选/状态分组排序，§6.1）
- GET  /items/stats               状态统计（各状态计数 + 本月闭环数，§6.1）
- GET  /items/{id}                处置详情（清单行 + action_detail/kpi 固化/忽略原因，§6.1）
- POST /items/{id}/start           开始处置（PENDING/REOPENED → HANDLING）
- POST /items/{id}/submit          提交验证（HANDLING → VERIFYING，按类型校验必填子字段）
- POST /items/{id}/verify          验证结论（VERIFYING → CLOSED/REOPENED，服务端固化 KPI 前后快照）
- POST /items/{id}/ignore          忽略（PENDING → IGNORED，ignore_reason 必填）
- POST /items/{id}/kpi-comparison  KPI 前后对比预览（VERIFYING 阶段，不落库）

状态机后端强校验：所有非法迁移返回 ERR_STATE 400（§4.2 流转表为唯一合法迁移）。
CLOSED / IGNORED 为终态，CLOSED 不允许重开（评审决策 #9）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.api.v1.endpoints.diagnosis_v2 import _CATEGORY_LABELS
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop_action_item import ACTION_TYPES, LoopActionItem
from app.models.metric import KpiSnapshotHourly
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success

router = APIRouter(prefix="/handling", tags=["handling"])

#: 允许处置流转的角色（复用诊断触发角色口径 _DIAGNOSIS_TRIGGER_ROLES，§7）
_HANDLING_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN")

#: 处置类型中文名（§5.1）
_ACTION_TYPE_LABELS = {
    "TUNING": "参数整定",
    "VALVE": "阀门检修",
    "INSTRUMENT": "仪表校验",
    "LINK": "链路修复",
    "PROCESS": "工艺调整",
    "UTILIZATION": "恢复投用",
    "RECONFIG": "组态改造",
    "OTHER": "其他",
}

#: 状态中文名（§4.1）
_STATUS_LABELS = {
    "PENDING": "待处置",
    "HANDLING": "处置中",
    "VERIFYING": "验证中",
    "CLOSED": "已闭环",
    "REOPENED": "重开",
    "IGNORED": "已忽略",
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


def _err_state(item: LoopActionItem, action: str) -> BizError:
    return BizError(
        code="ERR_STATE",
        message=(
            f"当前状态不允许{action}：{_STATUS_LABELS.get(item.status, item.status)}"
            f"（{item.status}）"
        ),
        status_code=400,
    )


# ---------------------------------------------------------------------------
# 请求体（业务必填/枚举校验在端点内手工执行，保证 ERR_PARAM 400 口径，
# 不走 pydantic Field 约束的 422 通道）
# ---------------------------------------------------------------------------


class StartBody(BaseModel):
    """开始处置请求体（§6.2：actionType 必填；handler 缺省=当前登录用户）。"""

    actionType: str | None = None
    handler: str | None = Field(None, max_length=64)
    actionDetail: dict[str, Any] | None = None
    pidBefore: dict[str, Any] | None = None


class SubmitBody(BaseModel):
    """提交验证请求体（§6.2：actionDetail 必填，按类型校验子字段）。"""

    actionDetail: dict[str, Any] | None = None


class VerifyBody(BaseModel):
    """验证结论请求体（§6.2：verifyResult 必填 EFFECTIVE/INEFFECTIVE）。"""

    verifyResult: str | None = None
    verifyNote: str | None = Field(None, max_length=500)
    verifyRunId: str | None = None


class IgnoreBody(BaseModel):
    """忽略请求体（§6.2：ignoreReason 必填）。"""

    ignoreReason: str | None = Field(None, max_length=200)


# ---------------------------------------------------------------------------
# 序列化与公共查询
# ---------------------------------------------------------------------------


def _item_to_dict(row: LoopActionItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "runId": row.run_id,
        "loopId": row.loop_id,
        "source": row.source,
        "category": row.category,
        "content": row.content,
        "basis": row.basis,
        "priority": row.priority,
        "status": row.status,
        "statusLabel": _STATUS_LABELS.get(row.status, row.status),
        "suggestedBy": row.suggested_by,
        "suggestedAt": _iso(row.suggested_at),
        "actionType": row.action_type,
        "actionTypeLabel": (
            _ACTION_TYPE_LABELS.get(row.action_type, row.action_type) if row.action_type else None
        ),
        "actionDetail": row.action_detail,
        "handledBy": row.handled_by,
        "handledAt": _iso(row.handled_at),
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
        "ignoreReason": row.ignore_reason,
        "updatedAt": _iso(row.updated_at),
    }


async def _get_item_or_404(db: AsyncSession, item_id: str) -> LoopActionItem:
    row = (
        await db.execute(select(LoopActionItem).where(LoopActionItem.id == item_id))
    ).scalar_one_or_none()
    if row is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"处置项不存在: {item_id}", status_code=404)
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
    db: AsyncSession, item: LoopActionItem
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """拉取 KPI 前后窗口快照摘要（§4.3：前窗以 handled_at 为界，后窗以 submitted_at 为界）。

    - kpi_before：[handled_at − 24h, handled_at]（处置开始前的最后基线）
    - kpi_after：[submitted_at, submitted_at + 24h]（提交验证后线上效果）
    两侧窗口隔离：处置执行期数据不参与对比；窗口不足/无快照侧为 None。
    """
    before = after = None
    if item.handled_at:
        snap = await _latest_snapshot_in_window(
            db, item.loop_id, item.handled_at - _KPI_WINDOW, item.handled_at
        )
        before = _kpi_summary(snap)
    if item.submitted_at:
        snap = await _latest_snapshot_in_window(
            db, item.loop_id, item.submitted_at, item.submitted_at + _KPI_WINDOW
        )
        after = _kpi_summary(snap)
    return before, after


def _validate_action_detail(action_type: str | None, detail: dict[str, Any]) -> None:
    """submit 按类型校验必填子字段（§5.2：TUNING 必填 pidAfter；其余仅要求非空对象）。"""
    if not detail:
        raise _err_param("actionDetail 必填且为非空对象")
    if action_type == "TUNING":
        pid_after = detail.get("pidAfter")
        if not isinstance(pid_after, dict) or not pid_after:
            raise _err_param("TUNING 类型提交验证时 actionDetail.pidAfter 必填（非空对象）")


# ---------------------------------------------------------------------------
# 清单/详情/统计（§6.1）
# ---------------------------------------------------------------------------

#: 清单排序的状态分组优先级（§6.1：PENDING→REOPENED→HANDLING→VERIFYING→其他）
_STATUS_RANK_SQL = (
    "CASE ai.status WHEN 'PENDING' THEN 0 WHEN 'REOPENED' THEN 1 "
    "WHEN 'HANDLING' THEN 2 WHEN 'VERIFYING' THEN 3 ELSE 4 END"
)

_BJ_TZ = timezone(timedelta(hours=8))


def _to_naive_utc(dt: datetime) -> datetime:
    """aware datetime（前端 ISO 带 Z/+08:00）→ naive UTC（同诊断模块口径）。"""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _build_unit_paths(rows: list[Any]) -> dict[str, str]:
    """plant_node 平铺行 → 节点全路径映射（"装置.单元" 树回溯，评审决策 #12）。"""
    nodes = {
        str(r.id): (r.name, str(r.parent_id) if r.parent_id else None)
        for r in rows
        if r.id is not None
    }
    paths: dict[str, str] = {}
    for nid in nodes:
        parts: list[str] = []
        cur: str | None = nid
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            node = nodes.get(cur)
            if node is None:
                break
            parts.append(node[0])
            cur = node[1]
        paths[nid] = ".".join(reversed(parts))
    return paths


def _list_row_to_item(r: Any, unit_paths: dict[str, str]) -> dict[str, Any]:
    """清单行序列化（§6.1 返回行字段）。"""
    return {
        "id": str(r.id),
        "runId": str(r.run_id),
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
        "actionType": r.action_type,
        "actionTypeLabel": (
            _ACTION_TYPE_LABELS.get(r.action_type, r.action_type) if r.action_type else None
        ),
        "status": r.status,
        "statusLabel": _STATUS_LABELS.get(r.status, r.status),
        "priority": r.priority,
        "suggestedBy": r.suggested_by,
        "suggestedAt": _iso(r.suggested_at),
        "handledBy": r.handled_by,
        "handledAt": _iso(r.handled_at),
        "submittedAt": _iso(r.submitted_at),
        "verifyResult": r.verify_result,
        "verifyResultLabel": (
            {"EFFECTIVE": "有效", "INEFFECTIVE": "无效"}.get(r.verify_result)
            if r.verify_result
            else None
        ),
        "verifiedBy": r.verified_by,
        "verifiedAt": _iso(r.verified_at),
        "updatedAt": _iso(r.updated_at),
    }


def _build_list_filters(params: dict[str, Any], args: dict[str, Any]) -> str:
    """按查询参数拼装 WHERE 子句（仅追加出现的条件，参数走 named params）。"""
    conds = ["1=1"]
    if args.get("statuses"):
        conds.append("ai.status = ANY(CAST(:statuses AS text[]))")
        params["statuses"] = args["statuses"]
    if args.get("action_type"):
        conds.append("ai.action_type = :action_type")
        params["action_type"] = args["action_type"]
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


async def _load_unit_paths(db: AsyncSession) -> dict[str, str]:
    rows = list((await db.execute(text("SELECT id, name, parent_id FROM plant_node"))).all())
    return _build_unit_paths(rows)


@router.get("/items", response_model=ApiResponse[dict])
async def list_handling_items(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    status: str | None = Query(None, description="状态多值，逗号分隔"),
    actionType: str | None = Query(None),
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
    """处置清单（分页）。排序：状态分组优先级 + updatedAt DESC（§6.1）。"""
    args: dict[str, Any] = {
        "statuses": [s for s in (status or "").split(",") if s] or None,
        "action_type": actionType,
        "source": source,
        "loop_id": loopId,
        "importance_level": importanceLevel,
        "keyword": keyword,
        "start": _to_naive_utc(startTime) if startTime else None,
        "end": _to_naive_utc(endTime) if endTime else None,
    }
    if plantNodeId is not None:
        subtree = (
            await db.execute(
                text(
                    """
                    WITH RECURSIVE node_tree AS (
                        SELECT id FROM plant_node WHERE id = :root_id
                        UNION ALL
                        SELECT child.id FROM plant_node child
                        JOIN node_tree nt ON child.parent_id = nt.id
                    )
                    SELECT id FROM node_tree
                    """
                ),
                {"root_id": plantNodeId},
            )
        ).all()
        args["unit_ids"] = [str(r.id) for r in subtree]

    params: dict[str, Any] = {}
    where = _build_list_filters(params, args)

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
                           ll.importance_level, ll.unit_id
                    FROM loop_action_item ai
                    JOIN loop_ledger ll ON ll.id = ai.loop_id
                    WHERE {where}
                    ORDER BY {_STATUS_RANK_SQL}, ai.updated_at DESC
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
            "items": [_list_row_to_item(r, unit_paths) for r in rows],
            "total": int(total),
            "page": page,
            "pageSize": pageSize,
        }
    )


@router.get("/items/stats", response_model=ApiResponse[dict])
async def get_handling_stats(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """状态统计（§6.1：各状态计数 + 本月闭环数；本月按北京时间月界）。"""
    rows = (
        await db.execute(text("SELECT status, COUNT(*) FROM loop_action_item GROUP BY status"))
    ).all()
    counts = dict.fromkeys(_STATUS_LABELS, 0)
    for r in rows:
        counts[r.status] = int(r[1])

    month_start_utc = (
        (datetime.now(_BJ_TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    month_closed = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM loop_action_item "
                "WHERE status = 'CLOSED' AND verified_at >= :month_start"
            ),
            {"month_start": month_start_utc},
        )
    ).scalar() or 0
    return success({"counts": counts, "monthClosed": int(month_closed)})


@router.get("/items/{item_id}", response_model=ApiResponse[dict])
async def get_handling_item(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """处置详情（§6.1：清单行全部字段 + action_detail/kpi 固化/ignore_reason/basis）。"""
    rows = list(
        (
            await db.execute(
                text(
                    """
                    SELECT ai.*, ll.tag_name AS loop_tag_name,
                           ll.description AS loop_description,
                           ll.importance_level, ll.unit_id
                    FROM loop_action_item ai
                    JOIN loop_ledger ll ON ll.id = ai.loop_id
                    WHERE ai.id = :item_id
                    """
                ),
                {"item_id": item_id},
            )
        ).all()
    )
    if not rows:
        raise BizError(code="ERR_NOT_FOUND", message=f"处置项不存在: {item_id}", status_code=404)
    r = rows[0]
    unit_paths = await _load_unit_paths(db)
    item = _list_row_to_item(r, unit_paths)
    item.update(
        {
            "basis": r.basis,
            "actionDetail": r.action_detail,
            "kpiBefore": r.kpi_before,
            "kpiAfter": r.kpi_after,
            "verifyRunId": str(r.verify_run_id) if r.verify_run_id else None,
            "verifyNote": r.verify_note,
            "ignoreReason": r.ignore_reason,
            "tuningRecordId": str(r.tuning_record_id) if r.tuning_record_id else None,
        }
    )
    return success(item)


# ---------------------------------------------------------------------------
# 流转端点（§6.2）
# ---------------------------------------------------------------------------


@router.post("/items/{item_id}/start", response_model=ApiResponse[dict])
async def start_handling(
    item_id: str,
    body: StartBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """开始处置（PENDING/REOPENED → HANDLING）。

    守卫：action_type 必填且为 8 类枚举；handler 手工填写（缺省=当前登录用户）。
    REOPENED 再 start 时清空上一轮验证字段，待新轮回（§4.2 #3）。
    """
    item = await _get_item_or_404(db, item_id)
    if item.status not in ("PENDING", "REOPENED"):
        raise _err_state(item, "开始处置")
    if not body.actionType:
        raise _err_param("actionType 必填")
    if body.actionType not in ACTION_TYPES:
        raise _err_param(f"actionType 非法: {body.actionType}（合法值: {', '.join(ACTION_TYPES)}）")

    detail: dict[str, Any] = dict(body.actionDetail) if body.actionDetail else {}
    if body.pidBefore is not None:
        # TUNING 类型建议开始处置时回填调整前 P/I/D（§6.2）
        detail["pidBefore"] = body.pidBefore

    item.action_type = body.actionType
    item.action_detail = detail or None
    item.handled_by = body.handler or user.username
    item.handled_at = _utcnow_naive()
    if item.status == "REOPENED":
        # 新一轮处置：清空上一轮验证字段
        item.submitted_at = None
        item.verify_run_id = None
        item.verify_result = None
        item.verify_note = None
        item.verified_by = None
        item.verified_at = None
        item.kpi_before = None
        item.kpi_after = None
    item.status = "HANDLING"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/submit", response_model=ApiResponse[dict])
async def submit_handling(
    item_id: str,
    body: SubmitBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """提交验证（HANDLING → VERIFYING）。

    守卫：action_detail 必填（TUNING 必填 pidAfter）；与开始处置时已填详情合并
    （保留 pidBefore 等先填字段），提交键覆盖同名键。
    """
    item = await _get_item_or_404(db, item_id)
    if item.status != "HANDLING":
        raise _err_state(item, "提交验证")
    _validate_action_detail(item.action_type, body.actionDetail or {})

    merged: dict[str, Any] = dict(item.action_detail or {})
    merged.update(body.actionDetail or {})
    item.action_detail = merged
    item.submitted_at = _utcnow_naive()
    item.status = "VERIFYING"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/verify", response_model=ApiResponse[dict])
async def verify_handling(
    item_id: str,
    body: VerifyBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """验证结论（VERIFYING → CLOSED/REOPENED）。

    服务端此刻固化 kpi_before/kpi_after（防快照滚动导致对比漂移，§4.3），
    再落 verify 字段；验证人=当前登录用户。
    """
    item = await _get_item_or_404(db, item_id)
    if item.status != "VERIFYING":
        raise _err_state(item, "验证结论")
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

    kpi_before, kpi_after = await _pull_kpi_windows(db, item)
    item.kpi_before = kpi_before
    item.kpi_after = kpi_after
    item.verify_result = body.verifyResult
    item.verify_note = body.verifyNote
    item.verify_run_id = body.verifyRunId
    item.verified_by = user.username
    item.verified_at = _utcnow_naive()
    item.status = "CLOSED" if body.verifyResult == "EFFECTIVE" else "REOPENED"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/ignore", response_model=ApiResponse[dict])
async def ignore_handling(
    item_id: str,
    body: IgnoreBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """忽略（PENDING → IGNORED 终态）。守卫：ignore_reason 必填。"""
    item = await _get_item_or_404(db, item_id)
    if item.status != "PENDING":
        raise _err_state(item, "忽略")
    if not body.ignoreReason or not body.ignoreReason.strip():
        raise _err_param("ignoreReason 必填")
    item.ignore_reason = body.ignoreReason.strip()
    item.status = "IGNORED"
    await db.commit()
    return success(_item_to_dict(item))


@router.post("/items/{item_id}/kpi-comparison", response_model=ApiResponse[dict])
async def kpi_comparison(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_HANDLING_ROLES)),
) -> dict:
    """KPI 前后对比预览（VERIFYING 阶段实时拉取，不落库；verify 时才固化）。"""
    item = await _get_item_or_404(db, item_id)
    if item.status != "VERIFYING":
        raise _err_state(item, "KPI 对比预览")
    kpi_before, kpi_after = await _pull_kpi_windows(db, item)
    return success(
        {
            "id": item.id,
            "loopId": item.loop_id,
            "kpiBefore": kpi_before,
            "kpiAfter": kpi_after,
            "window": {
                "beforeStart": _iso(item.handled_at - _KPI_WINDOW) if item.handled_at else None,
                "beforeEnd": _iso(item.handled_at),
                "afterStart": _iso(item.submitted_at),
                "afterEnd": _iso(item.submitted_at + _KPI_WINDOW) if item.submitted_at else None,
            },
        }
    )


__all__ = ["router"]
