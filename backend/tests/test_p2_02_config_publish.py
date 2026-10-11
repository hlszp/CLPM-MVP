"""P2-02 分层配置发布与跨进程一致：单元测试（C10 行为单测，mock DB）.

覆盖（方案 01 §3.2 / 03 §5 P2-02 / 04 C10 的进程内行为面）：
- 统一作用域链解析 DEFAULT < TEMPLATE < NODE < LOOP < TASK（逐层遮盖、
  effective 逐参数来源、shadowed 遮盖关系）
- 快照内五层解析 resolve_from_snapshot（任务中不切参的解析语义）
- 发布 expectedRevision 乐观锁（失配 409 携带最新版本；commit 唯一约束兜底）
- 发布/重置/回退的发布账本 + 审计（before/after/原因/操作者/影响范围/回退版本）
- 跨层组合约束在"该层以下合并视图"求值（P2-01 遗留：threshold 与 Layer2 组合颠倒拦截）
- 重置解释（重置低层后高层仍生效可解释）
- pin_config_snapshot 任务边界固定（revision 失配即重载，Redis 不参与）
- 广播消息按版本去重（旧版本/坏消息不触发重载）
- 端点权限（发布/重置/回退仅 ADMIN——DEC-03；查询只读三角色）
- 兼容通道 LEGACY_SYNC（expectedRevision 409 / 发布登记入账）

真实 PG 并发 409 与真实多进程一致（C11）见 tests/integration/test_p2_02_c10_c11_real_pg.py
（integration 标记，默认门禁不跑，实验时显式选择）。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services import algorithm_config as ac
from app.services import config_publish as cp
from tests.conftest import TEST_USERS, mock_current_user


def asyncio_run(coro):
    """pytest-asyncio auto 模式下在同步测试中执行协程的便捷包装."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _none_result():
    return _scalar_result(None)


def _count_result(count: int = 5):
    result = MagicMock()
    result.scalar_one.return_value = count
    return result


def _scalars_all_result(items: list):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _all_result(items: list):
    result = MagicMock()
    result.all.return_value = items
    return result


def _make_override_row(
    layer: str,
    scope_id: str,
    metric_code: str,
    params: dict,
    *,
    published_revision: int = 1,
    is_enabled: bool = True,
    id_: str = "ov-1",
):
    row = MagicMock()
    row.id = id_
    row.layer = layer
    row.scope_id = scope_id
    row.metric_code = metric_code
    row.params = dict(params)
    row.published_revision = published_revision
    row.is_enabled = is_enabled
    row.updated_by = "admin"
    row.updated_at = None
    row.version = 1
    return row


def _make_ap_row(metric_code: str, ct: str, params: dict):
    row = MagicMock()
    row.metric_code = metric_code
    row.control_type = ct
    row.params = dict(params)
    return row


def _make_publication_row(
    revision: int,
    operation: str = "PUBLISH",
    layer: str | None = "LOOP",
    scope: dict | None = None,
    before_value: str | None = None,
    after_value: str | None = None,
    affected_loops: int | None = 1,
    rollback_revision: int | None = None,
    reason: str = "r",
    operator: str = "admin",
):
    row = MagicMock()
    row.revision = revision
    row.operation = operation
    row.layer = layer
    row.scope = scope or {}
    row.before_value = before_value
    row.after_value = after_value
    row.affected_loops = affected_loops
    row.rollback_revision = rollback_revision
    row.reason = reason
    row.operator = operator
    row.created_at = None
    return row


@pytest.fixture
def reset_publish_state():
    """每个测试前后复位 config_publish 进程内状态与算法参数缓存."""
    saved_revision = cp._local_synced_revision
    saved_cache = dict(ac._merged_cache)
    cp._local_synced_revision = -1
    yield
    cp._local_synced_revision = saved_revision
    ac._merged_cache = saved_cache


# ---------------------------------------------------------------------------
# C10：统一作用域链解析（DEFAULT < TEMPLATE < NODE < LOOP < TASK）
# ---------------------------------------------------------------------------


