"""异常预测与提前预警服务（P3-05）.

基于历史 KPI 快照趋势，预测未来 24 小时可能出问题的回路。

算法：
1. 对每个活跃回路取最近 7 天 ``kpi_snapshot_hourly``（status=SUCCESS）数据
2. 对关键指标计算最小二乘线性回归斜率：
   - ``score`` 下降 → 性能退化风险
   - ``oscillation_rate`` 上升 → 振荡加剧风险
   - ``saturation_rate`` 上升 → 饱和加剧风险
   - ``steady_rate`` 下降 → 平稳性下降风险
3. 结合斜率方向、斜率幅度、当前值与告警阈值的距离，计算综合风险分（0~100）
4. 按风险分降序排列，返回 Top N 高风险回路

设计依据：PRD §4.1, 实现契约 v2.4, IA 整改任务清单 P3-05
验收标准：预测预警卡片，准确率 >70%
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import LoopLedger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

# 历史数据窗口
PREDICTION_WINDOW_DAYS = 7

# 预测时间跨度（未来 24h）
FORECAST_HORIZON_HOURS = 24

# 最少数据点数（不足则跳过预测）
MIN_DATA_POINTS = 12  # 7 天 × 24h = 168，但实际可能有缺失；12 点约半天数据

# 返回的高风险回路数
TOP_N_RISK_LOOPS = 10

# 风险等级阈值
RISK_LEVEL_HIGH = 60.0  # ≥60 → HIGH
RISK_LEVEL_MEDIUM = 30.0  # ≥30 → MEDIUM

# 各指标的斜率权重（综合风险分计算用）
WEIGHT_SCORE_DECLINE = 35.0  # score 下降贡献最大
WEIGHT_OSCILLATION_RISE = 25.0
WEIGHT_SATURATION_RISE = 15.0
WEIGHT_STEADY_DECLINE = 25.0

# 各指标当前值的告警阈值（接近阈值时风险叠加）
ALERT_SCORE_THRESHOLD = 60.0  # 综合评分 < 60 为低效
ALERT_OSCILLATION_THRESHOLD = 20.0  # 振荡率 > 20% 为异常
ALERT_SATURATION_THRESHOLD = 20.0  # 饱和率 > 20% 为异常
ALERT_STEADY_THRESHOLD = 80.0  # 平稳率 < 80% 为异常


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class MetricTrend:
    """单个指标的趋势分析结果。"""

    current_value: float | None
    slope: float | None  # 每小时变化量
    projected_value: float | None  # 未来 24h 预测值
    is_risky: bool  # 是否为风险方向（score/steady 下降，oscillation/saturation 上升）

    @property
    def has_data(self) -> bool:
        return self.current_value is not None and self.slope is not None


@dataclass
class LoopPrediction:
    """单回路预测结果。"""

    loop_id: str
    tag_name: str
    description: str | None
    plant_name: str | None
    risk_score: float
    risk_level: str  # HIGH / MEDIUM / LOW
    risk_factors: list[str] = field(default_factory=list)  # 主要风险因素描述
    trends: dict[str, MetricTrend] = field(default_factory=dict)
    recent_diagnosis_labels: list[str] = field(default_factory=list)
    data_points: int = 0  # 参与分析的数据点数


# ---------------------------------------------------------------------------
# 线性回归（最小二乘法）
# ---------------------------------------------------------------------------


def _linear_regression(values: list[float]) -> tuple[float | None, float | None]:
    """对时序值做最小二乘线性回归，返回 (斜率, 截距)。

    x 为等间隔索引 [0, 1, ..., n-1]，y 为 values。
    数据点不足或方差为零时返回 (None, None)。
    """
    n = len(values)
    if n < 3:
        return None, None

    # 过滤 None 值（用有效值的索引）
    valid: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        if v is not None:
            valid.append((float(i), float(v)))

    if len(valid) < 3:
        return None, None

    n_valid = len(valid)
    sum_x = sum(x for x, _ in valid)
    sum_y = sum(y for _, y in valid)
    sum_xy = sum(x * y for x, y in valid)
    sum_x2 = sum(x * x for x, _ in valid)

    denominator = n_valid * sum_x2 - sum_x * sum_x
    if abs(denominator) < 1e-12:
        return None, None  # 方差为零，无法计算斜率

    slope = (n_valid * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n_valid
    return slope, intercept


def _project_value(slope: float | None, intercept: float | None, x: int) -> float | None:
    """预测第 x 个点的值。"""
    if slope is None or intercept is None:
        return None
    return slope * x + intercept


# ---------------------------------------------------------------------------
# 风险分计算
# ---------------------------------------------------------------------------


def _calc_risk_score(trends: dict[str, MetricTrend]) -> tuple[float, list[str]]:
    """计算综合风险分（0~100）和风险因素列表。

    评分逻辑：
    - 对每个风险指标，计算 ``斜率幅度 × 权重 × 当前值接近度``
    - 当前值越接近告警阈值，风险叠加越大
    """
    score = 0.0
    factors: list[str] = []

    # score 下降风险
    t_score = trends.get("score")
    if t_score and t_score.has_data and t_score.is_risky:
        # 斜率幅度：每小时下降 0.5 分以上为显著
        decline_rate = abs(t_score.slope)  # type: ignore[arg-type]
        # 当前值接近度：score 越低风险越大
        proximity = 1.0
        if t_score.current_value is not None:
            if t_score.current_value < ALERT_SCORE_THRESHOLD:
                proximity = 1.5  # 已低于阈值，风险加倍
            else:
                # 距阈值越近，proximity 越高
                proximity = max(0.5, 100.0 - t_score.current_value) / 40.0
        contribution = min(
            WEIGHT_SCORE_DECLINE,
            decline_rate * 20 * proximity * WEIGHT_SCORE_DECLINE / 35.0,
        )
        score += contribution
        factors.append(
            f"综合评分下降趋势（当前 {t_score.current_value:.1f}，"
            f"每小时 -{decline_rate:.2f}，24h 后预计 {_projected(t_score):.1f}）"
            if t_score.current_value is not None
            else "综合评分下降趋势"
        )

    # oscillation_rate 上升风险
    t_osc = trends.get("oscillation_rate")
    if t_osc and t_osc.has_data and t_osc.is_risky:
        rise_rate = t_osc.slope or 0
        proximity = 1.0
        if t_osc.current_value is not None:
            if t_osc.current_value > ALERT_OSCILLATION_THRESHOLD:
                proximity = 1.5
            else:
                proximity = max(0.5, t_osc.current_value / ALERT_OSCILLATION_THRESHOLD)
        contribution = min(
            WEIGHT_OSCILLATION_RISE,
            rise_rate * 20 * proximity * WEIGHT_OSCILLATION_RISE / 25.0,
        )
        score += contribution
        factors.append(
            f"振荡率上升趋势（当前 {t_osc.current_value:.1f}%，每小时 +{rise_rate:.2f}%）"
            if t_osc.current_value is not None
            else "振荡率上升趋势"
        )

    # saturation_rate 上升风险
    t_sat = trends.get("saturation_rate")
    if t_sat and t_sat.has_data and t_sat.is_risky:
        rise_rate = t_sat.slope or 0
        proximity = 1.0
        if t_sat.current_value is not None:
            if t_sat.current_value > ALERT_SATURATION_THRESHOLD:
                proximity = 1.5
            else:
                proximity = max(0.5, t_sat.current_value / ALERT_SATURATION_THRESHOLD)
        contribution = min(
            WEIGHT_SATURATION_RISE,
            rise_rate * 20 * proximity * WEIGHT_SATURATION_RISE / 15.0,
        )
        score += contribution
        factors.append(
            f"饱和率上升趋势（当前 {t_sat.current_value:.1f}%）"
            if t_sat.current_value is not None
            else "饱和率上升趋势"
        )

    # steady_rate 下降风险
    t_steady = trends.get("steady_rate")
    if t_steady and t_steady.has_data and t_steady.is_risky:
        decline_rate = abs(t_steady.slope or 0)
        proximity = 1.0
        if t_steady.current_value is not None:
            if t_steady.current_value < ALERT_STEADY_THRESHOLD:
                proximity = 1.5
            else:
                proximity = max(0.5, (100.0 - t_steady.current_value) / 20.0)
        contribution = min(
            WEIGHT_STEADY_DECLINE,
            decline_rate * 20 * proximity * WEIGHT_STEADY_DECLINE / 25.0,
        )
        score += contribution
        factors.append(
            f"平稳率下降趋势（当前 {t_steady.current_value:.1f}%）"
            if t_steady.current_value is not None
            else "平稳率下降趋势"
        )

    return min(100.0, score), factors


def _projected(trend: MetricTrend) -> float | None:
    """预测 24h 后的值。"""
    return trend.projected_value


def _risk_level(score: float) -> str:
    """风险分 → 风险等级。"""
    if score >= RISK_LEVEL_HIGH:
        return "HIGH"
    if score >= RISK_LEVEL_MEDIUM:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# 主入口：批量预测
# ---------------------------------------------------------------------------


async def predict_loop_risks(
    db: AsyncSession,
    *,
    plant_id: str | None = None,
    top_n: int = TOP_N_RISK_LOOPS,
) -> dict:
    """批量预测回路风险，返回高风险回路列表。

    Args:
        db: 异步数据库会话
        plant_id: 装置 ID 筛选（可选）
        top_n: 返回的高风险回路数

    Returns:
        ``{
            "predictions": list[LoopPrediction dict],
            "totalLoopsAnalyzed": int,
            "highRiskCount": int,
            "mediumRiskCount": int,
            "generatedAt": ISO string,
            "forecastHorizonHours": int,
        }``
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    start = now - timedelta(days=PREDICTION_WINDOW_DAYS)

    # 查询活跃回路
    loop_query = select(LoopLedger).where(
        LoopLedger.is_active.is_(True),
        LoopLedger.include_in_evaluation.is_(True),
    )
    if plant_id:
        loop_query = loop_query.where(LoopLedger.unit_id == plant_id)

    result = await db.execute(loop_query)
    loops = list(result.scalars().all())

    if not loops:
        return {
            "predictions": [],
            "totalLoopsAnalyzed": 0,
            "totalLoopsEligible": 0,
            "highRiskCount": 0,
            "mediumRiskCount": 0,
            "generatedAt": now.isoformat(),
            "forecastHorizonHours": FORECAST_HORIZON_HOURS,
        }

    loop_ids = [str(loop.id) for loop in loops]
    loop_map = {str(loop.id): loop for loop in loops}

    # 查询 KPI 快照（7 天内 SUCCESS）
    from app.models.metric import KpiSnapshotHourly

    snap_query = (
        select(KpiSnapshotHourly)
        .where(
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= start,
            KpiSnapshotHourly.ts_start <= now,
            KpiSnapshotHourly.status == "SUCCESS",
        )
        .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_start.asc())
    )
    snap_result = await db.execute(snap_query)
    all_snapshots = list(snap_result.scalars().all())

    # 按 loop_id 分组
    snapshots_by_loop: dict[str, list] = {}
    for snap in all_snapshots:
        lid = str(snap.loop_id) if snap.loop_id else ""
        if lid:
            snapshots_by_loop.setdefault(lid, []).append(snap)

    # 批量查询最近诊断标签
    diagnosis_labels_map = await _batch_query_recent_diagnosis_labels(db, loop_ids, start, now)

    # 批量查询装置名称
    plant_names = await _batch_query_plant_names(db, loops)

    # 对每个回路做趋势分析
    predictions: list[LoopPrediction] = []
    for loop_id, snapshots in snapshots_by_loop.items():
        loop = loop_map.get(loop_id)
        if not loop or len(snapshots) < MIN_DATA_POINTS:
            continue

        pred = _analyze_loop(
            loop_id=loop_id,
            loop=loop,
            snapshots=snapshots,
            plant_name=plant_names.get(str(loop.unit_id)) if loop.unit_id else None,
            diagnosis_labels=diagnosis_labels_map.get(loop_id, []),
        )
        if pred is not None and pred.risk_level != "LOW":
            predictions.append(pred)

    # 按风险分降序排列，取 Top N
    predictions.sort(key=lambda p: p.risk_score, reverse=True)
    predictions = predictions[:top_n]

    high_count = sum(1 for p in predictions if p.risk_level == "HIGH")
    medium_count = sum(1 for p in predictions if p.risk_level == "MEDIUM")

    return {
        "predictions": [_prediction_to_dict(p) for p in predictions],
        # camelCase 对齐前端 PredictionResult 契约（整改 BL-3：
        # 汇总键曾为 snake_case，前端 highRiskCount 等读取 undefined）
        "totalLoopsAnalyzed": len(snapshots_by_loop),
        "totalLoopsEligible": len(loops),
        "highRiskCount": high_count,
        "mediumRiskCount": medium_count,
        "generatedAt": now.isoformat(),
        "forecastHorizonHours": FORECAST_HORIZON_HOURS,
    }


