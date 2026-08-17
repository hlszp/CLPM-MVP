"""诊断模块 API（MVP v2，重设计版）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.1
端点清单：
- POST /diagnosis/run        发起诊断（异步任务，仅手动触发）
- GET  /diagnosis/runs       诊断记录列表（筛选/分页）
- GET  /diagnosis/runs/{id}  诊断详情（算子结果+证据链+波形快照）
- GET  /diagnosis/operators  算子注册表元数据（前端+AI 共用）
- GET  /diagnosis/export     记录 CSV 导出

旧 endpoints/diagnosis.py 按 MVP 屏蔽策略保留不动（未注册）。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.task import TaskType
from app.services.diagnosis_operators import list_operators
from app.services.diagnosis_operators.classification import get_confidence_definitions
from app.services.task_tracker import create_task

router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])

#: 允许发起诊断的角色（复用任务创建者角色口径）
_DIAGNOSIS_TRIGGER_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN")

#: 时间窗预设 → 小时数
_TIME_WINDOW_PRESETS = {"last_24h": 24, "last_7d": 24 * 7, "last_30d": 24 * 30}

_CATEGORY_LABELS = {
    "TUNING": "参数问题（PID 整定）",
    "VALVE": "阀门/执行机构问题",
    "INSTRUMENT": "仪表/测量问题",
    "COMMUNICATION": "通信链路问题",
    "PROCESS": "工艺/外扰问题",
    "UTILIZATION": "投用/操作问题",
    "DESIGN": "组态/设计问题",
    "DATA_INSUFFICIENT": "数据不足/无法判定",
}


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TimeWindowBody(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    preset: str | None = Field(None, description="last_24h / last_7d / last_30d")


class TriggerDiagnosisBody(BaseModel):
    loopIds: list[UUID]
    timeWindow: TimeWindowBody = Field(default_factory=TimeWindowBody)
    operatorGroup: str = Field("full", pattern="^(full|fast)$")
    """单算子细选白名单（None/空=按 operatorGroup 执行；落库记 custom）"""
    operators: list[str] | None = None


def _to_naive_utc(dt: datetime) -> datetime:
    """aware datetime（前端 ISO 带 Z/+08:00）→ naive UTC。

    PG 业务列统一 TIMESTAMP WITHOUT TIME ZONE（naive UTC 口径），
    aware 值直接比较/落库会抛 asyncpg DataError
    （can't subtract offset-naive and offset-aware datetimes）。
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _resolve_window(body: TimeWindowBody) -> tuple[datetime, datetime]:
    end = _to_naive_utc(body.end) if body.end else _utcnow_naive()
    if body.preset:
        hours = _TIME_WINDOW_PRESETS.get(body.preset)
        if hours is None:
            raise BizError(
                code="ERR_PARAM", message=f"未知时间窗预设: {body.preset}", status_code=400
            )
        start = end - timedelta(hours=hours)
    elif body.start:
        start = _to_naive_utc(body.start)
    else:
        raise BizError(
            code="ERR_PARAM", message="timeWindow 需提供 preset 或 start", status_code=400
        )
    if start >= end:
        raise BizError(code="ERR_PARAM", message="时间窗起点必须早于终点", status_code=400)
    return start, end


