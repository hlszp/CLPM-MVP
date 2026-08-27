"""Report configuration + statistics endpoints (IA 优化 P0/P3).

Routes:
- GET  /reports/configs   — List report configs (ADMIN)
- POST /reports/configs   — Create report config (ADMIN)
- PUT  /reports/configs/{id} — Update report config (ADMIN)
- POST /reports/generate  — Trigger report generation (ADMIN, async)
- GET  /reports/tasks/{task_id} — Report task status (ADMIN)
- GET  /reports/overview  — Management overview (S1/S2/S3 adaptive, P3)
- GET  /reports/diagnosis-statistics — Diagnosis stats (P0)
- GET  /reports/benefit   — Benefit report (P0)
- GET/PUT /reports/stage-lock — Read/set maturity stage lock (ADMIN for PUT, P3)
- POST /reports/export-pdf — Trigger overview PDF export (async, P3)
- GET  /reports/export-tasks/{task_id} — PDF export task status
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.base import CamelModel
from app.schemas.common import ApiResponse, success
from app.schemas.report import (
    ReportBenefitData,
    ReportConfigCreateRequest,
    ReportConfigItem,
    ReportConfigUpdateRequest,
    ReportDiagnosisStatisticsData,
    ReportGenerateData,
    ReportGenerateRequest,
    ReportOverviewData,
)
from app.services.report import (
    create_config,
    get_task_status,
    list_configs,
    trigger_report_generation,
    update_config,
)
from app.services.report_stats import (
    default_report_window,
    determine_maturity_stage,
    get_benefit,
    get_diagnosis_statistics,
    get_overview,
    get_stage_lock,
    set_stage_lock,
)

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# P3: Stage lock schemas
# ---------------------------------------------------------------------------


class ReportStageLockState(CamelModel):
    locked: bool = False
    lockedStage: str | None = None  # S1/S2/S3 or None when unlocked
    detectedStage: str = "S1"
    availability: dict = Field(default_factory=dict)
    counts: dict = Field(default_factory=dict)


class ReportStageLockUpdate(CamelModel):
    """锁定阶段；传 stage=null 解除锁定。"""

    stage: str | None = Field(None, description="'S1' | 'S2' | 'S3' | 解除锁定 None")


class ReportPdfExportRequest(CamelModel):
    stage: str | None = Field(None, description="'S1' | 'S2' | 'S3'，默认自动")
    startDate: str | None = None
    endDate: str | None = None
    plantNodeId: str | None = None


class ReportPdfExportTask(CamelModel):
    taskId: str
    taskType: str = "PDF_EXPORT"
    status: str = "PROCESSING"  # PROCESSING / COMPLETED / FAILED
    fileUrl: str | None = None
    fileName: str | None = None
    fileSize: int | None = None
    error: str | None = None
    estimatedSeconds: int = 20


def _parse_date_range(
    start_date: str | None, end_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """解析 YYYY-MM-DD 为 naive UTC 当日 0 点起 / 次日 0 点止（半开区间）。"""
    start = (
        datetime.combine(datetime.strptime(start_date, "%Y-%m-%d").date(), time.min)
        if start_date
        else None
    )
    end = (
        datetime.combine(datetime.strptime(end_date, "%Y-%m-%d").date(), time.min)
        + timedelta(days=1)
        if end_date
        else None
    )
    return start, end


@router.get("/configs", response_model=ApiResponse[list[ReportConfigItem]])
async def list_configs_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """获取报表配置列表（仅 ADMIN）。"""
    data = await list_configs(db)
    return success(data=data)


@router.post("/configs", status_code=201, response_model=ApiResponse[ReportConfigItem])
async def create_config_endpoint(
    body: ReportConfigCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建报表配置（仅 ADMIN）。"""
    data = await create_config(
        db=db,
        operator=user.username,
        name=body.name,
        report_period=body.reportPeriod,
        recipients=body.recipients,
        content_template=body.contentTemplate,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="报表配置创建成功")


@router.put("/configs/{config_id}", response_model=ApiResponse[ReportConfigItem])
async def update_config_endpoint(
    config_id: uuid.UUID,
    body: ReportConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新报表配置（仅 ADMIN）。"""
    data = await update_config(
        db=db,
        operator=user.username,
        config_id=str(config_id),
        name=body.name,
        report_period=body.reportPeriod,
        recipients=body.recipients,
        content_template=body.contentTemplate,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="报表配置更新成功")


@router.post("/generate", response_model=ApiResponse[ReportGenerateData])
async def generate_report_endpoint(
    body: ReportGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """手动触发报表生成（仅 ADMIN，异步任务，返回 taskId）。"""
    data = await trigger_report_generation(
        db=db,
        operator=user.username,
        config_id=body.configId,
        report_period=body.reportPeriod,
    )
    return success(data=data, message="任务已提交")


@router.get("/tasks/{task_id}", response_model=ApiResponse[dict])
async def get_task_status_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询报表任务状态（仅 ADMIN，用于前端轮询）。"""
    data = await get_task_status(db=db, task_id=str(task_id))
    return success(data=data)


# ---------------------------------------------------------------------------
# 统计报告聚合（IA 优化 P0，2026-08-22）
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse[ReportOverviewData])
async def get_report_overview(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    stage: str = Query("S1", pattern="^(S1|S2|S3)$"),
    startDate: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    endDate: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    plantNodeId: str | None = Query(None),
) -> dict:
    """管理总览聚合（P3：S1/S2/S3 自适应填充，锁定配置优先）。"""
    start, end = _parse_date_range(startDate, endDate)
    if not start or not end:
        start, end = default_report_window()
    data = await get_overview(
        db,
        stage=stage,
        start_date=start,
        end_date=end,
        plant_node_id=plantNodeId,
    )
    return success(data=data)


