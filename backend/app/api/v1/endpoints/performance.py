"""Performance evaluation endpoints (IDS v3.2 §2.3 — S3-METRIC-001~006).

路由清单：
- GET    /api/v1/performance/metrics            — 获取 6 大 KPI 配置列表
- PUT    /api/v1/performance/metrics/{metricId} — 更新指标配置（仅 ADMIN）
- GET    /api/v1/performance/rules              — 获取引擎规则列表
- PUT    /api/v1/performance/rules/{ruleId}     — 更新引擎规则（仅 ADMIN）
- GET    /api/v1/performance/board              — 全局看板
- GET    /api/v1/performance/ranking            — 低效回路排行
- GET    /api/v1/performance/analytics          — 统计报表数据
- POST   /api/v1/performance/analytics/export   — 导出报表（CSV）
- GET    /api/v1/performance/loops/snapshots    — 回路小时指标快照列表
- GET    /api/v1/performance/grade-distribution — 各性能等级回路数分布（SQL 聚合）
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.performance import (
    AnalyticsData,
    EngineRuleItem,
    EngineRuleUpdate,
    ExportRequest,
    KpiSnapshotListData,
    KpiSnapshotListItem,
    MetricConfigItem,
    MetricConfigUpdate,
    RankingItem,
)
from app.services.performance import (
    SNAPSHOT_SORT_COLUMNS,
    export_analytics_csv,
    get_analytics,
    get_board,
    get_grade_distribution,
    get_ranking,
    list_engine_rules,
    list_loop_snapshots,
    list_metric_configs,
    update_engine_rule,
    update_metric_config,
)

router = APIRouter(prefix="/performance", tags=["performance"])


# ---------------------------------------------------------------------------
# S3-METRIC-001: 指标配置 API
# ---------------------------------------------------------------------------


@router.get("/metrics", response_model=ApiResponse[list[MetricConfigItem]])
async def list_metrics_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取 6 大 KPI 指标配置列表（所有角色可查看）。"""
    data = await list_metric_configs(db)
    return success(data=data)


