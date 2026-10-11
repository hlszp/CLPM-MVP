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
- GET    /api/v1/performance/loops/result-records/{recordId} — 结果账本记录详情
    （P1-05：按不可变 recordId 读取；旧快照 ID 经迁移映射兼容）
- GET    /api/v1/performance/grade-distribution — 各性能等级回路数分布（SQL 聚合）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.timeparse import parse_iso_datetime, to_naive_utc
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
    MetricSeriesData,
    MetricSeriesItem,
    MetricSeriesPoint,
    RankingItem,
    SnapshotBatchDeleteRequest,
)
from app.services import result_ledger
from app.services.gate_overview import get_gate_overview
from app.services.performance import (
    SNAPSHOT_SORT_COLUMNS,
    export_analytics_csv,
    get_analytics,
    get_grade_distribution,
    get_loop_metric_series,
    get_ranking,
    get_valve_alerts,
    list_engine_rules,
    list_loop_snapshots,
    list_metric_configs,
    update_engine_rule,
    update_metric_config,
)

router = APIRouter(prefix="/performance", tags=["performance"])

# 跨表归并排序兜底（ts_start 为 None 的行恒排末尾）
_FLOOR_DT = datetime(1970, 1, 1)


@router.get("/gate-overview", response_model=ApiResponse[dict])
async def get_gate_overview_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """评估数据门禁健康总览（0921 监控面板数据源）.

    latest-per-loop 口径：断点比例分布（vs 30% 门槛）、门禁失败原因榜、
    无快照回路数、断点比例 TOP 回路榜——回答"哪些回路为何没有评估得分"。
    """
    data = await get_gate_overview(db)
    return success(data=data)


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
    fitnessFilter: bool = Query(
        False,
        description="适用性过滤：服务端先剔除最新快照为 L0/L1 的回路再排序截断"
        "（客户端过滤在 L0/L1 回路数 ≥limit 时会把榜单滤空）",
    ),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """低效回路排行（所有角色）。

    性能 #12：新增 ``offset`` 参数支持前端循环分页拉全量，
    解决 >100 回路时等级占比饼图少计的问题。
    """

    def _parse_dt(s: str | None) -> datetime | None:
        # 空值视为未指定；非空但非法的时间串由 parse_iso_datetime 抛 400
        if not s:
            return None
        return to_naive_utc(parse_iso_datetime(s, field="startTime/endTime"))

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
        exclude_unfit=fitnessFilter,
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
    """解析 ISO 8601 时间字符串（兼容 Z 后缀）.

    带时区的输入先换算到 UTC 再去掉时区标记（DB 字段为 UTC naive）；
    无时区输入按 UTC 解释（历史行为）。

    空值返回 None（未指定）；非空但非法的输入抛 400（旧实现静默返回 None，
    会让前端以为"筛选生效了"却拿到默认窗口的数据）。
    """
    if not s:
        return None
    return to_naive_utc(parse_iso_datetime(s, field="时间参数"))


