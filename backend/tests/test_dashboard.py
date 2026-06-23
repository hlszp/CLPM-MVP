"""Dashboard aggregation API tests (S6-PORTAL-001).

Covers:
- GET /api/v1/dashboard/overview (工作台聚合)
- 各角色数据范围测试（ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT）
- Redis 缓存命中/未命中测试
- 筛选参数测试（plant_id / granularity）
- 服务层单元测试
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------


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


def _make_loop(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_name: str = "FIC-101",
    description: str = "进料流量控制",
    unit_id: str | None = "00000000-0000-0000-0000-000000000111",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = description
    loop.unit_id = unit_id
    return loop


def _make_plant_node(
    node_id: str = "00000000-0000-0000-0000-000000000111",
    name: str = "加氢装置",
    node_type: str = "UNIT",
) -> MagicMock:
    """构造 PlantNode mock。"""
    node = MagicMock()
    node.id = node_id
    node.name = name
    node.type = node_type
    return node


def _make_tracker(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    action_status: str = "PENDING",
    diagnosis_label: str | None = "OSCILLATION",
) -> MagicMock:
    """构造 ActionTracker mock。"""
    t = MagicMock()
    t.id = "00000000-0000-0000-0000-000000000601"
    t.loop_id = loop_id
    t.action_status = action_status
    t.diagnosis_label = diagnosis_label
    t.updated_at = datetime.now(UTC)
    return t


def _make_scalars_mock(items: list) -> MagicMock:
    """构造返回 list 的 execute 结果。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    """构造返回单个值的 execute 结果。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalar_mock(value) -> MagicMock:
    """构造返回 scalar 的 execute 结果（用于 count 查询）。"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_all_mock(items: list) -> MagicMock:
    """构造返回 all() 的 execute 结果（用于元组查询）。"""
    result = MagicMock()
    result.all.return_value = items
    return result


# ---------------------------------------------------------------------------
# 端点测试：GET /api/v1/dashboard/overview
# ---------------------------------------------------------------------------


class TestDashboardOverviewEndpoint:
    """GET /api/v1/dashboard/overview tests."""

    def test_overview_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 角色可以获取工作台聚合数据。"""
        # mock_db 仅用于 _get_plant_name（plant_id=None 时不调用）
        # 并行查询通过 patched AsyncSessionLocal 使用通用 mock 结果
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/dashboard/overview",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "filter_scope" in data
        assert "kpi_cards" in data
        assert "inefficient_loops" in data
        assert "trend_summary" in data
        assert "pending_alerts" in data
        assert "cached" in data
        assert data["cached"] is False
        # 6 大 KPI 卡片
        kpi_cards = data["kpi_cards"]
        assert "auto_mode_rate" in kpi_cards
        assert "steady_rate" in kpi_cards
        assert "composite_score" in kpi_cards
        assert "alarm_count" in kpi_cards
        assert "operation_count" in kpi_cards
        assert "good_value_rate" in kpi_cards
        # 每个卡片含 value/unit/trend/delta
        for card_key in (
            "auto_mode_rate",
            "steady_rate",
            "composite_score",
            "alarm_count",
            "operation_count",
            "good_value_rate",
        ):
            card = kpi_cards[card_key]
            assert "value" in card
            assert "unit" in card
            assert "trend" in card
            assert "delta" in card
        # ADMIN 可以看到低效回路列表
        assert isinstance(data["inefficient_loops"], list)
        # 趋势摘要含 dates 和 composite_scores
        assert "dates" in data["trend_summary"]
        assert "composite_scores" in data["trend_summary"]
        # 待处理异常数
        assert "open_diagnoses" in data["pending_alerts"]
        assert "open_trackers" in data["pending_alerts"]

    def test_overview_sponsor_no_loops(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 角色不返回低效回路列表。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/dashboard/overview",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        # SPONSOR 低效回路列表为空
        assert data["inefficient_loops"] == []
        # 但仍有 KPI 卡片
        assert "kpi_cards" in data

    def test_overview_with_plant_id(self, client, mock_db, fake_redis) -> None:
        """按装置筛选工作台数据。"""
        plant_id = "00000000-0000-0000-0000-000000000111"
        # mock_db 仅用于 _get_plant_name 查询
        mock_db.execute = AsyncMock(
            return_value=_make_scalar_one_or_none_mock(
                _make_plant_node(node_id=plant_id)
            )
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                f"/api/v1/dashboard/overview?plantId={plant_id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["filter_scope"]["plant_id"] == plant_id
        assert data["filter_scope"]["plant_name"] == "加氢装置"

    def test_overview_with_granularity(self, client, mock_db, fake_redis) -> None:
        """时间粒度筛选（week）。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/dashboard/overview?granularity=week",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert data["filter_scope"]["granularity"] == "week"

    def test_overview_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/dashboard/overview")
        assert resp.status_code == 401

    def test_overview_all_roles_accessible(self, client, mock_db, fake_redis) -> None:
        """所有角色都可以访问工作台。"""
        roles = ["admin", "ic_engineer", "pe_engineer", "sponsor", "expert"]

        for role_key in roles:
            mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
            with mock_current_user(TEST_USERS[role_key]):
                resp = client.get(
                    "/api/v1/dashboard/overview",
                    headers={"Authorization": "Bearer fake-token"},
                )
            assert resp.status_code == 200, f"角色 {role_key} 访问失败: {resp.json()}"