class TestResolveEffectiveParams:
    """resolve_effective_params：逐层遮盖 / effective 来源 / shadowed 关系."""

    @staticmethod
    def _db_with(side_effect):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=side_effect)
        db.add = MagicMock()
        return db

    def test_default_only(self) -> None:
        """无任何覆盖：全部键 source=DEFAULT、sourceRevision=builtin."""
        db = self._db_with([_scalar_result(3), _none_result(), _none_result()])
        result = asyncio_run(cp.resolve_effective_params(db, "oscillation_rate", "STABLE"))
        assert result["revision"] == 3
        assert result["params"]["similarity_threshold"] == 0.4  # 算法默认
        by_key = {e["key"]: e for e in result["effective"]}
        assert by_key["similarity_threshold"]["source"] == "DEFAULT"
        assert by_key["similarity_threshold"]["sourceId"] is None
        assert by_key["similarity_threshold"]["sourceRevision"] == "builtin"
        assert result["shadowed"] == []

    def test_default_layer_internal_order(self) -> None:
        """DEFAULT 层三段内部次序：算法默认 < algorithm_parameter < metric.threshold."""
        db = self._db_with(
            [
                _scalar_result(0),
                _scalar_result(_make_ap_row("oscillation_rate", "STABLE", {"min_ratio": 0.08})),
                _scalar_result({"similarity_threshold": 0.6}),
            ]
        )
        result = asyncio_run(cp.resolve_effective_params(db, "oscillation_rate", "STABLE"))
        by_key = {e["key"]: e for e in result["effective"]}
        # algorithm_parameter 覆盖 min_ratio（遮盖算法默认）
        assert result["params"]["min_ratio"] == 0.08
        assert by_key["min_ratio"]["sourceId"] == "algorithm_parameter:STABLE"
        assert by_key["min_ratio"]["sourceRevision"] == "legacy"
        # metric_config.threshold 覆盖 similarity_threshold（遮盖算法默认）
        assert result["params"]["similarity_threshold"] == 0.6
        assert by_key["similarity_threshold"]["sourceId"] == "metric_config.threshold"
        # 两处遮盖都进 shadowed
        shadowed_keys = {(s["key"], s["shadowedBy"]) for s in result["shadowed"]}
        assert ("min_ratio", "DEFAULT") in shadowed_keys
        assert ("similarity_threshold", "DEFAULT") in shadowed_keys

    def test_layer_chain_template_node_loop_task(self) -> None:
        """TEMPLATE < NODE < LOOP < TASK 全链逐层遮盖（C10 核心）."""
        template_row = _make_override_row(
            "TEMPLATE",
            "FAST_RESP",
            "oscillation_rate",
            {"similarity_threshold": 0.5},
            published_revision=1,
        )
        node_row = _make_override_row(
            "NODE",
            "unit-1",
            "oscillation_rate",
            {"similarity_threshold": 0.65, "min_ratio": 0.06},
            published_revision=2,
        )
        loop_row = _make_override_row(
            "LOOP",
            "loop-1",
            "oscillation_rate",
            {"similarity_threshold": 0.8},
            published_revision=3,
        )
        db = self._db_with(
            [
                _scalar_result(3),  # revision
                _none_result(),  # algorithm_parameter
                _none_result(),  # metric threshold
                _scalar_result(template_row),  # TEMPLATE
                _scalar_result(node_row),  # NODE
                _scalar_result(loop_row),  # LOOP
            ]
        )
        result = asyncio_run(
            cp.resolve_effective_params(
                db,
                "oscillation_rate",
                "STABLE",
                template_key="FAST_RESP",
                node_id="unit-1",
                loop_id="loop-1",
                task_overrides={"similarity_threshold": 0.95},
            )
        )
        # 最高层胜出：TASK 0.95；未达高层的键回落 NODE 层 min_ratio=0.06
        assert result["params"]["similarity_threshold"] == 0.95
        assert result["params"]["min_ratio"] == 0.06
        by_key = {e["key"]: e for e in result["effective"]}
        assert by_key["similarity_threshold"]["source"] == "TASK"
        assert by_key["min_ratio"]["source"] == "NODE"
        assert by_key["min_ratio"]["sourceId"] == "unit-1"
        assert by_key["min_ratio"]["sourceRevision"] == "2"
        # 遮盖链完整：TEMPLATE 遮 DEFAULT，NODE 遮 TEMPLATE，LOOP 遮 NODE，TASK 遮 LOOP
        chain = [
            (s["key"], s["source"], s["shadowedBy"])
            for s in result["shadowed"]
            if s["key"] == "similarity_threshold"
        ]
        assert ("similarity_threshold", "DEFAULT", "TEMPLATE") in chain
        assert ("similarity_threshold", "TEMPLATE", "NODE") in chain
        assert ("similarity_threshold", "NODE", "LOOP") in chain
        assert ("similarity_threshold", "LOOP", "TASK") in chain

    def test_missing_scope_skips_layer(self) -> None:
        """未提供作用域的 TEMPLATE/LOOP 层不参与解析（查询不发、值不进链）."""
        db = self._db_with(
            [
                _scalar_result(0),
                _none_result(),
                _none_result(),
                _none_result(),  # NODE 层查询（node_id 已给，无覆盖行）
            ]
        )
        result = asyncio_run(
            cp.resolve_effective_params(db, "oscillation_rate", "STABLE", node_id="unit-1")
        )
        # revision + DEFAULT 两查询 + NODE 一查询 = 4（TEMPLATE/LOOP 未给作用域不发查询）
        assert db.execute.await_count == 4
        assert result["params"]["similarity_threshold"] == 0.4

    def test_unknown_metric_404(self) -> None:
        db = self._db_with([])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(cp.resolve_effective_params(db, "no_such_metric", "STABLE"))
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# C10：快照内五层解析（任务中不切参）
# ---------------------------------------------------------------------------


