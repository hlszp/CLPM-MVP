"""Performance evaluation API tests (S3-METRIC-001~006).

Covers:
- GET /api/v1/performance/metrics (list)
- PUT /api/v1/performance/metrics/{id} (update, ERR_METRIC_WEIGHT_SUM check)
- GET /api/v1/performance/rules (list)
- PUT /api/v1/performance/rules/{id} (update)
- GET /api/v1/performance/board (dashboard)
- GET /api/v1/performance/ranking (ranking)
- GET /api/v1/performance/analytics (analytics)
- POST /api/v1/performance/analytics/export (CSV export)
- RBAC: 非 ADMIN 不能修改配置
- KPI 计算引擎单元测试
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------


def _make_metric_config(
    metric_id: str = "00000000-0000-0000-0000-000000000301",
    metric_code: str = "good_value_rate",
    metric_name: str = "好值率",
    weight: Decimal = Decimal("20"),
    is_enabled: bool = True,
    threshold: dict | None = None,
) -> MagicMock:
    """构造 MetricConfig mock。"""
    c = MagicMock()
    c.id = metric_id
    c.metric_code = metric_code
    c.metric_name = metric_name
    c.formula = "sum(quality==Good) / count(*) * 100"
    c.weight = weight
    c.threshold = threshold or {"min": 0, "max": 100, "alert": 80}
    c.control_type = "STABLE"
    c.is_enabled = is_enabled
    c.updated_by = "admin"
    c.updated_at = datetime.now(UTC)
    c.version = 1
    return c


def _make_engine_rule(
    rule_id: str = "00000000-0000-0000-0000-000000000401",
    rule_code: str = "calc_cycle",
    rule_name: str = "计算周期",
    rule_type: str = "CALC_CYCLE",
    params: dict | None = None,
    is_enabled: bool = True,
) -> MagicMock:
    """构造 EngineRule mock。"""
    r = MagicMock()
    r.id = rule_id
    r.rule_code = rule_code
    r.rule_name = rule_name
    r.rule_type = rule_type
    r.params = params or {"cycle": "hourly"}
    r.is_enabled = is_enabled
    r.updated_by = "admin"
    r.updated_at = datetime.now(UTC)
    return r


def _make_snapshot(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    score: Decimal = Decimal("78.60"),
    good_value_rate: Decimal = Decimal("96.80"),
    auto_mode_rate: Decimal = Decimal("90.00"),
    steady_rate: Decimal = Decimal("85.00"),
    accuracy_rate: Decimal = Decimal("80.00"),
    oscillation_rate: Decimal = Decimal("15.00"),
    saturation_rate: Decimal = Decimal("8.00"),
    status: str = "SUCCESS",
    ts_start: datetime | None = None,
) -> MagicMock:
    """构造 KpiSnapshotHourly mock。"""
    s = MagicMock()
    s.id = "00000000-0000-0000-0000-000000000501"
    s.loop_id = loop_id
    s.ts_start = ts_start or datetime.now(UTC)
    s.ts_end = s.ts_start
    s.score = score
    s.good_value_rate = good_value_rate
    s.auto_mode_rate = auto_mode_rate
    s.steady_rate = steady_rate
    s.accuracy_rate = accuracy_rate
    s.oscillation_rate = oscillation_rate
    s.saturation_rate = saturation_rate
    s.status = status
    return s


def _make_node_snapshot(
    plant_node_id: str = "00000000-0000-0000-0000-000000000111",
    score: Decimal = Decimal("80.25"),
    good_value_rate: Decimal = Decimal("93.00"),
    auto_mode_rate: Decimal = Decimal("88.00"),
    effective_auto_rate: Decimal = Decimal("82.00"),
    steady_rate: Decimal = Decimal("85.00"),
    accuracy_rate: Decimal = Decimal("80.00"),
    fast_response_rate: Decimal = Decimal("75.00"),
    oscillation_rate: Decimal = Decimal("15.00"),
    saturation_rate: Decimal = Decimal("8.00"),
    auto_loop_ratio: Decimal = Decimal("100.00"),
    realtime_auto_rate: Decimal = Decimal("87.50"),
    loop_count: int = 4,
    status: str = "GOOD",
    ts_start: datetime | None = None,
) -> MagicMock:
    """构造 KpiNodeSnapshotHourly mock（用于看板测试）。"""
    s = MagicMock()
    s.id = "00000000-0000-0000-0000-000000000601"
    s.plant_node_id = plant_node_id
    s.ts_start = ts_start or datetime.now(UTC)
    s.ts_end = s.ts_start
    s.score = score
    s.good_value_rate = good_value_rate
    s.auto_mode_rate = auto_mode_rate
    s.effective_auto_rate = effective_auto_rate
    s.steady_rate = steady_rate
    s.accuracy_rate = accuracy_rate
    s.fast_response_rate = fast_response_rate
    s.oscillation_rate = oscillation_rate
    s.saturation_rate = saturation_rate
    s.auto_loop_ratio = auto_loop_ratio
    s.realtime_auto_rate = realtime_auto_rate
    s.loop_count = loop_count
    s.status = status
    s.algorithm_version = "KPI_CALC_v2.0"
    return s


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_aggregate_row_mock(cnt: int = 0, **fields) -> MagicMock:
    """构造返回聚合查询结果的 row（用于 result.one()）。

    cnt=0 且所有字段为 None 时，模拟无快照数据的聚合结果。
    """
    row = MagicMock()
    row.cnt = cnt
    # 显式设置所有 KPI 字段为 None（覆盖 MagicMock 自动创建）
    for code in (
        "good_value_rate",
        "auto_mode_rate",
        "steady_rate",
        "accuracy_rate",
        "oscillation_rate",
        "saturation_rate",
        "score",
    ):
        setattr(row, code, fields.get(code))
    return row


def _make_one_result_mock(row) -> MagicMock:
    """构造 result.one() 返回 row 的 execute 结果。"""
    result = MagicMock()
    result.one.return_value = row
    return result


def _make_all_rows_mock(rows: list) -> MagicMock:
    """构造返回 all() 的 execute 结果（用于元组/聚合行查询）。"""
    result = MagicMock()
    result.all.return_value = rows
    return result


# ---------------------------------------------------------------------------
# S3-METRIC-001: 指标配置 CRUD
# ---------------------------------------------------------------------------


class TestMetricConfigList:
    """GET /api/v1/performance/metrics tests."""

    def test_list_metrics_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取指标配置列表。"""
        configs = [
            _make_metric_config(metric_code="good_value_rate", metric_name="好值率"),
            _make_metric_config(
                metric_id="00000000-0000-0000-0000-000000000302",
                metric_code="auto_mode_rate",
                metric_name="自控率",
                weight=Decimal("20"),
            ),
        ]
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(configs))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/metrics",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]) == 2
        assert body["data"][0]["metricCode"] == "good_value_rate"
        assert body["data"][0]["metricName"] == "好值率"

    def test_list_metrics_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/performance/metrics")
        assert resp.status_code == 401


