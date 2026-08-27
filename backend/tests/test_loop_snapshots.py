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
    # Phase 1 新增指标（HiaMonitor 借鉴，2026-07-23）
    s.pv_mean = Decimal("50.12")
    s.pv_std = Decimal("2.34")
    s.sp_mean = Decimal("50.00")
    s.sp_std = Decimal("0.10")
    s.op_mean = Decimal("55.60")
    s.op_std = Decimal("8.90")
    s.valve_linearity = Decimal("0.92")
    s.valve_nonlinearity = Decimal("0.08")
    s.valve_op_min = Decimal("12.30")
    s.valve_op_max = Decimal("88.70")
    s.oscillation_amplitude = Decimal("3.45")
    s.setpoint_crossing_count = 7
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

    def test_list_snapshots_includes_all_kpi_fields(self, client, mock_db, fake_redis) -> None:
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
        # Phase 1 新增指标（HiaMonitor 借鉴）
        assert "pvMean" in item
        assert "pvStd" in item
        assert "spMean" in item
        assert "spStd" in item
        assert "opMean" in item
        assert "opStd" in item
        assert "valveLinearity" in item
        assert "valveNonlinearity" in item
        assert "valveOpMin" in item
        assert "valveOpMax" in item
        assert "oscillationAmplitude" in item
        assert "setpointCrossingCount" in item

    def test_list_snapshots_phase1_metric_values(self, client, mock_db, fake_redis) -> None:
        """Phase 1 新增指标值正确序列化（Decimal→float，int 保留）。"""
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

        item = resp.json()["data"]["items"][0]
        # 信号统计（Decimal → float）
        assert item["pvMean"] == 50.12
        assert item["pvStd"] == 2.34
        assert item["spMean"] == 50.0
        assert item["spStd"] == 0.1
        assert item["opMean"] == 55.6
        assert item["opStd"] == 8.9
        # 阀门诊断
        assert item["valveLinearity"] == 0.92
        assert item["valveNonlinearity"] == 0.08
        assert item["valveOpMin"] == 12.3
        assert item["valveOpMax"] == 88.7
        # 振荡/穿越（振幅 Decimal→float，穿越次数 int）
        assert item["oscillationAmplitude"] == 3.45
        assert item["setpointCrossingCount"] == 7

    def test_list_snapshots_no_token(self, client) -> None:
        """未认证返回 401."""
        resp = client.get("/api/v1/performance/loops/snapshots")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 回归测试：默认时间范围必须为 naive datetime（避免 asyncpg TypeError）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_loop_snapshots_default_time_is_naive() -> None:
    """服务函数默认时间范围（近 7 天）必须生成 naive datetime.

    回归：list_loop_snapshots 此前用 datetime.now(UTC) 生成 aware datetime，
    与数据库 ts_start (naive) 比较时 asyncpg 抛
    "can't subtract offset-naive and offset-aware datetimes" → 500 → CORS 错误。
    """
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        # 返回空列表 + count=0，避免后续处理逻辑干扰
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    await list_loop_snapshots(db)  # 不传 start/end，使用默认近 7 天

    assert len(captured_stmts) >= 1
    # 编译 SQL 并提取绑定参数
    compiled = captured_stmts[0].compile()
    params = compiled.params
    # 找到时间参数（ts_start >= ? 和 ts_start <= ?）
    time_values = [v for v in params.values() if isinstance(v, datetime)]
    assert len(time_values) >= 2, f"应至少有 2 个时间参数，实际: {params}"
    for tv in time_values:
        assert tv.tzinfo is None, (
            f"默认时间范围必须为 naive datetime（数据库 ts_start 无时区），但收到 aware: {tv}"
        )


# ---------------------------------------------------------------------------
# 回归测试：latestOnly 模式 score 排序（回路性能页表头排序）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_loop_snapshots_latest_only_sort_by_score() -> None:
    """latest_only=True 时 sort_by="score" 应生成 score 排序（NULLS LAST）."""
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    await list_loop_snapshots(db, latest_only=True, sort_by="score", sort_order="asc")

    assert len(captured_stmts) >= 1
    sql = str(captured_stmts[0].compile()).upper()
    assert "ORDER BY" in sql
    assert "SCORE ASC" in sql
    assert "NULLS LAST" in sql