class TestResolveFromSnapshot:
    """resolve_from_snapshot：固定快照上的五层链解析（零 DB 访问）."""

    SNAPSHOT = {
        "schemaVersion": "1",
        "configRevision": 7,
        "pinnedAt": "2026-10-12T00:00:00+00:00",
        "params": {"oscillation_rate|STABLE": {"similarity_threshold": 0.4, "min_ratio": 0.05}},
        "overrides": {
            "TEMPLATE|FAST_RESP|oscillation_rate": {"similarity_threshold": 0.5},
            "NODE|unit-1|oscillation_rate": {"similarity_threshold": 0.65},
            "LOOP|loop-1|oscillation_rate": {"similarity_threshold": 0.8},
        },
        "hostname": "h",
        "pid": 1,
    }

    def test_full_chain_from_snapshot(self) -> None:
        result = cp.resolve_from_snapshot(
            self.SNAPSHOT,
            "oscillation_rate",
            "STABLE",
            template_key="FAST_RESP",
            node_id="unit-1",
            loop_id="loop-1",
            task_overrides={"similarity_threshold": 0.95},
        )
        assert result["configRevision"] == 7
        assert result["params"]["similarity_threshold"] == 0.95  # TASK 顶层
        assert result["params"]["min_ratio"] == 0.05  # DEFAULT 兜底
        assert result["sources"]["similarity_threshold"] == "TASK"
        assert result["sources"]["min_ratio"] == "DEFAULT"

    def test_partial_scopes(self) -> None:
        result = cp.resolve_from_snapshot(
            self.SNAPSHOT, "oscillation_rate", "STABLE", node_id="unit-1"
        )
        assert result["params"]["similarity_threshold"] == 0.65
        assert result["sources"]["similarity_threshold"] == "NODE"

    def test_invalid_control_type_falls_back_stable(self) -> None:
        result = cp.resolve_from_snapshot(self.SNAPSHOT, "oscillation_rate", "BOGUS")
        assert result["controlType"] == "STABLE"


# ---------------------------------------------------------------------------
# 发布：expectedRevision 乐观锁 + 发布账本 + 审计
# ---------------------------------------------------------------------------


