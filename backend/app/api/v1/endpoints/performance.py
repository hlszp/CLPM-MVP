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
"""

from __future__ import annotations

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
    MetricConfigItem,
    MetricConfigUpdate,
    RankingItem,
)
from app.services.performance import (
    export_analytics_csv,
    get_analytics,
    get_board,
    get_ranking,
    list_engine_rules,
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
        "today", description="时间窗：today/yesterday/last_7_days/last_30_days"
    ),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """全局看板（所有角色）。Redis 缓存 5 分钟。"""
    data = await get_board(
        db=db,
        plant_node_id=plantNodeId,
        time_window=timeWindow,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# S3-METRIC-005: 低效回路排行 API
# ---------------------------------------------------------------------------


@router.get("/ranking", response_model=ApiResponse[list[RankingItem]])
async def get_ranking_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    timeWindow: str = Query(
        "today", description="时间窗：today/yesterday/last_7_days/last_30_days"
    ),
    limit: int = Query(20, ge=1, le=100, description="返回条数（最多 100）"),
    sortBy: str = Query("score", description="排序字段：score/steady_rate/good_value_rate"),
    sortOrder: str = Query("asc", description="排序方向：asc/desc"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """低效回路排行（所有角色）。"""
    data = await get_ranking(
        db=db,
        plant_node_id=plantNodeId,
        time_window=timeWindow,
        limit=limit,
        sort_by=sortBy,
        sort_order=sortOrder,
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


__all__ = ["router"]