@pytest.mark.asyncio
async def test_list_loop_snapshots_default_sort_is_ts_start_desc() -> None:
    """不传排序参数时保持默认 ts_start DESC（回归：不得改变既有行为）."""
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    await list_loop_snapshots(db, latest_only=True)

    sql = str(captured_stmts[0].compile()).upper()
    assert "TS_START DESC" in sql


# ---------------------------------------------------------------------------
# 回归测试：_parse_dt 时区换算（带偏移输入必须先转 UTC 再去时区）
# ---------------------------------------------------------------------------


def test_parse_dt_converts_offset_to_utc() -> None:
    """带 +08:00 偏移的输入应换算为 UTC naive，而非直接丢弃偏移."""
    from app.api.v1.endpoints.performance import _parse_dt

    dt = _parse_dt("2026-07-19T15:00:00+08:00")
    assert dt == datetime(2026, 7, 19, 7, 0, 0)
    assert dt is not None and dt.tzinfo is None

    # Z 后缀同样按 UTC 处理
    dt_z = _parse_dt("2026-07-19T07:00:00Z")
    assert dt_z == datetime(2026, 7, 19, 7, 0, 0)

    # 无时区输入按 UTC 原样保留（历史行为）
    dt_naive = _parse_dt("2026-07-19T07:00:00")
    assert dt_naive == datetime(2026, 7, 19, 7, 0, 0)

    # 非法输入返回 None
    assert _parse_dt("not-a-date") is None
    assert _parse_dt(None) is None


# ---------------------------------------------------------------------------
# 回归测试：data_lineage 为 DB 存储的 snake_case 键时响应字段不为空
# ---------------------------------------------------------------------------


class TestDataLineageSnakeCase:
    """DB JSONB 存的是 snake_case 键（DataLineage.to_dict），
    响应 dataLineage 必须正确映射而非全空默认值。"""

    def test_snake_case_lineage_mapped(self, client, mock_db, fake_redis) -> None:
        snap = _make_snapshot_full()
        snap.data_lineage = {
            "sampling_freq": "5s",
            "aggregation_policy": "MEAN",
            "quality_policy": "KEEP_ALL",
            "tag_group": "OP_HF",
            "data_block_ids": ["blk_009"],
            "valid_rate": 0.75,
            "data_policy_version": "pre_v2",
            "algorithm_version": "KPI_CALC_v2.1",
        }
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

        assert resp.status_code == 200
        lineage = resp.json()["data"]["items"][0]["dataLineage"]
        assert lineage["samplingFreq"] == "5s"
        assert lineage["aggregationPolicy"] == "MEAN"
        assert lineage["qualityPolicy"] == "KEEP_ALL"
        assert lineage["tagGroup"] == "OP_HF"
        assert lineage["dataBlockIds"] == ["blk_009"]
        assert lineage["validRate"] == 0.75
        assert lineage["dataPolicyVersion"] == "pre_v2"
        assert lineage["algorithmVersion"] == "KPI_CALC_v2.1"


# ---------------------------------------------------------------------------
# Phase 4 性能项：grade 服务端筛选 + 等级分布聚合端点
# ---------------------------------------------------------------------------