class TestPublishOverride:
    """publish_override：409 / 校验 / 账本 / 审计."""

    def _db(self, side_effect):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=side_effect)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.delete = AsyncMock()
        return db

    def test_stale_expected_revision_409(self) -> None:
        db = self._db([_scalar_result(5)])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="LOOP",
                    scope_id="loop-1",
                    metric_code="oscillation_rate",
                    params={"similarity_threshold": 0.8},
                    expected_revision=4,
                    reason="r",
                    operator="admin",
                )
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ERR_CONFIG_REVISION_CONFLICT"
        assert exc_info.value.data == {"currentRevision": 5}

    def test_unknown_metric_404(self) -> None:
        db = self._db([])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="LOOP",
                    scope_id="l",
                    metric_code="nope",
                    params={},
                    expected_revision=0,
                    reason="r",
                    operator="a",
                )
            )
        assert exc_info.value.status_code == 404

    def test_invalid_layer_400(self) -> None:
        db = self._db([])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="TASK",
                    scope_id="l",
                    metric_code="oscillation_rate",
                    params={},
                    expected_revision=0,
                    reason="r",
                    operator="a",
                )
            )
        assert exc_info.value.status_code == 400

    def test_publish_happy_path_ledger_and_audit(self) -> None:
        """发布成功：覆盖 upsert + PUBLISH 账本 + 审计信封（含回退版本）."""
        db = self._db(
            [
                _scalar_result(9),  # 当前 revision
                _none_result(),  # DEFAULT 层 algorithm_parameter（校验）
                _none_result(),  # DEFAULT 层 metric threshold（校验）
                _none_result(),  # 既有覆盖（新建）
                # commit 后 sync_runtime_cache：
                _scalars_all_result([]),
                _all_result([]),
                _scalar_result(10),
            ]
        )
        result = asyncio_run(
            cp.publish_override(
                db,
                layer="LOOP",
                scope_id="loop-1",
                metric_code="oscillation_rate",
                params={"similarity_threshold": 0.8},
                expected_revision=9,
                reason="现场振荡误报治理",
                operator="admin",
                control_type="STABLE",
            )
        )
        assert result["revision"] == 10
        assert result["rollbackRevision"] == 9
        assert result["affectedLoops"] == 1  # LOOP 层精确 =1
        assert result["before"] is None and result["after"] == {"similarity_threshold": 0.8}
        db.commit.assert_awaited_once()

        added = [call.args[0] for call in db.add.call_args_list]
        override = next(o for o in added if type(o).__name__ == "ConfigOverride")
        publication = next(o for o in added if type(o).__name__ == "ConfigPublication")
        audit = next(o for o in added if type(o).__name__ == "SysAuditLog")
        assert override.published_revision == 10
        assert publication.revision == 10
        assert publication.operation == "PUBLISH"
        assert publication.reason == "现场振荡误报治理"
        assert publication.operator == "admin"
        assert publication.rollback_revision == 9
        assert publication.before_value is None
        assert json.loads(publication.after_value) == {"similarity_threshold": 0.8}
        # 审计信封：原因/作用范围/回退版本/影响范围随 before/after 入册
        before_envelope = json.loads(audit.before_value)
        after_envelope = json.loads(audit.after_value)
        assert before_envelope["reason"] == "现场振荡误报治理"
        assert before_envelope["rollbackRevision"] == 9
        assert before_envelope["scope"]["scopeId"] == "loop-1"
        assert before_envelope["value"] is None
        assert after_envelope["value"] == {"similarity_threshold": 0.8}

    def test_cross_layer_combo_inversion_rejected(self) -> None:
        """跨层组合求值（P2-01 遗留）：DEFAULT 层 trip_inactive=0.06 下发布
        trip_normal=0.05 → 合并后三档颠倒，400 原子拒绝."""
        db = self._db(
            [
                _scalar_result(0),
                _scalar_result(
                    _make_ap_row(
                        "output_trip_index",
                        "STABLE",
                        {"trip_inactive": 0.06, "trip_normal": 0.1, "trip_frequent": 1.0},
                    )
                ),
                _none_result(),
            ]
        )
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="LOOP",
                    scope_id="loop-1",
                    metric_code="output_trip_index",
                    params={"trip_normal": 0.05},
                    expected_revision=0,
                    reason="r",
                    operator="admin",
                    control_type="STABLE",
                )
            )
        assert exc_info.value.status_code == 400
        assert "组合约束" in exc_info.value.message
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    def test_all_control_types_validated_when_ct_omitted(self) -> None:
        """controlType 缺省：DEFAULT 层 4 控制类型合并视图全量校验."""
        db = self._db([_scalar_result(0)] + [_none_result(), _none_result()] * 4)
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="NODE",
                    scope_id="unit-1",
                    metric_code="oscillation_rate",
                    params={"min_ratio": 0.5, "max_ratio": 0.2},  # 颠倒
                    expected_revision=0,
                    reason="r",
                    operator="admin",
                )
            )
        assert exc_info.value.status_code == 400

    def test_commit_integrity_error_maps_409(self) -> None:
        """并发抢先提交同一 revision（唯一约束 IntegrityError）→ 409 兜底."""
        from sqlalchemy.exc import IntegrityError

        db = self._db(
            [
                _scalar_result(0),
                _none_result(),
                _none_result(),
                _none_result(),
                # rollback 后 get_current_revision（最新版本）
                _scalar_result(1),
            ]
        )
        db.commit = AsyncMock(side_effect=IntegrityError("dup", None, Exception()))
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="LOOP",
                    scope_id="loop-1",
                    metric_code="oscillation_rate",
                    params={"similarity_threshold": 0.8},
                    expected_revision=0,
                    reason="r",
                    operator="admin",
                    control_type="STABLE",
                )
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.data == {"currentRevision": 1}


