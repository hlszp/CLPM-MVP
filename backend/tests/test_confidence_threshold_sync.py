"""可信度阈值多进程同步测试（可信度统一 Phase 3 / P3-2 / D4）.

验证：
1. set_thresholds 带版本号的去重逻辑
2. _handle_threshold_message 消息解析 + 版本号去重
3. broadcast_thresholds 调用 Redis INCR + PUBLISH
4. load_thresholds_from_db 从 sys_config 加载阈值
5. start_threshold_subscriber 幂等性（不重复启动线程）

设计依据：confidence-unification-plan-2026-08-04.md §7.3 / §10.1 D4
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.confidence_evaluator import (
    DEFAULT_CONFIDENCE_THRESHOLDS,
    THRESHOLD_CHANNEL,
    THRESHOLD_VERSION_KEY,
    ConfidenceEvaluator,
    _handle_threshold_message,
    broadcast_thresholds,
    load_thresholds_from_db,
    start_threshold_subscriber,
)

# ---------------------------------------------------------------------------
# 辅助：每个测试前后重置阈值缓存与版本号，避免测试间互相干扰
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_thresholds():
    """每个测试前后重置阈值缓存为算法默认值，版本号归零."""
    import app.services.confidence_evaluator as ce

    ce._threshold_cache = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
    ce._threshold_version = 0
    yield
    ce._threshold_cache = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
    ce._threshold_version = 0


# ---------------------------------------------------------------------------
# 1. set_thresholds 版本号去重
# ---------------------------------------------------------------------------


class TestSetThresholdsWithVersion:
    """set_thresholds(thresholds, version) 的版本号更新逻辑."""

    def test_version_updates_when_higher(self):
        """version > 当前版本号时，版本号更新."""
        ConfidenceEvaluator.set_thresholds({"A": 0.90, "B": 0.70, "C": 0.50, "D": 0.10}, version=5)
        assert ConfidenceEvaluator.get_threshold_version() == 5
        assert ConfidenceEvaluator.get_thresholds()["A"] == 0.90

    def test_version_ignored_when_lower_or_equal(self):
        """version <= 当前版本号时，版本号不更新（但阈值仍更新）."""
        ConfidenceEvaluator.set_thresholds({"A": 0.90, "B": 0.70, "C": 0.50, "D": 0.10}, version=10)
        assert ConfidenceEvaluator.get_threshold_version() == 10

        # 旧版本号消息：阈值会更新但版本号不变
        ConfidenceEvaluator.set_thresholds({"A": 0.80, "B": 0.60, "C": 0.40, "D": 0.10}, version=5)
        assert ConfidenceEvaluator.get_threshold_version() == 10
        assert ConfidenceEvaluator.get_thresholds()["A"] == 0.80

    def test_version_none_does_not_update(self):
        """version=None 时（如启动预载），版本号不更新."""
        ConfidenceEvaluator.set_thresholds({"A": 0.90, "B": 0.70, "C": 0.50, "D": 0.10}, version=3)
        assert ConfidenceEvaluator.get_threshold_version() == 3

        ConfidenceEvaluator.set_thresholds({"A": 0.85, "B": 0.65, "C": 0.45, "D": 0.15})
        assert ConfidenceEvaluator.get_threshold_version() == 3
        assert ConfidenceEvaluator.get_thresholds()["A"] == 0.85

    def test_reset_to_default_with_none_thresholds(self):
        """thresholds=None 重置为算法默认值."""
        ConfidenceEvaluator.set_thresholds({"A": 0.90, "B": 0.70, "C": 0.50, "D": 0.10}, version=1)
        ConfidenceEvaluator.set_thresholds(None)
        assert ConfidenceEvaluator.get_thresholds() == DEFAULT_CONFIDENCE_THRESHOLDS


# ---------------------------------------------------------------------------
# 2. _handle_threshold_message 消息解析 + 去重
# ---------------------------------------------------------------------------


class TestHandleThresholdMessage:
    """_handle_threshold_message 的消息解析与版本号去重逻辑."""

    def test_valid_message_updates_thresholds(self):
        """有效消息：版本号更高时更新阈值."""
        msg = json.dumps(
            {
                "version": 1,
                "thresholds": {"A": 0.92, "B": 0.72, "C": 0.52, "D": 0.12},
                "updated_at": datetime.now(UTC).isoformat(),
                "source": "api:admin",
            }
        )
        result = _handle_threshold_message(msg)
        assert result is True
        assert ConfidenceEvaluator.get_thresholds()["A"] == 0.92
        assert ConfidenceEvaluator.get_threshold_version() == 1

    def test_stale_version_skipped(self):
        """版本号 <= 当前版本号时跳过（去重）."""
        # 先应用 version=5
        msg_v5 = json.dumps(
            {
                "version": 5,
                "thresholds": {"A": 0.92, "B": 0.72, "C": 0.52, "D": 0.12},
                "source": "api",
            }
        )
        _handle_threshold_message(msg_v5)
        assert ConfidenceEvaluator.get_threshold_version() == 5

        # 旧版本号消息应被跳过
        msg_v3 = json.dumps(
            {
                "version": 3,
                "thresholds": {"A": 0.80, "B": 0.60, "C": 0.40, "D": 0.10},
                "source": "api",
            }
        )
        result = _handle_threshold_message(msg_v3)
        assert result is False
        assert ConfidenceEvaluator.get_thresholds()["A"] == 0.92  # 未被覆盖
        assert ConfidenceEvaluator.get_threshold_version() == 5

    def test_equal_version_skipped(self):
        """版本号相等时也跳过（仅严格大于才更新）."""
        msg = json.dumps(
            {
                "version": 5,
                "thresholds": {"A": 0.92, "B": 0.72, "C": 0.52, "D": 0.12},
                "source": "api",
            }
        )
        _handle_threshold_message(msg)
        result = _handle_threshold_message(msg)  # 同版本号
        assert result is False

    def test_invalid_json_returns_false(self):
        """无效 JSON 返回 False."""
        result = _handle_threshold_message("not-json")
        assert result is False

    def test_missing_version_returns_false(self):
        """缺少 version 字段时返回 False（int(None) 抛 TypeError）."""
        msg = json.dumps({"thresholds": {"A": 0.9}, "source": "api"})
        result = _handle_threshold_message(msg)
        assert result is False

    def test_empty_thresholds_resets_to_default(self):
        """空 thresholds 字典重置为算法默认值."""
        msg = json.dumps({"version": 2, "thresholds": {}, "source": "api"})
        result = _handle_threshold_message(msg)
        assert result is True
        assert ConfidenceEvaluator.get_thresholds() == DEFAULT_CONFIDENCE_THRESHOLDS


# ---------------------------------------------------------------------------
# 3. broadcast_thresholds 调用 Redis INCR + PUBLISH
# ---------------------------------------------------------------------------


class TestBroadcastThresholds:
    """broadcast_thresholds 的 Redis 广播逻辑."""

    @pytest.mark.asyncio
    async def test_incr_and_publish_called(self):
        """广播时调用 Redis INCR 版本号 + PUBLISH 消息."""
        mock_redis = MagicMock()
        mock_redis.incr = AsyncMock(return_value=7)
        mock_redis.publish = AsyncMock(return_value=1)

        with patch("app.core.redis.redis_client", mock_redis):
            version = await broadcast_thresholds(
                {"A": 0.95, "B": 0.80, "C": 0.60, "D": 0.20},
                source="api:admin",
            )

        assert version == 7
        mock_redis.incr.assert_awaited_once_with(THRESHOLD_VERSION_KEY)
        mock_redis.publish.assert_awaited_once()
        # 验证 PUBLISH 频道
        call_args = mock_redis.publish.call_args
        assert call_args.args[0] == THRESHOLD_CHANNEL
        # 验证消息内容
        message = json.loads(call_args.args[1])
        assert message["version"] == 7
        assert message["thresholds"]["A"] == 0.95
        assert message["source"] == "api:admin"
        assert "updated_at" in message


# ---------------------------------------------------------------------------
# 4. load_thresholds_from_db 从 sys_config 加载
# ---------------------------------------------------------------------------


class TestLoadThresholdsFromDb:
    """load_thresholds_from_db 的 DB 预载逻辑."""

    @pytest.mark.asyncio
    async def test_loads_from_sys_config(self):
        """sys_config 有配置时加载到缓存."""
        saved = json.dumps(
            {
                "thresholds": [
                    {"level": 1, "name": "A", "minRate": 0.92},
                    {"level": 2, "name": "B", "minRate": 0.72},
                    {"level": 3, "name": "C", "minRate": 0.52},
                    {"level": 4, "name": "D", "minRate": 0.12},
                    {"level": 5, "name": "E", "minRate": 0.0},
                ],
                "updatedAt": "2026-08-04T10:00:00Z",
                "updatedBy": "admin",
            }
        )
        mock_cfg = MagicMock()
        mock_cfg.value = saved
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cfg
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        await load_thresholds_from_db(mock_db)

        thresholds = ConfidenceEvaluator.get_thresholds()
        assert thresholds["A"] == 0.92
        assert thresholds["B"] == 0.72
        assert thresholds["C"] == 0.52
        assert thresholds["D"] == 0.12

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_no_config(self):
        """sys_config 无配置时回退算法默认值."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        await load_thresholds_from_db(mock_db)

        assert ConfidenceEvaluator.get_thresholds() == DEFAULT_CONFIDENCE_THRESHOLDS

    @pytest.mark.asyncio
    async def test_falls_back_on_parse_error(self):
        """JSON 解析失败时回退算法默认值."""
        mock_cfg = MagicMock()
        mock_cfg.value = "invalid-json"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cfg
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        await load_thresholds_from_db(mock_db)

        assert ConfidenceEvaluator.get_thresholds() == DEFAULT_CONFIDENCE_THRESHOLDS

    @pytest.mark.asyncio
    async def test_falls_back_on_empty_thresholds(self):
        """thresholds 列表为空时回退算法默认值."""
        saved = json.dumps({"thresholds": [], "updatedAt": "2026-08-04T10:00:00Z"})
        mock_cfg = MagicMock()
        mock_cfg.value = saved
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cfg
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        await load_thresholds_from_db(mock_db)

        assert ConfidenceEvaluator.get_thresholds() == DEFAULT_CONFIDENCE_THRESHOLDS