@router.put("/metrics/{metric_id}", response_model=ApiResponse[MetricConfigItem])
async def update_metric_endpoint(
    metric_id: str,
    body: MetricConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新指标配置（仅 ADMIN）。

    校验：6 大 KPI 启用指标权重总和必须为 100，否则返回 ERR_METRIC_WEIGHT_SUM。
    """
    data = await update_metric_config(
        db=db,
        metric_id=metric_id,
        operator=user.username,
        metric_name=body.metricName,
        formula=body.formula,
        weight=body.weight,
        threshold=body.threshold,
        control_type=body.controlType,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="更新成功")


# ---------------------------------------------------------------------------
# S3-METRIC-002: 引擎规则配置 API
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=ApiResponse[list[EngineRuleItem]])
async def list_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取引擎规则列表（所有角色可查看）。"""
    data = await list_engine_rules(db)
    return success(data=data)


@router.put("/rules/{rule_id}", response_model=ApiResponse[EngineRuleItem])
async def update_rule_endpoint(
    rule_id: str,
    body: EngineRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新引擎规则（仅 ADMIN）。"""
    data = await update_engine_rule(
        db=db,
        rule_id=rule_id,
        operator=user.username,
        rule_name=body.ruleName,
        params=body.params,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="更新成功")


# ---------------------------------------------------------------------------
# S3-METRIC-004: 全局看板 API
# ---------------------------------------------------------------------------


@router.get("/board", response_model=ApiResponse[dict])
async def get_board_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    timeWindow: str = Query(
        "today",
        description="时间窗：today/yesterday/last_8_hours/last_24_hours/"
        "last_72_hours/last_168_hours/last_7_days/last_30_days/custom",
    ),
    startTime: str | None = Query(None, description="自定义窗口起始（ISO 8601，custom 时必填）"),
    endTime: str | None = Query(None, description="自定义窗口结束（ISO 8601，custom 时必填）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """全局看板（所有角色）。Redis 缓存 5 分钟。"""

    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.fromisoformat(s)

    data = await get_board(
        db=db,
        plant_node_id=plantNodeId,
        time_window=timeWindow,
        start_time=_parse_dt(startTime),
        end_time=_parse_dt(endTime),
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# S3-METRIC-005: 低效回路排行 API
# ---------------------------------------------------------------------------


@router.get("/ranking", response_model=ApiResponse[list[RankingItem]])
async def get_ranking_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    timeWindow: str = Query(
        "today",
        description="时间窗：today/yesterday/last_8_hours/last_24_hours/"
        "last_72_hours/last_168_hours/last_7_days/last_30_days/custom",
    ),
    startTime: str | None = Query(None, description="自定义窗口起始（ISO 8601，custom 时必填）"),
    endTime: str | None = Query(None, description="自定义窗口结束（ISO 8601，custom 时必填）"),
    limit: int = Query(20, ge=1, le=100, description="返回条数（最多 100）"),
    offset: int = Query(0, ge=0, description="偏移量（配合 limit 实现分页拉全量）"),
    sortBy: str = Query(
        "score",
        description="排序字段：score/accuracy_rate/auto_mode_rate/effective_auto_rate/"
        "steady_rate/good_value_rate/fast_rate（非法值回退 score）",
    ),
    sortOrder: str = Query("asc", description="排序方向：asc/desc"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """低效回路排行（所有角色）。

    性能 #12：新增 ``offset`` 参数支持前端循环分页拉全量，
    解决 >100 回路时等级占比饼图少计的问题。
    """

    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.fromisoformat(s)

    data = await get_ranking(
        db=db,
        plant_node_id=plantNodeId,
        time_window=timeWindow,
        limit=limit,
        offset=offset,
        sort_by=sortBy,
        sort_order=sortOrder,
        start_time=_parse_dt(startTime),
        end_time=_parse_dt(endTime),
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# S3-METRIC-006: 性能统计报表 API
# ---------------------------------------------------------------------------


@router.get("/analytics", response_model=ApiResponse[AnalyticsData])
async def get_analytics_endpoint(
    startTime: str = Query(..., description="开始时间（ISO 8601）"),
    endTime: str = Query(..., description="结束时间（ISO 8601）"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    metricKey: str = Query("score", description="指标键：score/good_value_rate/..."),
    granularity: str = Query("day", description="粒度：hour/day/week/month"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """性能统计报表数据（所有角色）。"""
    data = await get_analytics(
        db=db,
        start_time=startTime,
        end_time=endTime,
        plant_node_id=plantNodeId,
        metric_key=metricKey,
        granularity=granularity,
    )
    return success(data=data)


@router.post("/analytics/export", response_class=PlainTextResponse)
async def export_analytics_endpoint(
    body: ExportRequest,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> PlainTextResponse:
    """导出统计报表为 CSV（所有角色）。"""
    csv_content = await export_analytics_csv(
        db=db,
        start_time=body.startTime,
        end_time=body.endTime,
        plant_node_id=body.plantNodeId,
        metric_key=body.metricKey,
        granularity=body.granularity,
    )
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=performance_analytics.csv"},
    )


# ---------------------------------------------------------------------------
# 实时自控率 — 仪表盘组件
# ---------------------------------------------------------------------------


@router.get("/realtime-auto-rate", response_model=ApiResponse[dict])
async def get_realtime_auto_rate_endpoint(
    plantNodeId: str | None = Query(None, description="工厂节点 ID（不传则全厂）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> ApiResponse[dict]:
    """获取实时自控率统计（用于仪表盘组件）。

    返回: {plantNodeId, plantNodeName, autoCount, manualCount, totalCount, autoRate, readAt}
    """
    from sqlalchemy import select

    from app.models.loop import LoopLedger
    from app.models.plant_node import PlantNode
    from app.services.node_performance import query_realtime_auto_rate

    # 收集回路 ID
    stmt = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
    plant_node_name = None
    if plantNodeId:
        # 递归获取子孙节点
        from app.services.monitor import _get_descendant_node_ids

        all_ids = await _get_descendant_node_ids(db, plantNodeId)
        all_ids.append(plantNodeId)
        stmt = stmt.where(LoopLedger.unit_id.in_(all_ids))
        # 查节点名
        node_result = await db.execute(select(PlantNode.name).where(PlantNode.id == plantNodeId))
        row = node_result.first()
        if row:
            plant_node_name = row[0]

    result = await db.execute(stmt)
    loop_ids = [str(r[0]) for r in result.all()]

    # 查询实时自控率
    rate_data = await query_realtime_auto_rate(db, loop_ids)

    if rate_data is None:
        data = {
            "plantNodeId": plantNodeId,
            "plantNodeName": plant_node_name,
            "autoCount": 0,
            "manualCount": 0,
            "totalCount": 0,
            "autoRate": 0,
            "readAt": None,
        }
    else:
        data = {
            "plantNodeId": plantNodeId,
            "plantNodeName": plant_node_name,
            "autoCount": rate_data["auto_count"],
            "manualCount": rate_data["manual_count"],
            "totalCount": rate_data["total_count"],
            "autoRate": float(rate_data["rate"]),
            "readAt": rate_data["read_at"],
        }

    return success(data=data)


# ---------------------------------------------------------------------------
# 回路小时指标快照列表
# ---------------------------------------------------------------------------


def _parse_dt(s: str | None) -> datetime | None:
    """解析 ISO 8601 时间字符串（兼容 Z 后缀），失败返回 None.

    带时区的输入先换算到 UTC 再去掉时区标记（DB 字段为 UTC naive）；
    无时区输入按 UTC 解释（历史行为）。
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _to_float(val) -> float | None:
    """Decimal/float/None → float | None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 各性能等级回路数分布（Phase 4 性能项：替代前端全量拉取客户端统计）
# ---------------------------------------------------------------------------


@router.get("/grade-distribution", response_model=ApiResponse[dict])
async def get_grade_distribution_endpoint(
    loopId: str | None = Query(None, description="回路 ID（逗号分隔多个）"),
    plantNodeId: str | None = Query(None, description="装置 ID（逗号分隔多个）"),
    startTime: str | None = Query(None, description="起始时间（ISO 8601）"),
    endTime: str | None = Query(None, description="结束时间（ISO 8601）"),
    status: str | None = Query(None, description="快照状态（SUCCESS/INCONCLUSIVE/PARTIAL）"),
    confidenceLevel: str | None = Query(None, description="可信度等级（A/B/C/D/E）"),
    loopTagName: str | None = Query(None, description="回路编号模糊搜索"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """各性能等级回路数分布（所有角色）.

    每回路取最新一条快照（口径同 /loops/snapshots 默认 latestOnly=True），
    SQL 层 GROUP BY 等级聚合，返回
    {EXCELLENT, GOOD, FAIR, WARNING, POOR, INCONCLUSIVE, total}。
    等级判定使用当前生效的定级阈值（/configs/grading-thresholds）。
    """
    loop_ids = [s.strip() for s in loopId.split(",") if s.strip()] if loopId else None
    plant_node_ids = (
        [s.strip() for s in plantNodeId.split(",") if s.strip()] if plantNodeId else None
    )

    data = await get_grade_distribution(
        db=db,
        loop_ids=loop_ids,
        plant_node_ids=plant_node_ids,
        start=_parse_dt(startTime),
        end=_parse_dt(endTime),
        status_filter=status,
        confidence_level=confidenceLevel,
        loop_tag_name=loopTagName,
    )
    return success(data=data)


@router.get("/loops/snapshots", response_model=ApiResponse[KpiSnapshotListData])
async def list_loop_snapshots_endpoint(
    loopId: str | None = Query(None, description="回路 ID（逗号分隔多个）"),
    plantNodeId: str | None = Query(None, description="装置 ID（逗号分隔多个）"),
    startTime: str | None = Query(None, description="起始时间（ISO 8601）"),
    endTime: str | None = Query(None, description="结束时间（ISO 8601）"),
    status: str | None = Query(None, description="快照状态（SUCCESS/INCONCLUSIVE/PARTIAL）"),
    confidenceLevel: str | None = Query(None, description="可信度等级（A/B/C/D/E）"),
    loopTagName: str | None = Query(None, description="回路编号模糊搜索"),
    grade: str | None = Query(
        None,
        description="性能等级筛选（EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE），"
        "服务端按当前定级阈值过滤；不传则行为不变",
    ),
    latestOnly: bool = Query(
        True,
        description="True=每个回路只返回最新一条评估记录（默认）；"
        "False=返回所有快照（历史趋势/诊断历史用）",
    ),
    sortBy: str | None = Query(
        None,
        description="排序字段（默认 tsStart；可选 score/accuracy_rate/auto_mode_rate/"
        "effective_auto_rate/fast_rate/steady_rate/good_value_rate，非法值回退默认）",
    ),
    sortOrder: str | None = Query(None, description="排序方向（asc/desc，默认 desc）"),
    page: int = Query(1, ge=1, description="页码（1-based）"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询回路小时指标快照列表（所有角色可查看）.

    按回路 ID / 装置 / 时间范围 / 状态 / 可信度 / 回路编号 / 性能等级筛选，分页返回。
    默认 latestOnly=True：每个回路只返回最新一条评估记录。
    默认排序按 tsStart DESC；sortBy 可取 SNAPSHOT_SORT_COLUMNS 白名单内字段
    （score/accuracy_rate/auto_mode_rate/effective_auto_rate/fast_rate/steady_rate/
    good_value_rate；NULL 置末位，次排序 tsStart DESC，指标分析页 M3 联动扩展）。
    每条记录包含完整的 24 个 KPI 字段 + loopTagName。
    grade 参数（Phase 4 性能项）：服务端按当前定级阈值过滤等级，
    替代前端"全量拉取→客户端过滤→客户端分页"。
    """
    # 解析逗号分隔的 ID 列表
    loop_ids = [s.strip() for s in loopId.split(",") if s.strip()] if loopId else None
    plant_node_ids = (
        [s.strip() for s in plantNodeId.split(",") if s.strip()] if plantNodeId else None
    )

    start_dt = _parse_dt(startTime)
    end_dt = _parse_dt(endTime)

    rows, total = await list_loop_snapshots(
        db=db,
        loop_ids=loop_ids,
        plant_node_ids=plant_node_ids,
        start=start_dt,
        end=end_dt,
        status_filter=status,
        confidence_level=confidenceLevel,
        loop_tag_name=loopTagName,
        grade=grade,
        latest_only=latestOnly,
        page=page,
        page_size=pageSize,
        sort_by=sortBy if sortBy in SNAPSHOT_SORT_COLUMNS else None,
        sort_order=sortOrder if sortOrder in ("asc", "desc") else None,
    )

    # 组装响应
    items: list[KpiSnapshotListItem] = []
    for snap, tag_name in rows:
        from app.schemas.performance import DataLineageSchema

        data_lineage = None
        if snap.data_lineage:
            try:
                if isinstance(snap.data_lineage, dict):
                    data_lineage = DataLineageSchema(**snap.data_lineage)
                else:
                    data_lineage = DataLineageSchema(**snap.data_lineage)
            except (TypeError, ValueError):
                data_lineage = None

        items.append(
            KpiSnapshotListItem(
                loopId=str(snap.loop_id) if snap.loop_id else None,
                loopTagName=tag_name,
                tsStart=snap.ts_start.isoformat() if snap.ts_start else None,
                tsEnd=snap.ts_end.isoformat() if snap.ts_end else None,
                score=_to_float(snap.score),
                goodValueRate=_to_float(snap.good_value_rate),
                autoModeRate=_to_float(snap.auto_mode_rate),
                effectiveAutoRate=_to_float(snap.effective_auto_rate),
                steadyRate=_to_float(snap.steady_rate),
                accuracyRate=_to_float(snap.accuracy_rate),
                oscillationRate=_to_float(snap.oscillation_rate),
                saturationRate=_to_float(snap.saturation_rate),
                instrumentFaultRate=_to_float(snap.instrument_fault_rate),
                fastRate=_to_float(snap.fast_rate),
                stictionIndex=_to_float(snap.stiction_index),
                settlingTime=_to_float(snap.settling_time),
                outputTravelIndex=_to_float(snap.output_trip_index),
                status=snap.status or "INCONCLUSIVE",
                idealSettlingTime=_to_float(snap.ideal_settling_time),
                algorithmVersion=snap.algorithm_version,
                samplingFreq=snap.sampling_freq,
                qualityPolicy=snap.quality_policy,
                validRate=_to_float(snap.valid_rate),
                confidenceLevel=snap.confidence_level,
                dataLineage=data_lineage,
                # Phase 1 新增指标
                pvMean=_to_float(snap.pv_mean),
                pvStd=_to_float(snap.pv_std),
                spMean=_to_float(snap.sp_mean),
                spStd=_to_float(snap.sp_std),
                opMean=_to_float(snap.op_mean),
                opStd=_to_float(snap.op_std),
                valveLinearity=_to_float(snap.valve_linearity),
                valveNonlinearity=_to_float(snap.valve_nonlinearity),
                valveOpMin=_to_float(snap.valve_op_min),
                valveOpMax=_to_float(snap.valve_op_max),
                oscillationAmplitude=_to_float(snap.oscillation_amplitude),
                setpointCrossingCount=(
                    int(snap.setpoint_crossing_count)
                    if snap.setpoint_crossing_count is not None
                    else None
                ),
                # F5：时间常数（秒，激励不足窗口为 None）
                timeConstant=_to_float(snap.time_constant),
            )
        )

    data = KpiSnapshotListData(
        items=items,
        total=total,
        page=page,
        pageSize=pageSize,
    )
    return success(data=data)


__all__ = ["router"]