# ---------------------------------------------------------------------------
# 重置与解释（C10：重置低层后高层仍生效且可解释）
# ---------------------------------------------------------------------------


class TestResetOverride:
    """reset_override / explain_reset."""

    def _db(self, side_effect):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=side_effect)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.delete = AsyncMock()
        return db

    def test_reset_publishes_new_revision_and_explains_higher_layers(self) -> None:
        """重置 TEMPLATE 层：NODE/LOOP 更高层仍生效 → resetExplanation 逐键说明."""
        template_row = _make_override_row(
            "TEMPLATE",
            "FAST_RESP",
            "oscillation_rate",
            {"similarity_threshold": 0.5, "min_ratio": 0.06},
        )
        node_row = _make_override_row(
            "NODE",
            "unit-1",
            "oscillation_rate",
            {"similarity_threshold": 0.65},
            published_revision=4,
        )
        db = self._db(
            [
                _scalar_result(6),  # revision
                _scalar_result(template_row),  # 既有覆盖行
                # commit 后 sync_runtime_cache
                _scalars_all_result([]),
                _all_result([]),
                _scalar_result(7),
                # explain_reset：NODE 层加载（LOOP 层作用域未提供不查）
                _scalar_result(node_row),
            ]
        )
        result = asyncio_run(
            cp.reset_override(
                db,
                override_id="ov-1",
                expected_revision=6,
                reason="r",
                operator="admin",
            )
        )
        db.delete.assert_awaited_once()
        assert result["revision"] == 7
        assert result["before"] == {"similarity_threshold": 0.5, "min_ratio": 0.06}
        assert result["after"] is None

        explanation = asyncio_run(
            cp.explain_reset(
                db,
                layer="TEMPLATE",
                scope_id="FAST_RESP",
                metric_code="oscillation_rate",
                control_type=None,
                keys=["similarity_threshold", "min_ratio"],
                node_id="unit-1",
            )
        )
        # similarity_threshold 仍被 NODE 层遮盖；min_ratio 回落（无高层覆盖）
        assert explanation == [
            {
                "key": "similarity_threshold",
                "value": 0.65,
                "source": "NODE",
                "sourceId": "unit-1",
                "sourceRevision": "4",
            }
        ]

    def test_reset_not_found_404(self) -> None:
        db = self._db([_scalar_result(0), _none_result()])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.reset_override(
                    db, override_id="ghost", expected_revision=0, reason="r", operator="a"
                )
            )
        assert exc_info.value.status_code == 404

    def test_reset_stale_revision_409(self) -> None:
        db = self._db([_scalar_result(2)])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.reset_override(
                    db, override_id="ov-1", expected_revision=1, reason="r", operator="a"
                )
            )
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 回退（逆操作重放为新 revision，不删历史）
# ---------------------------------------------------------------------------


class TestRollbackPublication:
    """rollback_publication."""

    def _db(self, side_effect):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=side_effect)
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.delete = AsyncMock()
        return db

    def test_rollback_restores_before_as_new_revision(self) -> None:
        target = _make_publication_row(
            3,
            operation="PUBLISH",
            layer="LOOP",
            scope={"layer": "LOOP", "scopeId": "loop-1", "metricCode": "oscillation_rate"},
            before_value=json.dumps({"similarity_threshold": 0.5}),
            after_value=json.dumps({"similarity_threshold": 0.8}),
            rollback_revision=2,
        )
        existing = _make_override_row(
            "LOOP", "loop-1", "oscillation_rate", {"similarity_threshold": 0.8}
        )
        db = self._db(
            [
                _scalar_result(5),  # 当前 revision
                _scalar_result(target),  # 回退目标
                _scalar_result(existing),  # 现存覆盖
                # sync_runtime_cache
                _scalars_all_result([]),
                _all_result([]),
                _scalar_result(6),
            ]
        )
        result = asyncio_run(
            cp.rollback_publication(
                db,
                revision=3,
                expected_revision=5,
                reason="回退误发布",
                operator="admin",
            )
        )
        assert result["revision"] == 6  # 新 revision，不删历史
        assert result["rolledBackRevision"] == 3
        # 覆盖行恢复为 revision 3 的 before 状态
        assert existing.params == {"similarity_threshold": 0.5}
        assert existing.published_revision == 6
        added = [call.args[0] for call in db.add.call_args_list]
        publication = next(o for o in added if type(o).__name__ == "ConfigPublication")
        assert publication.operation == "ROLLBACK"
        assert publication.rollback_revision == 5
        assert json.loads(publication.after_value) == {"similarity_threshold": 0.5}

    def test_rollback_unknown_404(self) -> None:
        db = self._db([_scalar_result(1), _none_result()])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.rollback_publication(
                    db, revision=9, expected_revision=1, reason="r", operator="a"
                )
            )
        assert exc_info.value.status_code == 404

    def test_rollback_future_revision_400(self) -> None:
        target = _make_publication_row(9)
        db = self._db([_scalar_result(5), _scalar_result(target)])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.rollback_publication(
                    db, revision=9, expected_revision=5, reason="r", operator="a"
                )
            )
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 任务边界固定快照（Redis 不参与；失配即重载）
# ---------------------------------------------------------------------------