def _make_sys_config_none_result() -> MagicMock:
    """构造 select(SysConfig) 未命中（回退国标默认阈值）的 execute 结果."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


def _make_grade_rows_result(rows: list[tuple[str, int]]) -> MagicMock:
    """构造等级聚合查询（grade, cnt）的 execute 结果."""
    mock_rows = [MagicMock(grade=g, cnt=c) for g, c in rows]
    result = MagicMock()
    result.all.return_value = mock_rows
    return result


class TestListLoopSnapshotsGradeFilter:
    """GET /api/v1/performance/loops/snapshots?grade=..."""

    def test_grade_filter_success(self, client, mock_db, fake_redis) -> None:
        """按性能等级筛选（服务端过滤），响应结构不变."""
        snap = _make_snapshot_full(score=Decimal("95.00"))
        rows = [(snap, "tag1")]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第 1 次：读取定级阈值 sys_config（未配置 → 国标默认）
                return _make_sys_config_none_result()
            if call_count[0] == 2:
                return _make_list_result(rows)
            return _make_count_result(1)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots?grade=EXCELLENT&page=1&pageSize=20",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 1
        assert body["data"]["page"] == 1
        assert body["data"]["pageSize"] == 20
        assert body["data"]["items"][0]["score"] == 95.0

    def test_grade_filter_inconclusive(self, client, mock_db, fake_redis) -> None:
        """grade=INCONCLUSIVE 筛选 score 为 NULL 的快照."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_sys_config_none_result()
            if call_count[0] == 2:
                return _make_list_result([])
            return _make_count_result(0)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots?grade=INCONCLUSIVE",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0

    def test_invalid_grade_returns_400(self, client, mock_db, fake_redis) -> None:
        """非法等级名返回 400 ERR_INVALID_GRADE."""
        mock_db.execute = AsyncMock(return_value=_make_sys_config_none_result())

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots?grade=SUPERB",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_GRADE"


class TestListLoopSnapshotsSortWhitelist:
    """GET /api/v1/performance/loops/snapshots?sortBy=... 指标分析页 M3 排序白名单扩展"""

    @pytest.mark.parametrize(
        "sort_by",
        [
            "score",
            "accuracy_rate",
            "auto_mode_rate",
            "effective_auto_rate",
            "fast_rate",
            "steady_rate",
            "good_value_rate",
        ],
    )
    def test_sort_by_whitelist(self, client, mock_db, fake_redis, sort_by) -> None:
        """白名单内排序键：SQL 构建不报错，响应正常。"""
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
                f"/api/v1/performance/loops/snapshots?sortBy={sort_by}&sortOrder=asc",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]["items"]) == 1

    def test_sort_by_invalid_fallback(self, client, mock_db, fake_redis) -> None:
        """非法排序键回退默认 ts_start DESC（白名单外值不进入 SQL 列引用）。"""
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
                "/api/v1/performance/loops/snapshots?sortBy=malicious_column",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]["items"]) == 1


@pytest.mark.asyncio
async def test_list_loop_snapshots_grade_filter_sql() -> None:
    """grade 筛选应生成 score 区间 WHERE 条件（EXCELLENT → score >= 90）."""
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    await list_loop_snapshots(db, grade="EXCELLENT")

    # 第 1 条 SQL 是 sys_config 查询，第 2 条是快照列表查询
    assert len(captured_stmts) >= 2
    compiled = captured_stmts[1].compile()
    params = list(compiled.params.values())
    # 国标默认 EXCELLENT 下界 90 应作为绑定参数出现
    assert 90.0 in params or 90 in params, f"缺少 score >= 90 绑定参数: {compiled.params}"


@pytest.mark.asyncio
async def test_list_loop_snapshots_grade_inconclusive_sql() -> None:
    """grade=INCONCLUSIVE 应生成 score IS NULL 条件."""
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    await list_loop_snapshots(db, grade="inconclusive")  # 大小写不敏感

    assert len(captured_stmts) >= 2
    sql = str(captured_stmts[1].compile()).upper()
    assert "SCORE IS NULL" in sql


@pytest.mark.asyncio
async def test_list_loop_snapshots_without_grade_no_config_query() -> None:
    """不传 grade 时不读取定级阈值（向后兼容：SQL 次数与旧行为一致）."""
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    await list_loop_snapshots(db)

    # 旧行为：仅 列表查询 + count 查询 2 条 SQL（无 sys_config 查询）
    assert len(captured_stmts) == 2