def _run_to_summary(row: DiagnosisRun, loop_tag: str | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "taskId": row.task_id,
        "loopId": str(row.loop_id),
        "loopTagName": loop_tag,
        "triggeredBy": row.triggered_by,
        "timeWindowStart": row.time_window_start.isoformat() if row.time_window_start else None,
        "timeWindowEnd": row.time_window_end.isoformat() if row.time_window_end else None,
        "operatorGroup": row.operator_group,
        "status": row.status,
        "primaryCategory": row.primary_category,
        "primaryCategoryLabel": _CATEGORY_LABELS.get(row.primary_category or "", None),
        "primaryConfidence": float(row.primary_confidence)
        if row.primary_confidence is not None
        else None,
        "secondaryCategories": row.secondary_categories or [],
        "pendingReview": row.pending_review or [],
        "severity": row.severity,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _run_to_detail(row: DiagnosisRun, loop_tag: str | None) -> dict[str, Any]:
    detail = _run_to_summary(row, loop_tag)
    detail.update(
        {
            "dataGate": row.data_gate,
            "operatorResults": row.operator_results,
            "fusionResults": row.fusion_results,
            "symptomTags": row.symptom_tags,
            "rationale": row.rationale,
            "recommendations": row.recommendations,
            "evidenceCharts": row.evidence_charts,
            "thresholdVersion": row.threshold_version,
            "algorithmVersion": row.algorithm_version,
            "startedAt": row.started_at.isoformat() if row.started_at else None,
            "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
            "durationMs": row.duration_ms,
            # 置信度显式定义（分类级 + 算子级 + 融合规则，常量生成不入库）
            "confidenceDefinitions": get_confidence_definitions(),
        }
    )
    return detail


@router.post("/run", response_model=ApiResponse[dict])
async def trigger_diagnosis(
    body: TriggerDiagnosisBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_DIAGNOSIS_TRIGGER_ROLES)),
) -> dict:
    """发起诊断：即时校验 → TaskTracker 建单 → Celery 异步执行。"""
    from app.tasks.diagnosis_v2 import run_diagnosis_batch

    loop_ids = [str(x) for x in body.loopIds]
    if not loop_ids:
        raise BizError(code="ERR_PARAM", message="loopIds 不能为空", status_code=400)
    if len(loop_ids) > 50:
        raise BizError(code="ERR_PARAM", message="单次诊断回路数不超过 50", status_code=400)

    start, end = _resolve_window(body.timeWindow)

    loops = (
        (await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))).scalars().all()
    )
    found_ids = {str(x.id) for x in loops}
    missing = [x for x in loop_ids if x not in found_ids]
    if missing:
        raise BizError(
            code="ERR_PARAM",
            message=f"回路不存在: {missing[:5]}",
            status_code=400,
        )

    # PV tag 关联校验（缺 PV 无法诊断）
    pv_mappings = (
        (
            await db.execute(
                select(LoopTagMapping.loop_id).where(
                    LoopTagMapping.loop_id.in_(loop_ids),
                    LoopTagMapping.tag_role == "PV",
                )
            )
        )
        .scalars()
        .all()
    )
    pv_loop_ids = {str(x) for x in pv_mappings}
    no_pv = [x for x in loop_ids if x not in pv_loop_ids]
    if no_pv:
        raise BizError(
            code="ERR_PARAM",
            message=f"回路缺少 PV Tag 关联: {no_pv[:5]}",
            status_code=400,
        )

    task_id = await create_task(
        task_type=TaskType.DIAGNOSIS,
        created_by=user.username,
        created_by_id=str(user.id),
        loop_ids=loop_ids,
        triggered_by="user",
        title=f"回路诊断（{len(loop_ids)} 个回路）",
    )
    # 单算子细选：校验合法算子名（防拼写错误静默空跑）
    selected_ops = [o for o in (body.operators or []) if o]
    if selected_ops:
        valid = {m["name"] for m in list_operators()}
        unknown = [o for o in selected_ops if o not in valid]
        if unknown:
            raise BizError(
                code="ERR_PARAM",
                message=f"未知算子: {unknown[:5]}（可用: {sorted(valid)}）",
                status_code=400,
            )
    celery_result = run_diagnosis_batch.delay(
        loop_ids=loop_ids,
        start=start.isoformat(),
        end=end.isoformat(),
        task_id=task_id,
        operator_group=body.operatorGroup,
        triggered_by=user.username,
        operators=selected_ops or None,
    )
    from app.services.task_tracker import set_celery_task_ids

    await set_celery_task_ids(task_id, [celery_result.id])

    return success({"taskId": task_id, "accepted": len(loop_ids)})