class TestPinConfigSnapshot:
    """pin_config_snapshot：持久 revision 读取 + 失配重载 + 覆盖层入快照."""

    def test_pin_when_synced_skips_reload(self, reset_publish_state) -> None:
        cp._set_synced_revision(4)
        ac.apply_runtime({"oscillation_rate": {"STABLE": {"similarity_threshold": 0.4}}}, {})
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(4),  # revision 一致
                _scalars_all_result([]),  # 覆盖层查询（无行）
            ]
        )
        snapshot = asyncio_run(cp.pin_config_snapshot(db, metric_codes=["oscillation_rate"]))
        assert snapshot["configRevision"] == 4
        assert snapshot["params"]["oscillation_rate|STABLE"]["similarity_threshold"] == 0.4
        assert snapshot["overrides"] == {}
        assert db.execute.await_count == 2  # 未走重载路径

    def test_pin_detects_stale_cache_and_reloads(self, reset_publish_state) -> None:
        """本地 revision=1 落后 DB revision=2（漏收广播）→ 从 DB 重载后快照."""
        cp._set_synced_revision(1)
        ac.apply_runtime({"oscillation_rate": {"STABLE": {"similarity_threshold": 0.4}}}, {})
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(2),  # DB revision（失配）
                # sync_runtime_cache 重载三查询
                _scalars_all_result(
                    [_make_ap_row("oscillation_rate", "STABLE", {"similarity_threshold": 0.9})]
                ),
                _all_result([]),
                _scalar_result(2),
                # 覆盖层查询
                _scalars_all_result(
                    [
                        _make_override_row(
                            "LOOP",
                            "loop-1",
                            "oscillation_rate",
                            {"min_ratio": 0.07},
                        )
                    ]
                ),
            ]
        )
        snapshot = asyncio_run(cp.pin_config_snapshot(db))
        assert snapshot["configRevision"] == 2
        # 重载后的 DEFAULT 层值
        assert snapshot["params"]["oscillation_rate|STABLE"]["similarity_threshold"] == 0.9
        # 覆盖层一并固定
        assert snapshot["overrides"]["LOOP|loop-1|oscillation_rate"] == {"min_ratio": 0.07}
        # 进程同步标记已推进
        assert cp._get_synced_revision() == 2


# ---------------------------------------------------------------------------
# 广播消息处理（按版本去重；广播仅加速）
# ---------------------------------------------------------------------------


class TestConfigRevisionBroadcast:
    """_handle_config_revision_message：版本去重 / 坏消息忽略."""

    def test_stale_message_ignored(self, reset_publish_state) -> None:
        cp._set_synced_revision(5)
        with patch.object(cp, "_reload_runtime_cache_sync") as reload:
            assert cp._handle_config_revision_message('{"revision": 4}') is False
            reload.assert_not_called()

    def test_newer_message_reloads(self, reset_publish_state) -> None:
        cp._set_synced_revision(5)
        with patch.object(cp, "_reload_runtime_cache_sync", return_value=True) as reload:
            assert cp._handle_config_revision_message('{"revision": 6}') is True
            reload.assert_called_once()

    def test_malformed_message_ignored(self, reset_publish_state) -> None:
        with patch.object(cp, "_reload_runtime_cache_sync") as reload:
            assert cp._handle_config_revision_message("not-json{") is False
            reload.assert_not_called()

    def test_broadcast_failure_does_not_raise(self) -> None:
        """Redis 故障：广播失败仅告警，不阻断发布结果（持久 revision 是真相源）."""
        with patch(
            "app.core.redis.redis_client",
            MagicMock(**{"publish.side_effect": ConnectionError("redis down")}),
        ):
            asyncio_run(cp.broadcast_config_revision(1, source="test"))  # 不抛异常


