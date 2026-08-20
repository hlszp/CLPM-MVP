"""Loop ledger endpoints (IDS v3.2 §2.2.7~2.2.15).

路由顺序：固定路径（/monitor、/export、/import、/batch-config、/batch-grouping、
/complex-groups）必须在 {loop_id} 之前声明。

- GET    /api/v1/loops              — 分页查询回路列表
- POST   /api/v1/loops              — 创建回路
- POST   /api/v1/loops/batch-config — 批量配置回路（仅 ADMIN）
- POST   /api/v1/loops/batch-grouping — 批量建立复杂回路分组（ADMIN/IC_ENGINEER）
- GET    /api/v1/loops/complex-groups — 查询所有复杂回路分组
- GET    /api/v1/loops/monitor      — 回路监控列表
- GET    /api/v1/loops/export       — 导出回路 Excel
- POST   /api/v1/loops/import       — 批量导入回路 Excel
- GET    /api/v1/loops/{id}         — 回路详情
- PUT    /api/v1/loops/{id}         — 更新回路
- DELETE /api/v1/loops/{id}         — 删除回路
- GET    /api/v1/loops/{id}/tags    — 获取回路 Tag 关联状态
- PUT    /api/v1/loops/{id}/tags    — 批量更新 Tag 关联
- GET    /api/v1/loops/{id}/monitor — 回路运行详情
- GET    /api/v1/loops/{id}/confidence-latest — 回路最新一次可信度评估记录
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perms, require_roles
from app.api.upload_guard import read_excel_upload
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.metric import LoopConfidenceLatest
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop import (
    ComplexGroupItem,
    LoopBatchGroupingRequest,
    LoopBatchGroupingResult,
    LoopConfidenceLatestItem,
    LoopCreate,
    LoopDeleteResult,
    LoopImportResult,
    LoopListData,
    LoopTagMappingResponse,
    LoopTagMappingUpdate,
    LoopTagMappingUpdateResponse,
    LoopUpdate,
    LoopUpdateResult,
)
from app.schemas.loop_batch import LoopBatchConfigRequest, LoopBatchConfigResult
from app.services.loop import (
    batch_group_loops,
    create_loop,
    delete_loop,
    detect_tag_reassignment,
    export_loops,
    get_loop_detail,
    get_loop_role_tag_names,
    get_loop_type_stats,
    import_loops,
    list_complex_groups,
    list_loops,
    notify_tag_reassignment,
    update_loop,
)
from app.services.loop_batch import batch_delete_loops, batch_update_loops
from app.services.monitor import get_loop_monitor_detail, list_loop_monitor
from app.services.tag_mapping import get_loop_tags, update_loop_tags

router = APIRouter(prefix="/loops", tags=["loop"])


# ---------------------------------------------------------------------------
# Loop CRUD (固定路径优先)
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[LoopListData])
async def list_loops_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    controlMode: str | None = Query(None, description="按控制方式筛选：Manual/Auto/Cascade"),
    isActive: bool | None = Query(None, description="按启用状态筛选"),
    status: str | None = Query(None, description="按回路状态筛选：READY/PARTIAL/INACTIVE"),
    keyword: str | None = Query(None, description="按回路位号/描述模糊查询"),
    loopType: str | None = Query(None, description="按回路类型筛选"),
    controlType: str | None = Query(None, description="按控制类型筛选：STABLE/SLOW/FAST/LOGIC"),
    level: int | None = Query(
        None,
        ge=1,
        le=3,
        description="（已废弃，请使用 importanceLevel）按回路重要等级筛选：1/2/3",
        deprecated=True,
    ),
    importanceLevel: int | None = Query(None, ge=1, le=3, description="按回路重要等级筛选：1/2/3"),
    monitorStatus: bool | None = Query(
        None, description="按监控状态筛选：true=监控中/false=已停用"
    ),
    includeInEvaluation: bool | None = Query(
        None, description="按参评状态筛选：true=参评/false=不参评"
    ),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """分页查询回路列表。"""
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
    # v5.3 对齐 DDS v4.1：level → importanceLevel（保留 level 向后兼容）
    effective_level = importanceLevel if importanceLevel is not None else level
    try:
        data = await list_loops(
            db=db,
            plant_node_id=plantNodeId,
            control_mode=controlMode,
            is_active=isActive,
            status=status,
            keyword=keyword,
            loop_type=loopType,
            control_type=controlType,
            importance_level=effective_level,
            monitor_status=monitorStatus,
            include_in_evaluation=includeInEvaluation,
            page=page,
            page_size=pageSize,
        )
    except ValueError as e:
        # P3 #42: isActive 与 monitorStatus 语义冲突
        return {"code": "400", "message": str(e), "data": None}
    return success(data=data)


@router.post("", status_code=201, response_model=ApiResponse[dict])
async def create_loop_endpoint(
    body: LoopCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """创建回路。"""
    score_weights = None
    if body.scoreWeights is not None:
        score_weights = body.scoreWeights.model_dump()
    data = await create_loop(
        db=db,
        tag_name=body.tagName,
        description=body.description,
        unit_id=body.unitId,
        score_weights=score_weights,
        is_active=body.isActive,
        remark=body.remark,
        operator=user.username,
        loop_type=body.loopType,
        control_type=body.controlType,
        importance_level=body.importance_level,
        include_in_evaluation=body.include_in_evaluation,
        modeattr_tag_id=body.modeattrTagId,
        data_retention_days=body.dataRetentionDays,
        op_output_lower_limit=body.opOutputLowerLimit,
        op_output_upper_limit=body.opOutputUpperLimit,
        dcs_model_id=body.dcsModelId,
        ideal_settling_time=body.idealSettlingTime,
        complex_loop_group_id=body.complexLoopGroupId,
        complex_role=body.complexRole,
    )
    return success(data=data, message="创建成功")


# ---------------------------------------------------------------------------
# Loop Batch Config (固定路径，必须在 {loop_id} 之前)
# ---------------------------------------------------------------------------


@router.post(
    "/batch-config",
    response_model=ApiResponse[LoopBatchConfigResult],
)
async def batch_config_loops_endpoint(
    body: LoopBatchConfigRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量配置回路（仅 ADMIN）。

    两种模式（互斥）：
    - 更新模式：提供 updates 字段（isMonitored/isStatEnabled/importanceLevel/includeInEvaluation）
    - 删除模式：action="delete"（硬删除：解绑 Tag 映射 + 级联清理关联数据，不可恢复）

    所有操作均记录审计日志。
    """
    if body.action == "delete":
        # P1 #9: batch_delete_loops 返回 {"deleted": int, "skipped": list}
        del_result = await batch_delete_loops(
            db=db,
            loop_ids=body.loop_ids,
            operator=user.username,
        )
        affected = del_result["deleted"]
        skipped = del_result["skipped"]
        action = "delete"
    else:
        # 更新模式
        updates_dict: dict = {}
        if body.updates is not None:
            if body.updates.is_monitored is not None:
                updates_dict["is_monitored"] = body.updates.is_monitored
            if body.updates.is_stat_enabled is not None:
                updates_dict["is_stat_enabled"] = body.updates.is_stat_enabled
            if body.updates.importance_level is not None:
                updates_dict["importance_level"] = body.updates.importance_level
            if body.updates.include_in_evaluation is not None:
                updates_dict["include_in_evaluation"] = body.updates.include_in_evaluation
        affected = await batch_update_loops(
            db=db,
            loop_ids=body.loop_ids,
            updates=updates_dict,
            operator=user.username,
        )
        action = "update"
        skipped = None

    result = LoopBatchConfigResult(
        affected=affected,
        action=action,
        loop_ids=body.loop_ids,
        skipped=skipped,
    )
    return success(data=result.model_dump(by_alias=True), message="批量操作成功")