def _to_float(val) -> float | None:
    """Decimal/float/None → float | None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fitness_tags_list(val) -> list[str] | None:
    """fitness_tags JSONB → list[str] | None（IA-06，2026-10-10）.

    存量写入形如 ``{"tags": [...]}``（tasks/kpi_calc.py）；旧 list 直存或
    非法形状返回 None（不虚构标签），归一口径同 services/loop_fitness.py。
    """
    if isinstance(val, dict):
        tags = val.get("tags")
        if isinstance(tags, list):
            return [str(x) for x in tags]
        return None
    if isinstance(val, list):
        return [str(x) for x in val]
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
    status: str | None = Query(
        None,
        description="快照状态（SUCCESS/INCONCLUSIVE/PARTIAL，支持逗号分隔多值）",
    ),
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


@router.get("/valve-alerts", response_model=ApiResponse[dict])
async def get_valve_alerts_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选（递归子树）"),
    timeWindow: str = Query(
        "today",
        description="时间窗：today/last_8_hours/last_24_hours/last_7_days/last_30_days/custom",
    ),
    startTime: str | None = Query(None, description="自定义窗口起始（ISO 8601，custom 时必填）"),
    endTime: str | None = Query(None, description="自定义窗口结束（ISO 8601，custom 时必填）"),
    limit: int = Query(10, ge=1, le=100, description="返回条数（默认 10，最多 100）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """阀门运行区间异常回路 TOP N（OP 行程越限 5%~95%，所有角色）.

    窗口内每回路最新一条快照判定，按越限严重度（贴边深度）降序；
    返回 ``{"total": 越限回路总数, "items": [前 limit 条]}``。
    2026-10-03 评估总览改版：替代前端全量翻页拉快照的客户端聚合。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    if timeWindow == "custom" and startTime and endTime:
        start = to_naive_utc(parse_iso_datetime(startTime, field="startTime"))
        end = to_naive_utc(parse_iso_datetime(endTime, field="endTime"))
    else:
        delta = {
            "last_8_hours": timedelta(hours=8),
            "last_24_hours": timedelta(hours=24),
            "last_7_days": timedelta(days=7),
            "last_30_days": timedelta(days=30),
        }.get(timeWindow, timedelta(days=1))
        start, end = now - delta, now
    data = await get_valve_alerts(
        db=db,
        plant_node_id=plantNodeId,
        start=start,
        end=end,
        limit=limit,
    )
    return success(data=data)


@router.get("/loops/snapshots", response_model=ApiResponse[KpiSnapshotListData])
async def list_loop_snapshots_endpoint(
    loopId: str | None = Query(None, description="回路 ID（逗号分隔多个）"),
    plantNodeId: str | None = Query(None, description="装置 ID（逗号分隔多个）"),
    startTime: str | None = Query(None, description="起始时间（ISO 8601）"),
    endTime: str | None = Query(None, description="结束时间（ISO 8601）"),
    status: str | None = Query(
        None,
        description="快照状态（SUCCESS/INCONCLUSIVE/PARTIAL，支持逗号分隔多值）",
    ),
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
    source: str | None = Query(
        None,
        description="评估来源筛选（SCHEDULED/MANUAL_STANDARD/MANUAL_CUSTOM/BACKFILL，"
        "逗号分隔多值）；MANUAL_CUSTOM 走自定义任务快照表",
    ),
    taskId: str | None = Query(
        None,
        description="自定义评估任务 ID：指定后列表切换为该任务的自定义快照"
        "（整合方案 B4：手动评估结果可浏览）",
    ),
    includeCustom: bool = Query(
        False,
        description="（G2 关闭，2026-10-03）True 且 latestOnly=False 时，合并"
        " kpi_snapshot_custom 手动评估记录（按 tsStart DESC 跨表归并分页，"
        "手动行 source=MANUAL_CUSTOM）；latestOnly=True 时忽略本参数",
    ),
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

    wants_custom = bool(taskId) or (
        bool(source) and {x.strip().upper() for x in source.split(",")} == {"MANUAL_CUSTOM"}
    )
    if wants_custom:
        # 整合方案 B4：自定义评估任务快照（手动评估结果首次可列表浏览）
        from app.services.performance import list_custom_snapshots

        rows, total = await list_custom_snapshots(
            db=db,
            task_id=taskId,
            loop_ids=loop_ids,
            plant_node_ids=plant_node_ids,
            start=start_dt,
            end=end_dt,
            status_filter=status,
            page=page,
            page_size=pageSize,
        )
    else:
        # G2 关闭（2026-10-03）：includeCustom 且历史模式时跨表合并手动评估记录——
        # 两表各取前 page*pageSize 条，按 ts_start DESC 归并后切当前页；total=两表之和。
        # latestOnly/grade 场景不合并（最新快照与等级分布保持小时表口径，custom 不参与聚合）。
        merge_custom = includeCustom and not latestOnly
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
            page=1 if merge_custom else page,
            page_size=page * pageSize if merge_custom else pageSize,
            sort_by=sortBy if sortBy in SNAPSHOT_SORT_COLUMNS else None,
            sort_order=sortOrder if sortOrder in ("asc", "desc") else None,
            source_filter=source,
        )
        if merge_custom:
            from app.services.performance import list_custom_snapshots as _list_custom

            custom_rows, custom_total = await _list_custom(
                db=db,
                task_id=None,
                loop_ids=loop_ids,
                plant_node_ids=plant_node_ids,
                start=start_dt,
                end=end_dt,
                status_filter=status,
                page=1,
                page_size=page * pageSize,
            )
            merged = sorted(
                [*rows, *custom_rows],
                key=lambda pair: pair[0].ts_start or _FLOOR_DT,
                reverse=True,
            )
            rows = merged[(page - 1) * pageSize : page * pageSize]
            total = total + custom_total

    # 组装响应
    items: list[KpiSnapshotListItem] = []
    for snap, tag_name in rows:
        # 来源标注（整合方案 B3）：非字符串（测试 MagicMock 等）置 None
        from app.models.metric import KpiSnapshotCustom as _KpiSnapshotCustom

        _source = getattr(snap, "source", None)
        _source_task_id = getattr(snap, "source_task_id", None)
        _source = _source if isinstance(_source, str) else None
        _source_task_id = _source_task_id if isinstance(_source_task_id, str) else None
        if isinstance(snap, _KpiSnapshotCustom):
            # custom 表无 source 列；task_id 即来源任务
            _source = "MANUAL_CUSTOM"
            _source_task_id = _source_task_id or (
                str(snap.task_id) if getattr(snap, "task_id", None) else None
            )
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
                source=_source,
                sourceTaskId=_source_task_id,
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
                # IA-06（2026-10-10）：适用性权威接线——快照行 fitness 列填充。
                # fitness_level 直读（String(2)，L0~L4，旧快照 NULL 透传）；
                # fitness_tags JSONB 形如 {"tags": [...]}（loop_fitness 同款归一）
                fitnessLevel=(snap.fitness_level if isinstance(snap.fitness_level, str) else None),
                fitnessTags=_fitness_tags_list(snap.fitness_tags),
            )
        )

    data = KpiSnapshotListData(
        items=items,
        total=total,
        page=page,
        pageSize=pageSize,
    )
    return success(data=data)