# ---------------------------------------------------------------------------
# 5. start_threshold_subscriber 幂等性
# ---------------------------------------------------------------------------


class TestStartThresholdSubscriber:
    """start_threshold_subscriber 的幂等启动逻辑."""

    def test_idempotent_start(self):
        """重复调用不会启动多个线程."""
        import app.services.confidence_evaluator as ce

        # 重置启动标记（避免其他测试干扰）
        ce._subscriber_started = False

        with patch("app.services.confidence_evaluator.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            start_threshold_subscriber()
            assert mock_thread.call_count == 1
            mock_thread_instance.start.assert_called_once()

            # 第二次调用不应启动新线程
            start_threshold_subscriber()
            assert mock_thread.call_count == 1

        # 清理：重置启动标记
        ce._subscriber_started = False


# ---------------------------------------------------------------------------
# 6. 端到端：广播 → 消息处理 → 阈值更新（模拟多进程同步）
# ---------------------------------------------------------------------------


class TestEndToEndSync:
    """模拟多进程同步：进程 A 广播，进程 B 收到消息后更新."""

    def test_broadcast_then_handle_updates_thresholds(self):
        """模拟进程 A 广播、进程 B 收到消息后的完整同步流程.

        场景：进程 A（API）调用 broadcast_thresholds 发布 version=3 的阈值，
        进程 B（worker）的订阅线程通过 _handle_threshold_message 处理消息，
        阈值应更新为广播中的值。
        """
        # 进程 A 广播的阈值
        new_thresholds = {"A": 0.93, "B": 0.73, "C": 0.53, "D": 0.13}
        message = json.dumps(
            {
                "version": 3,
                "thresholds": new_thresholds,
                "updated_at": datetime.now(UTC).isoformat(),
                "source": "api:admin",
            }
        )

        # 进程 B 当前版本号为 0（刚启动）
        assert ConfidenceEvaluator.get_threshold_version() == 0

        # 进程 B 收到消息并处理
        result = _handle_threshold_message(message)
        assert result is True
        assert ConfidenceEvaluator.get_threshold_version() == 3
        assert ConfidenceEvaluator.get_thresholds() == new_thresholds

    def test_concurrent_messages_ordered_by_version(self):
        """模拟乱序到达的多条消息，仅最新版本生效."""
        msgs = [
            json.dumps(
                {
                    "version": 1,
                    "thresholds": {"A": 0.91, "B": 0.71, "C": 0.51, "D": 0.11},
                    "source": "api",
                }
            ),
            json.dumps(
                {
                    "version": 3,
                    "thresholds": {"A": 0.93, "B": 0.73, "C": 0.53, "D": 0.13},
                    "source": "api",
                }
            ),
            json.dumps(
                {
                    "version": 2,
                    "thresholds": {"A": 0.92, "B": 0.72, "C": 0.52, "D": 0.12},
                    "source": "api",
                }
            ),
        ]

        for msg in msgs:
            _handle_threshold_message(msg)

        # version=3 的阈值应生效，version=2 被跳过
        assert ConfidenceEvaluator.get_threshold_version() == 3
        assert ConfidenceEvaluator.get_thresholds()["A"] == 0.93


# ---------------------------------------------------------------------------
# 7. P3-3 / D5：valid_rate [0.20, 0.30) 告警监控
# ---------------------------------------------------------------------------


class TestNearInconclusiveAlert:
    """valid_rate 濒临 INCONCLUSIVE 时的告警监控（D5）."""

    def test_warns_when_valid_rate_in_alert_zone(self, caplog):
        """valid_rate ∈ [0.20, 0.30) 时记 WARN 告警."""
        import logging

        with caplog.at_level(logging.WARNING, logger="app.services.confidence_evaluator"):
            ConfidenceEvaluator.evaluate(0.25)

        assert any("濒临 INCONCLUSIVE" in r.message for r in caplog.records)

    def test_no_warn_when_valid_rate_above_alert_zone(self, caplog):
        """valid_rate >= 0.30 时不告警."""
        import logging

        with caplog.at_level(logging.WARNING, logger="app.services.confidence_evaluator"):
            ConfidenceEvaluator.evaluate(0.50)

        assert not any("濒临 INCONCLUSIVE" in r.message for r in caplog.records)

    def test_no_warn_when_valid_rate_below_d_threshold(self, caplog):
        """valid_rate < D 阈值（0.20）时不告警（已 INCONCLUSIVE，告警无意义）."""
        import logging

        with caplog.at_level(logging.WARNING, logger="app.services.confidence_evaluator"):
            ConfidenceEvaluator.evaluate(0.15)

        assert not any("濒临 INCONCLUSIVE" in r.message for r in caplog.records)

    def test_alert_zone_follows_configured_d_threshold(self, caplog):
        """告警区间跟随 D 阈值配置：D=0.30 时告警区间为 [0.30, 0.40)."""
        import logging

        ConfidenceEvaluator.set_thresholds(
            {"A": 0.95, "B": 0.80, "C": 0.60, "D": 0.30},
        )
        with caplog.at_level(logging.WARNING, logger="app.services.confidence_evaluator"):
            # 0.35 在 [0.30, 0.40) 告警区间
            ConfidenceEvaluator.evaluate(0.35)
            # 0.25 在 D 阈值以下，不告警
            ConfidenceEvaluator.evaluate(0.25)

        warn_count = sum(1 for r in caplog.records if "濒临 INCONCLUSIVE" in r.message)
        assert warn_count == 1  # 只有 0.35 触发告警