# ---------------------------------------------------------------------------
# Loop Complex Grouping (固定路径，必须在 {loop_id} 之前)
# ---------------------------------------------------------------------------


@router.post("/batch-grouping", response_model=ApiResponse[LoopBatchGroupingResult])
async def batch_group_loops_endpoint(
    body: LoopBatchGroupingRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """批量建立复杂回路分组（仅 ADMIN / IC_ENGINEER）。

    将 2-20 个回路归为一个复杂控制回路（串级/超驰等），系统自动生成 group ID，
    指定一个 MAIN 回路（聚合代表），其余自动为 SUB。
    """
    data = await batch_group_loops(
        db=db,
        loop_ids=body.loopIds,
        main_loop_id=body.mainLoopId,
        operator=user.username,
    )
    return success(data=data, message="批量分组成功")


@router.get("/complex-groups", response_model=ApiResponse[list[ComplexGroupItem]])
async def list_complex_groups_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """查询所有复杂回路分组（含主回路位号与组成员数）。"""
    data = await list_complex_groups(db=db)
    return success(data=data)


# ---------------------------------------------------------------------------
# Loop Monitor (固定路径，必须在 {loop_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/monitor", response_model=ApiResponse[dict])
async def list_loop_monitor_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    view: str = Query("list", description="视图模式：list/card"),
    keyword: str | None = Query(None, description="按回路位号/描述模糊查询"),
    loopType: str | None = Query(None, description="按回路类型筛选"),
    loopId: str | None = Query(
        None,
        description="精确查询指定回路（供深链接解析；不回退其他回路）",
    ),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """回路监控列表（含实时 PV/SP/OP/MODE 值、质量码、评分）。"""
    data = await list_loop_monitor(
        db=db,
        plant_node_id=plantNodeId,
        view=view,
        keyword=keyword,
        loop_type=loopType,
        loop_id=loopId,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.get("/monitor/stats", response_model=ApiResponse[dict])
async def get_loop_type_stats_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选（含子节点）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """按回路类型统计数量（支持递归子节点）。"""
    stats = await get_loop_type_stats(db=db, plant_node_id=plantNodeId)
    return success(data=stats)


# ---------------------------------------------------------------------------
# Loop Export / Import (固定路径，必须在 {loop_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_loops_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    status: str | None = Query(None, description="按回路状态筛选：READY/PARTIAL/INACTIVE"),
    keyword: str | None = Query(None, description="按回路位号/描述模糊查询"),
    controlType: str | None = Query(None, description="按控制类型筛选：STABLE/SLOW/FAST/LOGIC"),
    importanceLevel: int | None = Query(None, ge=1, le=3, description="按回路等级筛选：1/2/3"),
    includeInEvaluation: bool | None = Query(None, description="按参评状态筛选"),
    loopType: str | None = Query(None, description="按回路类型筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> StreamingResponse:
    """导出回路台账为 Excel 文件（.xlsx）。"""
    content = await export_loops(
        db=db,
        plant_node_id=plantNodeId,
        status=status,
        keyword=keyword,
        control_type=controlType,
        importance_level=importanceLevel,
        include_in_evaluation=includeInEvaluation,
        loop_type=loopType,
    )
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=loops_export.xlsx",
        },
    )


@router.post("/import", response_model=ApiResponse[LoopImportResult])
async def import_loops_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """批量导入回路台账（Excel .xlsx）。

    逐行处理：回路编号已存在则更新，否则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await read_excel_upload(file)
    data = await import_loops(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


# ---------------------------------------------------------------------------
# Loop CRUD by ID
# ---------------------------------------------------------------------------


@router.get("/{loop_id}", response_model=ApiResponse[dict])
async def get_loop_detail_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """获取回路详情。"""
    data = await get_loop_detail(db=db, loop_id=loop_id)
    return success(data=data)


@router.put("/{loop_id}", response_model=ApiResponse[LoopUpdateResult])
async def update_loop_endpoint(
    loop_id: str,
    body: LoopUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """更新回路（描述/所属单元/评分权重/启用状态/备注/回路类型/控制类型/重要等级/参评/APC位号/保留周期/OP输出限位/理想稳态时间/复杂回路分组）。"""
    score_weights = None
    if body.scoreWeights is not None:
        score_weights = body.scoreWeights.model_dump()
    # v6.1：使用 model_fields_set 区分"未传递"和"传递了 NULL"
    # 确保用户可以通过 PUT null 清空 OP 输出限位（恢复默认值）
    _fs = body.model_fields_set
    op_output_lower_limit = body.opOutputLowerLimit if "opOutputLowerLimit" in _fs else None
    op_output_upper_limit = body.opOutputUpperLimit if "opOutputUpperLimit" in _fs else None
    ideal_settling_time = body.idealSettlingTime if "idealSettlingTime" in _fs else None
    complex_loop_group_id = body.complexLoopGroupId if "complexLoopGroupId" in _fs else None
    complex_role = body.complexRole if "complexRole" in _fs else None
    data = await update_loop(
        db=db,
        loop_id=loop_id,
        operator=user.username,
        description=body.description,
        unit_id=body.unitId,
        score_weights=score_weights,
        is_active=body.isActive,
        remark=body.remark,
        loop_type=body.loopType,
        control_type=body.controlType,
        importance_level=body.importance_level,
        include_in_evaluation=body.include_in_evaluation,
        modeattr_tag_id=body.modeattrTagId,
        data_retention_days=body.dataRetentionDays,
        op_output_lower_limit=op_output_lower_limit,
        op_output_upper_limit=op_output_upper_limit,
        dcs_model_id=body.dcsModelId,
        ideal_settling_time=ideal_settling_time,
        _op_lower_set="opOutputLowerLimit" in body.model_fields_set,
        _op_upper_set="opOutputUpperLimit" in body.model_fields_set,
        _dcs_model_id_set="dcsModelId" in body.model_fields_set,
        _ideal_settling_time_set="idealSettlingTime" in body.model_fields_set,
        complex_loop_group_id=complex_loop_group_id,
        complex_role=complex_role,
        _complex_group_set="complexLoopGroupId" in body.model_fields_set,
        _complex_role_set="complexRole" in body.model_fields_set,
    )
    return success(data=data, message="更新成功")


@router.delete("/{loop_id}", response_model=ApiResponse[LoopDeleteResult])
async def delete_loop_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除回路（仅 ADMIN，硬删除）。

    级联解绑：删除 LoopTagMapping 关联记录后硬删回路本体，
    ON DELETE CASCADE 自动清理 kpi_snapshot/action_tracker/diagnosis_result 等关联数据。
    批量删除保持软删除（可恢复），单删为硬删除（不可恢复，与前端弹窗承诺一致）。
    """
    data = await delete_loop(db=db, loop_id=loop_id, operator=user.username)
    return success(data=data, message="删除成功")


# ---------------------------------------------------------------------------
# Loop Tag Mapping (S2-LOOP-005)
# ---------------------------------------------------------------------------


@router.get("/{loop_id}/tags", response_model=ApiResponse[LoopTagMappingResponse])
async def get_loop_tags_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """获取回路 7 个 Tag 槽位关联状态。"""
    data = await get_loop_tags(db=db, loop_id=loop_id)
    return success(data=data)


@router.put("/{loop_id}/tags", response_model=ApiResponse[LoopTagMappingUpdateResponse])
async def update_loop_tags_endpoint(
    loop_id: str,
    body: LoopTagMappingUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """批量更新回路 Tag 关联。

    PV/SP/OP/MODE 必填，缺失时 status→PARTIAL（API 调用成功，不报错）。
    PID_P/PID_I/PID_D 可选。
    全部必填为 null → ERR_LOOP_TAG_REQUIRED。
    Tag 不存在于 tag_registry → ERR_TAG_NOT_FOUND。
    tag 名发生变更时响应带 warnings（历史数据在新 subtable 下重新开始）。
    """
    # P2：变更前记录各角色 tag 名，用于检测 tag 重关联（历史数据孤儿化风险）。
    # 回路不存在时由 update_loop_tags 抛出 ERR_LOOP_NOT_FOUND，此处查询返回空 dict
    before_role_tags = await get_loop_role_tag_names(db, loop_id)
    data = await update_loop_tags(
        db=db,
        loop_id=loop_id,
        operator=user.username,
        pv=body.pv,
        sp=body.sp,
        op=body.op,
        mode=body.mode,
        pid_p=body.pid_p,
        pid_i=body.pid_i,
        pid_d=body.pid_d,
    )
    after_role_tags = {t["role"]: t["tagName"] for t in data["tags"] if t.get("tagName")}
    changed_roles = detect_tag_reassignment(before_role_tags, after_role_tags)
    if changed_roles:
        loop_tag_name = await db.scalar(select(LoopLedger.tag_name).where(LoopLedger.id == loop_id))
        warning = await notify_tag_reassignment(
            loop_id, str(loop_tag_name or loop_id), changed_roles
        )
        data["warnings"] = [warning]
    return success(data=data, message="Tag 关联更新成功")


# ---------------------------------------------------------------------------
# Loop Monitor Detail (S2-LOOP-006)
# ---------------------------------------------------------------------------


@router.get("/{loop_id}/monitor", response_model=ApiResponse[dict])
async def get_loop_monitor_detail_endpoint(
    loop_id: str,
    trendWindow: str = Query(
        "last_24_hours",
        description="趋势数据时间窗：last_1_hour/last_2_hours/last_4_hours/last_8_hours/last_24_hours/last_72_hours",
    ),
    db: AsyncSession = Depends(get_db),
    # WS-D 性能#7 R1：SPONSOR 只读工作台，禁止下钻回路监控详情（趋势/性能）
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """回路运行详情（7 Tag 当前值、PID 参数、波形数据）。"""
    data = await get_loop_monitor_detail(db=db, loop_id=loop_id, trend_window=trendWindow)
    return success(data=data)


# ---------------------------------------------------------------------------
# Loop Confidence Latest（回路最新一次可信度评估记录）
# ---------------------------------------------------------------------------


@router.get("/{loop_id}/confidence-latest", response_model=ApiResponse[dict])
async def get_loop_confidence_latest_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("loop:view")),
) -> dict:
    """获取回路最新一次可信度评估记录（含 12 子指标值与各自可信度）。

    无评估记录时返回 ``data=null``（HTTP 200，不抛 404），
    前端据此展示"暂无评估记录"。
    """
    result = await db.execute(
        select(LoopConfidenceLatest).where(LoopConfidenceLatest.loop_id == loop_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        return success(data=None)
    # 显式构造（CamelModel 的 from_attributes 按 camelCase 别名取属性，
    # 无法直接 model_validate snake_case 属性的 ORM 对象）
    item = LoopConfidenceLatestItem(
        loop_id=record.loop_id,
        eval_time=record.eval_time,
        data_ts_start=record.data_ts_start,
        data_ts_end=record.data_ts_end,
        status=record.status,
        score=float(record.score) if record.score is not None else None,
        confidence_level=record.confidence_level,
        valid_rate=record.valid_rate,
        metrics=record.metrics or {},
        algorithm_version=record.algorithm_version,
        updated_at=record.updated_at,
    )
    return success(data=item.model_dump(by_alias=True, mode="json"))


__all__ = ["router"]