# ---------------------------------------------------------------------------
# Redis 缓存测试
# ---------------------------------------------------------------------------


class TestDashboardCache:
    """Redis 缓存命中/未命中测试。"""

    def test_cache_miss_then_hit(self, client, mock_db, fake_redis) -> None:
        """第一次请求未命中缓存，第二次命中缓存。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

        with mock_current_user(TEST_USERS["admin"]):
            # 第一次请求：缓存未命中
            resp1 = client.get(
                "/api/v1/dashboard/overview",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp1.status_code == 200
            data1 = resp1.json()["data"]
            assert data1["cached"] is False

            # 第二次请求：缓存命中
            resp2 = client.get(
                "/api/v1/dashboard/overview",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp2.status_code == 200
            data2 = resp2.json()["data"]
            assert data2["cached"] is True

    def test_cache_key_differs_by_role(self, client, mock_db, fake_redis) -> None:
        """不同角色使用不同缓存 key（服务层验证）。"""
        from app.services.dashboard import _build_cache_key

        # 直接验证缓存 key 格式包含角色信息
        admin_key = _build_cache_key(None, "day", "ADMIN")
        expert_key = _build_cache_key(None, "day", "EXPERT")
        ic_key = _build_cache_key(None, "day", "IC_ENGINEER")

        assert admin_key != expert_key
        assert admin_key != ic_key
        assert expert_key != ic_key
        assert "ADMIN" in admin_key
        assert "EXPERT" in expert_key
        assert "IC_ENGINEER" in ic_key
    def test_cache_key_differs_by_plant_id(self, client, mock_db, fake_redis) -> None:
        """不同 plant_id 使用不同缓存 key（服务层验证）。"""
        from app.services.dashboard import _build_cache_key

        key_all = _build_cache_key(None, "day", "ADMIN")
        key_plant1 = _build_cache_key("plant-1", "day", "ADMIN")
        key_plant2 = _build_cache_key("plant-2", "day", "ADMIN")

        assert key_all != key_plant1
        assert key_plant1 != key_plant2
        assert "all" in key_all
        assert "plant-1" in key_plant1
        assert "plant-2" in key_plant2

    def test_redis_unavailable_degrades_gracefully(
        self, client, mock_db, fake_redis
    ) -> None:
        """Redis 不可用时降级为直接查询，不报错。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

        # 模拟 Redis 不可用
        with (
            patch("app.services.dashboard.redis_client") as mock_redis,
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_redis.get = AsyncMock(side_effect=Exception("Redis connection refused"))
            mock_redis.setex = AsyncMock(side_effect=Exception("Redis connection refused"))
            resp = client.get(
                "/api/v1/dashboard/overview",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["cached"] is False


# ---------------------------------------------------------------------------
# 服务层单元测试
# ---------------------------------------------------------------------------


class TestDashboardService:
    """Dashboard service 单元测试。"""

    async def test_get_dashboard_overview_admin(
        self, mock_dashboard_session_local
    ) -> None:
        """ADMIN 角色获取工作台数据。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="ADMIN", plant_id=None, granularity="day"
            )
        assert "kpi_cards" in result
        assert "inefficient_loops" in result
        assert "trend_summary" in result
        assert "pending_alerts" in result
        assert result["filter_scope"]["user_role"] == "ADMIN"
        assert result["cached"] is False

    async def test_get_dashboard_overview_sponsor_no_loops(
        self, mock_dashboard_session_local
    ) -> None:
        """SPONSOR 角色不返回低效回路列表。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="SPONSOR", plant_id=None, granularity="day"
            )
        assert result["inefficient_loops"] == []
        assert result["filter_scope"]["user_role"] == "SPONSOR"

    async def test_get_dashboard_overview_cache_hit(self) -> None:
        """缓存命中时直接返回缓存数据。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        cached_data = {
            "filter_scope": {"plant_id": None, "granularity": "day", "user_role": "ADMIN"},
            "kpi_cards": {},
            "inefficient_loops": [],
            "trend_summary": {"dates": [], "composite_scores": []},
            "pending_alerts": {"open_diagnoses": 5, "open_trackers": 3},
        }
        import json

        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="ADMIN", plant_id=None, granularity="day"
            )
        # 缓存命中，不应调用 DB
        db.execute.assert_not_called()
        assert result["cached"] is True
        assert result["pending_alerts"]["open_diagnoses"] == 5

    async def test_get_dashboard_overview_empty_data(
        self, mock_dashboard_session_local
    ) -> None:
        """无数据时返回空结构。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="ADMIN", plant_id=None, granularity="day"
            )
        # KPI 卡片值应为 None（无数据）
        assert result["kpi_cards"]["auto_mode_rate"]["value"] is None
        assert result["kpi_cards"]["composite_score"]["value"] is None
        # 低效回路列表为空
        assert result["inefficient_loops"] == []
        # 趋势摘要 dates 长度为 7
        assert len(result["trend_summary"]["dates"]) == 7
        # 所有 composite_scores 为 None
        assert all(s is None for s in result["trend_summary"]["composite_scores"])

    async def test_get_dashboard_overview_with_plant_id(
        self, mock_dashboard_session_local
    ) -> None:
        """带 plant_id 时查询装置名称。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        plant_id = "00000000-0000-0000-0000-000000000111"
        # mock_db 仅用于 _get_plant_name 查询
        db.execute = AsyncMock(
            return_value=_make_scalar_one_or_none_mock(
                _make_plant_node(node_id=plant_id, name="加氢装置")
            )
        )
        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="ADMIN", plant_id=plant_id, granularity="day"
            )
        assert result["filter_scope"]["plant_id"] == plant_id
        assert result["filter_scope"]["plant_name"] == "加氢装置"

    async def test_granularity_week(self, mock_dashboard_session_local) -> None:
        """week 粒度使用 7 天时间窗。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="ADMIN", plant_id=None, granularity="week"
            )
        assert result["filter_scope"]["granularity"] == "week"

    async def test_granularity_month(self, mock_dashboard_session_local) -> None:
        """month 粒度使用 30 天时间窗。"""
        from app.services.dashboard import get_dashboard_overview

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with patch("app.services.dashboard.redis_client") as mock_redis:
            mock_redis.get = AsyncMock(return_value=None)
            mock_redis.setex = AsyncMock(return_value=None)
            result = await get_dashboard_overview(
                db=db, user_role="ADMIN", plant_id=None, granularity="month"
            )
        assert result["filter_scope"]["granularity"] == "month"

    async def test_inefficient_loops_sorted_by_score_asc(self) -> None:
        """低效回路按综合评分升序排序。"""
        from datetime import UTC, datetime

        from app.services.dashboard import _build_inefficient_loops

        db = AsyncMock()
        # 3 个回路，评分分别为 50, 30, 70
        snapshots = [
            _make_snapshot(
                loop_id="loop-1", score=Decimal("50.00"), ts_start=datetime.now(UTC)
            ),
            _make_snapshot(
                loop_id="loop-2", score=Decimal("30.00"), ts_start=datetime.now(UTC)
            ),
            _make_snapshot(
                loop_id="loop-3", score=Decimal("70.00"), ts_start=datetime.now(UTC)
            ),
        ]
        loops = [
            _make_loop(loop_id="loop-1", tag_name="FIC-101"),
            _make_loop(loop_id="loop-2", tag_name="FIC-102"),
            _make_loop(loop_id="loop-3", tag_name="FIC-103"),
        ]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(snapshots)
            if call_count[0] == 2:
                return _make_scalars_mock(loops)
            if call_count[0] == 3:
                return _make_scalars_mock([_make_plant_node()])
            if call_count[0] == 4:
                return _make_all_mock([])
            return _make_scalars_mock([])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        result = await _build_inefficient_loops(
            db=db, plant_id=None, start=datetime.now(UTC) - timedelta(days=1),
            end=datetime.now(UTC),
        )
        assert len(result) == 3
        # 升序：30, 50, 70
        assert result[0]["composite_score"] == 30.0
        assert result[1]["composite_score"] == 50.0
        assert result[2]["composite_score"] == 70.0

    async def test_inefficient_loops_top_10_limit(self) -> None:
        """低效回路最多返回 10 个。"""
        from app.services.dashboard import _build_inefficient_loops

        db = AsyncMock()
        # 15 个回路
        snapshots = [
            _make_snapshot(
                loop_id=f"loop-{i}",
                score=Decimal(str(100 - i)),
                ts_start=datetime.now(UTC),
            )
            for i in range(15)
        ]
        loops = [_make_loop(loop_id=f"loop-{i}", tag_name=f"FIC-{i}") for i in range(15)]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(snapshots)
            if call_count[0] == 2:
                return _make_scalars_mock(loops)
            if call_count[0] == 3:
                return _make_scalars_mock([_make_plant_node()])
            if call_count[0] == 4:
                return _make_all_mock([])
            return _make_scalars_mock([])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        result = await _build_inefficient_loops(
            db=db, plant_id=None, start=datetime.now(UTC) - timedelta(days=1),
            end=datetime.now(UTC),
        )
        assert len(result) == 10

    async def test_trend_summary_7_days(self) -> None:
        """趋势摘要返回最近 7 天数据。"""
        from app.services.dashboard import _build_trend_summary

        db = AsyncMock()
        now = datetime.now(UTC)
        # 构造最近 3 天的快照
        snapshots = [
            _make_snapshot(
                score=Decimal("80.00"),
                ts_start=now - timedelta(days=1),
            ),
            _make_snapshot(
                score=Decimal("85.00"),
                ts_start=now - timedelta(days=2),
            ),
        ]
        db.execute = AsyncMock(return_value=_make_scalars_mock(snapshots))
        result = await _build_trend_summary(db=db, plant_id=None, now=now)
        assert len(result["dates"]) == 7
        assert len(result["composite_scores"]) == 7
        # 至少有一些非 None 值
        assert any(s is not None for s in result["composite_scores"])

    async def test_pending_alerts_count(self) -> None:
        """待处理异常数正确统计。"""
        from app.services.dashboard import _build_pending_alerts

        db = AsyncMock()
        trackers = [
            _make_tracker(action_status="PENDING"),
            _make_tracker(action_status="IN_PROGRESS"),
        ]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(trackers)
            if call_count[0] == 2:
                return _make_scalar_mock(5)
            return _make_scalars_mock([])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        result = await _build_pending_alerts(db=db, plant_id=None)
        assert result["open_trackers"] == 2
        assert result["open_diagnoses"] == 5

    async def test_kpi_cards_trend_calculation(self) -> None:
        """KPI 卡片趋势计算正确。"""
        from app.services.dashboard import _build_kpi_cards

        # 当前周期评分 85，上一周期 80，趋势应为 up
        current = [_make_snapshot(score=Decimal("85.00"), auto_mode_rate=Decimal("90.00"))]
        previous = [_make_snapshot(score=Decimal("80.00"), auto_mode_rate=Decimal("85.00"))]
        cards = _build_kpi_cards(
            current_snapshots=current, previous_snapshots=previous
        )
        # 综合评分上升 5 > 0.5 阈值，trend=up
        assert cards["composite_score"]["value"] == 85.0
        assert cards["composite_score"]["delta"] == 5.0
        assert cards["composite_score"]["trend"] == "up"
        # 自控率上升 5 > 0.5，trend=up
        assert cards["auto_mode_rate"]["trend"] == "up"

    async def test_kpi_cards_trend_stable(self) -> None:
        """变化幅度小于阈值时趋势为 stable。"""
        from app.services.dashboard import _build_kpi_cards

        current = [_make_snapshot(score=Decimal("85.00"))]
        previous = [_make_snapshot(score=Decimal("85.20"))]
        cards = _build_kpi_cards(
            current_snapshots=current, previous_snapshots=previous
        )
        # delta = -0.2，绝对值 < 0.5，trend=stable
        assert cards["composite_score"]["delta"] == -0.2
        assert cards["composite_score"]["trend"] == "stable"

    async def test_kpi_cards_trend_down(self) -> None:
        """评分下降时趋势为 down。"""
        from app.services.dashboard import _build_kpi_cards

        current = [_make_snapshot(score=Decimal("70.00"))]
        previous = [_make_snapshot(score=Decimal("85.00"))]
        cards = _build_kpi_cards(
            current_snapshots=current, previous_snapshots=previous
        )
        assert cards["composite_score"]["delta"] == -15.0
        assert cards["composite_score"]["trend"] == "down"

    async def test_kpi_cards_no_previous_data(self) -> None:
        """无上一周期数据时 delta 为 0，trend 为 stable。"""
        from app.services.dashboard import _build_kpi_cards

        current = [_make_snapshot(score=Decimal("85.00"))]
        previous = []
        cards = _build_kpi_cards(
            current_snapshots=current, previous_snapshots=previous
        )
        assert cards["composite_score"]["value"] == 85.0
        assert cards["composite_score"]["delta"] == 0.0
        assert cards["composite_score"]["trend"] == "stable"


# ---------------------------------------------------------------------------
# 缓存 key 构建测试
# ---------------------------------------------------------------------------


class TestCacheKey:
    """缓存 key 构建测试。"""

    def test_cache_key_with_plant_id(self) -> None:
        """带 plant_id 的缓存 key。"""
        from app.services.dashboard import _build_cache_key

        key = _build_cache_key("plant-123", "day", "ADMIN")
        assert key == "dashboard:overview:plant-123:day:ADMIN"

    def test_cache_key_without_plant_id(self) -> None:
        """无 plant_id 时使用 all。"""
        from app.services.dashboard import _build_cache_key

        key = _build_cache_key(None, "week", "IC_ENGINEER")
        assert key == "dashboard:overview:all:week:IC_ENGINEER"

    def test_cache_key_differs_by_granularity(self) -> None:
        """不同粒度不同 key。"""
        from app.services.dashboard import _build_cache_key

        key_day = _build_cache_key(None, "day", "ADMIN")
        key_week = _build_cache_key(None, "week", "ADMIN")
        key_month = _build_cache_key(None, "month", "ADMIN")
        assert key_day != key_week
        assert key_week != key_month
        assert key_day != key_month
