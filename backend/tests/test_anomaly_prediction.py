"""P3-05 异常预测与提前预警服务测试。

测试覆盖：
- 线性回归算法（最小二乘法）
- 风险分计算逻辑
- 预测主流程（mock DB）
- API 端点（集成测试）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.anomaly_prediction import (
    FORECAST_HORIZON_HOURS,
    MetricTrend,
    _calc_risk_score,
    _last_valid,
    _linear_regression,
    _risk_level,
    _to_float,
    predict_loop_risks,
)

# ---------------------------------------------------------------------------
# 线性回归测试
# ---------------------------------------------------------------------------


class TestLinearRegression:
    """最小二乘线性回归测试。"""

    def test_perfect_positive_slope(self):
        """完全正相关的数据斜率应为 1。"""
        slope, intercept = _linear_regression([1.0, 2.0, 3.0, 4.0, 5.0])
        assert slope is not None
        assert abs(slope - 1.0) < 1e-6
        assert abs(intercept - 1.0) < 1e-6

    def test_perfect_negative_slope(self):
        """完全负相关的数据斜率应为 -1。"""
        slope, _ = _linear_regression([5.0, 4.0, 3.0, 2.0, 1.0])
        assert slope is not None
        assert abs(slope - (-1.0)) < 1e-6

    def test_flat_line(self):
        """水平线斜率应为 0。"""
        slope, _ = _linear_regression([3.0, 3.0, 3.0, 3.0, 3.0])
        assert slope is not None
        assert abs(slope) < 1e-6

    def test_insufficient_data(self):
        """数据点不足时返回 (None, None)。"""
        slope, intercept = _linear_regression([1.0, 2.0])
        assert slope is None
        assert intercept is None

    def test_with_none_values(self):
        """包含 None 值时应跳过并计算有效数据点。"""
        slope, _ = _linear_regression([1.0, None, 3.0, None, 5.0])
        assert slope is not None
        assert abs(slope - 1.0) < 1e-6

    def test_all_none(self):
        """全部 None 时返回 (None, None)。"""
        slope, intercept = _linear_regression([None, None, None])
        assert slope is None
        assert intercept is None


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------


class TestHelpers:
    """辅助函数测试。"""

    def test_to_float_decimal(self):
        assert _to_float(Decimal("3.14")) == 3.14

    def test_to_float_none(self):
        assert _to_float(None) is None

    def test_last_valid(self):
        assert _last_valid([1.0, None, 3.0, None]) == 3.0

    def test_last_valid_all_none(self):
        assert _last_valid([None, None]) is None

    def test_risk_level_high(self):
        assert _risk_level(75.0) == "HIGH"

    def test_risk_level_medium(self):
        assert _risk_level(45.0) == "MEDIUM"

    def test_risk_level_low(self):
        assert _risk_level(15.0) == "LOW"


# ---------------------------------------------------------------------------
# 风险分计算测试
# ---------------------------------------------------------------------------


class TestRiskScore:
    """风险分计算测试。"""

    def test_no_risk_flat_trends(self):
        """所有指标平稳时风险分应为 0。"""
        trends = {
            "score": MetricTrend(
                current_value=85.0, slope=0.0, projected_value=85.0, is_risky=False
            ),
            "oscillation_rate": MetricTrend(
                current_value=5.0, slope=0.0, projected_value=5.0, is_risky=False
            ),
            "saturation_rate": MetricTrend(
                current_value=5.0, slope=0.0, projected_value=5.0, is_risky=False
            ),
            "steady_rate": MetricTrend(
                current_value=90.0, slope=0.0, projected_value=90.0, is_risky=False
            ),
        }
        score, factors = _calc_risk_score(trends)
        assert score == 0.0
        assert len(factors) == 0

    def test_score_decline_risk(self):
        """综合评分下降应为风险因素。"""
        trends = {
            "score": MetricTrend(
                current_value=55.0, slope=-0.5, projected_value=43.0, is_risky=True
            ),
            "oscillation_rate": MetricTrend(
                current_value=5.0, slope=0.0, projected_value=5.0, is_risky=False
            ),
            "saturation_rate": MetricTrend(
                current_value=5.0, slope=0.0, projected_value=5.0, is_risky=False
            ),
            "steady_rate": MetricTrend(
                current_value=90.0, slope=0.0, projected_value=90.0, is_risky=False
            ),
        }
        score, factors = _calc_risk_score(trends)
        assert score > 0
        assert any("综合评分下降" in f for f in factors)

    def test_oscillation_rise_risk(self):
        """振荡率上升应为风险因素。"""
        trends = {
            "score": MetricTrend(
                current_value=75.0, slope=0.0, projected_value=75.0, is_risky=False
            ),
            "oscillation_rate": MetricTrend(
                current_value=25.0, slope=0.2, projected_value=30.0, is_risky=True
            ),
            "saturation_rate": MetricTrend(
                current_value=5.0, slope=0.0, projected_value=5.0, is_risky=False
            ),
            "steady_rate": MetricTrend(
                current_value=90.0, slope=0.0, projected_value=90.0, is_risky=False
            ),
        }
        score, factors = _calc_risk_score(trends)
        assert score > 0
        assert any("振荡率上升" in f for f in factors)

    def test_multiple_risks_high_score(self):
        """多指标同时恶化时风险分应较高。"""
        trends = {
            "score": MetricTrend(
                current_value=45.0, slope=-0.8, projected_value=26.0, is_risky=True
            ),
            "oscillation_rate": MetricTrend(
                current_value=30.0, slope=0.5, projected_value=42.0, is_risky=True
            ),
            "saturation_rate": MetricTrend(
                current_value=25.0, slope=0.3, projected_value=32.0, is_risky=True
            ),
            "steady_rate": MetricTrend(
                current_value=65.0, slope=-0.5, projected_value=53.0, is_risky=True
            ),
        }
        score, factors = _calc_risk_score(trends)
        assert score >= 60.0  # HIGH 风险
        assert len(factors) >= 3  # 至少 3 个风险因素


# ---------------------------------------------------------------------------
# 预测主流程测试（mock DB）
# ---------------------------------------------------------------------------


class TestPredictLoopRisks:
    """预测主流程测试。"""

    @pytest.mark.asyncio
    async def test_no_active_loops(self):
        """无活跃回路时返回空结果。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        result = await predict_loop_risks(db)

        assert result["predictions"] == []
        assert result["total_loops_analyzed"] == 0
        assert result["high_risk_count"] == 0

    @pytest.mark.asyncio
    async def test_response_structure(self):
        """验证响应数据结构完整性。"""
        db = AsyncMock()

        # mock 活跃回路
        loop = MagicMock()
        loop.id = uuid4()
        loop.tag_name = "FIC-101"
        loop.description = "温度控制"
        loop.unit_id = uuid4()
        loop.is_active = True
        loop.include_in_evaluation = True

        mock_loop_result = MagicMock()
        mock_loop_result.scalars.return_value.all.return_value = [loop]

        # mock KPI 快照（模拟下降趋势）
        now = datetime.now(UTC).replace(tzinfo=None)
        snapshots = []
        for i in range(20):
            snap = MagicMock()
            snap.loop_id = loop.id
            snap.ts_start = now - timedelta(hours=20 - i)
            snap.score = Decimal(str(80 - i * 0.5))  # 80→70 下降趋势
            snap.oscillation_rate = Decimal(str(5 + i * 0.3))  # 5→11 上升趋势
            snap.saturation_rate = Decimal("5")
            snap.steady_rate = Decimal(str(90 - i * 0.2))
            snapshots.append(snap)

        mock_snap_result = MagicMock()
        mock_snap_result.scalars.return_value.all.return_value = snapshots

        # mock 诊断标签查询
        mock_diag_result = MagicMock()
        mock_diag_result.all.return_value = []

        # mock 装置名称查询
        mock_plant_result = MagicMock()
        mock_plant_result.all.return_value = []

        # 按调用顺序设置返回值
        db.execute.side_effect = [
            mock_loop_result,  # loop query
            mock_snap_result,  # snapshot query
            mock_diag_result,  # diagnosis labels
            mock_plant_result,  # plant names
        ]

        result = await predict_loop_risks(db)

        assert "predictions" in result
        assert "total_loops_analyzed" in result
        assert "high_risk_count" in result
        assert "medium_risk_count" in result
        assert "generated_at" in result
        assert "forecast_horizon_hours" in result
        assert result["forecast_horizon_hours"] == FORECAST_HORIZON_HOURS

        # 应检测到风险（score 下降 + oscillation 上升）
        if result["predictions"]:
            pred = result["predictions"][0]
            assert "loopId" in pred
            assert "tagName" in pred
            assert "riskScore" in pred
            assert "riskLevel" in pred
            assert "riskFactors" in pred
            assert "trends" in pred
            assert "score" in pred["trends"]
            assert "oscillation_rate" in pred["trends"]
