"""Diagnosis center API tests (S4-DIAG-001~006).

Covers:
- GET /api/v1/diagnosis/metrics (list)
- PUT /api/v1/diagnosis/metrics/{id} (update, RBAC)
- GET /api/v1/diagnosis/list (filter/pagination)
- GET /api/v1/diagnosis/{loopId} (detail)
- GET /api/v1/timeseries/{loopId}/waveform (LTTB/quality code)
- PATCH /api/v1/tracker/{loopId}/status (status update, RBAC)
- POST /api/v1/tracker/{loopId}/export (PDF export)
- GET /api/v1/diagnosis/analytics (analytics)
- POST /api/v1/diagnosis/analytics/export (export)
- RBAC: 非 ADMIN 不能修改配置；非 IC_ENGINEER 不能更新 Tracker 状态
- 诊断引擎单元测试（FFT/散点拟合/DS 融合）
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------


def _make_diag_config(
    diag_id: str = "00000000-0000-0000-0000-000000000a01",
    diag_code: str = "OSCILLATION",
    diag_name: str = "振荡",
    is_enabled: bool = True,
) -> MagicMock:
    """构造 DiagnosisConfig mock。"""
    c = MagicMock()
    c.id = diag_id
    c.diag_code = diag_code
    c.diag_name = diag_name
    c.algorithm_type = "FFT"
    c.calc_method = "frequency_domain"
    c.params = {"window_size": 1024}
    c.threshold = {"oscillation_index": 0.3}
    c.is_enabled = is_enabled
    c.updated_by = "admin"
    c.updated_at = datetime.now(UTC)
    c.version = 1
    return c


def _make_diag_result(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    diag_label: str = "VALVE_STICTION",
    confidence: Decimal = Decimal("85.00"),
    evidence_chain: dict | None = None,
    feature_values: dict | None = None,
) -> MagicMock:
    """构造 DiagnosisResult mock。"""
    r = MagicMock()
    r.id = str(uuid4())
    r.loop_id = loop_id
    r.diag_label = diag_label
    r.confidence = confidence
    r.feature_values = feature_values or {"stiction_index": 0.78}
    r.evidence_chain = evidence_chain or {
        "fused_confidence": 0.82,
        "reasoning": "PV-OP 散点图呈现椭圆轨迹",
        "scatter_plot": "/api/v1/timeseries/xxx/scatter",
    }
    r.algorithm_version = "DIAG_ENGINE_v1.0"
    r.diagnosed_at = datetime.now(UTC)
    return r


def _make_loop(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_name: str = "101-FC-1023",
    unit_id: str = "00000000-0000-0000-0000-000000000111",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "测试回路"
    loop.unit_id = unit_id
    loop.status = "READY"
    loop.is_active = True
    loop.score_weight = Decimal("45.20")
    return loop


def _make_tracker(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    action_status: str = "PENDING",
    diagnosis_label: str = "VALVE_STICTION",
) -> MagicMock:
    """构造 ActionTracker mock。"""
    t = MagicMock()
    t.id = str(uuid4())
    t.loop_id = loop_id
    t.diagnosis_label = diagnosis_label
    t.action_status = action_status
    t.evidence_url = None
    t.updated_by = "ic_engineer"
    t.updated_at = datetime.now(UTC)
    return t


def _make_snapshot(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    score: Decimal = Decimal("45.20"),
) -> MagicMock:
    """构造 KpiSnapshotHourly mock。"""
    s = MagicMock()
    s.id = str(uuid4())
    s.loop_id = loop_id
    s.ts_start = datetime.now(UTC)
    s.ts_end = s.ts_start
    s.score = score
    s.status = "SUCCESS"
    return s


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_rows_mock(rows: list[tuple]) -> MagicMock:
    """构造 select 多列结果 mock（.all() 返回 rows 列表）。"""
    result = MagicMock()
    result.all.return_value = rows
    # 部分代码使用 .scalars().all()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [r[0] for r in rows]
    result.scalars.return_value = scalars_mock
    return result


# ---------------------------------------------------------------------------
# S4-DIAG-001: 诊断指标配置 CRUD
# ---------------------------------------------------------------------------


class TestDiagnosisConfigList:
    """GET /api/v1/diagnosis/metrics tests."""

    def test_list_metrics_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取诊断指标配置列表。"""
        configs = [
            _make_diag_config(diag_code="OSCILLATION", diag_name="振荡"),
            _make_diag_config(
                diag_id="00000000-0000-0000-0000-000000000a02",
                diag_code="VALVE_STICTION",
                diag_name="阀门粘滞",
            ),
        ]
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(configs))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/metrics",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]) == 2
        assert body["data"][0]["diagCode"] == "OSCILLATION"
        assert body["data"][0]["diagName"] == "振荡"

    def test_list_metrics_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/metrics")
        assert resp.status_code == 401