@router.get(
    "/diagnosis-statistics",
    response_model=ApiResponse[ReportDiagnosisStatisticsData],
)
async def get_report_diagnosis_statistics(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    startDate: str | None = Query(None),
    endDate: str | None = Query(None),
    plantNodeId: str | None = Query(None),
) -> dict:
    """诊断统计（基于 DiagnosisRun 表，不复用旧 DiagnosisResult 导出）。"""
    start, end = _parse_date_range(startDate, endDate)
    data = await get_diagnosis_statistics(
        db, start_date=start, end_date=end, plant_node_id=plantNodeId
    )
    return success(data=data)


@router.get("/benefit", response_model=ApiResponse[ReportBenefitData])
async def get_report_benefit(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    startDate: str | None = Query(None),
    endDate: str | None = Query(None),
    plantNodeId: str | None = Query(None),
) -> dict:
    """收益报告：整定前后 KPI 对比、自控率提升曲线、装置标杆（仅技术指标）。"""
    start, end = _parse_date_range(startDate, endDate)
    data = await get_benefit(db, start_date=start, end_date=end, plant_node_id=plantNodeId)
    return success(data=data)


# ---------------------------------------------------------------------------
# P3: 阶段状态读取 / 锁定
# ---------------------------------------------------------------------------


@router.get("/stage-lock", response_model=ApiResponse[ReportStageLockState])
async def get_stage_lock_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    plantNodeId: str | None = Query(None),
) -> dict:
    """读取当前自动判定阶段 + 锁定状态（全角色可读）。"""
    lock_info = await get_stage_lock(db)
    maturity = await determine_maturity_stage(db, plant_node_id=plantNodeId)
    data = ReportStageLockState(
        locked=lock_info["locked"],
        lockedStage=lock_info["lockedStage"],
        detectedStage=maturity["detectedStage"],
        availability=maturity["availability"],
        counts=maturity["counts"],
    )
    return success(data=data.model_dump())


@router.put("/stage-lock", response_model=ApiResponse[ReportStageLockState])
async def put_stage_lock_endpoint(
    body: ReportStageLockUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
    plantNodeId: str | None = Query(None),
) -> dict:
    """设置/解除阶段锁定（仅 ADMIN）。传 stage=null 解除锁定。"""
    if body.stage is not None and body.stage not in ("S1", "S2", "S3"):
        raise HTTPException(status_code=400, detail="非法阶段，允许：S1/S2/S3/None")
    lock_info = await set_stage_lock(db, stage=body.stage, operator=user.username)
    maturity = await determine_maturity_stage(db, plant_node_id=plantNodeId)
    data = ReportStageLockState(
        locked=lock_info["locked"],
        lockedStage=lock_info["lockedStage"],
        detectedStage=maturity["detectedStage"],
        availability=maturity["availability"],
        counts=maturity["counts"],
    )
    return success(data=data.model_dump(), message="阶段锁定状态已更新")


# ---------------------------------------------------------------------------
# P3: PDF 异步导出（基于 Celery 任务模式，复用 report_generator 风格）
# ---------------------------------------------------------------------------

# 简易内存型任务状态表（10 分钟 TTL，单次部署即可；可替代为 Redis 持久化）
import asyncio  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_pdf_tasks: dict[str, dict] = {}
_pdf_tasks_lock = threading.Lock()
PDF_EXPORT_DIR = _Path(os.environ.get("CLPM_PDF_DIR", "/tmp/clpm-pdf-export"))
PDF_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _ttl_cleanup_pdf_tasks() -> None:
    """清理超过 1 小时的任务记录（粗粒度，后台线程单次循环）。"""
    import time as _time

    while True:
        try:
            now = _time.time()
            with _pdf_tasks_lock:
                expired = [
                    tid for tid, t in _pdf_tasks.items() if now - float(t.get("_ct", now)) > 3600
                ]
                for tid in expired:
                    _pdf_tasks.pop(tid, None)
        except Exception:
            pass
        _time.sleep(60)


_ttl_thread = threading.Thread(target=_ttl_cleanup_pdf_tasks, daemon=True)
_ttl_thread.start()


