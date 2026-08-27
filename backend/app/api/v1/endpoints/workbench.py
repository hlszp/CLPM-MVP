"""工作台 v2.0 BFF 聚合端点（M1 skeleton）。

12 个端点对应方案 §3.1 A-01~A-13（跳过 A-06）：
- A-01 GET /overview      — 三窗口 KPI + 装置/单元排名 + Pareto/根因
- A-02 GET /assessment    — 6 项 KPI 卡片 + 单元热力 + 回路排名
- A-03 GET /diagnosis     — 异常回路 + 诊断结论时间线 + 适用性门禁
- A-04 GET /tuning        — 整定批次 + 待整定队列
- A-05 GET /handling      — 处置看板 + 漏斗 + 人员负载
- A-07 GET /flags         — 趋势 flags 气泡
- A-08 GET /staff-load    — 人员负载（MV-01 包装）
- A-09 GET /lane-more     — 泳道展开更多
- A-10 GET /plugins       — 模块 4 态列表（已实现：读 module_plugin）
- A-11 GET /aggregate      — 首屏批量预取（8 块合并 + WBFF_CACHE）
- A-12 POST /events/read  — 批量标记已读（已实现：调 event_bus.mark_read）
- A-13 GET /tuning-scatters — 整定前后散点

M1 skeleton：除 A-10/A-12 外返回结构完整的空数据，标注 TODO: M2 填充。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.event_bus import mark_read
from app.models.module_plugin import ModulePlugin
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workbench", tags=["workbench"])


# ---------------------------------------------------------------------------
# A-00 GET /scope-tree — 范围选择器数据
# ---------------------------------------------------------------------------


@router.get("/scope-tree", response_model=ApiResponse[list[dict]])
async def get_scope_tree(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """返回可选范围列表：全厂 + 工厂 + 装置。

    每条记录：{id, node_id, name, type, parent_id, parent_source_id}
    - id = plant_node.source_node_id（整数，与 workbench_window_summary.scope_id 对齐）
    - node_id = plant_node.id（字符串；下钻映射 plantNodeId 用，追溯矩阵 G2）
    - type = FACTORY / AREA（不返回 UNIT，选择器只到装置级）
    - parent_source_id = 父节点的 source_node_id（用于层级分组）
    """
    result = await db.execute(
        text(
            """
            SELECT n.source_node_id AS id,
                   n.id::text AS node_id,
                   n.name,
                   n.type,
                   n.parent_id::text AS parent_id,
                   p.source_node_id AS parent_source_id
            FROM plant_node n
            LEFT JOIN plant_node p ON n.parent_id = p.id
            WHERE n.source_node_id IS NOT NULL
              AND n.type IN ('FACTORY', 'AREA')
            ORDER BY n.type, n.name
            """
        )
    )
    nodes = [dict(row._mapping) for row in result.all()]
    return success(data=nodes)


# ---------------------------------------------------------------------------
# 共享参数说明（各端点按需声明）
#   scopeType: GLOBAL / FACTORY / AREA / UNIT / LOOP
#   scopeId:   范围 ID（GLOBAL 时忽略）
#   window:    24h / 7d / 30d
# ---------------------------------------------------------------------------


class EventReadRequest(BaseModel):
    """A-12 批量标记已读请求体。"""

    event_ids: list[int]


# ---------------------------------------------------------------------------
# A-01 GET /overview — 工作台总览
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse[dict])
async def get_overview(
    scopeType: str = Query("GLOBAL", description="范围：GLOBAL/FACTORY/AREA/UNIT/LOOP"),
    scopeId: int | None = Query(None, description="范围 ID"),
    window: str = Query("24h", description="时间窗口：24h/7d/30d"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-01 工作台总览：三窗口 KPI + 装置/单元排名 + Pareto/根因 + 处置漏斗。

    G-总览填充：读 workbench_window_summary 预计算表 + MV-02/MV-03 + diagnosis_run
    聚合（A3 迁 v2，旧 DiagnosisTag 读方已退役，见 14 号方案）。
    部分失败容错：单块异常返回空/None，不阻断其余块。
    """
    from app.services.workbench_overview import build_overview

    data = await build_overview(db, scope_type=scopeType, scope_id=scopeId, window=window)
    return success(data=data)