class TestDiagnosisConfigUpdate:
    """PUT /api/v1/diagnosis/metrics/{diagId} tests."""

    def test_update_metric_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以更新诊断指标配置。"""
        config = _make_diag_config()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(config))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/diagnosis/metrics/{config.id}",
                headers={"Authorization": "Bearer fake-token"},
                json={"diagName": "振荡（更新）", "isEnabled": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["diagName"] == "振荡（更新）"

    def test_update_metric_not_found(self, client, mock_db, fake_redis) -> None:
        """配置不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/diagnosis/metrics/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
                json={"diagName": "更新"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_CONFIG_NOT_FOUND"

    def test_update_metric_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能修改诊断配置（403，仅 ADMIN）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/diagnosis/metrics/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"diagName": "更新"},
            )
        assert resp.status_code == 403

    def test_update_metric_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能修改诊断配置（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.put(
                "/api/v1/diagnosis/metrics/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"diagName": "更新"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# S4-DIAG-003: 诊断列表与详情
# ---------------------------------------------------------------------------


class TestDiagnosisList:
    """GET /api/v1/diagnosis/list tests."""

    def test_list_diagnosis_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取诊断列表（含全量聚合统计）。"""
        loop = _make_loop()
        diag = _make_diag_result()
        tracker = _make_tracker()
        snapshot = _make_snapshot()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            # 1: count, 2: 聚合（标签×状态）, 3: 主查询, 4: unit_name, 5: score
            if call_count[0] == 1:
                m = MagicMock()
                m.scalar.return_value = 1
                return m
            if call_count[0] == 2:
                return _make_rows_mock([("VALVE_STICTION", "PENDING", 1)])
            if call_count[0] == 3:
                return _make_rows_mock([(diag, loop, tracker)])
            if call_count[0] == 4:
                node = MagicMock()
                node.id = loop.unit_id
                node.name = "常减压装置-单元A"
                return _make_scalars_mock([node])
            # score subquery
            score_result = MagicMock()
            score_result.all.return_value = [(loop.id, snapshot.score)]
            return score_result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/list",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "pageSize" in data
        if data["items"]:
            item = data["items"][0]
            assert "loopId" in item
            assert "tagName" in item
            assert "diagnosisLabel" in item
            assert "confidence" in item
            assert "actionStatus" in item
        # 聚合统计：对全部筛选结果聚合（A4）
        assert data["aggregates"]["total"] == 1
        assert data["aggregates"]["statusCounts"] == {"PENDING": 1}
        assert data["aggregates"]["labelCounts"] == {"VALVE_STICTION": 1}

    def test_list_diagnosis_aggregates_stable_across_pages(
        self, client, mock_db, fake_redis
    ) -> None:
        """翻页不影响聚合计数（A4：聚合基于全部筛选结果而非当前页）。"""
        # 每个请求固定 3 步查询：count → 聚合（标签×状态） → 主查询（返回空页）
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            pos = (call_count[0] - 1) % 3
            if pos == 0:
                m = MagicMock()
                m.scalar.return_value = 25
                return m
            if pos == 1:
                return _make_rows_mock(
                    [
                        ("VALVE_STICTION", "PENDING", 15),
                        ("OSCILLATION", "IMPLEMENTED", 10),
                    ]
                )
            return _make_rows_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp_p1 = client.get(
                "/api/v1/diagnosis/list?page=1&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
            resp_p2 = client.get(
                "/api/v1/diagnosis/list?page=3&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp_p1.status_code == 200
        assert resp_p2.status_code == 200
        agg_p1 = resp_p1.json()["data"]["aggregates"]
        agg_p2 = resp_p2.json()["data"]["aggregates"]
        # 第 3 页已无数据，但聚合计数与第 1 页一致
        assert resp_p2.json()["data"]["items"] == []
        assert agg_p1 == agg_p2
        assert agg_p1["total"] == 25
        assert agg_p1["statusCounts"] == {"PENDING": 15, "IMPLEMENTED": 10}
        assert agg_p1["labelCounts"] == {"VALVE_STICTION": 15, "OSCILLATION": 10}

    def test_list_diagnosis_with_filter(self, client, mock_db, fake_redis) -> None:
        """诊断列表支持筛选。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                m = MagicMock()
                m.scalar.return_value = 0
                return m
            return _make_rows_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/diagnosis/list?diagnosisLabel=VALVE_STICTION&actionStatus=PENDING",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 0

    def test_list_diagnosis_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/list")
        assert resp.status_code == 401


class TestDiagnosisDetail:
    """GET /api/v1/diagnosis/{loopId} tests."""

    def test_get_detail_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取诊断详情。"""
        loop = _make_loop()
        diag = _make_diag_result()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 1. loop (scalar_one_or_none)
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                # 2. latest_diag (scalar_one_or_none) → latest_record
                return _make_scalar_one_or_none_mock(diag)
            if call_count[0] == 3:
                # 3. diag_result (scalars) — latest_record.task_id 为 truthy MagicMock
                return _make_scalars_mock([diag])
            # 4. snapshot (scalar_one_or_none)
            return _make_scalar_one_or_none_mock(_make_snapshot())

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/{loop.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == loop.id
        assert data["tagName"] == loop.tag_name
        assert "diagnosisLabels" in data
        assert "evidenceChain" in data
        assert "featureValues" in data
        assert "algorithmVersion" in data

    def test_get_detail_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/00000000-0000-0000-0000-000000000000",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_get_detail_no_diag_result(self, client, mock_db, fake_redis) -> None:
        """回路无诊断结果返回 404。"""
        loop = _make_loop()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 1. loop (scalar_one_or_none)
                return _make_scalar_one_or_none_mock(loop)
            # 2. latest_diag (scalar_one_or_none) → None，触发 ERR_DIAG_RESULT_NOT_FOUND
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/{loop.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_RESULT_NOT_FOUND"


# ---------------------------------------------------------------------------
# S4-DIAG-004: 波形查询
# ---------------------------------------------------------------------------


class TestWaveform:
    """GET /api/v1/timeseries/{loopId}/waveform tests."""

    def test_get_waveform_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取波形数据。"""
        loop = _make_loop()
        # 构造 Tag 关联和 Tag 详情
        mapping = MagicMock()
        mapping.tag_role = "PV"
        mapping.tag_id = "tag-001"
        tag = MagicMock()
        tag.id = "tag-001"
        tag.tag_name = "101-FC-1023.PV"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return _make_scalars_mock([mapping])
            return _make_scalars_mock([tag])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        # Mock 宽表查询
        from app.contracts.data_types import RawTimeSeries

        raw_series = RawTimeSeries(
            timestamps=[f"2026-06-22T08:00:{i:02d}Z" for i in range(10)],
            signals={"pv": [50.0 + i * 0.1 for i in range(10)]},
            quality_codes={"pv_quality": ["GOOD"] * 10},
        )

        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
            mock_get_provider.return_value = mock_provider
            with mock_current_user(TEST_USERS["admin"]):
                resp = client.get(
                    f"/api/v1/timeseries/{loop.id}/waveform",
                    headers={"Authorization": "Bearer fake-token"},
                    params={
                        "startTime": "2026-06-22T08:00:00Z",
                        "endTime": "2026-06-22T08:00:10Z",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == loop.id
        assert "timestamps" in data
        assert "pv" in data
        assert "sp" in data
        assert "op" in data
        assert "mode" in data
        assert "pvQuality" in data
        assert len(data["timestamps"]) == 10
        assert len(data["pv"]) == 10

    def test_get_waveform_quality_bad(self, client, mock_db, fake_redis) -> None:
        """PV 质量码为 Bad 时，pv 值为 null。"""
        loop = _make_loop()
        # 构造 Tag 关联和 Tag 详情
        mapping = MagicMock()
        mapping.tag_role = "PV"
        mapping.tag_id = "tag-001"
        tag = MagicMock()
        tag.id = "tag-001"
        tag.tag_name = "101-FC-1023.PV"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return _make_scalars_mock([mapping])
            return _make_scalars_mock([tag])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        from app.contracts.data_types import RawTimeSeries

        raw_series = RawTimeSeries(
            timestamps=[
                "2026-06-22T08:00:00Z",
                "2026-06-22T08:00:01Z",
                "2026-06-22T08:00:02Z",
            ],
            signals={"pv": [50.0, 51.0, 52.0]},
            quality_codes={"pv_quality": ["GOOD", "BAD", "GOOD"]},
        )

        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
            mock_get_provider.return_value = mock_provider
            with mock_current_user(TEST_USERS["admin"]):
                resp = client.get(
                    f"/api/v1/timeseries/{loop.id}/waveform",
                    headers={"Authorization": "Bearer fake-token"},
                    params={
                        "startTime": "2026-06-22T08:00:00Z",
                        "endTime": "2026-06-22T08:00:10Z",
                    },
                )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 第二个点质量为 Bad，pv 应为 null
        assert len(data["pv"]) == 3
        assert data["pv"][1] is None
        assert data["pvQuality"][1] == "Bad"
        assert data["pv"][0] == 50.0
        assert data["pvQuality"][0] == "Good"

    def test_get_waveform_time_window_exceeded(self, client, mock_db, fake_redis) -> None:
        """时间窗超过 30 天返回 ERR_TS_001。"""
        loop = _make_loop()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(loop))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/timeseries/{loop.id}/waveform",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startTime": "2026-05-01T00:00:00Z",
                    "endTime": "2026-06-22T00:00:00Z",  # 超过 30 天
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_TS_001"

    def test_get_waveform_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/timeseries/00000000-0000-0000-0000-000000000000/waveform",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startTime": "2026-06-22T08:00:00Z",
                    "endTime": "2026-06-22T08:00:10Z",
                },
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_get_waveform_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get(
            "/api/v1/timeseries/00000000-0000-0000-0000-000000000001/waveform",
            params={"startTime": "2026-06-22T08:00:00Z", "endTime": "2026-06-22T08:00:10Z"},
        )
        assert resp.status_code == 401


class TestLTTBDownsample:
    """LTTB 降采样单元测试。"""

    def test_lttb_no_downsample(self) -> None:
        """数据点数不超过阈值时不降采样。"""
        from app.services.waveform import lttb_downsample_multi_series

        timestamps = list(range(100))
        series_map = {"pv": [float(i) for i in range(100)]}
        new_ts, new_series = lttb_downsample_multi_series(timestamps, series_map, 200)
        assert len(new_ts) == 100
        assert len(new_series["pv"]) == 100

    def test_lttb_downsample(self) -> None:
        """数据点数超过阈值时降采样。"""
        from app.services.waveform import lttb_downsample_multi_series

        timestamps = list(range(1000))
        series_map = {
            "pv": [float(i) for i in range(1000)],
            "sp": [50.0] * 1000,
        }
        new_ts, new_series = lttb_downsample_multi_series(timestamps, series_map, 100)
        assert len(new_ts) == 100
        assert len(new_series["pv"]) == 100
        assert len(new_series["sp"]) == 100
        # 首尾点保留
        assert new_ts[0] == 0
        assert new_ts[-1] == 999


# ---------------------------------------------------------------------------
# S4-DIAG-005: Action Tracker
# ---------------------------------------------------------------------------


class TestTrackerStatusUpdate:
    """PATCH /api/v1/tracker/{loopId}/status tests."""

    def test_update_status_success(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 可以更新 Tracker 状态。"""
        loop = _make_loop()
        tracker = _make_tracker()
        diag = _make_diag_result()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return _make_scalar_one_or_none_mock(tracker)
            # IMPLEMENTED 时查询 diag
            return _make_scalar_one_or_none_mock(diag)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.patch(
                f"/api/v1/tracker/{loop.id}/status",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "IN_PROGRESS"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["actionStatus"] == "IN_PROGRESS"

    def test_update_status_resolved(self, client, mock_db, fake_redis) -> None:
        """IMPLEMENTED 状态时自动生成 A/B 对比视图。"""
        loop = _make_loop()
        tracker = _make_tracker()
        diag = _make_diag_result()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return _make_scalar_one_or_none_mock(tracker)
            return _make_scalar_one_or_none_mock(diag)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.patch(
                f"/api/v1/tracker/{loop.id}/status",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "IMPLEMENTED"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["actionStatus"] == "IMPLEMENTED"
        assert body["data"]["abComparison"] is not None
        assert "beforeWindow" in body["data"]["abComparison"]
        assert "afterWindow" in body["data"]["abComparison"]

    def test_update_status_invalid(self, client, mock_db, fake_redis) -> None:
        """无效状态返回 422。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.patch(
                "/api/v1/tracker/00000000-0000-0000-0000-000000000001/status",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "INVALID"},
            )
        assert resp.status_code == 422

    def test_update_status_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.patch(
                "/api/v1/tracker/00000000-0000-0000-0000-000000000000/status",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "IN_PROGRESS"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_update_status_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """ADMIN 不能更新 Tracker 状态（403，仅 IC_ENGINEER）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.patch(
                "/api/v1/tracker/00000000-0000-0000-0000-000000000000/status",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "IN_PROGRESS"},
            )
        assert resp.status_code == 403

    def test_update_status_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能更新 Tracker 状态（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.patch(
                "/api/v1/tracker/00000000-0000-0000-0000-000000000001/status",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "IN_PROGRESS"},
            )
        assert resp.status_code == 403

    def test_update_status_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.patch(
            "/api/v1/tracker/00000000-0000-0000-0000-000000000001/status",
            json={"status": "IN_PROGRESS"},
        )
        assert resp.status_code == 401


class TestTrackerExport:
    """POST /api/v1/tracker/{loopId}/export tests."""

    def test_export_pdf_success(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 可以导出 PDF（同步生成，直接返回 application/pdf）。"""
        loop = _make_loop()
        diag = _make_diag_result()
        snapshot = _make_snapshot()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            # 1: export_tracker_pdf 校验回路
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            # 2~5: get_diagnosis_detail（loop / 最新诊断 / 该任务全部诊断 / 最新快照）
            if call_count[0] == 2:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 3:
                return _make_scalar_one_or_none_mock(diag)
            if call_count[0] == 4:
                return _make_scalars_mock([diag])
            if call_count[0] == 5:
                return _make_scalar_one_or_none_mock(snapshot)
            # 6~7: get_recommendations_for_loop（loop / 去重标签）
            if call_count[0] == 6:
                return _make_scalar_one_or_none_mock(loop)
            result = MagicMock()
            result.all.return_value = [("VALVE_STICTION",)]
            return result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/tracker/{loop.id}/export",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")
        disposition = resp.headers["content-disposition"]
        assert "attachment" in disposition
        # 文件名：CLPM-诊断建议书-[位号]-[日期].pdf（中文部分经 RFC 5987 编码）
        assert "CLPM-" in disposition

    def test_export_pdf_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/tracker/00000000-0000-0000-0000-000000000000/export",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_export_pdf_no_diag_result(self, client, mock_db, fake_redis) -> None:
        """回路无诊断结果时返回 404（ERR_DIAG_RESULT_NOT_FOUND）。"""
        loop = _make_loop()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                # 1: export 校验回路；2: get_diagnosis_detail 查回路
                return _make_scalar_one_or_none_mock(loop)
            # 3: 无诊断结果
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/tracker/{loop.id}/export",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_RESULT_NOT_FOUND"


class TestAbCompare:
    """GET /api/v1/diagnosis/ab-compare tests."""

    @staticmethod
    def _make_window_agg_mock(count: int, avgs: list) -> MagicMock:
        """构造窗口聚合查询（func.count + 8 项 func.avg）结果 mock。"""
        result = MagicMock()
        result.one.return_value = (count, *avgs)
        return result

    def test_ab_compare_success_with_implemented_at(self, client, mock_db, fake_redis) -> None:
        """按 implementedAt 截取 [T-7d,T) 与 (T,T+7d]，返回 KPI 前后对比。"""
        loop = _make_loop()
        # 8 项 KPI 均值（顺序对齐 AB_COMPARE_KPIS）
        before_avgs = [
            Decimal("40"),
            Decimal("70"),
            Decimal("80"),
            Decimal("60"),
            Decimal("90"),
            Decimal("20"),
            Decimal("10"),
            Decimal("30"),
        ]
        after_avgs = [
            Decimal("50"),
            Decimal("75"),
            Decimal("85"),
            Decimal("65"),
            Decimal("95"),
            Decimal("10"),
            Decimal("5"),
            Decimal("20"),
        ]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return self._make_window_agg_mock(100, before_avgs)
            # after 窗口 200 条快照（>=24 → 数据充足）
            return self._make_window_agg_mock(200, after_avgs)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/ab-compare?loopId={loop.id}&implementedAt=2026-07-10T08:00:00Z",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == loop.id
        assert data["tagName"] == loop.tag_name
        assert data["implementedAt"] is not None
        assert data["dataInsufficient"] is False
        # 窗口：[T-7d,T) 与 (T,T+7d]
        assert data["beforeWindow"]["startTime"].startswith("2026-07-03")
        assert data["afterWindow"]["endTime"].startswith("2026-07-17")
        assert "waveformUrl" in data["beforeWindow"]
        assert "waveformUrl" in data["afterWindow"]
        kpis = {item["metricKey"]: item for item in data["kpiComparison"]}
        assert len(kpis) == 8
        # 综合评分 40→50 上升，正向指标 → 改善
        assert kpis["score"]["before"] == 40.0
        assert kpis["score"]["after"] == 50.0
        assert kpis["score"]["improved"] is True
        # 振荡率 20→10 下降，负向指标 → 改善
        assert kpis["oscillation_rate"]["improved"] is True
        # 粘滞指数 30→20 下降，负向指标 → 改善
        assert kpis["stiction_index"]["improved"] is True

    def test_ab_compare_data_insufficient(self, client, mock_db, fake_redis) -> None:
        """实施后窗口快照数 <24 时 dataInsufficient=true。"""
        loop = _make_loop()
        avgs = [Decimal("50")] * 8

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return self._make_window_agg_mock(100, avgs)
            # after 窗口仅 10 条快照（<24 → 数据不足）
            return self._make_window_agg_mock(10, avgs)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/ab-compare?loopId={loop.id}&implementedAt=2026-07-10T08:00:00Z",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["dataInsufficient"] is True

    def test_ab_compare_explicit_windows(self, client, mock_db, fake_redis) -> None:
        """显式传入前后窗口参数时按显式窗口聚合。"""
        loop = _make_loop()
        avgs = [Decimal("50")] * 8

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return self._make_window_agg_mock(100, avgs)
            return self._make_window_agg_mock(100, avgs)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/ab-compare?loopId={loop.id}"
                "&beforeStartTime=2026-07-01 00:00:00&beforeEndTime=2026-07-07 00:00:00"
                "&afterStartTime=2026-07-08 00:00:00&afterEndTime=2026-07-14 00:00:00",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["implementedAt"] is None
        assert data["beforeWindow"]["startTime"].startswith("2026-07-01")
        assert data["afterWindow"]["endTime"].startswith("2026-07-14")

    def test_ab_compare_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/ab-compare?loopId=00000000-0000-0000-0000-000000000000"
                "&implementedAt=2026-07-10T08:00:00Z",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_ab_compare_missing_window_params(self, client, mock_db, fake_redis) -> None:
        """既无 implementedAt 也无完整窗口参数时返回 422。"""
        loop = _make_loop()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(loop))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/ab-compare?loopId={loop.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_ab_compare_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get(
            "/api/v1/diagnosis/ab-compare?loopId=00000000-0000-0000-0000-000000000001"
            "&implementedAt=2026-07-10T08:00:00Z"
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# S4-DIAG-006: 诊断统计报表
# ---------------------------------------------------------------------------


class TestDiagnosisAnalytics:
    """GET /api/v1/diagnosis/analytics tests."""

    def test_get_analytics_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取诊断统计报表。"""
        loop = _make_loop()
        diag = _make_diag_result()
        tracker = _make_tracker(action_status="IMPLEMENTED")

        mock_db.execute = AsyncMock(return_value=_make_rows_mock([(diag, loop, tracker)]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/analytics",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startTime": "2026-06-01T00:00:00Z",
                    "endTime": "2026-06-22T00:00:00Z",
                    "granularity": "day",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "filterScope" in data
        assert "labelDistribution" in data
        assert "efficiencyTrend" in data
        assert "closeDurationDistribution" in data
        # 闭环时长分布应为 3 档
        assert len(data["closeDurationDistribution"]) == 3
        ranges = [item["range"] for item in data["closeDurationDistribution"]]
        assert "0-24h" in ranges
        assert "24-72h" in ranges
        assert "72h+" in ranges

    def test_get_analytics_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/analytics")
        assert resp.status_code == 401


class TestAnalyticsExport:
    """POST /api/v1/diagnosis/analytics/export tests."""

    def test_export_analytics_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以导出统计报表。"""
        with mock_current_user(TEST_USERS["admin"]):
            with patch("app.tasks.report_generator.export_diagnosis_statistics") as mock_task:
                mock_result = MagicMock()
                mock_result.id = "test-task-id"
                mock_task.delay.return_value = mock_result
                resp = client.post(
                    "/api/v1/diagnosis/analytics/export",
                    headers={"Authorization": "Bearer fake-token"},
                    json={
                        "startTime": "2026-06-01T00:00:00Z",
                        "endTime": "2026-06-22T00:00:00Z",
                        "granularity": "day",
                        "format": "csv",
                    },
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert "taskId" in body["data"]

    def test_export_analytics_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.post(
            "/api/v1/diagnosis/analytics/export",
            json={
                "startTime": "2026-06-01T00:00:00Z",
                "endTime": "2026-06-22T00:00:00Z",
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 诊断引擎单元测试
# ---------------------------------------------------------------------------


class TestDiagnosisEngine:
    """诊断引擎算法单元测试。"""

    def test_detect_oscillation_fft_no_oscillation(self) -> None:
        """测试 FFT 振荡检测：无振荡信号。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        # 单调递增序列：无振荡
        pv_values = np.array([float(i) for i in range(100)], dtype=float)
        result = _detect_oscillation_fft(pv_values)
        assert result["detected"] is False
        assert result["confidence"] == 0.0

    def test_detect_oscillation_fft_with_oscillation(self) -> None:
        """测试 FFT 振荡检测：有振荡信号。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        # 正弦波：明显振荡
        t = np.linspace(0, 10 * np.pi, 200)
        pv_values = 50.0 + 10.0 * np.sin(t)
        result = _detect_oscillation_fft(pv_values)
        assert result["detected"] is True
        assert result["confidence"] > 0.0
        assert result["amplitude"] > 0.0

    def test_detect_oscillation_fft_short_data(self) -> None:
        """测试 FFT 振荡检测：数据不足。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values = np.array([1.0, 2.0, 3.0], dtype=float)
        result = _detect_oscillation_fft(pv_values)
        assert result["detected"] is False

    def test_detect_valve_stiction_no_stiction(self) -> None:
        """测试阀门粘滞检测：无粘滞。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _detect_valve_stiction

        # PV 和 OP 完全同步：无粘滞
        t = np.linspace(0, 10, 100)
        pv = 50.0 + t
        op = 50.0 + t
        result = _detect_valve_stiction(pv, op)
        # 完全同步时，op_static & pv_moving 应为 False
        assert result["stiction_index"] < 0.5

    def test_detect_valve_stiction_short_data(self) -> None:
        """测试阀门粘滞检测：数据不足。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _detect_valve_stiction

        pv = np.array([1.0, 2.0], dtype=float)
        op = np.array([1.0, 2.0], dtype=float)
        result = _detect_valve_stiction(pv, op)
        assert result["detected"] is False

    def test_analyze_quality_normal(self) -> None:
        """测试 PV 质量码统计：正常。"""
        from app.tasks.diagnosis_engine import _analyze_quality

        pv_data = [{"value": 50.0, "quality": "GOOD"} for _ in range(100)]
        result = _analyze_quality(pv_data)
        assert result["abnormal"] is False
        assert result["bad_rate"] == 0.0

    def test_analyze_quality_abnormal(self) -> None:
        """测试 PV 质量码统计：异常。"""
        from app.tasks.diagnosis_engine import _analyze_quality

        pv_data = [{"value": 50.0, "quality": "BAD"} for _ in range(20)]
        pv_data.extend([{"value": 50.0, "quality": "GOOD"} for _ in range(80)])
        result = _analyze_quality(pv_data)
        assert result["abnormal"] is True
        assert result["bad_rate"] == 0.2

    def test_analyze_saturation_normal(self) -> None:
        """测试 OP 饱和率分析：正常。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _analyze_saturation

        op_values = np.array([50.0] * 100, dtype=float)
        result = _analyze_saturation(op_values)
        # op_range = 0，返回 detected=False
        assert result["detected"] is False

    def test_analyze_saturation_high(self) -> None:
        """测试 OP 饱和率分析：高饱和。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _analyze_saturation

        # 30% 高饱和
        op_values = np.array([100.0] * 30 + [50.0] * 70, dtype=float)
        result = _analyze_saturation(op_values)
        assert result["detected"] is True
        assert result["saturation_rate"] > 0.2

    def test_dempster_shafer_fusion_single(self) -> None:
        """测试 DS 证据理论融合：单条证据。"""
        from app.tasks.diagnosis_engine import _dempster_shafer_fusion

        result = _dempster_shafer_fusion([("OSCILLATION", 0.8)])
        assert result == 0.8

    def test_dempster_shafer_fusion_empty(self) -> None:
        """测试 DS 证据理论融合：空证据。"""
        from app.tasks.diagnosis_engine import _dempster_shafer_fusion

        result = _dempster_shafer_fusion([])
        assert result == 0.0

    def test_dempster_shafer_fusion_multiple(self) -> None:
        """测试 DS 证据理论融合：多条证据。"""
        from app.tasks.diagnosis_engine import _dempster_shafer_fusion

        result = _dempster_shafer_fusion([("OSCILLATION", 0.8), ("VALVE_STICTION", 0.6)])
        assert 0.0 <= result <= 1.0

    def test_dempster_shafer_fusion_same_label(self) -> None:
        """测试 DS 证据理论融合：相同标签。"""
        from app.tasks.diagnosis_engine import _dempster_shafer_fusion

        result = _dempster_shafer_fusion([("OSCILLATION", 0.8), ("OSCILLATION", 0.6)])
        # 相同标签融合后置信度应更高
        assert result >= 0.8


# ---------------------------------------------------------------------------
# Celery Beat 调度测试
# ---------------------------------------------------------------------------


class TestDiagnosisBeatSchedule:
    """Celery Beat 调度配置测试。"""

    def test_beat_schedule_has_diagnosis_engine(self) -> None:
        """Beat 调度应包含诊断引擎任务（crontab，对齐 KPI 整点后第 10 分钟）。"""
        from celery.schedules import crontab

        import app.tasks.diagnosis_engine  # noqa: F401
        from app.tasks.celery_app import celery_app

        beat = celery_app.conf.beat_schedule
        assert "diagnosis-engine-hourly" in beat
        assert (
            beat["diagnosis-engine-hourly"]["task"]
            == "app.tasks.diagnosis_engine.run_diagnosis_hourly"
        )
        schedule = beat["diagnosis-engine-hourly"]["schedule"]
        assert isinstance(schedule, crontab)
        assert schedule.minute == {10}


# ---------------------------------------------------------------------------
# 服务层单元测试
# ---------------------------------------------------------------------------


class TestDiagnosisService:
    """Diagnosis service 单元测试。"""

    async def test_list_diagnosis_configs(self) -> None:
        """list_diagnosis_configs 返回配置列表。"""
        from app.services.diagnosis import list_diagnosis_configs

        db = AsyncMock()
        configs = [_make_diag_config()]
        db.execute = AsyncMock(return_value=_make_scalars_mock(configs))
        result = await list_diagnosis_configs(db)
        assert len(result) == 1
        assert result[0]["diagCode"] == "OSCILLATION"

    async def test_update_diagnosis_config_not_found(self) -> None:
        """更新不存在的配置返回 ERR_DIAG_CONFIG_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.diagnosis import update_diagnosis_config

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await update_diagnosis_config(db, "nonexistent", "admin", diag_name="更新")
        assert exc_info.value.code == "ERR_DIAG_CONFIG_NOT_FOUND"

    async def test_get_diagnosis_detail_not_found(self) -> None:
        """回路不存在返回 ERR_LOOP_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.diagnosis import get_diagnosis_detail

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await get_diagnosis_detail(db, "nonexistent")
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"


class TestTrackerService:
    """Tracker service 单元测试。"""

    async def test_update_tracker_status_loop_not_found(self) -> None:
        """回路不存在返回 ERR_LOOP_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.tracker import update_tracker_status

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await update_tracker_status(db, "nonexistent", "ic_engineer", status="IN_PROGRESS")
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"

    async def test_update_tracker_status_invalid_status(self) -> None:
        """无效状态返回 ERR_VALIDATION。"""
        from app.core.exceptions import BizError
        from app.services.tracker import update_tracker_status

        db = AsyncMock()
        loop = _make_loop()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(loop))
        with pytest.raises(BizError) as exc_info:
            await update_tracker_status(db, loop.id, "ic_engineer", status="INVALID")
        assert exc_info.value.code == "ERR_VALIDATION"


class TestWaveformService:
    """Waveform service 单元测试。"""

    async def test_get_waveform_loop_not_found(self) -> None:
        """回路不存在返回 ERR_LOOP_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.waveform import get_waveform

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await get_waveform(
                db,
                "nonexistent",
                start_time="2026-06-22T08:00:00Z",
                end_time="2026-06-22T08:00:10Z",
            )
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"

    async def test_get_waveform_time_window_exceeded(self) -> None:
        """时间窗超过 30 天返回 ERR_TS_001。"""
        from app.core.exceptions import BizError
        from app.services.waveform import get_waveform

        db = AsyncMock()
        loop = _make_loop()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(loop))
        with pytest.raises(BizError) as exc_info:
            await get_waveform(
                db,
                str(loop.id),
                start_time="2026-05-01T00:00:00Z",
                end_time="2026-06-22T00:00:00Z",
            )
        assert exc_info.value.code == "ERR_TS_001"
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# S3-C4: FFT 频率精度验证测试
# ---------------------------------------------------------------------------


class TestFFTPrecision:
    """FFT 频率精度验证测试 — 使用已知频率的正弦波数据。

    验证 _detect_oscillation_fft 检测到的主频与已知输入频率的偏差 < 1%。
    """

    @staticmethod
    def _generate_sine_wave(
        frequency: float,
        sample_rate: float,
        duration: float,
        amplitude: float = 10.0,
        offset: float = 50.0,
    ):
        """生成已知频率的正弦波数据。

        Args:
            frequency: 信号频率（Hz）
            sample_rate: 采样率（Hz）
            duration: 信号时长（秒）
            amplitude: 振幅
            offset: 直流偏置

        Returns:
            (pv_values, sample_interval)
        """
        import numpy as np

        n = int(duration * sample_rate)
        t = np.linspace(0, duration, n, endpoint=False)
        pv_values = offset + amplitude * np.sin(2.0 * np.pi * frequency * t)
        sample_interval = 1.0 / sample_rate
        return pv_values, sample_interval

    def test_fft_precision_0_5hz(self) -> None:
        """0.5 Hz 正弦波频率检测精度 < 1%。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values, sample_interval = self._generate_sine_wave(
            frequency=0.5, sample_rate=10.0, duration=10.0
        )
        result = _detect_oscillation_fft(pv_values, sample_interval)

        assert result["detected"] is True, "0.5 Hz 正弦波应被检测为振荡"
        assert result["frequency"] > 0, "检测到的频率应大于 0"

        # 频率误差 < 1%
        freq_err = abs(result["frequency"] - 0.5) / 0.5
        assert freq_err < 0.01, (
            f"0.5 Hz 频率检测误差 {freq_err:.2%} 超过 1%（检测值={result['frequency']:.4f} Hz）"
        )

    def test_fft_precision_1_0hz(self) -> None:
        """1.0 Hz 正弦波频率检测精度 < 1%。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values, sample_interval = self._generate_sine_wave(
            frequency=1.0, sample_rate=20.0, duration=10.0
        )
        result = _detect_oscillation_fft(pv_values, sample_interval)

        assert result["detected"] is True, "1.0 Hz 正弦波应被检测为振荡"
        assert result["frequency"] > 0

        freq_err = abs(result["frequency"] - 1.0) / 1.0
        assert freq_err < 0.01, (
            f"1.0 Hz 频率检测误差 {freq_err:.2%} 超过 1%（检测值={result['frequency']:.4f} Hz）"
        )

    def test_fft_precision_2_0hz(self) -> None:
        """2.0 Hz 正弦波频率检测精度 < 1%。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values, sample_interval = self._generate_sine_wave(
            frequency=2.0, sample_rate=50.0, duration=5.0
        )
        result = _detect_oscillation_fft(pv_values, sample_interval)

        assert result["detected"] is True, "2.0 Hz 正弦波应被检测为振荡"
        assert result["frequency"] > 0

        freq_err = abs(result["frequency"] - 2.0) / 2.0
        assert freq_err < 0.01, (
            f"2.0 Hz 频率检测误差 {freq_err:.2%} 超过 1%（检测值={result['frequency']:.4f} Hz）"
        )

    def test_fft_precision_different_sample_rates(self) -> None:
        """不同采样率下频率检测精度 < 1%。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        # 测试多种采样率
        test_cases = [
            (1.0, 10.0, 10.0),  # 1 Hz 信号，10 Hz 采样，10 秒
            (1.0, 20.0, 5.0),  # 1 Hz 信号，20 Hz 采样，5 秒
            (0.5, 5.0, 20.0),  # 0.5 Hz 信号，5 Hz 采样，20 秒
        ]

        for freq, sample_rate, duration in test_cases:
            pv_values, sample_interval = self._generate_sine_wave(
                frequency=freq, sample_rate=sample_rate, duration=duration
            )
            result = _detect_oscillation_fft(pv_values, sample_interval)

            assert result["detected"] is True, (
                f"频率 {freq} Hz、采样率 {sample_rate} Hz 应被检测为振荡"
            )
            assert result["frequency"] > 0

            freq_err = abs(result["frequency"] - freq) / freq
            assert freq_err < 0.01, (
                f"频率 {freq} Hz、采样率 {sample_rate} Hz 检测误差 {freq_err:.2%} 超过 1%"
                f"（检测值={result['frequency']:.4f} Hz）"
            )

    def test_fft_precision_different_data_lengths(self) -> None:
        """不同数据长度下频率检测精度 < 1%。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        # 固定频率和采样率，变化数据长度
        frequency = 1.0
        sample_rate = 20.0
        durations = [5.0, 10.0, 20.0, 50.0]

        for duration in durations:
            pv_values, sample_interval = self._generate_sine_wave(
                frequency=frequency, sample_rate=sample_rate, duration=duration
            )
            result = _detect_oscillation_fft(pv_values, sample_interval)

            assert result["detected"] is True, (
                f"时长 {duration} 秒的 {frequency} Hz 信号应被检测为振荡"
            )

            freq_err = abs(result["frequency"] - frequency) / frequency
            assert freq_err < 0.01, (
                f"时长 {duration} 秒检测误差 {freq_err:.2%} 超过 1%"
                f"（检测值={result['frequency']:.4f} Hz）"
            )

    def test_fft_precision_with_noise(self) -> None:
        """噪声干扰下频率检测精度应仍 < 5%（放宽阈值）。"""
        import numpy as np

        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        # 使用固定随机种子保证测试可复现
        rng = np.random.default_rng(seed=42)
        frequency = 1.0
        sample_rate = 50.0
        duration = 10.0
        n = int(duration * sample_rate)
        t = np.linspace(0, duration, n, endpoint=False)

        # 信噪比约 10dB 的噪声
        signal = 10.0 * np.sin(2.0 * np.pi * frequency * t)
        noise = rng.normal(0, 1.0, n)  # 标准差 1.0 的噪声
        pv_values = 50.0 + signal + noise

        sample_interval = 1.0 / sample_rate
        result = _detect_oscillation_fft(pv_values, sample_interval)

        assert result["detected"] is True, "含噪声的 1.0 Hz 信号应被检测为振荡"

        # 噪声环境下放宽阈值至 5%
        freq_err = abs(result["frequency"] - frequency) / frequency
        assert freq_err < 0.05, (
            f"含噪声频率检测误差 {freq_err:.2%} 超过 5%（检测值={result['frequency']:.4f} Hz）"
        )

    def test_fft_amplitude_precision(self) -> None:
        """FFT 振幅检测应接近真实振幅。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        amplitude = 10.0
        pv_values, sample_interval = self._generate_sine_wave(
            frequency=1.0, sample_rate=50.0, duration=10.0, amplitude=amplitude
        )
        result = _detect_oscillation_fft(pv_values, sample_interval)

        # 振幅检测应大于 0（具体值取决于 FFT 实现，这里只验证合理性）
        assert result["amplitude"] > 0, "检测到的振幅应大于 0"
        # 振幅应在合理范围内（0.1 * amplitude ~ 2 * amplitude）
        assert result["amplitude"] < 2 * amplitude, (
            f"检测振幅 {result['amplitude']} 异常偏大（真实振幅 {amplitude}）"
        )