class TestGradeDistributionEndpoint:
    """GET /api/v1/performance/grade-distribution"""

    def test_grade_distribution_success(self, client, mock_db, fake_redis) -> None:
        """聚合端点返回各等级计数与 total."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_sys_config_none_result()
            return _make_grade_rows_result([("EXCELLENT", 2), ("GOOD", 3), ("INCONCLUSIVE", 1)])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/grade-distribution",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["EXCELLENT"] == 2
        assert data["GOOD"] == 3
        assert data["FAIR"] == 0
        assert data["WARNING"] == 0
        assert data["POOR"] == 0
        assert data["INCONCLUSIVE"] == 1
        assert data["total"] == 6

    def test_grade_distribution_with_filters(self, client, mock_db, fake_redis) -> None:
        """支持 plantNodeId / 时间窗 / 状态等筛选参数."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_sys_config_none_result()
            return _make_grade_rows_result([("POOR", 4)])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/grade-distribution"
                f"?plantNodeId={PLANT_NODE_ID}"
                "&startTime=2026-07-01T00:00:00Z&endTime=2026-07-05T00:00:00Z"
                "&status=SUCCESS",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["POOR"] == 4
        assert data["total"] == 4

    def test_grade_distribution_empty(self, client, mock_db, fake_redis) -> None:
        """无数据时各等级为 0、total 为 0."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_sys_config_none_result()
            return _make_grade_rows_result([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/grade-distribution",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        grade_keys = ("EXCELLENT", "GOOD", "FAIR", "WARNING", "POOR", "INCONCLUSIVE")
        assert all(data[g] == 0 for g in grade_keys)

    def test_grade_distribution_no_token(self, client) -> None:
        """未认证返回 401."""
        resp = client.get("/api/v1/performance/grade-distribution")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_grade_distribution_sql_group_by() -> None:
    """等级分布应生成 GROUP BY 等级的聚合 SQL（服务端下推，非全量拉取）."""
    from app.services.performance import get_grade_distribution

    db = AsyncMock()
    captured_stmts: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    distribution = await get_grade_distribution(db)

    assert (
        len(captured_stmts) == 3
    )  # sys_config + 等级聚合 + 适用性分层聚合（SQL 下推，无全量拉取）
    sql = str(captured_stmts[1].compile()).upper()
    assert "GROUP BY" in sql
    assert "CASE" in sql
    assert "ROW_NUMBER" in sql  # 每回路最新一条（口径同列表 latestOnly）
    fitness_sql = str(captured_stmts[2].compile()).upper()
    assert "GROUP BY" in fitness_sql
    assert "FITNESS_LEVEL" in fitness_sql
    assert distribution["total"] == 0
    assert distribution["fitnessDistribution"] == {
        "L0": 0,
        "L1": 0,
        "L2": 0,
        "L3": 0,
        "L4": 0,
        "total": 0,
    }


# ---------------------------------------------------------------------------
# status 逗号分隔多值（追溯矩阵小缺口：/loops/snapshots status 扩多值）
# ---------------------------------------------------------------------------


def _pg_sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def _capture_snapshot_sql(**kwargs) -> list:
    """调用 list_loop_snapshots 并捕获 SQL（无 plantNodeId/grade → 列表 + count 两条）。"""
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured: list = []

    async def execute_side_effect(stmt, *args, **kw):
        captured.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)
    await list_loop_snapshots(db, **kwargs)
    return captured


@pytest.mark.asyncio
async def test_snapshots_status_multi_value_sql() -> None:
    """status_filter 逗号多值 → status IN 多值过滤。"""
    captured = await _capture_snapshot_sql(status_filter="SUCCESS,PARTIAL")
    for stmt in captured:
        assert "kpi_snapshot_hourly.status IN ('SUCCESS', 'PARTIAL')" in _pg_sql(stmt)


@pytest.mark.asyncio
async def test_snapshots_status_single_value_backward_compatible() -> None:
    """单值 status 向后兼容：走 IN 单元素，语义等同原等值过滤。"""
    captured = await _capture_snapshot_sql(status_filter="INCONCLUSIVE")
    for stmt in captured:
        assert "kpi_snapshot_hourly.status IN ('INCONCLUSIVE')" in _pg_sql(stmt)


# ---------------------------------------------------------------------------
# GET /api/v1/performance/loops/metric-series — 指标矩阵页批量序列接口
# 设计依据：docs/MVP设计/15-回路指标矩阵页设计方案.md §4.1
# ---------------------------------------------------------------------------


def _make_series_result(rows: list[tuple]) -> MagicMock:
    """构造 select(loop_id, tag_name, ts_start, col).execute() 结果."""
    result = MagicMock()
    result.all.return_value = rows
    return result


class TestGetLoopMetricSeries:
    """GET /api/v1/performance/loops/metric-series"""

    def test_metric_series_success(self, client, mock_db, fake_redis) -> None:
        """认证用户批量查询单指标多回路时间序列."""
        from decimal import Decimal as D

        ts1 = datetime(2026, 8, 26, 10, 0, 0)
        ts2 = datetime(2026, 8, 26, 11, 0, 0)
        rows = [
            (LOOP_ID_1, "L101", ts1, D("85.50")),
            (LOOP_ID_1, "L101", ts2, D("86.20")),
            (LOOP_ID_2, "L102", ts1, D("70.10")),
        ]
        mock_db.execute = AsyncMock(return_value=_make_series_result(rows))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/performance/loops/metric-series"
                f"?loopIds={LOOP_ID_1},{LOOP_ID_2}&metricKey=steady_rate",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["metricKey"] == "steady_rate"
        assert len(data["series"]) == 2
        s1 = next(s for s in data["series"] if s["loopId"] == LOOP_ID_1)
        assert s1["loopTagName"] == "L101"
        assert [p["value"] for p in s1["points"]] == [85.5, 86.2]
        assert s1["points"][0]["ts"] == ts1.isoformat()

    def test_metric_series_invalid_metric_key(self, client, mock_db, fake_redis) -> None:
        """白名单外指标键 → 400 ERR_METRIC_SERIES_INVALID。"""
        mock_db.execute = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/performance/loops/metric-series"
                f"?loopIds={LOOP_ID_1}&metricKey=evil_column",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_METRIC_SERIES_INVALID"
        mock_db.execute.assert_not_awaited()

    def test_metric_series_too_many_loops(self, client, mock_db, fake_redis) -> None:
        """回路数超过 10 → 400 ERR_METRIC_SERIES_LOOPS。"""
        loop_ids = ",".join(f"00000000-0000-0000-0000-{i:012d}" for i in range(11))
        mock_db.execute = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/performance/loops/metric-series?loopIds={loop_ids}&metricKey=score",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_METRIC_SERIES_LOOPS"

    def test_metric_series_empty_loops(self, client, mock_db, fake_redis) -> None:
        """loopIds 为空（仅逗号）→ 400 ERR_METRIC_SERIES_LOOPS。"""
        mock_db.execute = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/metric-series?loopIds=,&metricKey=score",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_METRIC_SERIES_LOOPS"

    def test_metric_series_unauthenticated_401(self, client, mock_db, fake_redis) -> None:
        """未认证 → 401。"""
        resp = client.get(
            f"/api/v1/performance/loops/metric-series?loopIds={LOOP_ID_1}&metricKey=score"
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_metric_series_sql_shape() -> None:
    """服务层 SQL：loop_id IN + 指标列 IS NOT NULL + 双键升序排序."""
    from app.services.performance import get_loop_metric_series

    db = AsyncMock()
    captured: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        captured.append(stmt)
        return _make_series_result([])

    db.execute = AsyncMock(side_effect=execute_side_effect)
    await get_loop_metric_series(
        db,
        [LOOP_ID_1, LOOP_ID_2],
        "oscillation_rate",
        start=datetime(2026, 8, 20),
        end=datetime(2026, 8, 27),
    )

    assert len(captured) == 1
    sql = str(captured[0].compile(compile_kwargs={"literal_binds": False})).upper()
    assert "KPI_SNAPSHOT_HOURLY.LOOP_ID IN" in sql
    assert "OSCILLATION_RATE IS NOT NULL" in sql
    assert "ORDER BY" in sql
    assert "TS_START ASC" in sql