@router.post("/export-pdf", response_model=ApiResponse[ReportPdfExportTask])
async def trigger_export_pdf(
    body: ReportPdfExportRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """触发管理总览 PDF 异步导出（三阶段自适应，返回 taskId 供前端轮询）。"""

    task_id = str(uuid.uuid4())
    start, end = _parse_date_range(body.startDate, body.endDate)
    if not start or not end:
        start, end = default_report_window()
    # 写入初始状态
    file_name = f"CLPM管理总览_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{task_id[:8]}.pdf"
    with _pdf_tasks_lock:
        _pdf_tasks[task_id] = {
            "_ct": __import__("time").time(),
            "status": "PROCESSING",
            "fileUrl": None,
            "fileName": file_name,
            "fileSize": None,
            "error": None,
        }
    # 后台线程执行导出（避免强依赖 Celery，与 report_generator 任务独立）
    kwargs = {
        "task_id": task_id,
        "requested_stage": body.stage,
        "start_date_iso": start.isoformat() if start else None,
        "end_date_iso": end.isoformat() if end else None,
        "plant_node_id": body.plantNodeId,
        "operator": user.username,
    }
    threading.Thread(target=_sync_run_pdf_export_thread, args=(kwargs,), daemon=True).start()
    resp = ReportPdfExportTask(taskId=task_id, fileName=file_name)
    return success(data=resp.model_dump(), message="PDF 导出任务已提交")


def _sync_run_pdf_export_thread(kwargs: dict) -> None:
    """子线程：跑 asyncio 事件循环执行真正的导出协程。"""
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_async_pdf_export_wrapper(kwargs))
        finally:
            loop.close()
    except Exception as exc:  # pragma: no cover - 兜底记录
        tid = kwargs.get("task_id", "")
        with _pdf_tasks_lock:
            rec = _pdf_tasks.get(tid)
            if rec:
                rec["status"] = "FAILED"
                rec["error"] = f"导出线程异常: {exc}"


async def _async_pdf_export_wrapper(kwargs: dict) -> None:
    """执行 PDF 导出协程（重入独立 DB 会话），结果写入内存任务表。"""
    from app.core.db import AsyncSessionLocal
    from app.tasks.overview_pdf_export import run_overview_pdf_export

    task_id = kwargs["task_id"]
    try:
        async with AsyncSessionLocal() as db:
            file_bytes, file_name = await run_overview_pdf_export(
                db=db,
                requested_stage=kwargs.get("requested_stage"),
                start_date_iso=kwargs.get("start_date_iso"),
                end_date_iso=kwargs.get("end_date_iso"),
                plant_node_id=kwargs.get("plant_node_id"),
                operator=kwargs.get("operator"),
            )
        out_path = PDF_EXPORT_DIR / file_name
        out_path.write_bytes(file_bytes)
        file_size = len(file_bytes)
        with _pdf_tasks_lock:
            rec = _pdf_tasks.get(task_id)
            if rec:
                rec["status"] = "COMPLETED"
                rec["fileUrl"] = f"/api/v1/reports/export-download/{task_id}"
                rec["fileName"] = file_name
                rec["fileSize"] = file_size
                rec["_bytes"] = file_bytes  # 内存缓存以便下载（小文件）
    except Exception as exc:
        with _pdf_tasks_lock:
            rec = _pdf_tasks.get(task_id)
            if rec:
                rec["status"] = "FAILED"
                rec["error"] = str(exc)


@router.get("/export-tasks/{task_id}", response_model=ApiResponse[ReportPdfExportTask])
async def get_export_task_status_endpoint(
    task_id: str,
    _: SysUser = Depends(get_current_user),
) -> dict:
    """PDF 导出任务状态查询（前端轮询）。"""
    with _pdf_tasks_lock:
        t = _pdf_tasks.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    resp = ReportPdfExportTask(
        taskId=task_id,
        status=t.get("status", "PROCESSING"),
        fileUrl=t.get("fileUrl"),
        fileName=t.get("fileName"),
        fileSize=t.get("fileSize"),
        error=t.get("error"),
    )
    return success(data=resp.model_dump())


from fastapi.responses import Response as _Response  # noqa: E402


@router.get("/export-download/{task_id}")
async def download_export_pdf(
    task_id: str,
    _: SysUser = Depends(get_current_user),
) -> _Response:
    """下载已完成的 PDF 报告（COMPLETED 状态，按 task_id）。"""
    with _pdf_tasks_lock:
        t = _pdf_tasks.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if t.get("status") != "COMPLETED":
        raise HTTPException(status_code=400, detail="PDF 尚未生成完成")
    # 优先读内存缓存
    file_bytes = t.get("_bytes")
    fname = t.get("fileName") or f"overview-{task_id}.pdf"
    if file_bytes is None:
        fpath = PDF_EXPORT_DIR / fname
        if not fpath.exists():
            raise HTTPException(status_code=404, detail="PDF 文件不存在")
        file_bytes = fpath.read_bytes()
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return _Response(
        content=bytes(file_bytes),
        media_type="application/pdf",
        headers=headers,
    )


__all__ = ["router"]
