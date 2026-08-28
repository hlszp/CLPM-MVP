"""诊断模块 API（MVP v2，重设计版）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.1
端点清单：
- POST /diagnosis/run        发起诊断（异步任务，仅手动触发）
- GET  /diagnosis/runs       诊断记录列表（筛选/分页）
- GET  /diagnosis/runs/{id}  诊断详情（算子结果+证据链+波形快照）
- GET  /diagnosis/operators  算子注册表元数据（前端+AI 共用）
- GET  /diagnosis/export     记录 CSV 导出
- GET  /diagnosis/runs/loop-archive        回路诊断档案（16 号文 F1）
- GET  /diagnosis/runs/{id}/compare        复诊对比（16 号文 F2）

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
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.loop_action_item import LoopActionItem
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.task import TaskType
from app.services import diagnosis_insights
from app.services.diagnosis_operators import list_operators
from app.services.diagnosis_operators.classification import get_confidence_definitions
from app.services.diagnosis_system_actions import (
    generate_system_actions as _generate_system_actions,
)
from app.services.loop_fitness import get_latest_fitness_per_loop
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

#: 触发类型标签（§12 自动诊断：MANUAL 手动 / SCHEDULED 分级定时 / EVENT 预警事件）
_TRIGGER_TYPE_LABELS = {"MANUAL": "手动诊断", "SCHEDULED": "定期诊断", "EVENT": "事件触发"}


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


class ReviewRunBody(BaseModel):
    """复核请求体（§9.3 复核闭环）。"""

    reviewResults: list[str] = Field(min_length=1, description="复核结论多选（原因分类代码）")
    reviewComment: str | None = Field(None, max_length=500, description="复核意见")


class CreateActionBody(BaseModel):
    """人工新增处置措施请求体（§9.4 处置建议）。"""

    content: str = Field(min_length=1, max_length=500, description="处置措施内容")
    basis: str | None = Field(None, max_length=500, description="依据（可选）")


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
    review_results = row.review_results or []
    return {
        "id": row.id,
        "taskId": row.task_id,
        "loopId": str(row.loop_id),
        "loopTagName": loop_tag,
        "triggeredBy": row.triggered_by,
        "triggerType": row.trigger_type,
        "triggerTypeLabel": _TRIGGER_TYPE_LABELS.get(row.trigger_type, row.trigger_type),
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
        "reviewStatus": row.review_status,
        "reviewResults": review_results,
        "reviewResultLabels": [
            _CATEGORY_LABELS.get(c, c) for c in review_results if isinstance(c, str)
        ],
        "reviewComment": row.review_comment,
        "reviewedBy": row.reviewed_by,
        "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
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
            # 方案 A：诊断指标汇总（窗口 KPI 均值 + 算子特征，0~100 统一口径）
            "metricSummary": row.metric_summary,
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

    # P2 IA优化：诊断发起门禁（fitness L0/L1 直接阻止，L2 允许但提示横幅）
    fitness_map = await get_latest_fitness_per_loop(db, loop_ids)
    blocked: list[dict[str, Any]] = []
    condition_warning: list[dict[str, Any]] = []
    for lid in loop_ids:
        fit = fitness_map.get(lid)
        if fit is None or fit.level is None:
            continue  # 无 fitness 数据 → 暂放过（兼容首次计算前窗口）
        if fit.level in ("L0", "L1"):
            blocked.append(
                {
                    "loopId": lid,
                    "fitnessLevel": fit.level,
                    "reasons": fit.human_readable_tags or ["适用性不足"],
                }
            )
        elif fit.level == "L2":
            condition_warning.append(
                {
                    "loopId": lid,
                    "fitnessLevel": fit.level,
                    "warnings": fit.human_readable_tags or [],
                }
            )
    if blocked:
        raise BizError(
            code="ERR_DIAGNOSIS_FITNESS_INSUFFICIENT",
            message=(
                f"{len(blocked)} 条回路适用性不足以诊断（L0/L1）："
                f"原因包含手动主导/自控率极低/数据严重不足，"
                f"请先处理控制状态后再发起诊断（示例回路 {blocked[0]['loopId']}）"
            ),
            status_code=400,
            data={"blocked": blocked, "conditionWarning": condition_warning},
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

    resp: dict[str, Any] = {"taskId": task_id, "accepted": len(loop_ids)}
    if condition_warning:
        resp["conditionWarning"] = condition_warning
    return success(resp)


@router.get("/runs", response_model=ApiResponse[dict])
async def list_diagnosis_runs(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    loopId: str | None = Query(None),
    category: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None, alias="status"),
    reviewStatus: str | None = Query(None, description="PENDING / REVIEWED"),
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
    if reviewStatus:
        if reviewStatus not in ("PENDING", "REVIEWED"):
            raise BizError(
                code="ERR_PARAM",
                message="reviewStatus 仅支持 PENDING / REVIEWED",
                status_code=400,
            )
        conditions.append(DiagnosisRun.review_status == reviewStatus)
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


@router.post("/runs/{run_id}/review", response_model=ApiResponse[dict])
async def review_diagnosis_run(
    run_id: str,
    body: ReviewRunBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_DIAGNOSIS_TRIGGER_ROLES)),
) -> dict:
    """人工复核诊断结论（§9.3 复核闭环）。

    - 复核结论：多选原因分类（与诊断分类同域 8 类），至少 1 项
    - 复核意见：≤500 字可选
    - 幂等语义：重复复核覆盖上一次结论（reviewed_by/at 更新为最新）
    """
    valid_categories = set(_CATEGORY_LABELS)
    unknown = [c for c in body.reviewResults if c not in valid_categories]
    if unknown:
        raise BizError(
            code="ERR_PARAM",
            message=f"未知复核结论分类: {unknown[:5]}（可用: {sorted(valid_categories)}）",
            status_code=400,
        )

    row = (
        await db.execute(select(DiagnosisRun).where(DiagnosisRun.id == run_id))
    ).scalar_one_or_none()
    if row is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"诊断记录不存在: {run_id}", status_code=404)

    row.review_status = "REVIEWED"
    row.review_results = body.reviewResults
    row.review_comment = body.reviewComment
    row.reviewed_by = user.username
    row.reviewed_at = _utcnow_naive()
    # 复核结论变更后，按复核结论重新带出系统处置建议（保留人工新增）
    await db.execute(
        sa_delete(LoopActionItem).where(
            LoopActionItem.run_id == row.id, LoopActionItem.source == "SYSTEM"
        )
    )
    await db.commit()

    return success(_run_to_summary(row, None))


def _action_to_item(row: LoopActionItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "runId": row.run_id,
        "loopId": row.loop_id,
        "source": row.source,
        "category": row.category,
        "categoryLabel": _CATEGORY_LABELS.get(row.category, row.category) if row.category else None,
        "content": row.content,
        "basis": row.basis,
        "priority": row.priority,
        "status": row.status,
        "suggestedBy": row.suggested_by,
        "suggestedAt": row.suggested_at.isoformat() + "Z" if row.suggested_at else None,
    }


@router.get("/runs/{run_id}/actions", response_model=ApiResponse[dict])
async def list_run_actions(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """处置建议列表（§9.4）。首次拉取为空时自动按诊断/复核结论生成系统建议。"""
    run = (
        await db.execute(select(DiagnosisRun).where(DiagnosisRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"诊断记录不存在: {run_id}", status_code=404)

    rows = (
        (
            await db.execute(
                select(LoopActionItem)
                .where(LoopActionItem.run_id == run_id)
                .order_by(
                    LoopActionItem.priority.asc().nulls_last(), LoopActionItem.suggested_at.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        await _generate_system_actions(db, run)
        await db.commit()
        rows = (
            (
                await db.execute(
                    select(LoopActionItem)
                    .where(LoopActionItem.run_id == run_id)
                    .order_by(
                        LoopActionItem.priority.asc().nulls_last(),
                        LoopActionItem.suggested_at.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
    return success({"items": [_action_to_item(r) for r in rows]})


@router.post("/runs/{run_id}/actions", response_model=ApiResponse[dict])
async def create_run_action(
    run_id: str,
    body: CreateActionBody,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_DIAGNOSIS_TRIGGER_ROLES)),
) -> dict:
    """人工新增处置措施（建议人=当前登录用户，建议时间=服务器当前时间）。"""
    run = (
        await db.execute(select(DiagnosisRun).where(DiagnosisRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"诊断记录不存在: {run_id}", status_code=404)

    row = LoopActionItem(
        run_id=run.id,
        loop_id=run.loop_id,
        source="MANUAL",
        category=None,
        content=body.content,
        basis=body.basis,
        priority=None,
        status="PENDING",
        suggested_by=user.username,
        suggested_at=_utcnow_naive(),
    )
    db.add(row)
    await db.commit()
    return success(_action_to_item(row))


class UpdateActionBody(BaseModel):
    """人工修改处置措施请求体（仅 MANUAL 可改）。"""

    content: str = Field(min_length=1, max_length=500, description="处置措施内容")
    basis: str | None = Field(None, max_length=500, description="依据（可选）")


async def _get_action_or_404(db: AsyncSession, action_id: str) -> LoopActionItem:
    row = (
        await db.execute(select(LoopActionItem).where(LoopActionItem.id == action_id))
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code="ERR_NOT_FOUND", message=f"处置建议不存在: {action_id}", status_code=404
        )
    return row


@router.put("/runs/actions/{action_id}", response_model=ApiResponse[dict])
async def update_run_action(
    action_id: str,
    body: UpdateActionBody,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_DIAGNOSIS_TRIGGER_ROLES)),
) -> dict:
    """修改人工新增的处置措施（仅 MANUAL 可改；SYSTEM 建议不可编辑）。"""
    row = await _get_action_or_404(db, action_id)
    if row.source != "MANUAL":
        raise BizError(
            code="ERR_PARAM",
            message="系统建议不可编辑（可删除或重新复核后自动重建）",
            status_code=400,
        )
    row.content = body.content
    row.basis = body.basis
    await db.commit()
    return success(_action_to_item(row))


@router.delete("/runs/actions/{action_id}", response_model=ApiResponse[dict])
async def delete_run_action(
    action_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_DIAGNOSIS_TRIGGER_ROLES)),
) -> dict:
    """删除处置建议（系统建议与人工新增均可删）。"""
    await _get_action_or_404(db, action_id)
    await db.execute(sa_delete(LoopActionItem).where(LoopActionItem.id == action_id))
    await db.commit()
    return success({"id": action_id, "deleted": True})


@router.get("/runs/latest", response_model=ApiResponse[dict])
async def get_latest_runs_per_loop(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    plantNodeId: str | None = Query(None, description="装置节点（递归下钻到单元）"),
    loopId: str | None = Query(None, description="回路 ID（单回路最新诊断，工作台/整定上下文用）"),
) -> dict:
    """每回路 1 条最新诊断概览（工作台概览列表，2026-08-18 重构）。

    - 一回路一记录：仅取该回路最新 1 次诊断结论（对比需求由"历史"抽屉承担）
    - 补充回路静态属性：名称（description）、回路等级（importance_level）
    - 性能评分：kpi_snapshot_hourly 该回路最新一条有 score 的快照
    - 诊断次序：该回路累计诊断次数（run_count，"第 N 次"语义）
    - 复核：review_status / review_results / reviewed_by / reviewed_at
    - metricSummary：诊断指标汇总（窗口 KPI 均值+算子特征，0~100 口径，
      回路工作台 R5 诊断卡 / 整定工作台摘要条消费）
    无诊断记录的回路也列出（runId=null，前端显示"未诊断"）。
    """
    # 防御：非 UUID 直接 400（否则 PG UUID 列比较抛 500）
    for param_name, param_value in (("plantNodeId", plantNodeId), ("loopId", loopId)):
        if param_value is not None:
            try:
                UUID(param_value)
            except ValueError:
                raise BizError(
                    code="ERR_PARAM",
                    message=f"{param_name} 格式非法（应为 UUID）",
                    status_code=400,
                ) from None

    cte = (
        "WITH RECURSIVE node_tree AS ("
        "SELECT id FROM plant_node WHERE id = :root_id "
        "UNION ALL "
        "SELECT child.id FROM plant_node child "
        "JOIN node_tree nt ON child.parent_id = nt.id) "
        if plantNodeId
        else ""
    )
    conditions = ["ll.is_active = true"]
    if plantNodeId:
        conditions.append("ll.unit_id IN (SELECT id FROM node_tree)")
    if loopId:
        conditions.append("ll.id = :loop_id")

    sql = text(
        f"""
        {cte}
        SELECT ll.id AS loop_id, ll.tag_name, ll.description AS loop_description,
               ll.importance_level,
               r.id AS run_id, r.primary_category, r.primary_confidence,
               r.severity, r.status, r.trigger_type,
               r.review_status, r.review_results, r.reviewed_by, r.reviewed_at,
               COALESCE(r.finished_at, r.created_at) AS last_diagnosed_at,
               r.time_window_start, r.time_window_end,
               r.metric_summary,
               k.score AS latest_score,
               rc.run_count
        FROM loop_ledger ll
        LEFT JOIN LATERAL (
                SELECT * FROM diagnosis_run dr
                WHERE dr.loop_id = ll.id
                ORDER BY dr.created_at DESC LIMIT 1
            ) r ON true
        LEFT JOIN LATERAL (
                SELECT ks.score FROM kpi_snapshot_hourly ks
                WHERE ks.loop_id = ll.id AND ks.score IS NOT NULL
                ORDER BY ks.ts_start DESC LIMIT 1
            ) k ON true
        LEFT JOIN LATERAL (
                SELECT COUNT(*) AS run_count FROM diagnosis_run dr
                WHERE dr.loop_id = ll.id
            ) rc ON true
            WHERE {" AND ".join(conditions)}
            """
    )
    params: dict[str, str] = {}
    if plantNodeId:
        params["root_id"] = plantNodeId
    if loopId:
        params["loop_id"] = loopId

    rows = list((await db.execute(sql, params)).all())

    # 排序：有诊断的回路按"回路最新诊断时间"降序在前，未诊断回路垫底（按位号）
    def _sort_key(r: Any) -> tuple:
        latest = r.last_diagnosed_at
        return (
            0 if latest else 1,
            -latest.timestamp() if latest else 0,
            r.tag_name or "",
        )

    rows.sort(key=_sort_key)

    items = []
    for r in rows:
        review_results = (
            [c for c in (r.review_results or []) if isinstance(c, str)] if r.run_id else []
        )
        items.append(
            {
                "loopId": str(r.loop_id),
                "loopTagName": r.tag_name,
                "loopDescription": r.loop_description,
                "importanceLevel": int(r.importance_level) if r.importance_level else None,
                "runId": str(r.run_id) if r.run_id else None,
                # 诊断次序：该回路累计第几次诊断（未诊断为 None）
                "runCount": int(r.run_count) if r.run_id else 0,
                "latestScore": float(r.latest_score) if r.latest_score is not None else None,
                "triggerType": r.trigger_type if r.run_id else None,
                "triggerTypeLabel": (
                    _TRIGGER_TYPE_LABELS.get(r.trigger_type or "", "") if r.run_id else None
                ),
                "primaryCategory": r.primary_category,
                "primaryCategoryLabel": _CATEGORY_LABELS.get(r.primary_category or "", None)
                if r.run_id
                else None,
                "primaryConfidence": float(r.primary_confidence)
                if r.primary_confidence is not None
                else None,
                "severity": r.severity,
                "status": r.status,
                "reviewStatus": r.review_status if r.run_id else None,
                "reviewResults": review_results,
                "reviewResultLabels": [_CATEGORY_LABELS.get(c, c) for c in review_results],
                "reviewedBy": r.reviewed_by if r.run_id else None,
                "reviewedAt": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "lastDiagnosedAt": r.last_diagnosed_at.isoformat() if r.last_diagnosed_at else None,
                "timeWindowStart": r.time_window_start.isoformat() if r.time_window_start else None,
                "timeWindowEnd": r.time_window_end.isoformat() if r.time_window_end else None,
                # 诊断指标汇总（窗口 KPI 均值+算子特征，0~100 口径；未诊断为 None）
                "metricSummary": r.metric_summary if r.run_id else None,
            }
        )
    return success({"items": items, "total": len(items)})


@router.get("/runs/loop-archive", response_model=ApiResponse[dict])
async def get_loop_archive(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    loopId: str = Query(..., description="回路 ID"),
    window: str = Query("90d", description="时间窗 30d/90d/all（all 截断 90d）"),
) -> dict:
    """回路诊断档案（16 号文 F1）。

    run 时间轴（窗口内升序）+ KPI 趋势（LTTB ≤2000 点）+
    处置/整定事件（模块禁用时跳过查询，响应标记 handlingEnabled/tuningEnabled）。
    """
    return success(await diagnosis_insights.loop_archive(db, loopId, window))


@router.get("/runs/{run_id}/compare", response_model=ApiResponse[dict])
async def compare_diagnosis_runs(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    mode: str = Query("adjacent", description="adjacent 相邻对比 / verify 验证对比"),
) -> dict:
    """复诊对比（16 号文 F2，D3 双模式）。

    - adjacent：与同回路该 run 之前最近一条 SUCCESS/PARTIAL run 对比（纯诊断域恒可用）
    - verify：handling_order.verify_run_id 关联的处置前后 run 对（处置启用才查）
    """
    return success(await diagnosis_insights.compare_runs(db, run_id, mode))


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