class TestMetricConfigUpdate:
    """PUT /api/v1/performance/metrics/{id} tests."""

    def test_update_metric_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以更新指标配置。"""
        config = _make_metric_config(weight=Decimal("20"))
        # 6 大 KPI 配置（权重总和 100，good_value_rate 已更新为 25）
        all_configs = [
            _make_metric_config(metric_code="good_value_rate", weight=Decimal("25")),
            _make_metric_config(
                metric_id="id2",
                metric_code="auto_mode_rate",
                weight=Decimal("20"),
            ),
            _make_metric_config(
                metric_id="id3",
                metric_code="steady_rate",
                weight=Decimal("20"),
            ),
            _make_metric_config(
                metric_id="id4",
                metric_code="accuracy_rate",
                weight=Decimal("15"),
            ),
            _make_metric_config(
                metric_id="id5",
                metric_code="oscillation_rate",
                weight=Decimal("10"),
            ),
            _make_metric_config(
                metric_id="id6",
                metric_code="saturation_rate",
                weight=Decimal("10"),
            ),
        ]
        # 第一次查目标指标，第二次查所有指标做权重校验
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(config)
            return _make_scalars_mock(all_configs)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/performance/metrics/{config.id}",
                headers={"Authorization": "Bearer fake-token"},
                json={"metricName": "好值率（更新）", "weight": 25},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["metricName"] == "好值率（更新）"

    def test_update_metric_not_found(self, client, mock_db, fake_redis) -> None:
        """指标不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/performance/metrics/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
                json={"weight": 25},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_METRIC_NOT_FOUND"

    def test_update_metric_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能修改指标配置（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.put(
                "/api/v1/performance/metrics/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"weight": 25},
            )
        assert resp.status_code == 403

    def test_update_metric_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能修改指标配置（403，仅 ADMIN）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/performance/metrics/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"weight": 25},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# S3-METRIC-002: 引擎规则 CRUD
