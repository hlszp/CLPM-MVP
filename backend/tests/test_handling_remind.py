"""处置域主动提醒单测（2026-10-05 用户裁决②）。

覆盖：
1. beat 注册断言：handling-remind 每日 08:30；诊断 daily 00:30（裁决③锁定，
   避免与回路评估自动任务撞车）；模块条件化登记（handling-remind 随模块摘除）
2. 配置读取：sys_config 缺失/坏 JSON 回退默认，好 JSON 全量覆盖
3. 聚合计数：SQL 参数随阈值注入（pendingDays/verifyHours）
4. 主流程：模块禁用跳过 / 开关关闭不推送 / 全零静默 / 命中推送
5. 推送 payload 与 dispatcher._notify 同构（severity=INFO，snapshot 带摘要）

全部 mock session，不依赖真实 DB。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.tasks import handling_remind
from app.tasks.celery_app import celery_app


class _FakeSession:
    """async with AsyncSessionLocal() as db 的替身。"""

    def __init__(self, db) -> None:
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return None


def _patch_session(monkeypatch, db) -> None:
    monkeypatch.setattr("app.core.db.AsyncSessionLocal", lambda: _FakeSession(db))


# ---------------------------------------------------------------------------
# beat 注册断言
# ---------------------------------------------------------------------------


class TestBeatSchedule:
    def test_handling_remind_daily_0830(self) -> None:
        entry = celery_app.conf.beat_schedule["handling-remind"]
        assert entry["task"] == "app.tasks.handling_remind.handling_remind"
        crontab = entry["schedule"]
        assert crontab.hour == {8}
        assert crontab.minute == {30}

    def test_diagnosis_daily_0030(self) -> None:
        """2026-10-05 用户裁决③：daily 01:10→00:30，避开回路评估自动任务。"""
        entry = celery_app.conf.beat_schedule["diagnosis-scheduled-daily"]
        crontab = entry["schedule"]
        assert crontab.hour == {0}
        assert crontab.minute == {30}

    def test_handling_remind_registered_in_task_registry(self) -> None:
        assert "app.tasks.handling_remind.handling_remind" in celery_app.tasks

    def test_module_beat_entry_registered(self) -> None:
        """handling-remind 登记在 _MODULE_BEAT_ENTRIES（模块禁用时随 beat 摘除）。"""
        from app.tasks.beat_registry import _MODULE_BEAT_ENTRIES

        assert "handling-remind" in _MODULE_BEAT_ENTRIES.get("handling", [])


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_key_falls_back_to_defaults(self, monkeypatch) -> None:
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        _patch_session(monkeypatch, db)
        cfg = asyncio.run(handling_remind._load_config())
        assert cfg == {"enabled": True, "pendingDays": 3, "verifyHours": 24}

    def test_corrupt_json_falls_back(self, monkeypatch) -> None:
        db = AsyncMock()
        db.scalar = AsyncMock(return_value="{not-json")
        _patch_session(monkeypatch, db)
        cfg = asyncio.run(handling_remind._load_config())
        assert cfg["enabled"] is True

    def test_stored_json_overrides(self, monkeypatch) -> None:
        db = AsyncMock()
        db.scalar = AsyncMock(
            return_value=json.dumps({"enabled": False, "pendingDays": 7, "verifyHours": 48})
        )
        _patch_session(monkeypatch, db)
        cfg = asyncio.run(handling_remind._load_config())
        assert cfg == {"enabled": False, "pendingDays": 7, "verifyHours": 48}

    def test_non_dict_value_falls_back(self, monkeypatch) -> None:
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=json.dumps([1, 2]))
        _patch_session(monkeypatch, db)
        cfg = asyncio.run(handling_remind._load_config())
        assert cfg == {"enabled": True, "pendingDays": 3, "verifyHours": 24}


# ---------------------------------------------------------------------------
# 聚合计数
# ---------------------------------------------------------------------------


class TestCollectCounts:
    def _db_with_row(self) -> AsyncMock:
        row = MagicMock(
            pending_overdue=5,
            oldest_pending_at=datetime(2026, 10, 1, 0, 0, 0),
            schedule_overdue=2,
            verifying_stuck=1,
        )
        result = MagicMock()
        result.one.return_value = row
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        return db

    def test_counts_and_oldest_days(self, monkeypatch) -> None:
        db = self._db_with_row()
        _patch_session(monkeypatch, db)
        cfg = {"enabled": True, "pendingDays": 3, "verifyHours": 24}
        now_before = datetime.now(UTC).replace(tzinfo=None)
        counts = asyncio.run(handling_remind._collect_counts(cfg))
        assert counts["pendingOverdue"] == 5
        assert counts["scheduleOverdue"] == 2
        assert counts["verifyingStuck"] == 1
        # 最老天数的量纲：now − oldest（天）
        expected_days = (now_before - datetime(2026, 10, 1, 0, 0, 0)).days
        assert counts["oldestPendingDays"] == expected_days

    def test_sql_params_carry_thresholds(self, monkeypatch) -> None:
        db = self._db_with_row()
        _patch_session(monkeypatch, db)
        cfg = {"enabled": True, "pendingDays": 7, "verifyHours": 48}
        asyncio.run(handling_remind._collect_counts(cfg))
        params = db.execute.call_args[0][1]
        now_after = datetime.now(UTC).replace(tzinfo=None)
        # 微秒级时钟差容忍（任务内部与断言处各取一次 now）
        assert (params["pending_cutoff"] - (now_after - timedelta(days=7))).total_seconds() < 5
        assert (params["verify_cutoff"] - (now_after - timedelta(hours=48))).total_seconds() < 5


# ---------------------------------------------------------------------------
# 主流程与推送
# ---------------------------------------------------------------------------


_CFG = {"enabled": True, "pendingDays": 3, "verifyHours": 24}


class TestHandlingRemindFlow:
    def test_module_disabled_skips(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.modules.is_module_enabled", lambda m: False)
        result = asyncio.run(handling_remind._handling_remind_async())
        assert result["status"] == "skipped"

    def test_switch_off_no_push(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.modules.is_module_enabled", lambda m: True)
        pushed = []

        async def fake_load() -> dict:
            return {"enabled": False, "pendingDays": 3, "verifyHours": 24}

        monkeypatch.setattr(handling_remind, "_load_config", fake_load)
        monkeypatch.setattr(
            handling_remind, "_push_notify", AsyncMock(side_effect=lambda *a: pushed.append(1))
        )
        result = asyncio.run(handling_remind._handling_remind_async())
        assert result["status"] == "disabled"
        assert pushed == []

    def test_all_zero_silent(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.modules.is_module_enabled", lambda m: True)

        async def fake_load() -> dict:
            return dict(_CFG)

        async def fake_counts(cfg) -> dict:
            return {
                "pendingOverdue": 0,
                "oldestPendingDays": None,
                "scheduleOverdue": 0,
                "verifyingStuck": 0,
            }

        push_mock = AsyncMock()
        monkeypatch.setattr(handling_remind, "_load_config", fake_load)
        monkeypatch.setattr(handling_remind, "_collect_counts", fake_counts)
        monkeypatch.setattr(handling_remind, "_push_notify", push_mock)
        result = asyncio.run(handling_remind._handling_remind_async())
        assert result == {
            "status": "ok",
            "pushed": False,
            "pendingOverdue": 0,
            "oldestPendingDays": None,
            "scheduleOverdue": 0,
            "verifyingStuck": 0,
        }
        push_mock.assert_not_awaited()

    def test_hit_threshold_pushes(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.modules.is_module_enabled", lambda m: True)

        async def fake_load() -> dict:
            return dict(_CFG)

        async def fake_counts(cfg) -> dict:
            return {
                "pendingOverdue": 4,
                "oldestPendingDays": 8,
                "scheduleOverdue": 2,
                "verifyingStuck": 1,
            }

        push_mock = AsyncMock()
        monkeypatch.setattr(handling_remind, "_load_config", fake_load)
        monkeypatch.setattr(handling_remind, "_collect_counts", fake_counts)
        monkeypatch.setattr(handling_remind, "_push_notify", push_mock)
        result = asyncio.run(handling_remind._handling_remind_async())
        assert result["pushed"] is True
        push_mock.assert_awaited_once()
        cfg_arg, counts_arg = push_mock.call_args[0]
        assert cfg_arg == _CFG
        assert counts_arg["pendingOverdue"] == 4


class TestPushNotifyPayload:
    def test_payload_shape_and_badge(self, monkeypatch) -> None:
        """payload 与 dispatcher._notify 同构；徽章按处置操作角色计数。"""
        publish_mock = AsyncMock()
        badge_mock = AsyncMock()

        class _FakeRedis:
            async def publish(self, *a, **kw):
                await publish_mock(*a, **kw)

        class _FakeSuppressor:
            def increment_badge(self, user_ids):
                return badge_mock(user_ids)

        # _push_notify 内部 import 的名字在调用时解析，patch 源模块即可
        monkeypatch.setattr("app.core.redis.redis_client", _FakeRedis(), raising=False)
        monkeypatch.setattr("app.services.alert_rule_engine.suppressor.Suppressor", _FakeSuppressor)
        db = AsyncMock()
        res = MagicMock()
        res.all.return_value = [["u1"], ["u2"]]
        db.execute = AsyncMock(return_value=res)
        _patch_session(monkeypatch, db)

        counts = {
            "pendingOverdue": 4,
            "oldestPendingDays": 8,
            "scheduleOverdue": 2,
            "verifyingStuck": 1,
        }
        asyncio.run(handling_remind._push_notify(_CFG, counts))

        publish_mock.assert_awaited_once()
        channel, raw = publish_mock.call_args[0]
        assert channel == "alert:notify"
        payload = json.loads(raw)
        assert payload["type"] == "alert"
        assert payload["ruleCode"] == "HANDLING_REMIND"
        assert payload["severity"] == "INFO"
        assert payload["snapshot"]["pendingOverdue"] == 4
        assert "最老 8 天" in payload["snapshot"]["message"]
        badge_mock.assert_called_once_with(["u1", "u2"])
