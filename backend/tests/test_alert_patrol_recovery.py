"""预警工况恢复自动解除回归测试（0929 陈旧报警治理）。

巡检时对回路未决事件（ACTIVE/ACKNOWLEDGED）重估其规则：连续 3 次巡检
未再触发 → RESOLVED（resolved_by=system:auto-recovery）。此前 ACTIVE
事件无自动恢复链路，工况恢复后滞留关注队列形成陈旧报警（ISA-18.2）。
"""

from types import SimpleNamespace

import pytest

from app.tasks import alert_patrol


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)


class _FakeRedis:
    """incr 计数可编程；delete 记录调用。"""

    def __init__(self, incr_returns: dict[str, int] | None = None):
        self._incr_returns = incr_returns or {}
        self.deleted: list[str] = []

    async def incr(self, key: str) -> int:
        return self._incr_returns.get(key, 1)

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        return None

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


def _event(event_id: str, status: str, rule_id: str | None):
    return SimpleNamespace(
        id=event_id,
        status=status,
        rule_id=rule_id,
        resolution_note=None,
        resolved_by=None,
        resolved_at=None,
        acknowledged_at=None,
    )


@pytest.fixture
def patch_recovery_deps(monkeypatch):
    """替换恢复路径的三依赖：规则缓存/求值器/Redis。"""

    def _patch(
        rules: list[dict],
        triggered: bool,
        redis: _FakeRedis,
        eval_calls: list | None = None,
    ):
        async def _rules(db, loop_id):  # noqa: ARG001
            return rules

        async def _evaluate(db, rule, loop_id, current_values=None, confidence_level=None):  # noqa: ARG001
            if eval_calls is not None:
                eval_calls.append(rule.get("id"))
            return SimpleNamespace(triggered=triggered)

        import app.core.redis as redis_mod

        monkeypatch.setattr(redis_mod, "redis_client", redis, raising=True)
        monkeypatch.setattr("app.services.alert_rule_engine.cache.get_rules_for_loop", _rules)
        monkeypatch.setattr("app.services.alert_rule_engine.evaluator.evaluate_rule", _evaluate)

    return _patch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("misses", "expect_resolved"),
    [(3, True), (1, False)],
)
async def test_auto_recover_threshold(patch_recovery_deps, misses, expect_resolved):
    """未触发达连续 3 次阈值 → RESOLVED；未达 → 保持未决。"""
    event = _event("ev-1", "ACTIVE", "rule-1")
    redis = _FakeRedis({"alert:recovery_miss:ev-1": misses})
    patch_recovery_deps([{"id": "rule-1", "ruleCode": "R1"}], False, redis)

    recovered = await alert_patrol._auto_recover_events(_FakeDb([event]), "loop-1", None)

    assert recovered == (1 if expect_resolved else 0)
    if expect_resolved:
        assert event.status == "RESOLVED"
        assert event.resolved_by == "system:auto-recovery"
        assert "工况恢复自动解除" in event.resolution_note
        assert "alert:recovery_miss:ev-1" in redis.deleted
    else:
        assert event.status == "ACTIVE"
        assert event.resolved_by is None


@pytest.mark.asyncio
async def test_auto_recover_still_triggered_keeps_event(patch_recovery_deps):
    """规则仍触发 → 事件保持未决且清除未触发计数（防抖重置）。"""
    event = _event("ev-1", "ACTIVE", "rule-1")
    redis = _FakeRedis({"alert:recovery_miss:ev-1": 3})
    patch_recovery_deps([{"id": "rule-1"}], True, redis)

    recovered = await alert_patrol._auto_recover_events(_FakeDb([event]), "loop-1", None)

    assert recovered == 0
    assert event.status == "ACTIVE"
    assert "alert:recovery_miss:ev-1" in redis.deleted


@pytest.mark.asyncio
async def test_auto_recover_skips_event_without_rule(patch_recovery_deps):
    """rule_id 空（规则已删 SET NULL）的事件不自动恢复、不触发求值。"""
    event = _event("ev-2", "ACTIVE", None)
    eval_calls: list = []
    patch_recovery_deps([{"id": "rule-1"}], False, _FakeRedis({}), eval_calls=eval_calls)

    recovered = await alert_patrol._auto_recover_events(_FakeDb([event]), "loop-1", None)

    assert recovered == 0
    assert event.status == "ACTIVE"
    assert eval_calls == []  # 无匹配规则的事件跳过求值


@pytest.mark.asyncio
async def test_auto_recover_acknowledged_also_recovers(patch_recovery_deps):
    """ACKNOWLEDGED（人工已确认）事件工况恢复后同样解除，避免双轨滞留。"""
    event = _event("ev-3", "ACKNOWLEDGED", "rule-1")
    redis = _FakeRedis({"alert:recovery_miss:ev-3": 3})
    patch_recovery_deps([{"id": "rule-1"}], False, redis)

    recovered = await alert_patrol._auto_recover_events(_FakeDb([event]), "loop-1", None)

    assert recovered == 1
    assert event.status == "RESOLVED"
    assert event.acknowledged_at is None  # 确认信息不篡改，仅追加解除字段
