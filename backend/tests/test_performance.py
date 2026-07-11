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
    s.fast_rate = fast_response_rate
    s.oscillation_rate = oscillation_rate
    s.saturation_rate = saturation_rate
    s.auto_loop_ratio = auto_loop_ratio
    s.realtime_auto_rate = realtime_auto_rate
    s.loop_count = loop_count
    s.status = status
    s.algorithm_version = "KPI_CALC_v1.0"
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
            assert "score" in data[0]

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
                Decimal("20"), Decimal("20"), Decimal("20"),
                Decimal("15"), Decimal("15"), Decimal("10"),
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


class TestKpiCalcEngine:
    """KPI 计算引擎单元测试。"""

    def test_compute_kpis_basic(self) -> None:
        """测试 6 大 KPI 计算基础逻辑。"""
        from app.tasks.kpi_calc import _compute_kpis

        # 构造对齐的时序数据：10 个点，全部 Auto 模式，PV=SP
        aligned = [
            {
                "ts": f"2026-06-22T08:00:{i:02d}",
                "pv": 50.0,
                "sp": 50.0,
                "op": 50.0,
                "mode": 1,  # Auto
            }
            for i in range(10)
        ]
        metric_configs: dict = {}
        kpis = _compute_kpis(aligned, metric_configs)
        # 全部 Good → good_value_rate = 100
        assert kpis["good_value_rate"] == Decimal("100.00")
        # 全部 Auto → auto_mode_rate = 100
        assert kpis["auto_mode_rate"] == Decimal("100.00")
        # PV == SP → steady_rate = 100
        assert kpis["steady_rate"] == Decimal("100.00")
        # PV == SP → accuracy_rate = 100
        assert kpis["accuracy_rate"] == Decimal("100.00")
        # op = 50 → saturation_rate = 0
        assert kpis["saturation_rate"] == Decimal("0.00")

    def test_compute_kpis_empty(self) -> None:
        """空数据返回所有 None。"""
        from app.tasks.kpi_calc import _compute_kpis

        kpis = _compute_kpis([], {})
        for code in (
            "good_value_rate",
            "auto_mode_rate",
            "steady_rate",
            "accuracy_rate",
            "oscillation_rate",
            "saturation_rate",
        ):
            assert kpis[code] is None

    def test_compute_composite_score(self) -> None:
        """测试综合评分计算（国标 4 分项加法公式）。"""
        from app.tasks.kpi_calc import _compute_composite_score

        # 构造 4 分项指标配置（对齐 GB/T 44693.2-2024）
        configs = {
            "accuracy_rate": _make_metric_config(
                metric_code="accuracy_rate", weight=Decimal("30")
            ),
            "fast_response_rate": _make_metric_config(
                metric_id="id2",
                metric_code="fast_response_rate",
                weight=Decimal("20"),
            ),
            "steady_rate": _make_metric_config(
                metric_id="id3",
                metric_code="steady_rate",
                weight=Decimal("30"),
            ),
            "effective_auto_rate": _make_metric_config(
                metric_id="id4",
                metric_code="effective_auto_rate",
                weight=Decimal("20"),
            ),
        }

        kpi_values = {
            "accuracy_rate": Decimal("100"),
            "fast_response_rate": Decimal("100"),
            "steady_rate": Decimal("100"),
            "effective_auto_rate": Decimal("100"),
        }

        score = _compute_composite_score(kpi_values, configs)
        # P = (30*1 + 20*1 + 30*1 + 20*1) / 100 * 100 = 100
        assert score == Decimal("100.00")

    def test_compute_composite_score_disabled_metric(self) -> None:
        """停用的指标不参与评分（国标 4 分项加法公式）。"""
        from app.tasks.kpi_calc import _compute_composite_score

        configs = {
            "accuracy_rate": _make_metric_config(
                metric_code="accuracy_rate", weight=Decimal("30"), is_enabled=False
            ),
            "fast_response_rate": _make_metric_config(
                metric_id="id2",
                metric_code="fast_response_rate",
                weight=Decimal("20"),
                is_enabled=True,
            ),
            "steady_rate": _make_metric_config(
                metric_id="id3",
                metric_code="steady_rate",
                weight=Decimal("30"),
                is_enabled=True,
            ),
            "effective_auto_rate": _make_metric_config(
                metric_id="id4",
                metric_code="effective_auto_rate",
                weight=Decimal("20"),
                is_enabled=True,
            ),
        }
        kpi_values = {
            "accuracy_rate": Decimal("100"),
            "fast_response_rate": Decimal("100"),
            "steady_rate": Decimal("100"),
            "effective_auto_rate": Decimal("100"),
        }
        score = _compute_composite_score(kpi_values, configs)
        # accuracy_rate 停用，仅 3 指标参与
        # P = (20*1 + 30*1 + 20*1) / (20+30+20) * 100 = 70/70 * 100 = 100
        assert score == Decimal("100.00")

    def test_is_auto_mode(self) -> None:
        """测试 Auto 模式判定。"""
        from app.tasks.kpi_calc import _is_auto_mode

        assert _is_auto_mode(1) is True  # Auto
        assert _is_auto_mode(2) is True  # Cascade
        assert _is_auto_mode(3) is True  # Cascade
        assert _is_auto_mode(0) is False  # Manual
        assert _is_auto_mode(None) is False
        assert _is_auto_mode("invalid") is False

    def test_detect_oscillation(self) -> None:
        """测试振荡检测（IAE 零交叉相似率法）。"""
        from app.tasks.kpi_calc import _compute_oscillation_rate

        # 单调递增 → 无振荡（零交叉点 < 4）
        aligned_up = [{"pv": float(i), "sp": 0.0} for i in range(5)]
        osc_rate, is_osc, _ = _compute_oscillation_rate(aligned_up)
        assert osc_rate == 0
        assert is_osc is False

        # 交替变化 → 振荡（PV 围绕 SP 上下波动，产生正负交替偏差）
        aligned_osc = [
            {"pv": v, "sp": 0.0}
            for v in [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0]
        ]
        osc_rate, is_osc, _ = _compute_oscillation_rate(aligned_osc)
        assert osc_rate > 0

    def test_align_timeseries(self) -> None:
        """测试时序对齐。"""
        from app.tasks.kpi_calc import _align_timeseries

        pv_data = [
            {"ts": "t1", "value": 10.0, "quality": "GOOD"},
            {"ts": "t2", "value": 20.0, "quality": "GOOD"},
        ]
        sp_data = [{"ts": "t1", "value": 11.0}, {"ts": "t2", "value": 21.0}]
        op_data = [{"ts": "t1", "value": 50.0}]
        mode_data = [{"ts": "t1", "value": 1}]

        aligned = _align_timeseries(pv_data, sp_data, op_data, mode_data)
        assert len(aligned) == 2
        assert aligned[0]["pv"] == 10.0
        assert aligned[0]["sp"] == 11.0
        assert aligned[0]["op"] == 50.0
        assert aligned[0]["mode"] == 1
        # t2 的 op/mode 缺失
        assert aligned[1]["op"] is None
        assert aligned[1]["mode"] is None

    def test_align_timeseries_tolerance_numeric(self) -> None:
        """测试时序对齐：数值时间戳容差匹配（±500ms）。"""
        from app.tasks.kpi_calc import _align_timeseries

        # PV 时间戳与 SP/OP 略有偏差（200ms），应在容差范围内匹配
        pv_data = [
            {"ts": 1000.0, "value": 10.0, "quality": "GOOD"},
            {"ts": 1001.0, "value": 20.0, "quality": "GOOD"},
        ]
        sp_data = [
            {"ts": 1000.2, "value": 11.0},  # 偏差 200ms
            {"ts": 1001.1, "value": 21.0},  # 偏差 100ms
        ]
        op_data = [{"ts": 1000.3, "value": 50.0}]  # 偏差 300ms
        mode_data = [{"ts": 1000.4, "value": 1}]  # 偏差 400ms

        aligned = _align_timeseries(pv_data, sp_data, op_data, mode_data)
        assert len(aligned) == 2
        assert aligned[0]["pv"] == 10.0
        assert aligned[0]["sp"] == 11.0
        assert aligned[0]["op"] == 50.0
        assert aligned[0]["mode"] == 1
        # 第二个点只有 sp 在容差内
        assert aligned[1]["sp"] == 21.0
        assert aligned[1]["op"] is None
        assert aligned[1]["mode"] is None

    def test_align_timeseries_tolerance_out_of_range(self) -> None:
        """测试时序对齐：超出容差范围（>500ms）不匹配。"""
        from app.tasks.kpi_calc import _align_timeseries

        pv_data = [{"ts": 1000.0, "value": 10.0, "quality": "GOOD"}]
        # 偏差 600ms，超出容差
        sp_data = [{"ts": 1000.6, "value": 11.0}]

        aligned = _align_timeseries(pv_data, sp_data, [], [])
        assert aligned[0]["sp"] is None

    def test_align_timeseries_iso_string_tolerance(self) -> None:
        """测试时序对齐：ISO 字符串时间戳容差匹配。"""
        from app.tasks.kpi_calc import _align_timeseries

        pv_data = [
            {"ts": "2026-06-22T08:00:00.000Z", "value": 10.0, "quality": "GOOD"},
        ]
        # 偏差 200ms
        sp_data = [{"ts": "2026-06-22T08:00:00.200Z", "value": 11.0}]

        aligned = _align_timeseries(pv_data, sp_data, [], [])
        assert aligned[0]["sp"] == 11.0

    def test_detect_oscillation_amplitude_threshold(self) -> None:
        """S4-B2: 振荡检测振幅阈值过滤噪声（IAE 零交叉相似率法）。"""
        from app.tasks.kpi_calc import _compute_oscillation_rate

        # 微小幅度交替变化（噪声级），零交叉点不足或相似率低
        aligned_noise = [
            {"pv": v, "sp": 50.0}
            for v in [50.0, 50.001, 50.0, 50.001, 50.0, 50.001, 50.0]
        ]
        osc_rate, _, _ = _compute_oscillation_rate(aligned_noise)
        # 噪声级振荡不应被判定为严重振荡
        assert osc_rate == 0 or osc_rate <= Decimal("10")

        # 大幅度交替变化（真实振荡），需 >= 4 个零交叉点（>= 2 个周期）
        # PV 围绕 SP 上下波动，产生正负交替的偏差
        aligned_osc = [
            {"pv": v, "sp": 50.0}
            for v in [45.0, 55.0, 45.0, 55.0, 45.0, 55.0, 45.0, 55.0, 45.0, 55.0, 45.0]
        ]
        osc_rate, is_osc, _ = _compute_oscillation_rate(aligned_osc)
        assert osc_rate > 0

    def test_good_value_rate_before_filtering(self) -> None:
        """S4-B6: good_value_rate 在过滤前计算，反映真实数据质量。"""
        from decimal import Decimal

        from app.tasks.kpi_calc import _compute_kpis

        # 构造对齐数据（已过滤 Bad 质量码）
        aligned = [
            {
                "ts": f"t{i}",
                "pv": 50.0,
                "sp": 50.0,
                "op": 50.0,
                "mode": 1,
            }
            for i in range(10)
        ]
        # 好值率 80% 表示原始数据有 20% Bad 质量码（已过滤）
        kpis = _compute_kpis(aligned, {}, good_value_rate=Decimal("80.00"))
        assert kpis["good_value_rate"] == Decimal("80.00")

    def test_good_value_rate_defaults_to_100(self) -> None:
        """S4-B6: good_value_rate=None 时默认 100（向后兼容）。"""
        from decimal import Decimal

        from app.tasks.kpi_calc import _compute_kpis

        aligned = [
            {"ts": "t0", "pv": 50.0, "sp": 50.0, "op": 50.0, "mode": 1}
        ]
        kpis = _compute_kpis(aligned, {}, good_value_rate=None)
        assert kpis["good_value_rate"] == Decimal("100.00")


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

    async def test_get_board_empty(self) -> None:
        """无快照数据时看板返回空 KPI 卡片。"""
        from app.services.performance import get_board

        db = AsyncMock()
        # get_board 在 plant_node_id=None 时跳过装置名查询，依次调用：
        # 1. _aggregate_kpi_cards → result.one() 期望 row.cnt=0
        # 2. _aggregate_kpi_summary → result.one() 期望 row.cnt=0
        # 3. _aggregate_steady_trend → result.all() 期望空列表
        # 4. count_stmt (partialWarning) → result.all() 期望空列表
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
