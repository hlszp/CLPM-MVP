"""D4-2/D4-3 Tracker 整改效果验证测试.

覆盖：
D4-2:
- beat schedule 注册（tracker-verification-hourly）
- _judge_effect 判定逻辑（改善/恶化/持平）
- _get_verification_interval_hours 读取（默认/合法/非法/过小）
- 周期任务主流程（mock get_ab_compare，验证回写 effect_verified）
- dataInsufficient 跳过逻辑

D4-3:
- 整改有效率统计 service（空数据/有数据/有效率计算）
- 整改有效率统计 API（端点可访问/参数校验）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.tracker import get_tracker_effectiveness
from app.tasks.tracker_verification import (
    VERIFICATION_INTERVAL_DEFAULT,
    _get_verification_interval_hours,
    _judge_effect,
    _verify_single_tracker,
    verify_implementation_effect,
)
from tests.conftest import TEST_USERS, mock_current_user


class TestBeatScheduleRegistration:
    """Beat 调度注册测试。"""

    def test_beat_schedule_contains_tracker_verification(self) -> None:
        """beat_schedule 应包含 tracker-verification-hourly 条目。"""
        from app.tasks.celery_app import celery_app

        beat = celery_app.conf.beat_schedule
        assert "tracker-verification-hourly" in beat
        entry = beat["tracker-verification-hourly"]
        assert entry["task"] == "app.tasks.tracker_verification.verify_implementation_effect"


class TestJudgeEffect:
    """_judge_effect 判定逻辑测试。"""

    def test_improved_more_than_deteriorated(self) -> None:
        """改善指标数 > 恶化指标数 → effect_verified=True。"""
        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {"metricKey": "k1", "improved": True},
                {"metricKey": "k2", "improved": True},
                {"metricKey": "k3", "improved": False},
                {"metricKey": "k4", "improved": None},
            ],
        }
        effect, summary = _judge_effect(ab_result)
        assert effect is True
        assert summary["improvedCount"] == 2
        assert summary["deterioratedCount"] == 1
        assert summary["unchangedCount"] == 1

    def test_deteriorated_more_than_improved(self) -> None:
        """恶化指标数 > 改善指标数 → effect_verified=False。"""
        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {"metricKey": "k1", "improved": False},
                {"metricKey": "k2", "improved": False},
                {"metricKey": "k3", "improved": True},
            ],
        }
        effect, summary = _judge_effect(ab_result)
        assert effect is False
        assert summary["improvedCount"] == 1
        assert summary["deterioratedCount"] == 2

    def test_equal_improved_deteriorated(self) -> None:
        """改善==恶化 → effect_verified=True（无明显变化但已验证）。"""
        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {"metricKey": "k1", "improved": True},
                {"metricKey": "k2", "improved": False},
            ],
        }
        effect, summary = _judge_effect(ab_result)
        assert effect is True
        assert summary["improvedCount"] == 1
        assert summary["deterioratedCount"] == 1

    def test_all_unchanged(self) -> None:
        """全部持平 → effect_verified=True（已验证，无明显变化）。"""
        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {"metricKey": "k1", "improved": None},
                {"metricKey": "k2", "improved": None},
            ],
        }
        effect, summary = _judge_effect(ab_result)
        assert effect is True
        assert summary["unchangedCount"] == 2

    def test_summary_contains_kpi_snapshot(self) -> None:
        """summary 应包含精简版 KPI 对比快照。"""
        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {
                    "metricKey": "settling_time",
                    "metricName": "稳定时间",
                    "before": 100.0,
                    "after": 80.0,
                    "change": -20.0,
                    "improved": True,
                },
            ],
        }
        _, summary = _judge_effect(ab_result)
        assert len(summary["kpiComparison"]) == 1
        item = summary["kpiComparison"][0]
        assert item["metricKey"] == "settling_time"
        assert item["metricName"] == "稳定时间"
        assert item["before"] == 100.0
        assert item["after"] == 80.0
        assert item["improved"] is True


class TestGetVerificationInterval:
    """_get_verification_interval_hours 读取测试。"""

    @pytest.mark.asyncio
    async def test_default_when_config_missing(self) -> None:
        """sys_config 无此 key 时返回默认 24。"""
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        hours = await _get_verification_interval_hours(db)
        assert hours == VERIFICATION_INTERVAL_DEFAULT

    @pytest.mark.asyncio
    async def test_valid_config_value(self) -> None:
        """sys_config 有合法值时返回该值。"""
        cfg = MagicMock()
        cfg.value = "48"
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg))
        )
        hours = await _get_verification_interval_hours(db)
        assert hours == 48

    @pytest.mark.asyncio
    async def test_invalid_value_falls_back_to_default(self) -> None:
        """sys_config 值非法时回落默认值。"""
        cfg = MagicMock()
        cfg.value = "not-a-number"
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg))
        )
        hours = await _get_verification_interval_hours(db)
        assert hours == VERIFICATION_INTERVAL_DEFAULT

    @pytest.mark.asyncio
    async def test_too_small_value_falls_back_to_default(self) -> None:
        """sys_config 值 < 1 时回落默认值。"""
        cfg = MagicMock()
        cfg.value = "0"
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg))
        )
        hours = await _get_verification_interval_hours(db)
        assert hours == VERIFICATION_INTERVAL_DEFAULT


class TestVerifySingleTracker:
    """_verify_single_tracker 单条验证测试。"""

    @pytest.mark.asyncio
    async def test_data_insufficient_skips(self) -> None:
        """dataInsufficient=True 时跳过，不回写字段。"""
        tracker = MagicMock()
        tracker.id = str(uuid4())
        tracker.loop_id = str(uuid4())
        tracker.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
        tracker.effect_verified = None

        db = MagicMock()
        db.commit = AsyncMock()

        with patch(
            "app.tasks.tracker_verification.get_ab_compare",
            new_callable=AsyncMock,
            return_value={"dataInsufficient": True, "kpiComparison": []},
        ):
            done = await _verify_single_tracker(db, tracker)

        assert done is False
        assert tracker.effect_verified is None
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_improved_writes_true(self) -> None:
        """改善场景回写 effect_verified=True。"""
        tracker = MagicMock()
        tracker.id = str(uuid4())
        tracker.loop_id = str(uuid4())
        tracker.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
        tracker.effect_verified = None

        db = MagicMock()
        db.commit = AsyncMock()

        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {"metricKey": "k1", "improved": True},
                {"metricKey": "k2", "improved": True},
                {"metricKey": "k3", "improved": False},
            ],
        }

        with patch(
            "app.tasks.tracker_verification.get_ab_compare",
            new_callable=AsyncMock,
            return_value=ab_result,
        ):
            done = await _verify_single_tracker(db, tracker)

        assert done is True
        assert tracker.effect_verified is True
        assert tracker.effect_verified_at is not None
        assert tracker.ab_compare_summary is not None
        assert tracker.ab_compare_summary["improvedCount"] == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_deteriorated_writes_false(self) -> None:
        """恶化场景回写 effect_verified=False。"""
        tracker = MagicMock()
        tracker.id = str(uuid4())
        tracker.loop_id = str(uuid4())
        tracker.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
        tracker.effect_verified = None

        db = MagicMock()
        db.commit = AsyncMock()

        ab_result = {
            "dataInsufficient": False,
            "kpiComparison": [
                {"metricKey": "k1", "improved": False},
                {"metricKey": "k2", "improved": False},
                {"metricKey": "k3", "improved": True},
            ],
        }

        with patch(
            "app.tasks.tracker_verification.get_ab_compare",
            new_callable=AsyncMock,
            return_value=ab_result,
        ):
            done = await _verify_single_tracker(db, tracker)

        assert done is True
        assert tracker.effect_verified is False
        assert tracker.ab_compare_summary["deterioratedCount"] == 2

    @pytest.mark.asyncio
    async def test_no_loop_id_skips(self) -> None:
        """tracker 无 loop_id 时跳过。"""
        tracker = MagicMock()
        tracker.id = str(uuid4())
        tracker.loop_id = None

        db = MagicMock()
        done = await _verify_single_tracker(db, tracker)
        assert done is False

    @pytest.mark.asyncio
    async def test_ab_compare_exception_skips(self) -> None:
        """A/B 对比计算异常时跳过，不回写。"""
        tracker = MagicMock()
        tracker.id = str(uuid4())
        tracker.loop_id = str(uuid4())
        tracker.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
        tracker.effect_verified = None

        db = MagicMock()
        db.commit = AsyncMock()

        with patch(
            "app.tasks.tracker_verification.get_ab_compare",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB error"),
        ):
            done = await _verify_single_tracker(db, tracker)

        assert done is False
        assert tracker.effect_verified is None
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_uses_implemented_at_field(self) -> None:
        """P3-01: 优先用 tracker.implemented_at 而非 updated_at 作为 A/B 对比 T。"""
        tracker = MagicMock()
        tracker.id = str(uuid4())
        tracker.loop_id = str(uuid4())
        tracker.implemented_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
        tracker.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        tracker.effect_verified = None

        db = MagicMock()
        db.commit = AsyncMock()

        captured: dict = {}

        async def _capture(*args, **kwargs):
            captured.update(kwargs)
            return {
                "dataInsufficient": False,
                "kpiComparison": [{"metricKey": "k1", "improved": True}],
            }

        with patch(
            "app.tasks.tracker_verification.get_ab_compare",
            side_effect=_capture,
        ):
            await _verify_single_tracker(db, tracker)

        # get_ab_compare 收到的 implemented_at 应来自 tracker.implemented_at
        impl_iso = captured.get("implemented_at", "")
        assert impl_iso.startswith(tracker.implemented_at.isoformat()[:19])
        # 不应等于 updated_at 的时间
        assert not impl_iso.startswith(tracker.updated_at.isoformat()[:19])


class TestFetchPendingTrackers:
    """P3-01: _fetch_pending_trackers 状态机修复测试。"""

    @pytest.mark.asyncio
    async def test_query_includes_verifying_status(self) -> None:
        """查询条件应覆盖 VERIFYING 状态（P1a 闭环状态机修复）。"""
        from app.tasks.tracker_verification import _fetch_pending_trackers

        db = MagicMock()
        verifying_tracker = MagicMock()
        verifying_tracker.action_status = "VERIFYING"
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [verifying_tracker]
        db.execute = AsyncMock(return_value=result_mock)

        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
        trackers = await _fetch_pending_trackers(db, cutoff)

        # 返回 VERIFYING tracker
        assert len(trackers) == 1
        assert trackers[0].action_status == "VERIFYING"
        # 编译后的 SQL 包含 VERIFYING 和 IMPLEMENTED
        stmt = db.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "VERIFYING" in compiled
        assert "IMPLEMENTED" in compiled


class TestTaskEntry:
    """任务入口测试。"""

    def test_verify_implementation_effect_callable(self) -> None:
        """任务入口可调用且返回 dict。"""
        expected = {"total": 5, "verified": 3, "skipped": 2}
        with patch(
            "app.tasks.tracker_verification._do_verify_implementation_effect",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = verify_implementation_effect()

        assert result == expected


class TestVerificationConfigAPI:
    """D4-2 验证周期配置 API 测试。"""

    def test_get_config_returns_default(self, client, mock_db, fake_redis) -> None:
        """sys_config 无此 key 时返回默认 24 小时。"""
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tracker/verification-config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["intervalHours"] == 24
        assert body["data"]["updatedBy"] is None

    def test_get_config_returns_stored_value(self, client, mock_db, fake_redis) -> None:
        """sys_config 有值时返回存储值。"""
        cfg = MagicMock()
        cfg.value = "48"
        cfg.updated_by = "admin"
        cfg.updated_at = datetime(2026, 7, 26, 10, 0, 0)
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cfg))
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tracker/verification-config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["intervalHours"] == 48
        assert body["data"]["updatedBy"] == "admin"

    def test_update_config_admin_only(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 角色不能修改验证周期。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.patch(
                "/api/v1/tracker/verification-config",
                headers={"Authorization": "Bearer fake-token"},
                json={"intervalHours": 48},
            )
        assert resp.status_code == 403

    def test_update_config_invalid_range(self, client, mock_db, fake_redis) -> None:
        """验证周期超出 1~720 范围时返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.patch(
                "/api/v1/tracker/verification-config",
                headers={"Authorization": "Bearer fake-token"},
                json={"intervalHours": 0},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# D4-3 整改有效率统计测试
# ---------------------------------------------------------------------------


def _make_scalar_mock(value):
    """构造一个 scalar() 返回 value 的 mock 结果。"""
    mock_result = MagicMock()
    mock_result.scalar = MagicMock(return_value=value)
    return mock_result


class TestGetTrackerEffectiveness:
    """D4-3 整改有效率统计 service 测试。"""

    @pytest.mark.asyncio
    async def test_empty_data_returns_zeros(self) -> None:
        """无验证数据时返回全 0 和 null effectiveRate。"""
        db = MagicMock()
        # 5 次 execute：impl / verified / improved / deteriorated / pending / trend
        # 全部返回 0
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_mock(0),  # total_implemented
                _make_scalar_mock(0),  # verified_count
                _make_scalar_mock(0),  # improved_count
                _make_scalar_mock(0),  # deteriorated_count
                _make_scalar_mock(0),  # pending_count
                MagicMock(all=MagicMock(return_value=[])),  # trend
            ]
        )

        result = await get_tracker_effectiveness(db, time_window="last_30_days")

        assert result["totalImplemented"] == 0
        assert result["verifiedCount"] == 0
        assert result["improvedCount"] == 0
        assert result["deterioratedCount"] == 0
        assert result["effectiveRate"] is None
        assert result["pendingVerificationCount"] == 0
        assert result["trend"] == []

    @pytest.mark.asyncio
    async def test_effective_rate_calculation(self) -> None:
        """有验证数据时正确计算 effectiveRate = improved / verified。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_mock(10),  # total_implemented
                _make_scalar_mock(8),  # verified_count
                _make_scalar_mock(6),  # improved_count
                _make_scalar_mock(2),  # deteriorated_count
                _make_scalar_mock(2),  # pending_count
                MagicMock(all=MagicMock(return_value=[])),  # trend
            ]
        )

        result = await get_tracker_effectiveness(db, time_window="last_30_days")

        assert result["totalImplemented"] == 10
        assert result["verifiedCount"] == 8
        assert result["improvedCount"] == 6
        assert result["deterioratedCount"] == 2
        assert result["effectiveRate"] == 0.75  # 6/8
        assert result["pendingVerificationCount"] == 2

    @pytest.mark.asyncio
    async def test_all_improved_rate_is_one(self) -> None:
        """全部改善时 effectiveRate=1.0。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_mock(5),  # total_implemented
                _make_scalar_mock(5),  # verified_count
                _make_scalar_mock(5),  # improved_count
                _make_scalar_mock(0),  # deteriorated_count
                _make_scalar_mock(0),  # pending_count
                MagicMock(all=MagicMock(return_value=[])),  # trend
            ]
        )

        result = await get_tracker_effectiveness(db, time_window="last_7_days")
        assert result["effectiveRate"] == 1.0

    @pytest.mark.asyncio
    async def test_all_deteriorated_rate_is_zero(self) -> None:
        """全部恶化时 effectiveRate=0.0。"""
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_mock(3),  # total_implemented
                _make_scalar_mock(3),  # verified_count
                _make_scalar_mock(0),  # improved_count
                _make_scalar_mock(3),  # deteriorated_count
                _make_scalar_mock(0),  # pending_count
                MagicMock(all=MagicMock(return_value=[])),  # trend
            ]
        )

        result = await get_tracker_effectiveness(db, time_window="last_90_days")
        assert result["effectiveRate"] == 0.0