# ---------------------------------------------------------------------------
# 兼容通道 LEGACY_SYNC（algorithm-params PUT 集成）
# ---------------------------------------------------------------------------


class TestLegacySyncChannel:
    """note_legacy_sync：发布登记 + expectedRevision 语义."""

    def test_note_legacy_sync_registers_publication(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(2),  # 当前 revision
                _count_result(120),  # 影响回路数
            ]
        )
        db.add = MagicMock()
        new_revision = asyncio_run(
            cp.note_legacy_sync(
                db,
                metric_code="oscillation_rate",
                before={"STABLE": {"similarity_threshold": 0.4}},
                after={"STABLE": {"similarity_threshold": 0.55}},
                reason="调参",
                operator="admin",
                control_types=["STABLE"],
            )
        )
        assert new_revision == 3
        added = [call.args[0] for call in db.add.call_args_list]
        publication = next(o for o in added if type(o).__name__ == "ConfigPublication")
        audit = next(o for o in added if type(o).__name__ == "SysAuditLog")
        assert publication.operation == "LEGACY_SYNC"
        assert publication.layer is None
        assert json.loads(publication.before_value)["STABLE"]["similarity_threshold"] == 0.4
        assert json.loads(publication.after_value)["STABLE"]["similarity_threshold"] == 0.55
        envelope = json.loads(audit.after_value)
        assert envelope["reason"] == "调参"
        assert envelope["rollbackRevision"] == 2

    def test_note_legacy_sync_expected_mismatch_409(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(5)])
        db.add = MagicMock()
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.note_legacy_sync(
                    db,
                    metric_code="oscillation_rate",
                    before={},
                    after={},
                    reason="r",
                    operator="a",
                    expected_revision=4,
                )
            )
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 端点：权限（DEC-03）+ 409 + 查询
# ---------------------------------------------------------------------------


