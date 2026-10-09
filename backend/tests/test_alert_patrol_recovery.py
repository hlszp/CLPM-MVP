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

        async def _evaluate(
            db,  # noqa: ARG001
            rule,
            loop_id,  # noqa: ARG001
            current_values=None,  # noqa: ARG001
            confidence_level=None,  # noqa: ARG001
            read_only=False,
        ):
            if eval_calls is not None:
                eval_calls.append({"rule_id": rule.get("id"), "read_only": read_only})
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


@pytest.mark.asyncio
async def test_auto_recover_reevaluates_in_read_only_mode(patch_recovery_deps):
    """恢复重估必须以 read_only 调用（跳过周期节流），否则节流期内的
    "本周期已检查过"会被误计为未触发 miss（2026-10-09 生产事故根因）。"""
    event = _event("ev-4", "ACTIVE", "rule-1")
    eval_calls: list = []
    patch_recovery_deps([{"id": "rule-1"}], True, _FakeRedis({}), eval_calls=eval_calls)

    await alert_patrol._auto_recover_events(_FakeDb([event]), "loop-1", None)

    assert eval_calls == [{"rule_id": "rule-1", "read_only": True}]


# ---------------------------------------------------------------------------
# 只读探测模式回归：_evaluate_metric_threshold_rule(read_only=True)
# ---------------------------------------------------------------------------


class _ThrottleRedis:
    """set(nx) 可编程模拟节流键；记录全部写调用。"""

    def __init__(self, throttle_key_exists: bool):
        self._throttle_key_exists = throttle_key_exists
        self.set_calls: list[dict] = []
        self.incr_calls: list[str] = []

    async def set(self, key, value, ex=None, nx=False):  # noqa: A002
        self.set_calls.append({"key": key, "ex": ex, "nx": nx})
        if nx and self._throttle_key_exists and key.startswith("alert:metriccheck:"):
            return None  # 键已存在（redis-py SETNX 语义：返回 None）
        return True

    async def incr(self, key: str) -> int:
        self.incr_calls.append(key)
        return 1

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        return None

    async def delete(self, key: str) -> None:  # noqa: ARG002
        return None


class _ScalarDb:
    """scalar_one_or_none() 返回单行（LoopConfidenceLatest 替身）。"""

    def __init__(self, row):
        self._row = row

    async def execute(self, *_args, **_kwargs):
        outer = self

        class _R:
            def scalar_one_or_none(self):
                return outer._row

        return _R()


def _kpi_rule(duration_count: int = 1) -> tuple[dict, dict]:
    rule = {
        "id": "rule-9",
        "dsl": {"ruleType": "METRIC_THRESHOLD", "dedupKey": "${loop_id}+${rule_id}"},
    }
    condition = {
        "metricSource": "KPI",
        "metricCode": "score",
        "operator": "<",
        "value": 60,
        "checkIntervalMinutes": 60,
        "durationCount": duration_count,
    }
    return rule, condition


def _kpi_row(score: float = 30.0):
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(tzinfo=None)
    return SimpleNamespace(score=score, valid_rate=None, metrics={}, eval_time=now, updated_at=now)


@pytest.mark.asyncio
async def test_read_only_skips_throttle_and_still_evaluates(monkeypatch):
    """节流键存在时：正常路径返回未触发（interval_not_reached），
    read_only 路径仍真正求值（指标 30<60 → 触发）。"""
    from app.services.alert_rule_engine import evaluator

    rule, condition = _kpi_rule()

    async def _kpi(db, loop_id, metric_code):  # noqa: ARG001
        return 30.0, None

    monkeypatch.setattr(evaluator, "_get_latest_kpi_metric", _kpi)

    redis = _ThrottleRedis(throttle_key_exists=True)
    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "redis_client", redis, raising=True)

    normal = await evaluator._evaluate_metric_threshold_rule(
        _ScalarDb(_kpi_row()), rule, condition, "loop-1", "WARN", None
    )
    readonly = await evaluator._evaluate_metric_threshold_rule(
        _ScalarDb(_kpi_row()), rule, condition, "loop-1", "WARN", None, read_only=True
    )

    assert normal.triggered is False
    assert normal.condition_snapshot.get("reason") == "interval_not_reached"
    assert readonly.triggered is True  # 节流期内重估仍得到真实结论


@pytest.mark.asyncio
async def test_read_only_writes_no_throttle_or_mcount_keys(monkeypatch):
    """read_only 求值不设置节流键、不推进连续超限计数（纯探测无副作用）。"""
    from app.services.alert_rule_engine import evaluator

    rule, condition = _kpi_rule(duration_count=3)

    async def _kpi(db, loop_id, metric_code):  # noqa: ARG001
        return 30.0, None

    monkeypatch.setattr(evaluator, "_get_latest_kpi_metric", _kpi)

    redis = _ThrottleRedis(throttle_key_exists=False)
    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "redis_client", redis, raising=True)

    result = await evaluator._evaluate_metric_threshold_rule(
        _ScalarDb(_kpi_row()), rule, condition, "loop-1", "WARN", None, read_only=True
    )

    assert result.triggered is True
    assert redis.set_calls == []  # 未设置节流键
    assert redis.incr_calls == []  # 未推进 mcount