def _analyze_loop(
    *,
    loop_id: str,
    loop: LoopLedger,
    snapshots: list,
    plant_name: str | None,
    diagnosis_labels: list[str],
) -> LoopPrediction | None:
    """分析单回路趋势，返回预测结果。"""
    # 提取各指标时序
    score_values = [_to_float(s.score) for s in snapshots]
    osc_values = [_to_float(s.oscillation_rate) for s in snapshots]
    sat_values = [_to_float(s.saturation_rate) for s in snapshots]
    steady_values = [_to_float(s.steady_rate) for s in snapshots]

    # 预测索引 = 当前数据点数 + 预测时间跨度
    project_idx = len(snapshots) - 1 + FORECAST_HORIZON_HOURS

    # 计算趋势
    trends: dict[str, MetricTrend] = {}

    # score：下降为风险
    score_slope, score_intercept = _linear_regression(score_values)
    score_current = _last_valid(score_values)
    score_projected = _project_value(score_slope, score_intercept, project_idx)
    trends["score"] = MetricTrend(
        current_value=score_current,
        slope=score_slope,
        projected_value=score_projected,
        is_risky=score_slope is not None and score_slope < -0.01,  # 每小时下降 > 0.01
    )

    # oscillation_rate：上升为风险
    osc_slope, osc_intercept = _linear_regression(osc_values)
    osc_current = _last_valid(osc_values)
    osc_projected = _project_value(osc_slope, osc_intercept, project_idx)
    trends["oscillation_rate"] = MetricTrend(
        current_value=osc_current,
        slope=osc_slope,
        projected_value=osc_projected,
        is_risky=osc_slope is not None and osc_slope > 0.01,
    )

    # saturation_rate：上升为风险
    sat_slope, sat_intercept = _linear_regression(sat_values)
    sat_current = _last_valid(sat_values)
    sat_projected = _project_value(sat_slope, sat_intercept, project_idx)
    trends["saturation_rate"] = MetricTrend(
        current_value=sat_current,
        slope=sat_slope,
        projected_value=sat_projected,
        is_risky=sat_slope is not None and sat_slope > 0.01,
    )

    # steady_rate：下降为风险
    steady_slope, steady_intercept = _linear_regression(steady_values)
    steady_current = _last_valid(steady_values)
    steady_projected = _project_value(steady_slope, steady_intercept, project_idx)
    trends["steady_rate"] = MetricTrend(
        current_value=steady_current,
        slope=steady_slope,
        projected_value=steady_projected,
        is_risky=steady_slope is not None and steady_slope < -0.01,
    )

    # 计算风险分
    risk_score, risk_factors = _calc_risk_score(trends)

    return LoopPrediction(
        loop_id=loop_id,
        tag_name=loop.tag_name or "",
        description=loop.description,
        plant_name=plant_name,
        risk_score=round(risk_score, 1),
        risk_level=_risk_level(risk_score),
        risk_factors=risk_factors,
        trends=trends,
        recent_diagnosis_labels=diagnosis_labels,
        data_points=len(snapshots),
    )


