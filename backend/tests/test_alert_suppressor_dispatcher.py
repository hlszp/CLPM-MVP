"""智能预警规则引擎抑制器与分发器测试.

覆盖：
- Suppressor 冷却期/持续时长/节流/徽章/手动抑制
- dispatcher 动作分发（CREATE_EVENT/CREATE_TRACKER/NOTIFY）
- audit 审计日志写入与查询
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.alert_rule_engine import dispatcher, suppressor
from app.services.alert_rule_engine.audit import (
    _safe_serialize,
    list_audit_logs,
    write_audit,
)
from app.services.alert_rule_engine.dispatcher import dispatch
from app.services.alert_rule_engine.evaluator import EvaluationResult
from app.services.alert_rule_engine.suppressor import Suppressor

# ===========================================================================
# Suppressor 测试
# ===========================================================================


class TestSuppressorCooldown:
    """冷却期检查。"""

    @pytest.mark.asyncio
    async def test_no_cooldown_returns_false(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            assert await s.is_in_cooldown("key-1") is False

    @pytest.mark.asyncio
    async def test_in_cooldown_returns_true_and_increments(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await s.set_cooldown("key-1", 1800)
            assert await s.is_in_cooldown("key-1") is True
            # 重复触发应递增 trigger_count
            await s.is_in_cooldown("key-1")
            count = await s.get_trigger_count("key-1")
            assert count >= 2

    @pytest.mark.asyncio
    async def test_set_cooldown_sets_ttl(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await s.set_cooldown("key-1", 600)
            ttl = await s.get_cooldown_ttl("key-1")
            assert 0 < ttl <= 600

    @pytest.mark.asyncio
    async def test_get_trigger_count_no_record_returns_one(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            count = await s.get_trigger_count("missing-key")
            assert count == 1

    @pytest.mark.asyncio
    async def test_redis_exception_degrades_to_pass(self) -> None:
        """Redis 异常时冷却期检查降级为放行。"""
        s = Suppressor()
        bad_redis = AsyncMock()
        bad_redis.exists = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch.object(suppressor, "redis_client", bad_redis):
            assert await s.is_in_cooldown("key-1") is False
            assert await s.get_trigger_count("key-1") == 1


class TestSuppressorDuration:
    """持续时长（去抖）检查。"""

    @pytest.mark.asyncio
    async def test_zero_duration_instant_trigger(self, fake_redis) -> None:
        """duration=0 时瞬时触发。"""
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            should, reset = await s.check_duration("k", 0, condition_met=True)
            assert should is True
            assert reset is False

    @pytest.mark.asyncio
    async def test_condition_not_met_resets(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            should, reset = await s.check_duration("k", 300, condition_met=False)
            assert should is False
            assert reset is True

    @pytest.mark.asyncio
    async def test_first_seen_records_start_time(self, fake_redis) -> None:
        """首次满足条件记录开始时间，不立即告警。"""
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            should, reset = await s.check_duration("k", 300, condition_met=True)
            assert should is False
            assert reset is False
            # first_seen 已记录
            first = await fake_redis.hget("alert:duration:k", "first_seen")
            assert first is not None

    @pytest.mark.asyncio
    async def test_duration_not_reached(self, fake_redis) -> None:
        """持续时长未达到阈值时不告警。"""
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            # 预设一个 1 秒前的 first_seen
            past = datetime.now(UTC).timestamp() - 1
            await fake_redis.hset("alert:duration:k", "first_seen", str(past))
            should, _ = await s.check_duration("k", 300, condition_met=True)
            assert should is False

    @pytest.mark.asyncio
    async def test_duration_reached_triggers(self, fake_redis) -> None:
        """持续时长达到阈值时告警。"""
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            # 预设一个 301 秒前的 first_seen
            past = datetime.now(UTC).timestamp() - 301
            await fake_redis.hset("alert:duration:k", "first_seen", str(past))
            should, _ = await s.check_duration("k", 300, condition_met=True)
            assert should is True

    @pytest.mark.asyncio
    async def test_clear_duration(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await fake_redis.hset("alert:duration:k", "first_seen", "123")
            await s.clear_duration("k")
            assert await fake_redis.exists("alert:duration:k") == 0


class TestSuppressorThrottle:
    """实时轨节流。"""

    @pytest.mark.asyncio
    async def test_first_call_not_throttled(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            assert await s.is_throttled("loop-1", throttle_seconds=5) is False

    @pytest.mark.asyncio
    async def test_second_call_within_window_throttled(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await s.is_throttled("loop-1", throttle_seconds=5)
            assert await s.is_throttled("loop-1", throttle_seconds=5) is True

    @pytest.mark.asyncio
    async def test_different_loops_not_throttled(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await s.is_throttled("loop-1", throttle_seconds=5)
            assert await s.is_throttled("loop-2", throttle_seconds=5) is False


class TestSuppressorBadge:
    """徽章计数。"""

    @pytest.mark.asyncio
    async def test_increment_badge(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await s.increment_badge(["u1", "u2"])
            assert await s.get_badge_count("u1") == 1
            assert await s.get_badge_count("u2") == 1
            await s.increment_badge(["u1"])
            assert await s.get_badge_count("u1") == 2

    @pytest.mark.asyncio
    async def test_reset_badge(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            await s.increment_badge(["u1", "u1"])
            assert await s.get_badge_count("u1") == 2
            await s.reset_badge("u1")
            assert await s.get_badge_count("u1") == 0

    @pytest.mark.asyncio
    async def test_get_badge_no_record_returns_zero(self, fake_redis) -> None:
        s = Suppressor()
        with patch.object(suppressor, "redis_client", fake_redis):
            assert await s.get_badge_count("unknown") == 0


class TestSuppressorManualSuppression:
    """手动抑制检查。"""

    @pytest.mark.asyncio
    async def test_no_suppression_returns_false(self) -> None:
        s = Suppressor()
        mock_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=result_mock)

        # AsyncSessionLocal 在函数内从 app.core.db 导入，patch 源模块
        with patch("app.core.db.AsyncSessionLocal") as m:
            m.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            m.return_value.__aexit__ = AsyncMock(return_value=None)
            assert await s.is_manually_suppressed("loop-1", "rule-1") is False

    @pytest.mark.asyncio
    async def test_active_suppression_returns_true(self) -> None:
        s = Suppressor()
        mock_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = MagicMock()  # 有记录
        mock_session.execute = AsyncMock(return_value=result_mock)

        with patch("app.core.db.AsyncSessionLocal") as m:
            m.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            m.return_value.__aexit__ = AsyncMock(return_value=None)
            assert await s.is_manually_suppressed("loop-1", "rule-1") is True

    @pytest.mark.asyncio
    async def test_exception_degrades_to_false(self) -> None:
        s = Suppressor()
        with patch(
            "app.core.db.AsyncSessionLocal",
            side_effect=RuntimeError("db down"),
        ):
            assert await s.is_manually_suppressed("loop-1", "rule-1") is False


class TestSuppressorResetExpired:
    """过期抑制记录自动失效。"""

    @pytest.mark.asyncio
    async def test_reset_expired_returns_count(self) -> None:
        s = Suppressor()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.core.db.AsyncSessionLocal") as m:
            m.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            m.return_value.__aexit__ = AsyncMock(return_value=None)
            count = await s.reset_expired_suppressions()
        assert count == 3

    @pytest.mark.asyncio
    async def test_reset_expired_exception_returns_zero(self) -> None:
        s = Suppressor()
        with patch(
            "app.core.db.AsyncSessionLocal",
            side_effect=RuntimeError("db down"),
        ):
            count = await s.reset_expired_suppressions()
        assert count == 0


# ===========================================================================
# Audit 测试
# ===========================================================================


class TestSafeSerialize:
    """_safe_serialize 安全序列化。"""

    def test_none_returns_none(self) -> None:
        assert _safe_serialize(None) is None

    def test_dict_serialized_to_json(self) -> None:
        result = _safe_serialize({"a": 1, "b": "x"})
        assert json.loads(result) == {"a": 1, "b": "x"}

    def test_dict_with_datetime_uses_default_str(self) -> None:
        """datetime 等不可序列化对象用 default=str。"""
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        result = _safe_serialize({"at": dt})
        assert "2026" in result

    def test_non_serializable_falls_back_to_str(self) -> None:
        class Weird:
            def __repr__(self):
                return "<weird>"

        # json.dumps 对未知类型会先尝试 default=str → 应能序列化
        result = _safe_serialize({"obj": Weird()})  # type: ignore[dict-item]
        assert "<weird>" in result


class TestWriteAudit:
    """write_audit 审计日志写入。"""

    @pytest.mark.asyncio
    async def test_write_audit_calls_db_add_and_flush(self) -> None:
        db = AsyncMock()
        await write_audit(
            db=db,
            rule_id="rule-001",
            rule_code="R001",
            operation_type="CREATE",
            operator="admin",
            after_value={"ruleName": "测试"},
        )
        db.add.assert_called_once()
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_audit_with_before_value(self) -> None:
        db = AsyncMock()
        await write_audit(
            db=db,
            rule_id="rule-001",
            rule_code="R001",
            operation_type="UPDATE",
            operator="admin",
            before_value={"ruleName": "旧名"},
            after_value={"ruleName": "新名"},
        )
        db.add.assert_called_once()
        # 验证传入的 log 对象 before/after 已序列化
        log_obj = db.add.call_args[0][0]
        assert log_obj.before_value is not None
        assert log_obj.after_value is not None
        assert json.loads(log_obj.after_value)["ruleName"] == "新名"

    @pytest.mark.asyncio
    async def test_write_audit_none_rule_id_for_delete(self) -> None:
        """DELETE 操作 rule_id 可为 None。"""
        db = AsyncMock()
        await write_audit(
            db=db,
            rule_id=None,
            rule_code="R001",
            operation_type="DELETE",
            operator="admin",
            before_value={"ruleName": "已删"},
        )
        db.add.assert_called_once()
        log_obj = db.add.call_args[0][0]
        assert log_obj.rule_id is None

    @pytest.mark.asyncio
    async def test_write_audit_none_before_value(self) -> None:
        """before_value=None 时 log.before_value 为 None。"""
        db = AsyncMock()
        await write_audit(
            db=db,
            rule_id="rule-001",
            rule_code="R001",
            operation_type="CREATE",
            operator="admin",
        )
        log_obj = db.add.call_args[0][0]
        assert log_obj.before_value is None
        assert log_obj.after_value is None


class TestListAuditLogs:
    """list_audit_logs 审计日志查询。"""

    @pytest.mark.asyncio
    async def test_list_returns_logs(self) -> None:
        db = AsyncMock()
        log1 = MagicMock(rule_code="R001", operation_type="CREATE")
        log2 = MagicMock(rule_code="R001", operation_type="UPDATE")
        result_mock = MagicMock()
        result_mock.scalars.return_value = [log1, log2]
        db.execute = AsyncMock(return_value=result_mock)

        logs = await list_audit_logs(db, rule_id="rule-001")
        assert len(logs) == 2

    @pytest.mark.asyncio
    async def test_list_with_filters(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        await list_audit_logs(
            db,
            rule_id="rule-001",
            operator="admin",
            operation_type="CREATE",
            limit=10,
            offset=0,
        )
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_no_filters(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        await list_audit_logs(db)
        db.execute.assert_awaited_once()


# ===========================================================================
# Dispatcher 测试
# ===========================================================================


def _make_evaluation_result(
    triggered: bool = True,
    severity: str = "WARN",
    dedup_key: str = "loop-1+rule-001",
    triggered_value: float | None = 150.0,
    condition_snapshot: dict | None = None,
    confidence_level: str | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        triggered=triggered,
        triggered_value=triggered_value,
        condition_snapshot=condition_snapshot or {"metric": "PV", "actualValue": 150.0},
        confidence_level=confidence_level,
        severity=severity,
        dedup_key=dedup_key,
    )


def _make_rule_dict(
    rule_id: str = "rule-001",
    rule_code: str = "R001",
    actions: list | None = None,
    cooldown: int = 1800,
    severity: str = "WARN",
) -> dict:
    return {
        "id": rule_id,
        "ruleCode": rule_code,
        "ruleName": f"规则-{rule_code}",
        "version": 1,
        "dsl": {
            "severity": severity,
            "cooldownSeconds": cooldown,
            "actions": actions if actions is not None else [{"type": "CREATE_EVENT"}],
        },
    }


class TestDispatchCreateEvent:
    """dispatch CREATE_EVENT 动作。"""

    @pytest.mark.asyncio
    async def test_create_event_success(self, fake_redis) -> None:
        db = AsyncMock()
        rule = _make_rule_dict()
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            m_supp.increment_badge = AsyncMock()
            outcomes = await dispatch(db, rule, "loop-1", result)

        assert "CREATE_EVENT" in outcomes
        assert outcomes["CREATE_EVENT"] is not None
        db.add.assert_called_once()
        db.flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_create_event_severity_upgraded(self, fake_redis) -> None:
        """trigger_count >= 3 时严重度升级。"""
        db = AsyncMock()
        rule = _make_rule_dict(severity="WARN")
        result = _make_evaluation_result(severity="WARN")

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=3)  # 触发升级
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            await dispatch(db, rule, "loop-1", result)

        # 验证创建的 event 严重度被升级为 ERROR
        event_obj = db.add.call_args[0][0]
        assert event_obj.severity == "ERROR"

    @pytest.mark.asyncio
    async def test_cooldown_set_after_dispatch(self, fake_redis) -> None:
        """dispatch 完成后设置冷却期。"""
        db = AsyncMock()
        rule = _make_rule_dict(cooldown=600)
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            await dispatch(db, rule, "loop-1", result)

        m_supp.set_cooldown.assert_awaited_once_with("loop-1+rule-001", 600)
        m_supp.clear_duration.assert_awaited_once_with("loop-1+rule-001")


class TestDispatchCreateTracker:
    """dispatch CREATE_TRACKER 动作。"""

    @pytest.mark.asyncio
    async def test_create_tracker_no_existing(self, fake_redis) -> None:
        db = AsyncMock()
        # 查询无开放工单
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=existing_result)

        rule = _make_rule_dict(
            actions=[
                {"type": "CREATE_EVENT"},
                {"type": "CREATE_TRACKER"},
            ]
        )
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            outcomes = await dispatch(db, rule, "loop-1", result)

        assert "CREATE_TRACKER" in outcomes
        assert outcomes["CREATE_TRACKER"] is not None
        # db.add 被调用 2 次（event + tracker）
        assert db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_create_tracker_existing_returns_existing_id(self, fake_redis) -> None:
        """已有开放工单时返回已存在的 ID，不新建。"""
        db = AsyncMock()
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = "existing-tracker-id"
        db.execute = AsyncMock(return_value=existing_result)

        rule = _make_rule_dict(actions=[{"type": "CREATE_EVENT"}, {"type": "CREATE_TRACKER"}])
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            outcomes = await dispatch(db, rule, "loop-1", result)

        assert outcomes["CREATE_TRACKER"] == "existing-tracker-id"
        # 仍执行 event link 更新（db.execute 被调用）
        assert db.execute.await_count >= 2  # 查询 + link 更新


class TestDispatchNotify:
    """dispatch NOTIFY 动作。"""

    @pytest.mark.asyncio
    async def test_notify_publishes_to_redis(self, fake_redis) -> None:
        db = AsyncMock()
        rule = _make_rule_dict(actions=[{"type": "CREATE_EVENT"}, {"type": "NOTIFY"}])
        result = _make_evaluation_result()

        # mock SysUser 查询返回用户列表
        user_result = MagicMock()
        user_result.__iter__ = MagicMock(return_value=iter([("u1",), ("u2",)]))
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=user_result)

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
            patch("app.core.db.AsyncSessionLocal") as m_session,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            m_supp.increment_badge = AsyncMock()
            m_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            m_session.return_value.__aexit__ = AsyncMock(return_value=None)

            outcomes = await dispatch(db, rule, "loop-1", result)

        assert outcomes["NOTIFY"] == "published"
        m_supp.increment_badge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_redis_exception_does_not_crash(self, fake_redis) -> None:
        """Redis publish 异常不影响其他动作。"""
        db = AsyncMock()
        rule = _make_rule_dict(actions=[{"type": "NOTIFY"}])
        result = _make_evaluation_result()

        bad_redis = AsyncMock()
        bad_redis.publish = AsyncMock(side_effect=RuntimeError("redis down"))

        with (
            patch.object(dispatcher, "redis_client", bad_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
            patch("app.core.db.AsyncSessionLocal") as m_session,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            m_supp.increment_badge = AsyncMock()
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=MagicMock(__iter__=lambda self: iter([])))
            m_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            m_session.return_value.__aexit__ = AsyncMock(return_value=None)

            outcomes = await dispatch(db, rule, "loop-1", result)

        # NOTIFY 仍标记为 published（异常被捕获，不阻塞）
        assert outcomes.get("NOTIFY") in ("published", "failed", None)


class TestDispatchErrorHandling:
    """dispatch 异常处理。"""

    @pytest.mark.asyncio
    async def test_unknown_action_type_silently_skipped(self, fake_redis) -> None:
        """未知动作类型不崩溃，被静默跳过（不匹配任何分支，不进入 outcomes）。"""
        db = AsyncMock()
        rule = _make_rule_dict(actions=[{"type": "CREATE_EVENT"}, {"type": "UNKNOWN_ACTION"}])
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            outcomes = await dispatch(db, rule, "loop-1", result)

        # 未知动作不进入 outcomes，CREATE_EVENT 仍成功
        assert "UNKNOWN_ACTION" not in outcomes
        assert outcomes.get("CREATE_EVENT") is not None

    @pytest.mark.asyncio
    async def test_empty_actions_still_sets_cooldown(self, fake_redis) -> None:
        """空 actions 列表仍设置冷却期。"""
        db = AsyncMock()
        rule = _make_rule_dict(actions=[])
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            outcomes = await dispatch(db, rule, "loop-1", result)

        m_supp.set_cooldown.assert_awaited_once()
        assert outcomes == {}

    @pytest.mark.asyncio
    async def test_zero_cooldown_does_not_set(self, fake_redis) -> None:
        """cooldown=0 时不设置冷却期。"""
        db = AsyncMock()
        rule = _make_rule_dict(cooldown=0)
        result = _make_evaluation_result()

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            await dispatch(db, rule, "loop-1", result)

        m_supp.set_cooldown.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_dedup_key_skips_cooldown(self, fake_redis) -> None:
        """dedup_key=None 时跳过冷却期设置。"""
        db = AsyncMock()
        rule = _make_rule_dict()
        result = _make_evaluation_result(dedup_key=None)

        with (
            patch.object(dispatcher, "redis_client", fake_redis),
            patch.object(dispatcher, "_suppressor") as m_supp,
        ):
            m_supp.get_trigger_count = AsyncMock(return_value=1)
            m_supp.set_cooldown = AsyncMock()
            m_supp.clear_duration = AsyncMock()
            await dispatch(db, rule, "loop-1", result)

        m_supp.set_cooldown.assert_not_awaited()
