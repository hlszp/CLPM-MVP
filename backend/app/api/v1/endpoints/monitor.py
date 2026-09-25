"""监控模块 API 端点——关注队列 + 工作台摘要（整改方案 §8.1 / §8.2）。

路由清单：
- GET /api/v1/monitor/attention            统一关注队列（分页+筛选）
- GET /api/v1/monitor/loops/{loopId}/summary  工作台首屏摘要（BFF）

关注队列聚合五类来源（ALERT/DEGRADATION/DATA_QUALITY/FITNESS_ABNORMAL/HANDLING），
不新增数据库表；动作由服务端按角色生成。
（MVP 精简：已移除 TRACKER/VERIFICATION 来源；A2 新增 HANDLING 处置工单来源）

工作台摘要一次返回首屏所需的全部摘要（运行态/数据健康度/评分趋势/活跃关注/
评估/诊断/整定摘要/Tracker 时间线/生命周期/nextAction），单个来源失败时返回
``partial=true``，不让整页 500。
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.services.monitor_attention import list_attention
from app.services.workbench_summary import get_workbench_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitor", tags=["monitor"])

#: 合法来源
_VALID_SOURCES = frozenset(
    ("ALERT", "DEGRADATION", "DATA_QUALITY", "FITNESS_ABNORMAL", "HANDLING")
)  # MVP 精简：移除 TRACKER/VERIFICATION；P2 新增 FITNESS_ABNORMAL；A2 新增 HANDLING（处置工单）
#: 合法优先级
_VALID_PRIORITIES = frozenset(("URGENT", "HIGH", "MEDIUM", "LOW"))
#: 合法状态
_VALID_STATUSES = frozenset(("OPEN", "ACKNOWLEDGED", "SUPPRESSED", "IN_PROGRESS", "VERIFYING"))
#: 合法组级排序字段（与 monitor_attention.ATTENTION_SORT_FIELDS 保持同源含义）
_VALID_SORT_FIELDS = frozenset(("priority", "updatedAt", "itemCount", "overdue"))
_VALID_SORT_ORDERS = frozenset(("asc", "desc"))
#: 单次导出上限（组数）。超出即截断并在文件首行注明，避免响应体失控。
_ATTENTION_EXPORT_MAX_GROUPS = 2000


def _norm_filters(
    source: list[str] | None,
    priority: list[str] | None,
    status: list[str] | None,
) -> tuple[list[str] | None, list[str] | None, list[str] | None]:
    """过滤非法枚举值（静默忽略，不报 400；与列表端点同口径）。"""
    sources = [s for s in (source or []) if s in _VALID_SOURCES] or None
    priorities = [p for p in (priority or []) if p in _VALID_PRIORITIES] or None
    statuses = [s for s in (status or []) if s in _VALID_STATUSES] or None
    return sources, priorities, statuses


@router.get("/attention", response_model=ApiResponse[dict])
async def list_attention_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    source: list[str] | None = Query(
        None,
        description=(
            "来源筛选（可重复）：ALERT/DEGRADATION/DATA_QUALITY/FITNESS_ABNORMAL/HANDLING"
        ),
    ),
    priority: list[str] | None = Query(
        None, description="优先级筛选（可重复）：URGENT/HIGH/MEDIUM/LOW"
    ),
    status: list[str] | None = Query(
        None, description="状态筛选（可重复）：OPEN/ACKNOWLEDGED/SUPPRESSED/IN_PROGRESS/VERIFYING"
    ),
    loopId: str | None = Query(None, description="按回路精确筛选"),
    keyword: str | None = Query(None, description="按位号/标题模糊查询"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    sortBy: str = Query(
        "priority",
        description="组级排序字段：priority（默认，紧急在前）/updatedAt/itemCount/overdue",
    ),
    sortOrder: str = Query("asc", description="排序方向：asc/desc"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """统一关注队列——聚合预警/评分恶化/数据质量/适用性异常/处置工单。

    （MVP：Tracker/验证超期已移除；A2：新增 HANDLING 处置工单来源）

    2026-09-24 新增服务端组级排序：本端点按回路组分页，客户端排序只能作用于
    当前页，必须由服务端在分页前完成（sortBy/sortOrder）。
    """
    sources, priorities, statuses = _norm_filters(source, priority, status)
    if sortBy not in _VALID_SORT_FIELDS:
        sortBy = "priority"
    if sortOrder not in _VALID_SORT_ORDERS:
        sortOrder = "asc"

    data = await list_attention(
        db=db,
        plant_node_id=plantNodeId,
        sources=sources,
        priorities=priorities,
        statuses=statuses,
        loop_id=loopId,
        keyword=keyword,
        page=page,
        page_size=pageSize,
        role=user.role,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return success(data=data)


@router.get("/attention/export", response_class=PlainTextResponse)
async def export_attention_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    source: list[str] | None = Query(None, description="来源筛选（可重复）"),
    priority: list[str] | None = Query(None, description="优先级筛选（可重复）"),
    status: list[str] | None = Query(None, description="状态筛选（可重复）"),
    loopId: str | None = Query(None, description="按回路精确筛选"),
    keyword: str | None = Query(None, description="按位号/标题模糊查询"),
    sortBy: str = Query("priority", description="组级排序字段"),
    sortOrder: str = Query("asc", description="排序方向：asc/desc"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> PlainTextResponse:
    """关注队列全量导出（CSV，UTF-8 BOM，Excel 可直接打开）。

    设计（2026-09-24）：
    - **全量**：与列表端点共用同一过滤与排序口径，但不分页（上限
      ``_ATTENTION_EXPORT_MAX_GROUPS`` 组，超出时在文件首行的"口径"注释中注明），
      解决"页面导出只导当前页却提示已导出 N 条"的取证陷阱；
    - **可自证口径**：文件首行以 ``#`` 注释写明生成时间、筛选条件、排序与条数，
      导出的材料在会议上可自证范围（原实现无任何口径信息）；
    - 单位是一行一个"问题回路组"（与页面表格一致），并附该组关注项明细标题。
    """
    sources, priorities, statuses = _norm_filters(source, priority, status)
    if sortBy not in _VALID_SORT_FIELDS:
        sortBy = "priority"
    if sortOrder not in _VALID_SORT_ORDERS:
        sortOrder = "asc"

    data = await list_attention(
        db=db,
        plant_node_id=plantNodeId,
        sources=sources,
        priorities=priorities,
        statuses=statuses,
        loop_id=loopId,
        keyword=keyword,
        page=1,
        page_size=_ATTENTION_EXPORT_MAX_GROUPS,
        role=user.role,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    groups = data.get("items", [])
    total_groups = int(data.get("totalGroups") or 0)
    total_items = int(data.get("totalItems") or 0)

    filters = "；".join(
        [
            f"装置={plantNodeId or '全部'}",
            f"来源={','.join(sources) if sources else '全部'}",
            f"优先级={','.join(priorities) if priorities else '全部'}",
            f"状态={','.join(statuses) if statuses else '全部'}",
            f"回路={loopId or '全部'}",
            f"关键词={keyword or '无'}",
            f"排序={sortBy} {sortOrder}",
        ]
    )
    trunc_note = ""
    if total_groups > len(groups):
        trunc_note = f"；已截断到前 {len(groups)} 组（共 {total_groups} 组，请收窄筛选后导出）"
    scope_line = (
        f"# 关注队列导出 生成时间={datetime.now(UTC).isoformat()} "
        f"生成人={user.username} 口径：{filters}；"
        f"共 {len(groups)} 组 / {total_items} 个关注项{trunc_note}"
    )

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM：Excel 中文不乱码
    buffer.write(scope_line + "\n")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "回路号",
            "装置·单元",
            "优先级",
            "状态",
            "来源",
            "摘要",
            "关注项数",
            "是否超期",
            "更新时间",
            "明细",
        ]
    )
    for g in groups:
        children = g.get("children") or []
        detail = " / ".join(str(c.get("title") or "") for c in children[:5] if c.get("title"))
        if len(children) > 5:
            detail = f"{detail} …等 {len(children)} 项"
        writer.writerow(
            [
                g.get("tagName") or "",
                g.get("unitName") or "",
                g.get("priorityLabel") or g.get("priority") or "",
                g.get("status") or "",
                "、".join(g.get("sourceLabels") or []),
                str(g.get("summary") or "").replace("\n", " "),
                g.get("itemCount") or 0,
                "是" if g.get("isOverdue") else "否",
                g.get("updatedAt") or "",
                detail,
            ]
        )

    filename = f"attention-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.csv"
    return PlainTextResponse(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/loops/{loop_id}/summary", response_model=ApiResponse[dict])
async def get_workbench_summary_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    # 权限与当前工作台一致：ADMIN/IC/PE/EXPERT 可读，Sponsor 不开放（前端不发起）。
    # PE 返回同结构但所有写动作 disabled（由服务端 nextAction 按角色生成）。
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """工作台首屏摘要（BFF）——一次返回首屏所需的全部摘要。

    单个来源失败时返回 ``partial=true`` 且该来源在 ``unavailableSections`` 中，
    其他来源正常返回，不让整页 500。
    """
    data = await get_workbench_summary(db=db, loop_id=loop_id, role=user.role)
    return success(data=data)