@router.get("/runs", response_model=ApiResponse[dict])
async def list_diagnosis_runs(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    loopId: str | None = Query(None),
    category: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None, alias="status"),
    taskId: str | None = Query(None),
    startTime: datetime | None = Query(None),
    endTime: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
) -> dict:
    """诊断记录列表（分页，按创建时间倒序）。"""
    conditions = []
    if loopId:
        conditions.append(DiagnosisRun.loop_id == loopId)
    if category:
        conditions.append(DiagnosisRun.primary_category == category)
    if severity:
        conditions.append(DiagnosisRun.severity == severity)
    if status:
        conditions.append(DiagnosisRun.status == status)
    if taskId:
        conditions.append(DiagnosisRun.task_id == taskId)
    if startTime:
        conditions.append(DiagnosisRun.created_at >= startTime)
    if endTime:
        conditions.append(DiagnosisRun.created_at <= endTime)

    base = select(DiagnosisRun, LoopLedger.tag_name).outerjoin(
        LoopLedger, DiagnosisRun.loop_id == LoopLedger.id
    )
    if conditions:
        base = base.where(*conditions)

    total = (
        await db.execute(select(func.count()).select_from(DiagnosisRun).where(*conditions))
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(DiagnosisRun.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).all()

    items = [_run_to_summary(row, tag_name) for row, tag_name in rows]
    return success({"items": items, "total": total, "page": page, "pageSize": pageSize})


@router.get("/runs/latest", response_model=ApiResponse[dict])
async def get_latest_runs_per_loop(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    plantNodeId: str | None = Query(None, description="装置节点（递归下钻到单元）"),
) -> dict:
    """每回路最新一条诊断概览（工作台装置节点选中时的概览列表）。

    无诊断记录的回路也列出（runId=null，前端显示"未诊断"）。
    """
    # 防御：plantNodeId 非法 UUID 直接 400（否则 PG UUID 列比较抛 500）
    if plantNodeId is not None:
        try:
            UUID(plantNodeId)
        except ValueError:
            raise BizError(
                code="ERR_PARAM",
                message="plantNodeId 格式非法（应为 UUID）",
                status_code=400,
            ) from None

    sql = text(
        """
        SELECT ll.id AS loop_id, ll.tag_name,
               r.id AS run_id, r.primary_category, r.primary_confidence,
               r.severity, r.status,
               COALESCE(r.finished_at, r.created_at) AS last_diagnosed_at,
               r.time_window_start, r.time_window_end
        FROM loop_ledger ll
        LEFT JOIN LATERAL (
                SELECT * FROM diagnosis_run dr
                WHERE dr.loop_id = ll.id
                ORDER BY dr.created_at DESC LIMIT 1
            ) r ON true
            WHERE ll.is_active = true
            ORDER BY last_diagnosed_at DESC NULLS LAST, ll.tag_name
            """
    )
    params: dict[str, str] = {}
    if plantNodeId:
        sql = text(
            """
            WITH RECURSIVE node_tree AS (
                SELECT id FROM plant_node WHERE id = :root_id
                UNION ALL
                SELECT child.id FROM plant_node child
                JOIN node_tree nt ON child.parent_id = nt.id
            )
            SELECT ll.id AS loop_id, ll.tag_name,
                   r.id AS run_id, r.primary_category, r.primary_confidence,
                   r.severity, r.status,
                   COALESCE(r.finished_at, r.created_at) AS last_diagnosed_at,
                   r.time_window_start, r.time_window_end
            FROM loop_ledger ll
            LEFT JOIN LATERAL (
                SELECT * FROM diagnosis_run dr
                WHERE dr.loop_id = ll.id
                ORDER BY dr.created_at DESC LIMIT 1
            ) r ON true
            WHERE ll.is_active = true AND ll.unit_id IN (SELECT id FROM node_tree)
            ORDER BY last_diagnosed_at DESC NULLS LAST, ll.tag_name
            """
        )
        params["root_id"] = plantNodeId

    rows = (await db.execute(sql, params)).all()
    items = [
        {
            "loopId": str(r.loop_id),
            "loopTagName": r.tag_name,
            "runId": str(r.run_id) if r.run_id else None,
            "primaryCategory": r.primary_category,
            "primaryCategoryLabel": _CATEGORY_LABELS.get(r.primary_category or "", None)
            if r.run_id
            else None,
            "primaryConfidence": float(r.primary_confidence)
            if r.primary_confidence is not None
            else None,
            "severity": r.severity,
            "status": r.status,
            "lastDiagnosedAt": r.last_diagnosed_at.isoformat() if r.last_diagnosed_at else None,
            "timeWindowStart": r.time_window_start.isoformat() if r.time_window_start else None,
            "timeWindowEnd": r.time_window_end.isoformat() if r.time_window_end else None,
        }
        for r in rows
    ]
    return success({"items": items, "total": len(items)})


@router.get("/runs/{run_id}", response_model=ApiResponse[dict])
async def get_diagnosis_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """单次诊断完整详情。"""
    row = (
        await db.execute(
            select(DiagnosisRun, LoopLedger.tag_name)
            .outerjoin(LoopLedger, DiagnosisRun.loop_id == LoopLedger.id)
            .where(DiagnosisRun.id == run_id)
        )
    ).first()
    if row is None:
        raise BizError(code="ERR_NOT_FOUND", message="诊断记录不存在", status_code=404)
    run, tag_name = row
    return success(_run_to_detail(run, tag_name))


@router.get("/operators", response_model=ApiResponse[list[dict]])
async def get_operators(_: SysUser = Depends(get_current_user)) -> dict:
    """算子注册表元数据（前端算子说明 + AI 工具目录）。"""
    return success(list_operators())


@router.get("/export", response_class=PlainTextResponse)
async def export_diagnosis_runs(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    loopId: str | None = Query(None),
    category: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    taskId: str | None = Query(None),
    startTime: datetime | None = Query(None),
    endTime: datetime | None = Query(None),
) -> PlainTextResponse:
    """按筛选条件导出诊断记录 CSV（上限 5000 行）。"""
    conditions = []
    if loopId:
        conditions.append(DiagnosisRun.loop_id == loopId)
    if category:
        conditions.append(DiagnosisRun.primary_category == category)
    if severity:
        conditions.append(DiagnosisRun.severity == severity)
    if status:
        conditions.append(DiagnosisRun.status == status)
    if taskId:
        conditions.append(DiagnosisRun.task_id == taskId)
    if startTime:
        conditions.append(DiagnosisRun.created_at >= startTime)
    if endTime:
        conditions.append(DiagnosisRun.created_at <= endTime)

    rows = (
        await db.execute(
            select(DiagnosisRun, LoopLedger.tag_name)
            .outerjoin(LoopLedger, DiagnosisRun.loop_id == LoopLedger.id)
            .where(*conditions)
            .order_by(DiagnosisRun.created_at.desc())
            .limit(5000)
        )
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["时间", "回路", "主分类", "次分类", "置信度", "严重度", "时间窗", "发起人", "状态"]
    )
    for run, tag_name in rows:
        secondary = "、".join(
            _CATEGORY_LABELS.get(j.get("category", ""), j.get("category", ""))
            for j in (run.secondary_categories or [])
        )
        window = (
            f"{run.time_window_start:%Y-%m-%d %H:%M}~{run.time_window_end:%Y-%m-%d %H:%M}"
            if run.time_window_start and run.time_window_end
            else ""
        )
        writer.writerow(
            [
                run.created_at.strftime("%Y-%m-%d %H:%M:%S") if run.created_at else "",
                tag_name or "",
                _CATEGORY_LABELS.get(run.primary_category or "", run.primary_category or ""),
                secondary,
                f"{float(run.primary_confidence):.0%}"
                if run.primary_confidence is not None
                else "",
                run.severity or "",
                window,
                run.triggered_by,
                run.status,
            ]
        )
    return PlainTextResponse(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=diagnosis_runs.csv"},
    )