def _prediction_to_dict(p: LoopPrediction) -> dict:
    """将 LoopPrediction 转为 API 响应字典。"""
    return {
        "loopId": p.loop_id,
        "tagName": p.tag_name,
        "description": p.description,
        "plantName": p.plant_name,
        "riskScore": p.risk_score,
        "riskLevel": p.risk_level,
        "riskFactors": p.risk_factors,
        "trends": {
            key: {
                "currentValue": t.current_value,
                "slope": round(t.slope, 4) if t.slope is not None else None,
                "projectedValue": (
                    round(t.projected_value, 2) if t.projected_value is not None else None
                ),
                "isRisky": t.is_risky,
            }
            for key, t in p.trends.items()
        },
        "recentDiagnosisLabels": p.recent_diagnosis_labels,
        "dataPoints": p.data_points,
    }


# ---------------------------------------------------------------------------
# 辅助查询
# ---------------------------------------------------------------------------


async def _batch_query_recent_diagnosis_labels(
    db: AsyncSession,
    loop_ids: list[str],
    start: datetime,
    now: datetime,
) -> dict[str, list[str]]:
    """批量查询各回路最近的诊断标签。

    诊断模型已在重构中移除，暂返回空字典以保持接口兼容；
    调用方通过 ``.get(loop_id, [])`` 取到空列表，行为正确。
    """
    return {}


async def _batch_query_plant_names(db: AsyncSession, loops: Iterable[LoopLedger]) -> dict[str, str]:
    """批量查询回路所属装置名称。"""
    from app.models.plant_node import PlantNode

    unit_ids = {str(loop.unit_id) for loop in loops if loop.unit_id}
    if not unit_ids:
        return {}

    result = await db.execute(
        select(PlantNode.id, PlantNode.name).where(PlantNode.id.in_(list(unit_ids)))
    )
    return {str(row.id): row.name for row in result.all()}


def _to_float(value: Decimal | float | None) -> float | None:
    """Decimal/float → float，None 透传。"""
    if value is None:
        return None
    return float(value)


def _last_valid(values: list[float | None]) -> float | None:
    """取列表中最后一个非 None 值。"""
    for v in reversed(values):
        if v is not None:
            return v
    return None


__all__ = [
    "FORECAST_HORIZON_HOURS",
    "LoopPrediction",
    "MetricTrend",
    "PREDICTION_WINDOW_DAYS",
    "predict_loop_risks",
    "TOP_N_RISK_LOOPS",
]