@router.get("/loops/metric-series", response_model=ApiResponse[MetricSeriesData])
async def get_loop_metric_series_endpoint(
    loopIds: str = Query(..., description="回路 ID 列表（逗号分隔，≤10 个）"),
    metricKey: str = Query(..., description="指标键（snake_case，服务端白名单校验）"),
    startTime: str | None = Query(None, description="起始时间（ISO 8601）"),
    endTime: str | None = Query(None, description="结束时间（ISO 8601）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """批量查询单指标 × 多回路时间序列（指标矩阵页列头趋势对比）.

    数据源为 kpi_snapshot_hourly（小时粒度）。metricKey 白名单：
    score/accuracy_rate/auto_mode_rate/effective_auto_rate/fast_rate/steady_rate/
    good_value_rate/oscillation_rate/saturation_rate/instrument_fault_rate/
    stiction_index/settling_time/output_trip_index。

    Raises:
        BizError: ERR_METRIC_SERIES_INVALID（白名单外指标键）/
            ERR_METRIC_SERIES_LOOPS（回路数超限或为空）
    """
    loop_ids = [s.strip() for s in loopIds.split(",") if s.strip()]
    start_dt = _parse_dt(startTime)
    end_dt = _parse_dt(endTime)

    rows = await get_loop_metric_series(
        db=db,
        loop_ids=loop_ids,
        metric_key=metricKey,
        start=start_dt,
        end=end_dt,
    )

    series = [
        MetricSeriesItem(
            loopId=row["loop_id"],
            loopTagName=row["tag_name"],
            points=[MetricSeriesPoint(**p) for p in row["points"]],
        )
        for row in rows
    ]
    data = MetricSeriesData(metricKey=metricKey, series=series)
    return success(data=data)


@router.get("/loops/snapshots/window-agg", response_model=ApiResponse[dict])
async def window_agg_snapshots_endpoint(
    startTime: str = Query(..., description="窗口起始（ISO 8601）"),
    endTime: str = Query(..., description="窗口结束（ISO 8601）"),
    plantNodeId: str | None = Query(None),
    loopId: str | None = Query(None, description="回路 ID（逗号分隔多值）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """回路级窗口聚合（均值）快照——指标矩阵 8h/24h/168h 时间窗口径（2026-10-10）.

    latestOnly 在快照逐小时产生时任意窗口"最新"均为同一条（切换无变化），
    本端点给出窗口代表值：每回路窗口内均值（AVG 跳过 NULL）。
    """
    from app.services.performance import window_agg_snapshots

    loop_ids = [x.strip() for x in loopId.split(",") if x.strip()] if loopId else None
    items = await window_agg_snapshots(
        db,
        loop_ids=loop_ids,
        plant_node_ids=[plantNodeId] if plantNodeId else None,
        start=_parse_iso_arg(startTime),
        end=_parse_iso_arg(endTime),
    )
    return success(data={"items": items, "total": len(items)})


# ---------------------------------------------------------------------------
# 历史快照删除（2026-10-10：按筛选批量 + 单条）
# ---------------------------------------------------------------------------


def _parse_iso_arg(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


@router.post(
    "/loops/snapshots/batch-delete",
    response_model=ApiResponse[dict],
)
async def batch_delete_snapshots_endpoint(
    body: SnapshotBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """按筛选条件批量删除历史快照（dryRun 默认 True 仅预览计数）。

    与列表查询同口径（含停用回路历史快照）；至少指定 回路/装置/时间范围
    之一，无约束全表删除将被拒绝。
    """
    from app.services.performance import batch_delete_snapshots

    try:
        data = await batch_delete_snapshots(
            db,
            loop_ids=body.loopIds,
            plant_node_id=body.plantNodeId,
            start=_parse_iso_arg(body.startTime),
            end=_parse_iso_arg(body.endTime),
            status=body.status,
            source=body.source,
            dry_run=body.dryRun,
        )
    except ValueError as exc:
        raise BizError(
            code="ERR_SNAPSHOT_DELETE_UNCONSTRAINED",
            message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc
    return success(
        data=data,
        message=(
            f"匹配 {data['matched']} 条"
            + ("（预览，未删除）" if data["dryRun"] else f"，已删除 {data['deleted']} 条")
        ),
    )


@router.get(
    "/loops/result-records/{record_id}",
    response_model=ApiResponse[dict],
)
async def get_result_record_endpoint(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """结果账本记录详情（P1-05，所有角色可查看）.

    历史详情按不可变 recordId 读取；传入旧投影行 ID（kpi_snapshot_hourly /
    loop_confidence_latest / kpi_node_snapshot_hourly 的 id）时经迁移归档
    映射兼容解析。payload 含完整结果（节点记录另含 loopRecordIds 与
    各指标分母）；datasetSnapshotId 为空即"该结果不可完整复现"（P2-03 前
    的 P1 记录均如此，属显式事实而非缺数）。
    """
    record, matched_via = await result_ledger.find_record_any_id(db, record_id)
    if record is None:
        raise BizError(
            code="ERR_RESULT_RECORD_NOT_FOUND",
            message=f"结果记录 {record_id} 不存在（既非 recordId 也无法按旧 ID 映射）",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    data = result_ledger.record_to_dict(record)
    data["matchedVia"] = matched_via
    return success(data=data)


@router.delete(
    "/loops/snapshots/{snapshot_id}",
    response_model=ApiResponse[dict],
)
async def delete_snapshot_endpoint(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """删除单条历史快照。"""
    from app.services.performance import delete_snapshot_by_id

    deleted = await delete_snapshot_by_id(db, snapshot_id)
    if not deleted:
        raise BizError(
            code="ERR_SNAPSHOT_NOT_FOUND",
            message=f"快照 {snapshot_id} 不存在或已删除",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return success(data={"deleted": True}, message="快照已删除")


__all__ = ["router"]