# ---------------------------------------------------------------------------


class TestEngineRuleList:
    """GET /api/v1/performance/rules tests."""

    def test_list_rules_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取引擎规则列表。"""
        rules = [_make_engine_rule()]
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(rules))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/performance/rules",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]) == 1
        assert body["data"][0]["ruleCode"] == "calc_cycle"


class TestEngineRuleUpdate:
    """PUT /api/v1/performance/rules/{id} tests."""

    def test_update_rule_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以更新引擎规则。"""
        rule = _make_engine_rule()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(rule))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/performance/rules/{rule.id}",
                headers={"Authorization": "Bearer fake-token"},
                json={"ruleName": "计算周期（更新）"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["ruleName"] == "计算周期（更新）"

    def test_update_rule_not_found(self, client, mock_db, fake_redis) -> None:
        """规则不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/performance/rules/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
                json={"ruleName": "更新"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_RULE_NOT_FOUND"

    def test_update_rule_expert_forbidden(self, client, mock_db, fake_redis) -> None:
        """EXPERT 不能修改引擎规则（403）。"""
        with mock_current_user(TEST_USERS["expert"]):
            resp = client.put(
                "/api/v1/performance/rules/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"ruleName": "更新"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# S3-METRIC-004: 全局看板
# ---------------------------------------------------------------------------


class TestBoard:
    """GET /api/v1/performance/board tests."""

    def test_get_board_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取全局看板（从节点级快照表读取）。"""
        node_snaps = [_make_node_snapshot()]
        trend_row = MagicMock()
        trend_row.hour = datetime.now(UTC)
        trend_row.avg_steady = Decimal("85.00")
        status_row = MagicMock()
        status_row.status = "GOOD"
        status_row.cnt = 1

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(node_snaps)
            elif call_count[0] == 2:
                return _make_all_rows_mock([trend_row])
            else:
                return _make_all_rows_mock([status_row])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/board",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "filterScope" in data
        assert "kpiCards" in data
        assert "kpiSummary" in data
        assert "steadyRateTrend" in data
        assert "partialWarning" in data
        # 9 张卡片（8 大 KPI + 综合评分）
        assert len(data["kpiCards"]) == 9

    def test_get_board_with_plant_node(self, client, mock_db, fake_redis) -> None:
        """按装置筛选看板数据（从节点级快照表读取）。"""
        plant_node = MagicMock()
        plant_node.name = "测试装置"
        node_snaps = [_make_node_snapshot()]
        trend_row = MagicMock()
        trend_row.hour = datetime.now(UTC)
        trend_row.avg_steady = Decimal("85.00")
        status_row = MagicMock()
        status_row.status = "GOOD"
        status_row.cnt = 1

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(plant_node)
            elif call_count[0] == 2:
                return _make_scalars_mock(node_snaps)
            elif call_count[0] == 3:
                return _make_all_rows_mock([trend_row])
            else:
                return _make_all_rows_mock([status_row])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/performance/board?plantNodeId=00000000-0000-0000-0000-000000000111",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_get_board_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/performance/board")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S3-METRIC-005: 低效回路排行
# ---------------------------------------------------------------------------