# ---------------------------------------------------------------------------
# A-02 GET /assessment — 评估
# ---------------------------------------------------------------------------


@router.get("/assessment", response_model=ApiResponse[dict])
async def get_assessment(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("24h"),
    view: str = Query("plant", description="排名视图：plant|unit"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-02 评估：摘要带 + 装置/单元排名 + 单元×指标热力 + 综合趋势。

    G-评估填充（F-EV-01~03）：四块聚合 summary/ranking/heatmap/trend，
    复用 G-总览的递归 CTE 与 alarm/overdue 聚合；distribution JSONB
    提供 trend 块的等级/模式/数据质量分布。部分失败容错。
    """
    from app.services.workbench_assessment import build_assessment

    data = await build_assessment(
        db, scope_type=scopeType, scope_id=scopeId, window=window, view=view
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# A-03 GET /diagnosis — 诊断
# ---------------------------------------------------------------------------


@router.get("/diagnosis", response_model=ApiResponse[dict])
async def get_diagnosis(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("24h"),
    onlyActive: bool = Query(False, description="仅看未处置（UNADDRESSED）的 run"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-03 诊断：关键异常表 + 结论时间线 + 适用性门禁 + 规则统计 + Pareto/根因。

    G-诊断填充（F-DG-01~03，14 号方案 A2 迁诊断 v2 引擎 diagnosis_run）：
    open_tags Top6（每回路最新未处置异常 run，SLA 已下线 D1=a）+ concl_timeline
    （disposition 四态由 review_status/loop_action_item 合成）+ fitness_gates
    （L0~L4 门禁聚合）+ rule_stats（symptom_tags 聚合 × 复核确认率，D2=a）
    + pareto/rootcause_top（primary_category / symptom_tags 聚合）。
    onlyActive=True → concl_timeline 仅保留未处置 run（默认 False 不动行为）。
    部分失败容错。
    """
    from app.services.workbench_diagnosis import build_diagnosis

    data = await build_diagnosis(
        db, scope_type=scopeType, scope_id=scopeId, window=window, only_active=onlyActive
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# A-04 GET /tuning — 整定
# ---------------------------------------------------------------------------


@router.get("/tuning", response_model=ApiResponse[dict])
async def get_tuning(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("24h"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-04 整定：批次列表（含 BLOCKED/READY/RUNNING）+ 待整定队列 + 散点 + 门禁。

    G-整定填充（F-TN-01~03）：batches（B-06 前置阻塞动态判定 + block_reason）
    + pending_queue（阻塞灰化语义）+ scatters（B-12 固化快照优先，30d 口径）
    + fitness_gates（L0~L4，整定关注 L3 门禁）。部分失败容错。
    """
    from app.services.workbench_tuning import build_tuning

    data = await build_tuning(db, scope_type=scopeType, scope_id=scopeId, window=window)
    return success(data=data)


# ---------------------------------------------------------------------------
# A-05 GET /handling — 处置
# ---------------------------------------------------------------------------


@router.get("/handling", response_model=ApiResponse[dict])
async def get_handling(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("24h"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-05 处置：4 泳道看板 + 漏斗 + 人员负载 + 重开列表。"""
    # TODO: M2 填充 — kanban + funnel + staff_load + reopen_list
    return success(
        data={
            "kanban": {"PENDING": [], "EXECUTING": [], "VERIFYING": [], "CLOSED": []},
            "funnel": [],
            "staff_load": [],
            "reopen_list": [],
            "scope": {"type": scopeType, "id": scopeId},
            "window": window,
        }
    )


# ---------------------------------------------------------------------------
# A-07 GET /flags — 趋势 flags 气泡
# ---------------------------------------------------------------------------


@router.get("/flags", response_model=ApiResponse[dict])
async def get_flags(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("24h"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-07 趋势 flags 气泡（dip/spike/deterioration/jump/oscillation/saturation）。"""
    # TODO: M2 填充 — trend_flags 差分检测
    return success(
        data={"flags": [], "scope": {"type": scopeType, "id": scopeId}, "window": window}
    )


# ---------------------------------------------------------------------------
# A-08 GET /staff-load — 人员负载（MV-01 包装）
# ---------------------------------------------------------------------------


@router.get("/staff-load", response_model=ApiResponse[dict])
async def get_staff_load(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-08 人员负载（包装物化视图 mv_staff_workload）。"""
    # TODO: M2 填充 — 查询 mv_staff_workload MV
    return success(data={"staff": [], "scope": {"type": scopeType, "id": scopeId}})


# ---------------------------------------------------------------------------
# A-09 GET /lane-more — 泳道展开更多
# ---------------------------------------------------------------------------


@router.get("/lane-more", response_model=ApiResponse[dict])
async def get_lane_more(
    lane: str = Query(..., description="泳道：PENDING/EXECUTING/VERIFYING/CLOSED"),
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    offset: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-09 泳道展开更多（分页加载工单卡片）。"""
    # TODO: M2 填充 — 分页查询 handling_order by lane
    return success(
        data={"orders": [], "lane": lane, "offset": offset, "limit": limit, "has_more": False}
    )


# ---------------------------------------------------------------------------
# A-10 GET /plugins — 模块 4 态列表（已实现：读 module_plugin 表）
# ---------------------------------------------------------------------------


@router.get("/plugins", response_model=ApiResponse[dict])
async def get_plugins(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-10 模块 4 态列表（CORE/ENABLED/MAINTENANCE/UNINSTALLED）。"""
    result = await db.execute(select(ModulePlugin).order_by(ModulePlugin.order_index))
    plugins = result.scalars().all()
    return success(
        data={
            "plugins": [
                {
                    "module_key": p.module_key,
                    "display_name": p.display_name,
                    "status": p.status,
                    "version": p.version,
                    "is_core": p.is_core,
                    "order_index": p.order_index,
                    "maintenance_window": p.maintenance_window,
                }
                for p in plugins
            ]
        }
    )


# ---------------------------------------------------------------------------
# A-11 GET /aggregate — 首屏批量预取（8 块合并 + WBFF_CACHE）
# ---------------------------------------------------------------------------


@router.get("/aggregate", response_model=ApiResponse[dict])
async def get_aggregate(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("24h"),
    customStart: str | None = Query(None, description="自定义窗口起始（ISO8601）"),
    customEnd: str | None = Query(None, description="自定义窗口结束（ISO8601）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-11 首屏批量预取：8 块合并 + WBFF_CACHE 30s TTL。"""
    # TODO: M2 填充 — 并发聚合 A-01~A-05 + A-07 + A-08 + A-10 结果 + Redis 缓存
    return success(
        data={
            "results": {},
            "meta": {
                "cache_hit": False,
                "elapsed_ms": 0,
                "scope": {"type": scopeType, "id": scopeId},
                "window": window,
                "custom_start": customStart,
                "custom_end": customEnd,
            },
        }
    )


# ---------------------------------------------------------------------------
# A-12 POST /events/read — 批量标记已读（已实现：调 event_bus.mark_read）
# ---------------------------------------------------------------------------


@router.post("/events/read", response_model=ApiResponse[dict])
async def mark_events_read(
    body: EventReadRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-12 批量标记事件已读（更新 event_bus.read_by_users）。"""
    marked = await mark_read(db, event_ids=body.event_ids, user_id=user.id)
    await db.commit()
    return success(data={"marked": marked})


# ---------------------------------------------------------------------------
# A-13 GET /tuning-scatters — 整定前后散点
# ---------------------------------------------------------------------------


@router.get("/tuning-scatters", response_model=ApiResponse[dict])
async def get_tuning_scatters(
    scopeType: str = Query("GLOBAL"),
    scopeId: int | None = Query(None),
    window: str = Query("30d", description="散点默认 30d 窗口"),
    batchId: int | None = Query(None, description="按批次过滤（仅返回该批次固化快照点）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """A-13 整定前后散点（11 点 Δ 区分色：正绿负红）。

    G-整定填充（F-TN-03，B-12）：批次 COMPLETED 固化 scatters_before/after 优先，
    其次 TUNING 类工单 kpi_before/after；Δ=after-before，significance=Δ≥5。
    """
    from app.services.workbench_tuning import build_tuning_scatters

    data = await build_tuning_scatters(
        db, scope_type=scopeType, scope_id=scopeId, window=window, batch_id=batchId
    )
    return success(data=data)
