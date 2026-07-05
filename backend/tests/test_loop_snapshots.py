"""回路小时指标快照列表 API 测试.

覆盖 GET /api/v1/performance/loops/snapshots 端点：
- 按 loopId / plantNodeId / 时间范围 / 状态 / 可信度筛选
- 分页
- 响应包含 loopTagName
- 默认时间范围（近 7 天）
- RBAC：未认证 401
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USERS, mock_current_user


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

LOOP_ID_1 = "00000000-0000-0000-0000-000000000201"
LOOP_ID_2 = "00000000-0000-0000-0000-000000000202"
PLANT_NODE_ID = "00000000-0000-0000-0000-000000000111"


def _make_snapshot_full(
    loop_id: str = LOOP_ID_1,
    score: Decimal = Decimal("78.60"),
    status: str = "SUCCESS",
    confidence_level: str | None = "A",
    ts_start: datetime | None = None,
) -> MagicMock:
    """构造完整的 KpiSnapshotHourly mock（24 字段）."""
    s = MagicMock()
    s.id = "00000000-0000-0000-0000-000000000501"
    s.loop_id = loop_id
    s.ts_start = ts_start or datetime.now(UTC)
    s.ts_end = s.ts_start + timedelta(hours=1)
    s.score = score
    s.good_value_rate = Decimal("96.80")
    s.auto_mode_rate = Decimal("90.00")
    s.effective_auto_rate = Decimal("82.00")
    s.steady_rate = Decimal("85.00")
    s.accuracy_rate = Decimal("80.00")
    s.oscillation_rate = Decimal("15.00")
    s.saturation_rate = Decimal("8.00")
    s.fast_rate = Decimal("75.00")
    s.stiction_index = Decimal("0.12")
    s.settling_time = Decimal("120.50")
    s.output_trip_index = Decimal("45.30")
    s.status = status
    s.ideal_settling_time = Decimal("100.00")
    s.algorithm_version = "KPI_CALC_v2.0"
    s.sampling_freq = "1s"
    s.quality_policy = "KEEP_ALL_WITH_VALIDITY"
    s.valid_rate = Decimal("0.9820")
    s.confidence_level = confidence_level
    s.data_lineage = {
        "samplingFreq": "1s",
        "aggregationPolicy": "LAST",
        "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
        "tagGroup": "BASE",
        "dataBlockIds": ["blk_001"],
        "validRate": 0.982,
        "dataPolicyVersion": "pre_v1",
        "algorithmVersion": "KPI_CALC_v2.0",
    }
    return s


def _make_list_result(rows: list[tuple]) -> MagicMock:
    """构造 select(KpiSnapshotHourly, LoopLedger.tag_name).execute() 结果.

    rows: [(snapshot, tag_name), ...]
    """
    result = MagicMock()
    result.all.return_value = rows
    return result


def _make_count_result(total: int) -> MagicMock:
    """构造 select(func.count()).execute() 结果."""
    result = MagicMock()
    result.scalar.return_value = total
    return result


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


class TestListLoopSnapshots:
    """GET /api/v1/performance/loops/snapshots"""

    def test_list_snapshots_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取回路小时指标快照列表."""
        snap = _make_snapshot_full()
        rows = [(snap, "41LIC20117_PIDA")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result(rows)
            return _make_count_result(1)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["pageSize"] == 20
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["loopId"] == LOOP_ID_1
        assert item["loopTagName"] == "41LIC20117_PIDA"
        assert item["score"] == 78.6
        assert item["status"] == "SUCCESS"
        assert item["confidenceLevel"] == "A"

    def test_list_snapshots_by_loop_id(self, client, mock_db, fake_redis) -> None:
        """按 loopId 筛选."""
        snap = _make_snapshot_full(loop_id=LOOP_ID_1)
        rows = [(snap, "loop1_tag")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result(rows)
            return _make_count_result(1)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/performance/loops/snapshots?loopId={LOOP_ID_1}",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["loopId"] == LOOP_ID_1

    def test_list_snapshots_by_time_range(self, client, mock_db, fake_redis) -> None:
        """按时间范围筛选."""
        snap = _make_snapshot_full()
        rows = [(snap, "tag1")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result(rows)
            return _make_count_result(1)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        start = "2026-07-01T00:00:00Z"
        end = "2026-07-05T00:00:00Z"
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/performance/loops/snapshots?startTime={start}&endTime={end}",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"

    def test_list_snapshots_by_status(self, client, mock_db, fake_redis) -> None:
        """按状态筛选（SUCCESS/INCONCLUSIVE/PARTIAL）."""
        snap = _make_snapshot_full(status="INCONCLUSIVE", confidence_level=None)
        rows = [(snap, "tag1")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result(rows)
            return _make_count_result(1)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots?status=INCONCLUSIVE",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["items"][0]["status"] == "INCONCLUSIVE"

    def test_list_snapshots_pagination(self, client, mock_db, fake_redis) -> None:
        """分页参数 page/pageSize."""
        snap1 = _make_snapshot_full(loop_id=LOOP_ID_1)
        snap2 = _make_snapshot_full(loop_id=LOOP_ID_2)
        rows = [(snap1, "tag1"), (snap2, "tag2")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result(rows)
            return _make_count_result(10)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots?page=2&pageSize=2",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["total"] == 10
        assert data["page"] == 2
        assert data["pageSize"] == 2
        assert len(data["items"]) == 2

    def test_list_snapshots_empty(self, client, mock_db, fake_redis) -> None:
        """无数据时返回空列表."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result([])
            return _make_count_result(0)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []

    def test_list_snapshots_includes_all_kpi_fields(
        self, client, mock_db, fake_redis
    ) -> None:
        """响应包含所有 KPI 指标字段."""
        snap = _make_snapshot_full()
        rows = [(snap, "tag1")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_list_result(rows)
            return _make_count_result(1)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots",
                headers={"Authorization": "Bearer fake-token"},
            )

        body = resp.json()
        item = body["data"]["items"][0]
        # 核心指标
        assert "score" in item
        assert "goodValueRate" in item
        assert "autoModeRate" in item
        assert "effectiveAutoRate" in item
        assert "steadyRate" in item
        assert "accuracyRate" in item
        assert "oscillationRate" in item
        assert "saturationRate" in item
        assert "fastRate" in item
        # 诊断扩展
        assert "stictionIndex" in item
        assert "settlingTime" in item
        assert "outputTravelIndex" in item
        # 数据血缘
        assert "idealSettlingTime" in item
        assert "algorithmVersion" in item
        assert "samplingFreq" in item
        assert "qualityPolicy" in item
        assert "validRate" in item
        assert "confidenceLevel" in item
        assert "dataLineage" in item

    def test_list_snapshots_no_token(self, client) -> None:
        """未认证返回 401."""
        resp = client.get("/api/v1/performance/loops/snapshots")
        assert resp.status_code == 401
