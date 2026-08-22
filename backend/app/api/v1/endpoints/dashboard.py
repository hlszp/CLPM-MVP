"""Dashboard aggregation endpoints (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

路由清单：
- GET /api/v1/dashboard/overview      — 工作台聚合数据（6 大 KPI + 低效回路 Top 10 + 趋势 + 异常）
- GET /api/v1/dashboard/board         — 装置级三大 KPI 看板（综合性能/平均自控率/稳定率）
- GET /api/v1/dashboard/auto-rate-rt  — 实时自控率（每分钟刷新，来自 TDengine 最新 MODE 值）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db

# MVP 精简：已屏蔽诊断模块 → 不再导入 DiagnosisResult
# from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.sys_user import SysUser
from app.models.unit_kpi_summary import UnitKpiSummary
from app.schemas.common import ApiResponse, success
from app.services.dashboard import get_dashboard_overview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# S6-PORTAL-001: 工作台聚合 API
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse[dict])
async def get_overview_endpoint(
    plantId: str | None = Query(None, description="按装置/单元筛选"),
    granularity: str = Query(
        "day",
        description="时间粒度：day/week/month（day=最近24小时，week=最近7天，month=最近30天）",
    ),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """工作台聚合数据（所有角色可访问，不同角色数据范围不同）。

    - ADMIN/EXPERT：全厂数据
    - IC_ENGINEER/PE_ENGINEER：装置级数据
    - SPONSOR：工厂级汇总（仅 KPI 卡片，不返回低效回路列表）

    Redis 缓存 5 分钟，缓存 key 含 plant_id + granularity + user_role。
    """
    data = await get_dashboard_overview(
        db=db,
        user_role=user.role,
        plant_id=plantId,
        granularity=granularity,
    )
    return success(data=data)


def _build_board_item(summary: UnitKpiSummary, node_name: str) -> dict:
    """构建看板单项响应字典."""
    return {
        "nodeId": str(summary.node_id),
        "nodeName": node_name,
        "snapshotTime": (summary.snapshot_time.isoformat() if summary.snapshot_time else None),
        "avgScore": float(summary.avg_score) if summary.avg_score is not None else None,
        "autoModeRate": (
            float(summary.auto_mode_rate) if summary.auto_mode_rate is not None else None
        ),
        "stabilityRate": (
            float(summary.stability_rate) if summary.stability_rate is not None else None
        ),
        "effectiveAutoRate": (
            float(summary.effective_auto_rate) if summary.effective_auto_rate is not None else None
        ),
        "accuracyRate": (
            float(summary.accuracy_rate) if summary.accuracy_rate is not None else None
        ),
        "fastRate": float(summary.fast_rate) if summary.fast_rate is not None else None,
        "goodValueRate": (
            float(summary.good_value_rate) if summary.good_value_rate is not None else None
        ),
        "oscillationRate": (
            float(summary.oscillation_rate) if summary.oscillation_rate is not None else None
        ),
        "saturationRate": (
            float(summary.saturation_rate) if summary.saturation_rate is not None else None
        ),
        "instrumentFaultRate": (
            float(summary.instrument_fault_rate)
            if summary.instrument_fault_rate is not None
            else None
        ),
        "totalLoops": summary.total_loops or 0,
        "evaluatedLoops": summary.evaluated_loops or 0,
        "inconclusiveLoops": summary.inconclusive_loops or 0,
        "excludedLoops": summary.excluded_loops or 0,
        "status": summary.status,
        "algorithmVersion": summary.algorithm_version,
    }


# ---------------------------------------------------------------------------
# S6-PORTAL-003: 实时自控率（UIUX v5.3 ① — 半圆径向仪表盘）
# ---------------------------------------------------------------------------


@router.get("/auto-rate-rt", response_model=ApiResponse[dict])
async def get_auto_rate_rt_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """实时自控率（来自 TDengine 最新 MODE 值，每分钟刷新）.

    返回：
    - ``rate``: 自控率百分比（0-100）
    - ``autoCount``: 自动模式回路数
    - ``manualCount``: 手动模式回路数
    - ``totalCount``: 有效回路总数
    - ``modeCounts``: 5 种 MODE 各自的回路数（dict[int,int]，key 为 0/1/2/3/4）
    - ``readAt``: 数据最新时间（实时缓存 collectTime 最大值，ISO 字符串；
      全部回退 DB 值时为 None，表示实时流中断）

    若 TDengine 不可用或无 MODE 数据，返回 ``rate=null``。

    设计依据：FDS v5.1 §5.3.6, UIUX v5.3 ①

    v6.1 更新：
    - 支持递归聚合当前节点及所有下属节点的回路
    - modeCounts 字段用于前端饼图按 5 种 MODE 中文展示（0-手动/1-自动/2-串级/3-远程/4-先控）
    """
    from app.services.node_performance import collect_descendant_loop_ids, query_realtime_auto_rate

    # 查询活跃回路 ID 列表（递归包含当前节点及所有下属节点）
    if plantId:
        loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result = await db.execute(loop_query)
        loop_ids = [str(row[0]) for row in result.all()]

    if not loop_ids:
        return success(
            data={
                "rate": None,
                "autoCount": 0,
                "manualCount": 0,
                "totalCount": 0,
                "modeCounts": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0},
                "readAt": None,
                "message": "无活跃回路",
            }
        )

    data = await query_realtime_auto_rate(db, loop_ids)
    if data is None:
        return success(
            data={
                "rate": None,
                "autoCount": 0,
                "manualCount": 0,
                "totalCount": 0,
                "modeCounts": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0},
                "readAt": None,
                "message": "TDengine 不可用或无 MODE 数据",
            }
        )

    return success(
        data={
            "rate": float(data["rate"]),
            "autoCount": data["auto_count"],
            "manualCount": data["manual_count"],
            "totalCount": data["total_count"],
            "modeCounts": {str(k): v for k, v in data["mode_counts"].items()},
            "readAt": data["read_at"],
        }
    )


# ---------------------------------------------------------------------------
# S6-PORTAL-004: 节点级聚合 KPI（v6.1 新增）
# ---------------------------------------------------------------------------


@router.get("/board/trend", response_model=ApiResponse[dict])
async def get_board_trend_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    timeWindow: str = Query(
        "today",
        description="时间窗：last_8_hours/last_24_hours/last_72_hours/last_168_hours/"
        "today/yesterday/last_7_days/last_30_days/custom",
    ),
    startTime: str | None = Query(None, description="自定义窗口起始（ISO 8601，custom 时必填）"),
    endTime: str | None = Query(None, description="自定义窗口结束（ISO 8601，custom 时必填）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """节点级聚合趋势数据（来自 ``unit_kpi_summary`` 表，递归聚合当前节点及所有下属节点）.

    返回当前节点及其所有下属节点的趋势数据，按小时聚合：
    - ``avgScore``: 综合性能评分（加权平均）
    - ``autoModeRate``: 平均自控率（加权平均）
    - ``stabilityRate``: 稳定率（加权平均）
    - ``fastRate``: 快速率（加权平均，04-系统概览 v4.0）
    - ``accuracyRate``: 准确率（加权平均，04-系统概览 v4.0）
    - ``evaluatedLoops``: 参评回路数（求和）

    若指定 ``plantId``，返回该节点及其所有下属节点的聚合趋势；
    若未指定 ``plantId``，返回全厂所有节点的聚合趋势。

    设计依据：FDS v5.1 §5.3.7, UIUX v5.3 ①, DDS v4.1 §2.17

    v6.1 更新：支持递归聚合当前节点及所有下属节点的趋势数据（使用 PostgreSQL 递归 CTE）
    v4.4 更新：新增 last_24/72/168 小时滚动窗口与 custom 自定义起止窗口
    """

    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.fromisoformat(s)

    now = datetime.now(UTC).replace(tzinfo=None)
    if timeWindow == "custom" and startTime and endTime:
        start = _parse_dt(startTime) or (now - timedelta(hours=24))
        now = _parse_dt(endTime) or now
    elif timeWindow == "last_8_hours":
        start = now - timedelta(hours=8)
    elif timeWindow in ("last_24_hours", "last_72_hours", "last_168_hours"):
        hours = {"last_24_hours": 24, "last_72_hours": 72, "last_168_hours": 168}[timeWindow]
        start = now - timedelta(hours=hours)
    elif timeWindow == "today":
        start = now - timedelta(hours=24)
    elif timeWindow == "yesterday":
        start = now - timedelta(days=2)
        now = now - timedelta(days=1)
    elif timeWindow == "last_7_days":
        start = now - timedelta(days=7)
    elif timeWindow == "last_30_days":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(hours=24)

    # v6.1.2 修复：只聚合 UNIT 级节点，避免 FACTORY/AREA/UNIT 父子节点重复累加
    if plantId:
        cte_sql = text("""
            WITH RECURSIVE node_tree AS (
                SELECT id, type FROM plant_node WHERE id = :node_id
                UNION ALL
                SELECT child.id, child.type FROM plant_node child
                JOIN node_tree ON child.parent_id = node_tree.id
            )
            SELECT id FROM node_tree WHERE type = 'UNIT'
        """)
        result = await db.execute(cte_sql, {"node_id": plantId})
        descendant_ids = [str(row.id) for row in result.all()]
    else:
        result = await db.execute(select(PlantNode.id).where(PlantNode.type == "UNIT"))
        descendant_ids = [str(row.id) for row in result.all()]

    # 查询实际回路总数（去重，避免父子节点重复累加）
    from app.services.node_performance import collect_descendant_loop_ids

    if plantId:
        total_loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_result = await db.execute(select(LoopLedger.id).where(LoopLedger.is_active.is_(True)))
        total_loop_ids = [str(row[0]) for row in loop_result.all()]
    total_loops_count = len(total_loop_ids)

    if not descendant_ids:
        return success(
            data={
                "timestamps": [],
                "avgScore": [],
                "autoModeRate": [],
                "stabilityRate": [],
                "fastRate": [],
                "accuracyRate": [],
                "evaluatedLoops": [],
                "totalLoops": total_loops_count,
            }
        )

    hour_col = func.date_trunc("hour", UnitKpiSummary.snapshot_time).label("hour")

    subq = (
        select(
            hour_col,
            UnitKpiSummary.node_id.label("nid"),
            UnitKpiSummary.evaluated_loops,
            UnitKpiSummary.avg_score,
            UnitKpiSummary.auto_mode_rate,
            UnitKpiSummary.stability_rate,
            UnitKpiSummary.fast_rate,
            UnitKpiSummary.accuracy_rate,
        ).where(
            UnitKpiSummary.node_id.in_(descendant_ids),
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= now,
        )
    ).subquery()

    stmt = (
        select(
            subq.c.hour,
            func.sum(subq.c.evaluated_loops).label("total_evaluated"),
            func.sum(subq.c.avg_score * subq.c.evaluated_loops).label("score_weighted_sum"),
            func.sum(subq.c.auto_mode_rate * subq.c.evaluated_loops).label("auto_weighted_sum"),
            func.sum(subq.c.stability_rate * subq.c.evaluated_loops).label("stable_weighted_sum"),
            func.sum(subq.c.fast_rate * subq.c.evaluated_loops).label("fast_weighted_sum"),
            func.sum(subq.c.accuracy_rate * subq.c.evaluated_loops).label("acc_weighted_sum"),
        )
        .group_by(subq.c.hour)
        .order_by(subq.c.hour.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    # 构建按小时索引的数据字典
    row_map: dict[str, any] = {}
    for row in rows:
        hour_key = row.hour.strftime("%Y-%m-%dT%H:00:00")
        row_map[hour_key] = row

    # 生成完整的小时序列（填充缺失的小时）
    timestamps: list[str] = []
    avg_score: list[float | None] = []
    auto_mode_rate: list[float | None] = []
    stability_rate: list[float | None] = []
    fast_rate: list[float | None] = []
    accuracy_rate: list[float | None] = []
    evaluated_loops: list[int] = []

    current = start.replace(minute=0, second=0, microsecond=0)
    while current <= now:
        hour_key = current.strftime("%Y-%m-%dT%H:00:00")
        timestamps.append(hour_key)
        row = row_map.get(hour_key)
        if row is not None:
            total = row.total_evaluated or 0
            evaluated_loops.append(total)
            if total > 0:
                avg_score.append(round(float(row.score_weighted_sum or 0) / total, 2))
                auto_mode_rate.append(round(float(row.auto_weighted_sum or 0) / total, 2))
                stability_rate.append(round(float(row.stable_weighted_sum or 0) / total, 2))
                fast_rate.append(round(float(row.fast_weighted_sum or 0) / total, 2))
                accuracy_rate.append(round(float(row.acc_weighted_sum or 0) / total, 2))
            else:
                avg_score.append(None)
                auto_mode_rate.append(None)
                stability_rate.append(None)
                fast_rate.append(None)
                accuracy_rate.append(None)
        else:
            evaluated_loops.append(0)
            avg_score.append(None)
            auto_mode_rate.append(None)
            stability_rate.append(None)
            fast_rate.append(None)
            accuracy_rate.append(None)
        current += timedelta(hours=1)

    return success(
        data={
            "timestamps": timestamps,
            "avgScore": avg_score,
            "autoModeRate": auto_mode_rate,
            "stabilityRate": stability_rate,
            "fastRate": fast_rate,
            "accuracyRate": accuracy_rate,
            "evaluatedLoops": evaluated_loops,
            "totalLoops": total_loops_count,
        }
    )


# ---------------------------------------------------------------------------
# S6-PORTAL-005: 节点级聚合 KPI（v6.1 新增）
# ---------------------------------------------------------------------------


def _resolve_aggregate_window(
    time_window: str | None,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """解析 board/aggregate 时间窗为 (start, end)（naive UTC）；None=不启用窗口.

    取值与 board/trend 对齐：
    last_8_hours/last_24_hours/last_72_hours/last_168_hours（滚动窗口）/
    today/yesterday/last_7_days/last_30_days（北京日历日）/
    custom（start_dt/end_dt 必填），未识别值回退 today。

    注意：所有时间统一转换为 UTC 存储，计算本地日期时需加 UTC+8 偏移。
    """
    if not time_window:
        return None
    if time_window == "custom":
        if start_dt is None or end_dt is None:
            return None
        return start_dt, end_dt
    # UTC 现在时间，北京时间（UTC+8）
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    # 北京时间现在
    now_cst = now_utc + timedelta(hours=8)
    if time_window == "last_8_hours":
        start = now_utc - timedelta(hours=8)
        end = now_utc
    elif time_window in ("last_24_hours", "last_72_hours", "last_168_hours"):
        hours = {"last_24_hours": 24, "last_72_hours": 72, "last_168_hours": 168}[time_window]
        start = now_utc - timedelta(hours=hours)
        end = now_utc
    elif time_window == "today":
        # 今日：北京时间今日 00:00 → 当前时间
        today_start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today_start_cst - timedelta(hours=8)  # 转 UTC
        end = now_utc
    elif time_window == "yesterday":
        # 昨日：北京时间昨日 00:00 → 今日 00:00
        today_start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start_cst = today_start_cst - timedelta(days=1)
        start = yesterday_start_cst - timedelta(hours=8)  # 转 UTC
        end = today_start_cst - timedelta(hours=8)
    elif time_window == "last_7_days":
        # 近7天：从7天前的0点到现在
        start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        start = start_cst - timedelta(hours=8)
        end = now_utc
    elif time_window == "last_30_days":
        # 近30天：从30天前的0点到现在
        start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
        start = start_cst - timedelta(hours=8)
        end = now_utc
    else:
        start = now_utc - timedelta(hours=24)
        end = now_utc
    return start, end


# 窗口聚合的 rate 字段（unit_kpi_summary 列名 → 响应 item 键名）
_WINDOW_RATE_FIELD_KEYS = {
    "avg_score": "avgScore",
    "auto_mode_rate": "autoModeRate",
    "stability_rate": "stabilityRate",
    "effective_auto_rate": "effectiveAutoRate",
    "accuracy_rate": "accuracyRate",
    "fast_rate": "fastRate",
    "good_value_rate": "goodValueRate",
    "oscillation_rate": "oscillationRate",
    "saturation_rate": "saturationRate",
    "instrument_fault_rate": "instrumentFaultRate",
}


async def _load_window_items(
    db: AsyncSession,
    item_node_ids: list[str],
    window: tuple[datetime, datetime],
) -> list[dict]:
    """窗口模式明细行：rate 字段取窗口内 evaluated_loops 加权平均.

    计数字段（totalLoops/evaluatedLoops/inconclusiveLoops/excludedLoops/status 等）
    取窗口内最新快照行；snapshotTime=窗口内最新快照时间。
    """
    start, end = window

    # 每节点窗口内最新快照（计数字段来源）
    subq = (
        select(
            UnitKpiSummary.node_id.label("nid"),
            func.max(UnitKpiSummary.snapshot_time).label("max_ts"),
        )
        .where(
            UnitKpiSummary.node_id.in_(item_node_ids),
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= end,
        )
        .group_by(UnitKpiSummary.node_id)
        .subquery()
    )
    latest_stmt = (
        select(UnitKpiSummary, PlantNode.name.label("node_name"))
        .join(PlantNode, UnitKpiSummary.node_id == PlantNode.id)
        .join(
            subq,
            (UnitKpiSummary.node_id == subq.c.nid)
            & (UnitKpiSummary.snapshot_time == subq.c.max_ts),
        )
        .order_by(PlantNode.name)
    )
    result = await db.execute(latest_stmt)
    latest_rows = result.all()

    # 每节点窗口内 rate 字段加权和（权重=evaluated_loops，与 board/trend 同口径）。
    # 关键：每个字段使用独立的「非 NULL 分母」——SUM(evaluated_loops) FILTER (WHERE
    # field IS NOT NULL)。旧实现用统一 eval_sum 作所有字段分母，当某字段在部分快照为
    # NULL（如新增 instrument_fault_rate 在旧快照中为 NULL）时，这些快照的
    # evaluated_loops 仍计入分母，导致窗口加权均值被稀释到接近 0。
    w_stmt = (
        select(
            UnitKpiSummary.node_id.label("nid"),
            *[
                func.sum(getattr(UnitKpiSummary, field) * UnitKpiSummary.evaluated_loops).label(
                    f"{field}_wsum"
                )
                for field in _WINDOW_RATE_FIELD_KEYS
            ],
            *[
                func.sum(UnitKpiSummary.evaluated_loops)
                .filter(getattr(UnitKpiSummary, field).is_not(None))
                .label(f"{field}_esum")
                for field in _WINDOW_RATE_FIELD_KEYS
            ],
        )
        .where(
            UnitKpiSummary.node_id.in_(item_node_ids),
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= end,
        )
        .group_by(UnitKpiSummary.node_id)
    )
    w_result = await db.execute(w_stmt)
    w_map = {str(row.nid): row for row in w_result.all()}

    items: list[dict] = []
    for summary, node_name in latest_rows:
        item = _build_board_item(summary, node_name)
        w = w_map.get(str(summary.node_id))
        for field, key in _WINDOW_RATE_FIELD_KEYS.items():
            wsum = getattr(w, f"{field}_wsum") if w is not None else None
            esum = getattr(w, f"{field}_esum") if w is not None else None
            if esum and float(esum) > 0:
                item[key] = round(float(wsum or 0) / float(esum), 2)
            else:
                item[key] = None
        items.append(item)
    return items


@router.get("/board/aggregate", response_model=ApiResponse[dict])
async def get_board_aggregate_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    timeWindow: str | None = Query(
        None,
        description="时间窗：last_8_hours/last_24_hours/last_72_hours/last_168_hours/"
        "today/yesterday/last_7_days/last_30_days/custom；缺省=每节点最新快照（现状）",
    ),
    startTime: str | None = Query(None, description="自定义窗口起始（ISO 8601，custom 时必填）"),
    endTime: str | None = Query(None, description="自定义窗口结束（ISO 8601，custom 时必填）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """节点级聚合 KPI（来自 ``unit_kpi_summary`` 表，递归聚合当前节点及所有下属节点）.

    返回当前节点及其所有下属节点的最新 KPI 汇总快照：
    - ``avgScore``: 综合性能评分（加权平均）
    - ``autoModeRate``: 平均自控率（加权平均）
    - ``stabilityRate``: 稳定率（加权平均）
    - ``effectiveAutoRate``: 有效自控率（加权平均）
    - ``accuracyRate``: 准确率（加权平均）
    - ``fastRate``: 快速率（加权平均）
    - ``goodValueRate``: 好值率（加权平均）
    - ``totalLoops``: 总回路数（去重后实际回路数）
    - ``evaluatedLoops``: 参评回路数（去重后实际回路数）
    - ``inconclusiveLoops``: INCONCLUSIVE 回路数（去重后实际回路数）
    - ``excludedLoops``: 排除回路数（去重后实际回路数）

    若指定 ``plantId``，返回该节点及其所有下属节点的聚合 KPI；
    若未指定 ``plantId``，返回全厂所有节点的聚合 KPI。

    设计依据：FDS v5.1 §5.3.7, UIUX v5.3 ①, DDS v4.1 §2.17

    v6.1 更新：支持递归聚合当前节点及所有下属节点的 KPI（使用 PostgreSQL 递归 CTE）

    v6.1.1 修复：回路数统计改为从数据库直接查询实际回路数，避免父子节点重复累加

    v6.1.4 更新：新增可选 ``timeWindow``——缺省时保持"每节点最新快照"现状；
    指定后 rate 类字段改为窗口内 evaluated_loops 加权平均（与 board/trend 同口径），
    计数字段取窗口内最新快照，evaluatedLoops/inconclusiveLoops 按窗口边界统计，
    响应回显 timeWindow/windowStart/windowEnd 供前端标注统计窗口。
    """
    from app.services.node_performance import collect_descendant_loop_ids

    def _parse_dt(s: datetime | str | None) -> datetime | None:
        # isinstance 守卫：直接调用端点函数时（单测场景），未传参数的默认值是
        # FastAPI Query 对象而非 None，非字符串输入一律视为未指定
        if isinstance(s, datetime):
            return s.replace(tzinfo=None)
        if not isinstance(s, str):
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.fromisoformat(s)

    window = _resolve_aggregate_window(
        timeWindow,
        start_dt=_parse_dt(startTime),
        end_dt=_parse_dt(endTime),
    )

    # 获取实际回路数（去重，避免父子节点重复累加）
    if plantId:
        loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result = await db.execute(loop_query)
        loop_ids = [str(row[0]) for row in result.all()]

    # 明细表只列出当前节点及其直接子节点（不含本节点之外的下下层节点）：
    # 第一行固定为当前节点统计，后续行为下一层子节点性能数据
    if plantId:
        result = await db.execute(
            select(PlantNode.id).where((PlantNode.id == plantId) | (PlantNode.parent_id == plantId))
        )
        item_node_ids = [str(row.id) for row in result.all()]
    else:
        # 未指定节点时：列出全部根节点（顶层）
        result = await db.execute(select(PlantNode.id).where(PlantNode.parent_id.is_(None)))
        item_node_ids = [str(row.id) for row in result.all()]

    if not item_node_ids:
        return success(data={"items": [], "total": 0})

    if window is None:
        # 缺省（现状）：每个 node_id 取最新快照
        subq = (
            select(
                UnitKpiSummary.node_id.label("nid"),
                func.max(UnitKpiSummary.snapshot_time).label("max_ts"),
            )
            .where(UnitKpiSummary.node_id.in_(item_node_ids))
            .group_by(UnitKpiSummary.node_id)
            .subquery()
        )

        # 查询每个子节点的最新快照
        stmt = (
            select(UnitKpiSummary, PlantNode.name.label("node_name"))
            .join(PlantNode, UnitKpiSummary.node_id == PlantNode.id)
            .join(
                subq,
                (UnitKpiSummary.node_id == subq.c.nid)
                & (UnitKpiSummary.snapshot_time == subq.c.max_ts),
            )
            .order_by(PlantNode.name)
        )

        result = await db.execute(stmt)
        rows = result.all()
        items = [_build_board_item(summary, node_name) for summary, node_name in rows]
    else:
        # 窗口模式：rate 字段按窗口加权，计数字段取窗口内最新快照
        items = await _load_window_items(db, item_node_ids, window)

    # 获取聚合节点名称
    node_name = None
    if plantId:
        node_result = await db.execute(select(PlantNode.name).where(PlantNode.id == plantId))
        node_name = node_result.scalar_one_or_none()

    # 从数据库直接查询实际回路数统计（避免父子节点重复累加）
    total_loops = len(loop_ids)
    excluded_loops = 0
    evaluated_loops = 0
    inconclusive_loops = 0

    if loop_ids:
        # excluded_loops: include_in_evaluation=false 的回路数
        ex_result = await db.execute(
            select(func.count())
            .select_from(LoopLedger)
            .where(
                LoopLedger.id.in_(loop_ids),
                LoopLedger.include_in_evaluation.is_(False),
            )
        )
        excluded_loops = int(ex_result.scalar() or 0)

        # 参评/INCONCLUSIVE 统计窗口：窗口模式用窗口边界，缺省保持近 24 小时现状
        if window is not None:
            start_time = window[0]
        else:
            start_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)

        # 查询有 SUCCESS 快照的回路 ID
        success_conds = [
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= start_time,
            KpiSnapshotHourly.status == "SUCCESS",
            LoopLedger.include_in_evaluation.is_(True),
        ]
        if window is not None:
            success_conds.append(KpiSnapshotHourly.ts_start <= window[1])
        success_result = await db.execute(
            select(func.distinct(KpiSnapshotHourly.loop_id))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(*success_conds)
        )
        success_loop_ids = [str(row[0]) for row in success_result.all()]
        evaluated_loops = len(success_loop_ids)

        # inconclusive_loops: 只有 INCONCLUSIVE 快照但没有 SUCCESS 快照的回路数
        ic_conds = [
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= start_time,
            KpiSnapshotHourly.status == "INCONCLUSIVE",
            KpiSnapshotHourly.loop_id.not_in(success_loop_ids) if success_loop_ids else True,
            LoopLedger.include_in_evaluation.is_(True),
        ]
        if window is not None:
            ic_conds.append(KpiSnapshotHourly.ts_start <= window[1])
        ic_result = await db.execute(
            select(func.count(func.distinct(KpiSnapshotHourly.loop_id)))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(*ic_conds)
        )
        inconclusive_loops = int(ic_result.scalar() or 0)

    # 计算聚合值
    # v6.1.3 修复：优先取当前节点自身的 unit_kpi_summary 行——该表按节点持久化了
    # 递归聚合结果（含重要等级加权），是装置级聚合的权威口径，且与明细表首行一致；
    # 原实现仅对 items 中的 UNIT 级节点加权，选中 FACTORY 或全厂（items 全为
    # FACTORY 根节点）时无 UNIT 可加，聚合指标全部返回 NULL。
    # 当前节点无快照（或全厂视图）时，退化为 items 间按 evaluatedLoops 加权平均：
    # items 仅含当前节点+直接子节点（或全厂根节点），兄弟/根节点间回路不重叠，
    # 不存在父子重复计数。
    self_item = next((it for it in items if it.get("nodeId") == plantId), None) if plantId else None

    def weighted_avg(field: str) -> float | None:
        if self_item is not None and self_item.get(field) is not None:
            return round(float(self_item[field]), 2)
        total = 0.0
        count = 0
        for item in items:
            val = item.get(field)
            weight = item.get("evaluatedLoops") or 0
            if val is not None and weight > 0:
                total += float(val) * weight
                count += weight
        return round(total / count, 2) if count > 0 else None

    aggregate = {
        "nodeId": plantId,
        "nodeName": node_name or "全厂",
        "avgScore": weighted_avg("avgScore"),
        "autoModeRate": weighted_avg("autoModeRate"),
        "stabilityRate": weighted_avg("stabilityRate"),
        "effectiveAutoRate": weighted_avg("effectiveAutoRate"),
        "accuracyRate": weighted_avg("accuracyRate"),
        "fastRate": weighted_avg("fastRate"),
        "goodValueRate": weighted_avg("goodValueRate"),
        "instrumentFaultRate": weighted_avg("instrumentFaultRate"),
        "totalLoops": total_loops,
        "evaluatedLoops": evaluated_loops,
        "inconclusiveLoops": inconclusive_loops,
        "excludedLoops": excluded_loops,
    }

    data: dict = {"items": items, "total": len(items), "aggregate": aggregate}
    if window is not None:
        # 回显统计窗口，供前端 gauges 卡片标注
        data["timeWindow"] = timeWindow
        data["windowStart"] = window[0].isoformat()
        data["windowEnd"] = window[1].isoformat()
    return success(data=data)


# ---------------------------------------------------------------------------
# P3-05: 异常预测与提前预警
# ---------------------------------------------------------------------------

_PREDICTION_CACHE_KEY = "dashboard:predictions:{plant_id}"
_PREDICTION_CACHE_TTL = 600  # 10 分钟


@router.get("/predictions", response_model=ApiResponse[dict])
async def get_predictions_endpoint(
    plantId: str | None = Query(None, description="按装置筛选；为空分析全厂"),
    topN: int = Query(10, ge=1, le=50, description="返回的高风险回路数"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """异常预测与提前预警（P3-05）.

    基于最近 7 天 KPI 快照趋势（线性回归），预测未来 24 小时可能出问题的回路。
    返回高风险回路列表（按风险分降序），含风险等级/风险因素/趋势数据。

    - 权限：所有登录用户可访问
    - 缓存：Redis 10 分钟（缓存 key 含 plant_id）
    - 数据源：``kpi_snapshot_hourly`` 表（status=SUCCESS）

    风险等级：
    - HIGH（≥60分）：多个指标显著恶化，建议立即关注
    - MEDIUM（≥30分）：部分指标有恶化趋势，建议密切观察
    - LOW（<30分）：不返回（只返回 MEDIUM+HIGH）
    """
    import json

    from app.core.redis import redis_client
    from app.services.anomaly_prediction import predict_loop_risks

    # 尝试读缓存
    cache_key = _PREDICTION_CACHE_KEY.format(plant_id=plantId or "all")
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            data["cached"] = True
            return success(data=data)
    except Exception:  # noqa: BLE001
        pass  # Redis 不可用时降级为直接计算

    # 计算预测
    data = await predict_loop_risks(db, plant_id=plantId, top_n=topN)
    data["cached"] = False

    # 写缓存（失败不报错）
    try:
        await redis_client.setex(
            cache_key,
            _PREDICTION_CACHE_TTL,
            json.dumps(data, default=str),
        )
    except Exception:  # noqa: BLE001
        pass

    return success(data=data)


__all__ = ["router"]


# ---------------------------------------------------------------------------
# 系统概览标杆页聚合接口（04-系统概览）
# ---------------------------------------------------------------------------


@router.get("/system-overview", response_model=ApiResponse[dict])
async def get_system_overview_endpoint(
    plantId: str | None = Query(None, description="按装置/单元筛选；为空统计全厂"),
    timeWindow: str = Query(
        "last_8_hours",
        description="时间窗：last_8_hours/today/yesterday/last_7_days/last_30_days",
    ),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """系统概览标杆页聚合接口（04-系统概览）。

    一次返回概览页所需的全部统计数据，减少前端请求数：
    - summary: KPI 统计带（8 个统计卡片）
    - scoreDistribution: 评分等级分布
    - attentionSummary: 关注队列五来源汇总
    - autoRate: 实时自控率（含模式分布）
    - diagnosisDistribution: 诊断建议类型分布
    - topLoops: Top 10 问题回路
    - trend: 窗口内趋势对比数据
    - compare: 与上一窗口对比指标（scoreDelta/autoDelta/stabilityDelta）
    """
    from app.services.node_performance import (
        collect_descendant_loop_ids,
        query_realtime_auto_rate,
    )

    window = _resolve_aggregate_window(timeWindow)
    if window is None:
        window = _resolve_aggregate_window("last_8_hours")
    start, end = window  # type: ignore[misc]

    # 上一窗口（对比用）
    window_delta = end - start
    prev_start = start - window_delta
    prev_end = start

    # 获取节点范围内的回路 ID
    if plantId:
        loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result = await db.execute(loop_query)
        loop_ids = [str(row[0]) for row in result.all()]

    total_loops_count = len(loop_ids)

    # ========== 1. 基础回路数统计 ==========
    excluded_loops = 0
    evaluated_loops = 0
    inconclusive_loops = 0
    if loop_ids:
        # excluded_loops
        ex_result = await db.execute(
            select(func.count())
            .select_from(LoopLedger)
            .where(
                LoopLedger.id.in_(loop_ids),
                LoopLedger.include_in_evaluation.is_(False),
            )
        )
        excluded_loops = int(ex_result.scalar() or 0)

        # evaluated_loops: 窗口内有 SUCCESS 快照
        success_result = await db.execute(
            select(func.distinct(KpiSnapshotHourly.loop_id))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start,
                KpiSnapshotHourly.ts_start <= end,
                KpiSnapshotHourly.status == "SUCCESS",
                LoopLedger.include_in_evaluation.is_(True),
            )
        )
        success_loop_ids = [str(row[0]) for row in success_result.all()]
        evaluated_loops = len(success_loop_ids)

        # inconclusive_loops: 窗口内只有 INCONCLUSIVE 无 SUCCESS
        ic_result = await db.execute(
            select(func.count(func.distinct(KpiSnapshotHourly.loop_id)))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start,
                KpiSnapshotHourly.ts_start <= end,
                KpiSnapshotHourly.status == "INCONCLUSIVE",
                KpiSnapshotHourly.loop_id.notin_(success_loop_ids) if success_loop_ids else True,
                LoopLedger.include_in_evaluation.is_(True),
            )
        )
        inconclusive_loops = int(ic_result.scalar() or 0)

    # ========== 2. 综合评分、自控率、稳定率（每回路窗口内最新快照再平均） ==========
    async def _calc_window_metrics(s: datetime, e: datetime) -> dict:
        """取每个回路窗口内最新 SUCCESS 快照，再平均。"""
        if not loop_ids:
            return {
                "avgScore": None,
                "autoModeRate": None,
                "stabilityRate": None,
                "goodValueRate": None,
            }
        subq = (
            select(
                KpiSnapshotHourly.loop_id.label("lid"),
                func.max(KpiSnapshotHourly.ts_start).label("max_ts"),
            )
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= s,
                KpiSnapshotHourly.ts_start <= e,
                KpiSnapshotHourly.status == "SUCCESS",
            )
            .group_by(KpiSnapshotHourly.loop_id)
            .subquery()
        )
        stmt = select(
            KpiSnapshotHourly.score,
            KpiSnapshotHourly.auto_mode_rate,
            KpiSnapshotHourly.steady_rate,
            KpiSnapshotHourly.good_value_rate,
        ).join(
            subq,
            (KpiSnapshotHourly.loop_id == subq.c.lid)
            & (KpiSnapshotHourly.ts_start == subq.c.max_ts),
        )
        r = await db.execute(stmt)
        rows = r.all()
        if not rows:
            return {
                "avgScore": None,
                "autoModeRate": None,
                "stabilityRate": None,
                "goodValueRate": None,
            }
        scores = [float(x[0]) for x in rows if x[0] is not None]
        autos = [float(x[1]) for x in rows if x[1] is not None]
        stables = [float(x[2]) for x in rows if x[2] is not None]
        goods = [float(x[3]) for x in rows if x[3] is not None]
        return {
            "avgScore": round(sum(scores) / len(scores), 2) if scores else None,
            "autoModeRate": round(sum(autos) / len(autos), 2) if autos else None,
            "stabilityRate": round(sum(stables) / len(stables), 2) if stables else None,
            "goodValueRate": round(sum(goods) / len(goods), 2) if goods else None,
        }

    cur_metrics = await _calc_window_metrics(start, end)
    prev_metrics = await _calc_window_metrics(prev_start, prev_end)

    def _delta(cur: float | None, prev: float | None) -> float | None:
        if cur is None or prev is None or prev == 0:
            return None
        return round(cur - prev, 2)

    compare = {
        "scoreDelta": _delta(cur_metrics["avgScore"], prev_metrics["avgScore"]),
        "autoDelta": _delta(cur_metrics["autoModeRate"], prev_metrics["autoModeRate"]),
        "stabilityDelta": _delta(cur_metrics["stabilityRate"], prev_metrics["stabilityRate"]),
    }

    # ========== 3. 评分等级分布 ==========
    poor_loops = 0
    fair_loops = 0
    good_loops = 0
    excellent_loops = 0
    # 直接查窗口内最新 SUCCESS 快照评分
    if loop_ids:
        subq = (
            select(
                KpiSnapshotHourly.loop_id.label("lid"),
                func.max(KpiSnapshotHourly.ts_start).label("max_ts"),
            )
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start,
                KpiSnapshotHourly.ts_start <= end,
                KpiSnapshotHourly.status == "SUCCESS",
            )
            .group_by(KpiSnapshotHourly.loop_id)
            .subquery()
        )
        score_stmt = select(KpiSnapshotHourly.score).join(
            subq,
            (KpiSnapshotHourly.loop_id == subq.c.lid)
            & (KpiSnapshotHourly.ts_start == subq.c.max_ts),
        )
        score_rows = (await db.execute(score_stmt)).all()
        for (score_val,) in score_rows:
            if score_val is None:
                continue
            sv = float(score_val)
            if sv < 70:
                poor_loops += 1
            elif sv < 85:
                fair_loops += 1
            elif sv < 95:
                good_loops += 1
            else:
                excellent_loops += 1

    score_distribution = {
        "poor": poor_loops,
        "fair": fair_loops,
        "good": good_loops,
        "excellent": excellent_loops,
    }

    # ========== 4. 关注队列汇总（复用 monitor_attention 聚合逻辑） ==========
    alert_count = 0
    degradation_count = 0
    data_quality_count = 0
    tracker_count = 0
    attention_count = 0

    if loop_ids:
        # 预警事件（近24h未关闭）
        from app.models.alert import AlertEvent

        alert_result = await db.execute(
            select(func.count(func.distinct(AlertEvent.id))).where(
                AlertEvent.loop_id.in_(loop_ids),
                AlertEvent.status.in_(["ACTIVE", "ACKNOWLEDGED"]),
                AlertEvent.triggered_at
                >= datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24),
            )
        )
        alert_count = int(alert_result.scalar() or 0)

        # 性能退化：评分<70的回路（窗口内最新评分）
        degradation_count = poor_loops

        # 数据质量：窗口内 valid_rate < 80% 的回路
        dq_result = await db.execute(
            select(func.count(func.distinct(KpiSnapshotHourly.loop_id))).where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start,
                KpiSnapshotHourly.ts_start <= end,
                KpiSnapshotHourly.valid_rate < 0.80,
                KpiSnapshotHourly.status.in_(["SUCCESS", "INCONCLUSIVE"]),
            )
        )
        data_quality_count = int(dq_result.scalar() or 0)

        # MVP 精简：已屏蔽诊断/整改模块 → tracker 统计恒为 0
        # from app.models.tracker import ActionTracker
        # tracker_result = await db.execute(
        #     select(func.count(func.distinct(ActionTracker.id))).where(
        #         ActionTracker.loop_id.in_(loop_ids),
        #         ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS", "VERIFYING"]),
        #     )
        # )
        # tracker_count = int(tracker_result.scalar() or 0)
        tracker_count = 0

        # 简单去重：按回路计数有问题的回路数
        problem_loops = set()
        # 预警相关回路
        if alert_count > 0:
            alert_loops_result = await db.execute(
                select(func.distinct(AlertEvent.loop_id)).where(
                    AlertEvent.loop_id.in_(loop_ids),
                    AlertEvent.status.in_(["ACTIVE", "ACKNOWLEDGED"]),
                    AlertEvent.triggered_at
                    >= datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24),
                )
            )
            problem_loops.update(str(r[0]) for r in alert_loops_result.all())
        # 退化回路
        if poor_loops > 0:
            poor_result = await db.execute(
                select(KpiSnapshotHourly.loop_id)
                .join(
                    subq,
                    (KpiSnapshotHourly.loop_id == subq.c.lid)
                    & (KpiSnapshotHourly.ts_start == subq.c.max_ts),
                )
                .where(KpiSnapshotHourly.score < 70)
            )
            problem_loops.update(str(r[0]) for r in poor_result.all())
        # 数据质量问题回路
        if data_quality_count > 0:
            dq_loops_result = await db.execute(
                select(func.distinct(KpiSnapshotHourly.loop_id)).where(
                    KpiSnapshotHourly.loop_id.in_(loop_ids),
                    KpiSnapshotHourly.ts_start >= start,
                    KpiSnapshotHourly.ts_start <= end,
                    KpiSnapshotHourly.valid_rate < 0.80,
                    KpiSnapshotHourly.status.in_(["SUCCESS", "INCONCLUSIVE"]),
                )
            )
            problem_loops.update(str(r[0]) for r in dq_loops_result.all())
        # MVP 精简：tracker_count 恒为 0，跳过待处理跟踪项回路统计
        # if tracker_count > 0:
        #     tracker_loops_result = await db.execute(
        #         select(func.distinct(ActionTracker.loop_id)).where(
        #             ActionTracker.loop_id.in_(loop_ids),
        #             ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS", "VERIFYING"]),
        #         )
        #     )
        #     problem_loops.update(str(r[0]) for r in tracker_loops_result.all())
        attention_count = len(problem_loops)

    attention_summary = {
        "alertCount": alert_count,
        "degradationCount": degradation_count,
        "dataQualityCount": data_quality_count,
        "trackerCount": tracker_count,
        "total": attention_count,
        "pendingCount": tracker_count,
    }

    # ========== 5. 实时自控率 ==========
    auto_rate_data = None
    if loop_ids:
        auto_rate_data = await query_realtime_auto_rate(db, loop_ids)
    default_mode_counts = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0}
    if auto_rate_data and auto_rate_data.get("mode_counts"):
        default_mode_counts.update({str(k): v for k, v in auto_rate_data["mode_counts"].items()})
    auto_rate = {
        "rate": float(auto_rate_data["rate"])
        if auto_rate_data and auto_rate_data.get("rate") is not None
        else None,
        "autoCount": auto_rate_data["auto_count"] if auto_rate_data else 0,
        "manualCount": auto_rate_data["manual_count"] if auto_rate_data else 0,
        "totalCount": auto_rate_data["total_count"] if auto_rate_data else 0,
        "modeCounts": default_mode_counts,
        "readAt": auto_rate_data["read_at"] if auto_rate_data else None,
    }

    # ========== 6. 诊断建议类型分布 ==========
    # MVP 精简：已屏蔽诊断模块 → 诊断分布恒为空
    diagnosis_distribution: dict[str, int] = {}
    # if loop_ids:
    #     # 每回路取窗口内最新诊断结果
    #     diag_subq = (
    #         select(
    #             DiagnosisResult.loop_id.label("lid"),
    #             func.max(DiagnosisResult.diagnosed_at).label("max_diag_at"),
    #         )
    #         .where(
    #             DiagnosisResult.loop_id.in_(loop_ids),
    #             DiagnosisResult.diagnosed_at >= start,
    #             DiagnosisResult.diagnosed_at <= end,
    #         )
    #         .group_by(DiagnosisResult.loop_id)
    #         .subquery()
    #     )
    #     diag_stmt = select(DiagnosisResult.diag_label).join(
    #         diag_subq,
    #         (DiagnosisResult.loop_id == diag_subq.c.lid)
    #         & (DiagnosisResult.diagnosed_at == diag_subq.c.max_diag_at),
    #     )
    #     diag_rows = (await db.execute(diag_stmt)).all()
    #     for (label,) in diag_rows:
    #         if label:
    #             diagnosis_distribution[label] = diagnosis_distribution.get(label, 0) + 1

    # ========== 7. Top 10 问题回路 ==========
    top_loops: list[dict] = []
    if loop_ids:
        # 按评分升序取最差 10 个
        top_subq = (
            select(
                KpiSnapshotHourly.loop_id.label("lid"),
                func.max(KpiSnapshotHourly.ts_start).label("max_ts"),
            )
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start,
                KpiSnapshotHourly.ts_start <= end,
                KpiSnapshotHourly.status == "SUCCESS",
            )
            .group_by(KpiSnapshotHourly.loop_id)
            .subquery()
        )
        top_stmt = (
            select(
                LoopLedger.id,
                LoopLedger.tag_name,
                LoopLedger.description,
                KpiSnapshotHourly.score,
                KpiSnapshotHourly.auto_mode_rate,
                PlantNode.name.label("unit_name"),
            )
            .join(
                top_subq,
                (KpiSnapshotHourly.loop_id == top_subq.c.lid)
                & (KpiSnapshotHourly.ts_start == top_subq.c.max_ts),
            )
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .outerjoin(PlantNode, LoopLedger.unit_id == PlantNode.id)
            .order_by(KpiSnapshotHourly.score.asc().nullslast())
            .limit(10)
        )
        top_rows = (await db.execute(top_stmt)).all()
        for row in top_rows:
            top_loops.append(
                {
                    "loopId": str(row.id),
                    "tagName": row.tag_name,
                    "description": row.description,
                    "score": float(row.score) if row.score is not None else None,
                    "autoModeRate": float(row.auto_mode_rate)
                    if row.auto_mode_rate is not None
                    else None,
                    "unitName": row.unit_name,
                }
            )

    # ========== 8. 趋势数据：短窗口按小时，长窗口按天 ==========
    timestamps: list[str] = []
    score_trend: list[float | None] = []
    auto_trend: list[float | None] = []
    stability_trend: list[float | None] = []

    # 判断聚合粒度：近8h/today/yesterday按小时，近7天/近30天按天
    use_day_granularity = timeWindow in ["last_7_days", "last_30_days"]

    if loop_ids:
        if use_day_granularity:
            # 按天聚合
            day_col = func.date_trunc("day", KpiSnapshotHourly.ts_start).label("day")
            stmt = (
                select(
                    day_col,
                    func.avg(KpiSnapshotHourly.score).label("avg_score"),
                    func.avg(KpiSnapshotHourly.auto_mode_rate).label("avg_auto"),
                    func.avg(KpiSnapshotHourly.steady_rate).label("avg_stable"),
                    func.count(func.distinct(KpiSnapshotHourly.loop_id)).label("loop_count"),
                )
                .where(
                    KpiSnapshotHourly.loop_id.in_(loop_ids),
                    KpiSnapshotHourly.ts_start >= start,
                    KpiSnapshotHourly.ts_start <= end,
                    KpiSnapshotHourly.status == "SUCCESS",
                )
                .group_by(day_col)
                .order_by(day_col.asc())
            )
            rows = (await db.execute(stmt)).all()
            row_map = {r.day.strftime("%Y-%m-%d"): r for r in rows}
            current = start.replace(hour=0, minute=0, second=0, microsecond=0)
            while current <= end:
                day_key = current.strftime("%Y-%m-%d")
                timestamps.append(f"{day_key}T00:00:00")
                r = row_map.get(day_key)
                if r and r.loop_count and r.loop_count > 0:
                    score_trend.append(
                        round(float(r.avg_score), 2) if r.avg_score is not None else None
                    )
                    auto_trend.append(
                        round(float(r.avg_auto), 2) if r.avg_auto is not None else None
                    )
                    stability_trend.append(
                        round(float(r.avg_stable), 2) if r.avg_stable is not None else None
                    )
                else:
                    score_trend.append(None)
                    auto_trend.append(None)
                    stability_trend.append(None)
                current += timedelta(days=1)
        else:
            # 按小时聚合
            hour_col = func.date_trunc("hour", KpiSnapshotHourly.ts_start).label("hour")
            stmt = (
                select(
                    hour_col,
                    func.avg(KpiSnapshotHourly.score).label("avg_score"),
                    func.avg(KpiSnapshotHourly.auto_mode_rate).label("avg_auto"),
                    func.avg(KpiSnapshotHourly.steady_rate).label("avg_stable"),
                    func.count(func.distinct(KpiSnapshotHourly.loop_id)).label("loop_count"),
                )
                .where(
                    KpiSnapshotHourly.loop_id.in_(loop_ids),
                    KpiSnapshotHourly.ts_start >= start,
                    KpiSnapshotHourly.ts_start <= end,
                    KpiSnapshotHourly.status == "SUCCESS",
                )
                .group_by(hour_col)
                .order_by(hour_col.asc())
            )
            rows = (await db.execute(stmt)).all()
            row_map = {r.hour.strftime("%Y-%m-%dT%H:00:00"): r for r in rows}
            current = start.replace(minute=0, second=0, microsecond=0)
            while current <= end:
                hour_key = current.strftime("%Y-%m-%dT%H:00:00")
                timestamps.append(hour_key)
                r = row_map.get(hour_key)
                if r and r.loop_count and r.loop_count > 0:
                    score_trend.append(
                        round(float(r.avg_score), 2) if r.avg_score is not None else None
                    )
                    auto_trend.append(
                        round(float(r.avg_auto), 2) if r.avg_auto is not None else None
                    )
                    stability_trend.append(
                        round(float(r.avg_stable), 2) if r.avg_stable is not None else None
                    )
                else:
                    score_trend.append(None)
                    auto_trend.append(None)
                    stability_trend.append(None)
                current += timedelta(hours=1)

    trend = {
        "timestamps": timestamps,
        "avgScore": score_trend,
        "autoModeRate": auto_trend,
        "stabilityRate": stability_trend,
    }

    # ========== 组装响应 ==========
    summary = {
        "totalLoops": total_loops_count,
        "evaluatedLoops": evaluated_loops,
        "inconclusiveLoops": inconclusive_loops,
        "excludedLoops": excluded_loops,
        "avgScore": cur_metrics["avgScore"],
        "autoModeRate": cur_metrics["autoModeRate"],
        "stabilityRate": cur_metrics["stabilityRate"],
        "attentionCount": attention_count,
        "pendingTrackerCount": tracker_count,
    }

    # P2 IA优化：L0~L4 适用性分布
    fitness_distribution: dict[str, int] = {
        "L0": 0,
        "L1": 0,
        "L2": 0,
        "L3": 0,
        "L4": 0,
    }
    if loop_ids:
        try:
            subq_latest = (
                select(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.fitness_level)
                .distinct(KpiSnapshotHourly.loop_id)
                .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_start.desc())
            ).where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start,
                KpiSnapshotHourly.ts_start <= end,
            )
            subquery = subq_latest.subquery()
            stmt = (
                select(subquery.c.fitness_level, func.count())
                .select_from(subquery)
                .group_by(subquery.c.fitness_level)
            )
            rows = (await db.execute(stmt)).all()
            for lvl, cnt in rows:
                if lvl in fitness_distribution:
                    fitness_distribution[lvl] = int(cnt or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("system-overview fitness 分布计算失败，已忽略: %s", exc)

    return success(
        data={
            "summary": summary,
            "scoreDistribution": score_distribution,
            "fitnessDistribution": fitness_distribution,
            "attentionSummary": attention_summary,
            "autoRate": auto_rate,
            "diagnosisDistribution": diagnosis_distribution,
            "topLoops": top_loops,
            "trend": trend,
            "compare": compare,
            "timeWindow": timeWindow,
            "windowStart": start.isoformat(),
            "windowEnd": end.isoformat(),
        }
    )