class TestConfigPublishEndpoints:
    """/configs/publish/*：发布/重置/回退仅 ADMIN；查询三角色只读."""

    def test_get_revision_empty(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_none_result(), _none_result()])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/publish/revision",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["revision"] == 0
        assert resp.json()["data"]["lastPublication"] is None

    def test_get_revision_ic_readonly(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_none_result(), _none_result()])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/configs/publish/revision",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_publish_as_ic_forbidden_403(self, client, mock_db) -> None:
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/configs/publish/overrides",
                json={
                    "layer": "LOOP",
                    "scopeId": "loop-1",
                    "metricCode": "oscillation_rate",
                    "params": {"similarity_threshold": 0.8},
                    "expectedRevision": 0,
                    "reason": "r",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_publish_stale_revision_409_with_current(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(7)])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/publish/overrides",
                json={
                    "layer": "LOOP",
                    "scopeId": "loop-1",
                    "metricCode": "oscillation_rate",
                    "params": {"similarity_threshold": 0.8},
                    "expectedRevision": 6,
                    "reason": "r",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "ERR_CONFIG_REVISION_CONFLICT"
        assert body["data"]["currentRevision"] == 7

    def test_publish_happy_path(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(
            side_effect=[
                _scalar_result(0),
                _none_result(),
                _none_result(),
                _none_result(),
                _scalars_all_result([]),
                _all_result([]),
                _scalar_result(1),
            ]
        )
        mock_db.add = MagicMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/publish/overrides",
                json={
                    "layer": "LOOP",
                    "scopeId": "loop-1",
                    "metricCode": "oscillation_rate",
                    "params": {"similarity_threshold": 0.8},
                    "expectedRevision": 0,
                    "reason": "治理",
                    "controlType": "STABLE",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["revision"] == 1
        assert data["rollbackRevision"] == 0
        assert data["affectedLoops"] == 1

    def test_reset_endpoint_as_admin(self, client, mock_db) -> None:
        row = _make_override_row(
            "LOOP", "loop-1", "oscillation_rate", {"similarity_threshold": 0.8}
        )
        mock_db.execute = AsyncMock(
            side_effect=[
                _scalar_result(1),
                _scalar_result(row),
                _scalars_all_result([]),
                _all_result([]),
                _scalar_result(2),
            ]
        )
        mock_db.add = MagicMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/publish/overrides/ov-1/reset",
                json={"expectedRevision": 1, "reason": "回退层级"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["revision"] == 2
        assert data["after"] is None
        assert data["resetExplanation"] == []  # LOOP 顶层无更高覆盖层

    def test_rollback_endpoint_invalid_revision_400(self, client, mock_db) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/publish/publications/0/rollback",
                json={"expectedRevision": 0, "reason": "r"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400

    def test_effective_endpoint_returns_chain(self, client, mock_db) -> None:
        loop_row = _make_override_row(
            "LOOP", "loop-1", "oscillation_rate", {"similarity_threshold": 0.8}
        )
        mock_db.execute = AsyncMock(
            side_effect=[
                _scalar_result(1),
                _none_result(),
                _none_result(),
                _scalar_result(loop_row),
            ]
        )
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.get(
                "/api/v1/configs/publish/effective",
                params={
                    "metricCode": "oscillation_rate",
                    "controlType": "STABLE",
                    "loopId": "loop-1",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["revision"] == 1
        # params 为原始参数键（snake_case，不 camel 别名）；effective 逐项 key 同
        assert data["params"]["similarity_threshold"] == 0.8
        sources = {e["key"]: e["source"] for e in data["effective"]}
        assert sources["similarity_threshold"] == "LOOP"
        assert sources["min_ratio"] == "DEFAULT"
        assert any(
            s["key"] == "similarity_threshold" and s["shadowedBy"] == "LOOP"
            for s in data["shadowed"]
        )

    def test_list_overrides(self, client, mock_db) -> None:
        row = _make_override_row("NODE", "unit-1", "oscillation_rate", {"min_ratio": 0.06})
        mock_db.execute = AsyncMock(side_effect=[_scalars_all_result([row])])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/publish/overrides",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["layer"] == "NODE"

    def test_list_publications(self, client, mock_db) -> None:
        pub = _make_publication_row(2, operation="LEGACY_SYNC", layer=None)
        mock_db.execute = AsyncMock(side_effect=[_scalars_all_result([pub])])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/publish/publications",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["items"][0]["operation"] == "LEGACY_SYNC"


# ---------------------------------------------------------------------------
# 兼容通道端到端：PUT /algorithm-params + expectedRevision
# ---------------------------------------------------------------------------


class TestLegacyPutExpectedRevision:
    """PUT /configs/algorithm-params/{metric}：expectedRevision 409 / LEGACY_SYNC 入账."""

    def test_put_stale_expected_revision_409(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(3)])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json={
                    "items": [{"controlType": "STABLE", "params": {"similarity_threshold": 0.6}}],
                    "expectedRevision": 2,
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "ERR_CONFIG_REVISION_CONFLICT"
        assert body["data"]["currentRevision"] == 3
        mock_db.commit.assert_not_awaited()

    def test_put_with_matching_expected_revision_registers_legacy_sync(
        self, client, mock_db
    ) -> None:
        saved_row = _make_ap_row("oscillation_rate", "STABLE", {"similarity_threshold": 0.55})
        mock_db.execute = AsyncMock(
            side_effect=[
                _scalar_result(0),  # 当前 revision（匹配 expectedRevision=0）
                _none_result(),  # before 快照
                _none_result(),  # items 存量查询（新建）
                _none_result(),  # after 快照
                _scalar_result(0),  # note_legacy_sync revision
                _count_result(9),  # 影响回路数
                _scalars_all_result([saved_row]),  # commit 后缓存同步
                _all_result([]),
                _scalar_result(1),
            ]
        )
        mock_db.add = MagicMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/oscillation_rate",
                json={
                    "items": [{"controlType": "STABLE", "params": {"similarity_threshold": 0.55}}],
                    "expectedRevision": 0,
                    "reason": "现场调参",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()
        added = [call.args[0] for call in mock_db.add.call_args_list]
        publication = next(o for o in added if type(o).__name__ == "ConfigPublication")
        assert publication.operation == "LEGACY_SYNC"
        assert publication.revision == 1
        assert publication.reason == "现场调参"
        # 既有 ALGORITHM_PARAMS_UPDATE 审计保留，且 before 补真实值（P2-01 遗留修复）
        audits = [o for o in added if type(o).__name__ == "SysAuditLog"]
        algo_audit = next(a for a in audits if a.operation_type == "ALGORITHM_PARAMS_UPDATE")
        assert json.loads(algo_audit.before_value) == {"STABLE": None}