class TestEffectivenessAPI:
    """D4-3 整改有效率统计 API 测试。"""

    def test_effectiveness_endpoint_accessible(self, client, mock_db, fake_redis) -> None:
        """所有角色可访问整改有效率统计接口。"""
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=0)
        mock_db.execute = AsyncMock(
            side_effect=[
                mock_result,  # total_implemented
                mock_result,  # verified_count
                mock_result,  # improved_count
                mock_result,  # deteriorated_count
                mock_result,  # pending_count
                MagicMock(all=MagicMock(return_value=[])),  # trend
            ]
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/tracker/effectiveness",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "totalImplemented" in body["data"]
        assert "verifiedCount" in body["data"]
        assert "improvedCount" in body["data"]
        assert "deterioratedCount" in body["data"]
        assert "effectiveRate" in body["data"]
        assert "pendingVerificationCount" in body["data"]
        assert "trend" in body["data"]

    def test_effectiveness_with_time_window_param(self, client, mock_db, fake_redis) -> None:
        """支持 timeWindow 查询参数。"""
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=0)
        mock_db.execute = AsyncMock(
            side_effect=[
                mock_result,
                mock_result,
                mock_result,
                mock_result,
                mock_result,
                MagicMock(all=MagicMock(return_value=[])),
            ]
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tracker/effectiveness?timeWindow=last_7_days",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_effectiveness_all_roles_can_access(self, client, mock_db, fake_redis) -> None:
        """所有角色（含 sponsor）均可访问。"""
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=0)
        mock_db.execute = AsyncMock(
            side_effect=[
                mock_result,
                mock_result,
                mock_result,
                mock_result,
                mock_result,
                MagicMock(all=MagicMock(return_value=[])),
            ]
        )
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/tracker/effectiveness",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