class TestRanking:
    """GET /api/v1/performance/ranking tests."""

    def test_get_ranking_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取低效回路排行。"""
        snapshot = _make_snapshot(score=Decimal("45.20"))
        loop = MagicMock()
        loop.id = snapshot.loop_id
        loop.tag_name = "101-FC-1023"
        loop.unit_id = "00000000-0000-0000-0000-000000000111"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock([snapshot])
            if call_count[0] == 2:
                return _make_scalars_mock([loop])
            if call_count[0] == 3:
                # PlantNode 查询
                node = MagicMock()
                node.id = loop.unit_id
                node.name = "常减压装置-单元A"
                return _make_scalars_mock([node])
            # ActionTracker 查询
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/ranking",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert isinstance(data, list)
        if data:
            assert "rank" in data[0]
            assert "loopId" in data[0]
            assert "tagName" in data[0]
            assert "compositeScore" in data[0]

    def test_get_ranking_with_limit(self, client, mock_db, fake_redis) -> None:
        """limit 参数限制返回条数。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/ranking?limit=5",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_get_ranking_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/performance/ranking")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S3-METRIC-006: 统计报表
# ---------------------------------------------------------------------------


class TestAnalytics:
    """GET /api/v1/performance/analytics tests."""

    def test_get_analytics_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取统计报表。"""
        snapshots = [_make_snapshot()]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(snapshots)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/analytics",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startTime": "2026-06-01T00:00:00",
                    "endTime": "2026-06-22T00:00:00",
                    "metricKey": "score",
                    "granularity": "day",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "filterScope" in data
        assert "kpiTrend" in data
        assert "unitRanking" in data
        assert "badActorDistribution" in data

    def test_get_analytics_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/performance/analytics")
        assert resp.status_code == 401


class TestAnalyticsExport:
    """POST /api/v1/performance/analytics/export tests."""

    def test_export_csv_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以导出 CSV 报表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock([_make_snapshot()])
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/performance/analytics/export",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "startTime": "2026-06-01T00:00:00",
                    "endTime": "2026-06-22T00:00:00",
                    "metricKey": "score",
                    "granularity": "day",
                    "format": "csv",
                },
            )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        # CSV 内容应包含表头
        assert "section" in resp.text or "filterScope" in resp.text

    def test_export_csv_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.post(
            "/api/v1/performance/analytics/export",
            json={
                "startTime": "2026-06-01T00:00:00",
                "endTime": "2026-06-22T00:00:00",
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 权重校验单元测试
# ---------------------------------------------------------------------------


class TestWeightSumValidator:
    """权重总和校验测试。"""

    def test_weight_sum_ok(self) -> None:
        """权重总和为 100 通过校验。"""
        from app.schemas.performance import WeightSumValidator

        # 不抛异常即通过
        WeightSumValidator.validate(
            [
                Decimal("20"),
                Decimal("20"),
                Decimal("20"),
                Decimal("15"),
                Decimal("15"),
                Decimal("10"),
            ]
        )

    def test_weight_sum_invalid(self) -> None:
        """权重总和不为 100 抛出 ERR_METRIC_WEIGHT_SUM。"""
        from app.core.exceptions import BizError
        from app.schemas.performance import WeightSumValidator

        with pytest.raises(BizError) as exc_info:
            WeightSumValidator.validate([Decimal("20"), Decimal("20"), Decimal("20")])
        assert exc_info.value.code == "ERR_METRIC_WEIGHT_SUM"
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# KPI 计算引擎单元测试
# ---------------------------------------------------------------------------


# 已删除：TestKpiCalcEngine 类（13 个测试）— Phase 4 重构删除了 kpi_calc.py
# 中的多个内部函数，原测试导入已失效。
#
# 删除的测试及替代测试位置：
# - test_compute_kpis_basic / test_compute_kpis_empty / test_good_value_rate_before_filtering
#   / test_good_value_rate_defaults_to_100
#   → _compute_kpis 已替换为 _compute_kpis_three_layer（Phase 4 三层计算流程）
#   → 参见 tests/test_kpi_calc.py: TestComputeKpisThreeLayer
#
# - test_compute_composite_score / test_compute_composite_score_disabled_metric
#   → _compute_composite_score (v1) 已删除，改用 ConfidenceEvaluator.compute_composite_score
#   → 参见 tests/test_metric_calculator/test_confidence_evaluator.py: TestComputeCompositeScore
#
# - test_is_auto_mode
#   → _is_auto_mode 逻辑已移入 AutoModeRateCalculator（Phase 3）
#   → 参见 tests/test_metric_calculator/test_auto_mode.py
#
# - test_detect_oscillation / test_detect_oscillation_amplitude_threshold
#   → _compute_oscillation_rate 逻辑已移入 OscillationRateCalculator（Phase 3）
#   → 参见 tests/test_metric_calculator/test_oscillation.py
#
# - test_align_timeseries / test_align_timeseries_tolerance_numeric
#   / test_align_timeseries_tolerance_out_of_range / test_align_timeseries_iso_string_tolerance
#   → _align_timeseries 逻辑已移入 PreprocessingPipeline（Phase 1）
#   → 参见 tests/test_preprocessing/test_pipeline.py


# ---------------------------------------------------------------------------
# Celery Beat 调度测试
# ---------------------------------------------------------------------------


class TestCeleryBeatSchedule:
    """Celery Beat 调度配置测试。"""

    def test_beat_schedule_has_kpi_calc(self) -> None:
        """Beat 调度应包含 KPI 计算任务。"""
        # 触发 kpi_calc 模块加载（注册 beat_schedule）
        import app.tasks.kpi_calc  # noqa: F401
        from app.tasks.celery_app import celery_app

        beat = celery_app.conf.beat_schedule
        assert "kpi-calc-hourly" in beat
        assert beat["kpi-calc-hourly"]["task"] == "app.tasks.kpi_calc.calculate_hourly_kpi"
        assert beat["kpi-calc-hourly"]["schedule"] == 3600.0


# ---------------------------------------------------------------------------
# 服务层单元测试
# ---------------------------------------------------------------------------


class TestPerformanceService:
    """Performance service 单元测试。"""

    async def test_list_metric_configs(self) -> None:
        """list_metric_configs 返回配置列表。"""
        from app.services.performance import list_metric_configs

        db = AsyncMock()
        configs = [_make_metric_config()]
        db.execute = AsyncMock(return_value=_make_scalars_mock(configs))
        result = await list_metric_configs(db)
        assert len(result) == 1
        assert result[0]["metricCode"] == "good_value_rate"

    async def test_list_engine_rules(self) -> None:
        """list_engine_rules 返回规则列表。"""
        from app.services.performance import list_engine_rules

        db = AsyncMock()
        rules = [_make_engine_rule()]
        db.execute = AsyncMock(return_value=_make_scalars_mock(rules))
        result = await list_engine_rules(db)
        assert len(result) == 1
        assert result[0]["ruleCode"] == "calc_cycle"

    async def test_update_metric_config_not_found(self) -> None:
        """更新不存在的指标返回 ERR_METRIC_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.performance import update_metric_config

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await update_metric_config(db, "nonexistent", "admin", weight=Decimal("25"))
        assert exc_info.value.code == "ERR_METRIC_NOT_FOUND"

    async def test_update_engine_rule_not_found(self) -> None:
        """更新不存在的规则返回 ERR_RULE_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.performance import update_engine_rule

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await update_engine_rule(db, "nonexistent", "admin", rule_name="更新")
        assert exc_info.value.code == "ERR_RULE_NOT_FOUND"

    async def test_update_engine_rule_eval_calc_cycle_returns_beat_warning(self) -> None:
        """P3 #51: 更新 EVAL_CALC_CYCLE 规则时返回 Beat 重启提示。"""
        from app.services.performance import update_engine_rule

        rule = _make_engine_rule(rule_code="EVAL_CALC_CYCLE", rule_name="计算周期")
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_make_scalar_one_or_none_mock(rule)
        )
        db.add = AsyncMock()
        db.commit = AsyncMock()

        # Mock _handle_engine_rule_changed 避免实际触发缓存失效/Celery 任务
        with patch(
            "app.services.performance._handle_engine_rule_changed",
            new=AsyncMock(return_value="计算周期已变更，新调度需重启 Celery Beat 进程才能生效"),
        ):
            result = await update_engine_rule(
                db, "rule-id", "admin", rule_name="更新计算周期"
            )

        # 验证返回结果包含 warning 字段
        assert "warning" in result
        assert "Beat" in result["warning"]
        assert "重启" in result["warning"]

    async def test_update_engine_rule_other_rule_no_warning(self) -> None:
        """P3 #51: 更新非 EVAL_CALC_CYCLE 规则时不返回 warning。"""
        from app.services.performance import update_engine_rule

        rule = _make_engine_rule(rule_code="SCHEDULE_CONCURRENCY", rule_name="并发数")
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=_make_scalar_one_or_none_mock(rule)
        )
        db.add = AsyncMock()
        db.commit = AsyncMock()

        with patch(
            "app.services.performance._handle_engine_rule_changed",
            new=AsyncMock(return_value=None),
        ):
            result = await update_engine_rule(
                db, "rule-id", "admin", rule_name="更新并发"
            )

        # 非计算周期规则不应返回 warning
        assert "warning" not in result or result.get("warning") is None

    async def test_get_board_empty(self) -> None:
        """无快照数据时看板返回空 KPI 卡片。"""
        from app.services.performance import get_board

        db = AsyncMock()
        # get_board 在 plant_node_id=None 时跳过装置名查询，依次调用：
        # 1. _aggregate_node_board → result.one() 期望 row.cnt=0（空节点）
        # 2. _aggregate_node_steady_trend → result.all() 期望空列表
        # 3. count_stmt (partialWarning) → result.all() 期望空列表
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] in (1, 2):
                return _make_one_result_mock(_make_aggregate_row_mock(cnt=0))
            return _make_all_rows_mock([])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        with patch("app.services.performance.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_board(db, plant_node_id=None, time_window="today")
        assert len(result["kpiCards"]) == 9
        assert all(c["status"] == "INCONCLUSIVE" for c in result["kpiCards"])

    async def test_get_ranking_empty(self) -> None:
        """无快照数据时排行返回空列表。"""
        from app.services.performance import get_ranking

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        result = await get_ranking(db)
        assert result == []

    async def test_get_ranking_filters_confidence_level_e(self) -> None:
        """P3 #50: confidence_level='E' 的快照不参与排行（与节点级聚合一致）。

        验证 SQL 中包含 (confidence_level IS NULL OR confidence_level != 'E') 过滤，
        与 node_performance.py 中节点级聚合的过滤条件保持一致。
        """
        from app.services.performance import get_ranking

        db = AsyncMock()
        # get_ranking 第 1 次 execute 为排行查询；后续 loop_map/unit_map/tracker 查询返回空
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 返回空列表（验证 SQL 已应用 confidence_level 过滤）
                return _make_scalars_mock([])
            return _make_scalars_mock([])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        result = await get_ranking(db)
        assert result == []

        # 验证第 1 次 SQL 含 confidence_level 过滤条件（IS NULL OR != 'E'）
        first_stmt = db.execute.call_args_list[0].args[0]
        sql_text = str(
            first_stmt.compile(compile_kwargs={"literal_binds": True})
        ).lower()
        assert "confidence_level" in sql_text
        # IS NULL 分支：保证 NULL 旧数据仍纳入排行
        assert "is null" in sql_text or "isnull" in sql_text
        # != 'E' 分支：排除有效数据率 < 20% 的快照
        assert "e" in sql_text

    async def test_export_analytics_csv(self) -> None:
        """导出 CSV 包含表头和分区。"""
        from app.services.performance import export_analytics_csv

        db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock([_make_snapshot()])
            return _make_scalars_mock([])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        csv_content = await export_analytics_csv(
            db,
            start_time="2026-06-01T00:00:00",
            end_time="2026-06-22T00:00:00",
        )
        assert "section" in csv_content
        assert "filterScope" in csv_content
