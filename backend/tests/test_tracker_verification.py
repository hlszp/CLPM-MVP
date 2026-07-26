"""D4-2 Tracker 整改效果自动验证任务测试.

覆盖：
- beat schedule 注册（tracker-verification-hourly）
- _judge_effect 判定逻辑（改善/恶化/持平）
- _get_verification_interval_hours 读取（默认/合法/非法/过小）
- 周期任务主流程（mock get_ab_compare，验证回写 effect_verified）
- dataInsufficient 跳过逻辑
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

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
