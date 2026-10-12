"""P4-03 scope 贯穿单元测试（mock；真实 PG/WS 链见 integration/test_p4_03_c25_real.py）.

覆盖：
- ScopeContext：交集/缓存 key（授权 hash+revision）/节点归属
- tag 可见性规则（classify_tags）：ADMIN 全量 / 非 ADMIN 绑定归属 / 未绑定
  仅 ADMIN / ALL 不推定全可见
- WS realtime：旧客户端授权集过滤（_tag_allowed）、raw 载荷仅 ADMIN、
  订阅交集语义；发送栅栏 revision 变化重过滤缓冲（patch _fence 依赖）
- WS alert：预警按关联 loop/node 过滤（_alert_allowed）
- realtime HTTP：越界 tag 进 rejected（client + patch classify_tags）
- 任务栅栏：TaskSubject SYSTEM identity / SubjectRevokedError 语义
- 迁移与引导 SQL 同批（guard 表建表语句一致性）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints import ws_alert, ws_realtime
from app.services.authz_scope import (
    OP_NODE_REBIND,
    OP_TAG_REBIND,
    OP_TOKEN_REVOKE,
    OP_USER_STATE,
    SCOPE_ALL,
    SCOPE_NODE_SUBTREE,
    ScopeContext,
    classify_tags,
)
from app.services.authz_task_fence import (
    SubjectRevokedError,
    TaskSubject,
    system_subject,
)


def _ctx(
    role: str = "IC_ENGINEER",
    nodes: frozenset[str] | None = None,
    revision: int = 7,
) -> ScopeContext:
    return ScopeContext(
        user_id="u1",
        role=role,
        scope_type=SCOPE_ALL if nodes is None else SCOPE_NODE_SUBTREE,
        node_id=None if nodes is None else sorted(nodes)[0],
        allowed_node_ids=nodes,
        revision=revision,
    )


# ---------------------------------------------------------------------------
# ScopeContext 纯函数
# ---------------------------------------------------------------------------


class TestScopeContext:
    def test_intersect_all_respects_explicit_filter(self) -> None:
        ctx = _ctx(nodes=None)
        assert ctx.intersect_node_ids(["a", "b"]) == ["a", "b"]
        assert ctx.intersect_node_ids(None) is None

    def test_intersect_subtree_no_filter_returns_full_set(self) -> None:
        ctx = _ctx(nodes=frozenset({"a", "b"}))
        assert ctx.intersect_node_ids(None) == ["a", "b"]

    def test_intersect_subtree_with_filter_intersects(self) -> None:
        ctx = _ctx(nodes=frozenset({"a", "b"}))
        assert ctx.intersect_node_ids(["b", "c"]) == ["b"]  # 交集
        assert ctx.intersect_node_ids(["c"]) == []  # 空交集=空结果

    def test_cache_key_contains_scope_hash_and_revision(self) -> None:
        all_ctx = _ctx(nodes=None, revision=3)
        sub_ctx = _ctx(nodes=frozenset({"n1", "n2"}), revision=3)
        assert all_ctx.cache_key("k") == "k~ALL~r3"
        assert sub_ctx.cache_key("k").startswith("k~nodes-")
        assert sub_ctx.cache_key("k").endswith("~r3")
        # revision 推进 → key 变化（策略/拓扑变更自然失效）
        assert sub_ctx.cache_key("k") != _ctx(nodes=frozenset({"n1", "n2"}), revision=4).cache_key(
            "k"
        )
        # 范围不同 → key 不同（跨范围不共用全厂缓存）
        assert sub_ctx.cache_key("k") != _ctx(nodes=frozenset({"n9"}), revision=3).cache_key("k")

    def test_node_in_scope(self) -> None:
        all_ctx = _ctx(nodes=None)
        sub_ctx = _ctx(nodes=frozenset({"a"}))
        assert all_ctx.node_in_scope(None)
        assert sub_ctx.node_in_scope("a")
        assert not sub_ctx.node_in_scope("b")
        assert not sub_ctx.node_in_scope(None)  # 未挂单元回路仅 ALL 可见


# ---------------------------------------------------------------------------
# classify_tags（fake db：按语句分派可编程结果）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """按调用序返回预置结果的 fake 会话（classify_tags 两次查询：存在性→绑定）."""

    def __init__(self, existence: list[tuple], bindings: list[tuple]):
        self._queue = [_FakeResult(existence), _FakeResult(bindings)]
        self.calls: list[Any] = []

    async def execute(self, stmt):
        self.calls.append(stmt)
        return self._queue.pop(0)


class TestClassifyTags:
    async def test_admin_sees_all_existing_tags_including_unbound(self) -> None:
        db = _FakeDB(
            existence=[("t-1", "TAG_A"), ("t-2", "TAG_UNBOUND")],
            bindings=[],  # ADMIN 分支不查绑定
        )
        visible, rejected = await classify_tags(
            db, _ctx(role="ADMIN"), ["TAG_A", "TAG_UNBOUND", "TAG_X"]
        )
        assert visible == ["TAG_A", "TAG_UNBOUND"]
        assert rejected == ["TAG_X"]

    async def test_non_admin_all_scope_bound_only(self) -> None:
        """ALL 不推定可看任何 tag：仅"有绑定回路"的 tag 可见（01§3.5）."""
        db = _FakeDB(
            existence=[("t-1", "TAG_A"), ("t-2", "TAG_UNBOUND")],
            bindings=[("t-1",)],  # TAG_A 绑定任意回路（ALL 无 unit 条件）
        )
        ctx = _ctx(role="IC_ENGINEER", nodes=None)
        visible, rejected = await classify_tags(db, ctx, ["TAG_A", "TAG_UNBOUND"])
        assert visible == ["TAG_A"]
        assert rejected == ["TAG_UNBOUND"]  # 未绑定 tag 仅 ADMIN 可读

    async def test_non_admin_subtree_in_scope_binding_only(self) -> None:
        db = _FakeDB(
            existence=[("t-1", "TAG_A"), ("t-2", "TAG_B")],
            bindings=[("t-1",)],  # 仅 TAG_A 绑定到范围内回路
        )
        ctx = _ctx(role="IC_ENGINEER", nodes=frozenset({"unitA"}))
        visible, rejected = await classify_tags(db, ctx, ["TAG_A", "TAG_B"])
        assert visible == ["TAG_A"]
        assert rejected == ["TAG_B"]  # 绑定回路不在范围内 → 不可见（与未知同列表）

    async def test_dedup_preserves_order(self) -> None:
        db = _FakeDB(existence=[("t-1", "TAG_A")], bindings=[("t-1",)])
        visible, rejected = await classify_tags(db, _ctx(role="IC"), ["TAG_A", "TAG_A", "TAG_B"])
        assert visible == ["TAG_A"]
        assert rejected == ["TAG_B"]


# ---------------------------------------------------------------------------
# WS realtime：分发过滤与栅栏重过滤
# ---------------------------------------------------------------------------


def _client(
    role: str = "IC_ENGINEER",
    authorized: set[str] | None = None,
    subscribed: set[str] | None = None,
) -> ws_realtime._ClientState:
    c = ws_realtime._ClientState(ws=MagicMock())
    c.role = role
    c.authorized_tags = authorized
    c.filter_tags = subscribed
    return c


class TestWsRealtimeFilter:
    def test_legacy_client_authorized_set_only(self) -> None:
        """旧客户端（未订阅）最多转发授权集合（非 ADMIN）."""
        c = _client(authorized={"A_PV"}, subscribed=None)
        assert ws_realtime._tag_allowed(c, "A_PV")
        assert not ws_realtime._tag_allowed(c, "B_PV")  # 范围外 tag 不转发

    def test_legacy_admin_full_forward(self) -> None:
        c = _client(role="ADMIN", authorized=None, subscribed=None)
        assert ws_realtime._tag_allowed(c, "ANY")
        assert ws_realtime._tag_allowed(c, "UNBOUND_X")

    def test_raw_payload_admin_only(self) -> None:
        c_admin = _client(role="ADMIN", authorized=None)
        c_ic = _client(authorized={"A_PV"})
        assert ws_realtime._tag_allowed(c_admin, "__raw_1")
        assert not ws_realtime._tag_allowed(c_ic, "__raw_1")

    def test_subscribe_intersection(self) -> None:
        c = _client(authorized={"A_PV", "A_SP"}, subscribed={"A_PV"})
        assert ws_realtime._tag_allowed(c, "A_PV")
        assert not ws_realtime._tag_allowed(c, "A_SP")  # 未订阅不转发
        assert not ws_realtime._tag_allowed(c, "B_PV")

    async def test_fence_revalidate_refilters_buffer(self) -> None:
        """连接内降权（范围收窄）→ 栅栏重过滤缓冲，无新越权帧."""
        c = _client(authorized={"A_PV", "B_PV"}, subscribed={"A_PV", "B_PV"})
        c.user_id = "u1"
        c.revision = 5
        c.buffer["B_PV"] = {"tagCode": "B_PV", "value": "1"}
        c.buffer["A_PV"] = {"tagCode": "A_PV", "value": "2"}

        new_ctx = _ctx(role="IC_ENGINEER", nodes=frozenset({"unitA"}), revision=6)

        async def _fake_lock_share(db):
            return 6

        subject = MagicMock()
        subject.is_active = True
        subject.role = "IC_ENGINEER"

        async def _fake_resolve(db, user_id, role, revision=None):
            return new_ctx

        async def _fake_tags(db, ctx):
            return {"A_PV"}  # B 装置 tag 移出授权集

        fake_db = MagicMock()
        with (
            patch.object(ws_realtime, "is_token_blacklisted", AsyncMock(return_value=False)),
            patch("app.services.authz_scope.load_subject", AsyncMock(return_value=subject)),
            patch("app.services.authz_scope.lock_guard_for_share", _fake_lock_share),
            patch("app.services.authz_scope.resolve_scope", _fake_resolve),
            patch("app.services.authz_scope.authorized_tag_names", _fake_tags),
        ):
            ok = await ws_realtime._fence_revalidate(fake_db, c)

        assert ok is True
        assert c.revision == 6
        assert c.authorized_tags == {"A_PV"}
        assert c.filter_tags == {"A_PV"}  # 订阅集合同步收窄
        assert "B_PV" not in c.buffer  # 缓冲清除越权 tag
        assert "A_PV" in c.buffer

    async def test_fence_revalidate_revoked_on_disabled(self) -> None:
        """连接内禁用 → 栅栏判定失权（False → 调用方清缓冲关闭连接）."""
        c = _client(authorized={"A_PV"})
        c.user_id = "u1"
        c.revision = 5

        async def _fake_lock_share(db):
            return 6

        with (
            patch.object(ws_realtime, "is_token_blacklisted", AsyncMock(return_value=False)),
            patch(
                "app.services.authz_scope.load_subject",
                AsyncMock(return_value=None),  # 主体不存在/禁用
            ),
            patch("app.services.authz_scope.lock_guard_for_share", _fake_lock_share),
        ):
            ok = await ws_realtime._fence_revalidate(MagicMock(), c)
        assert ok is False

    async def test_fence_revoked_on_blacklisted_token(self) -> None:
        """连接内令牌撤销（重置密码）→ 无新帧（不靠前端退订）."""
        c = _client(authorized={"A_PV"})
        c.jti = "jti-1"
        with patch.object(ws_realtime, "is_token_blacklisted", AsyncMock(return_value=True)):
            ok = await ws_realtime._fence_revalidate(MagicMock(), c)
        assert ok is False


# ---------------------------------------------------------------------------
# WS alert：按关联 loop/node 过滤
# ---------------------------------------------------------------------------


class TestWsAlertFilter:
    def test_admin_receives_all(self) -> None:
        c = ws_alert._AlertClient(role="ADMIN")
        assert ws_alert._alert_allowed(c, {"loopId": "L_B"})
        assert ws_alert._alert_allowed(c, {})  # 无 loopId 预警仅 ADMIN

    def test_subtree_loop_scoped(self) -> None:
        c = ws_alert._AlertClient(role="IC_ENGINEER", allowed_loop_ids={"L_A"})
        assert ws_alert._alert_allowed(c, {"loopId": "L_A"})
        assert not ws_alert._alert_allowed(c, {"loopId": "L_B"})  # B 装置预警不泄漏

    def test_subtree_no_loop_id_denied(self) -> None:
        c = ws_alert._AlertClient(role="IC_ENGINEER", allowed_loop_ids={"L_A"})
        assert not ws_alert._alert_allowed(c, {})


# ---------------------------------------------------------------------------
# realtime HTTP：越界 tag 通用拒绝
# ---------------------------------------------------------------------------


class TestRealtimeHttp:
    def test_out_of_scope_tags_rejected(self, client) -> None:  # noqa: ANN001
        """请求未绑定/越界 tag → rejected 列表（不返回值）。"""
        from tests.conftest import TEST_USERS, mock_current_user

        async def _fake_classify(db, ctx, names):
            return [n for n in names if n == "A_PV"], ["UNBOUND_X"]

        async def _fake_cached(tag_codes):
            return []

        subscriber = MagicMock()
        subscriber.get_cached_values = _fake_cached
        with (
            mock_current_user(TEST_USERS["ic_engineer"]),
            patch("app.api.v1.endpoints.realtime.classify_tags", _fake_classify),
            patch("app.api.v1.endpoints.realtime.get_subscriber", lambda: subscriber),
        ):
            resp = client.get("/api/v1/realtime?tagCodes=A_PV&tagCodes=UNBOUND_X")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["rejected"] == ["UNBOUND_X"]
        codes = [i["tagCode"] for i in data["items"]]
        assert "UNBOUND_X" not in codes


# ---------------------------------------------------------------------------
# 任务栅栏语义（mock）
# ---------------------------------------------------------------------------


class TestTaskFence:
    def test_system_identity_not_revocable(self) -> None:
        subj = system_subject(["L1"])
        assert subj.is_system_identity
        manual = TaskSubject(subject_user_id="u1", subject_role="IC_ENGINEER", action="a")
        assert not manual.is_system_identity

    def test_audit_revoked_writes_no_business_rows(self) -> None:
        from app.services.authz_task_fence import audit_revoked

        db = MagicMock()
        subj = TaskSubject(subject_user_id="u1", action="diagnosis:trigger", policy_revision=9)
        audit_revoked(db, subj, "L1", "主体已禁用")
        added = [c.args[0] for c in db.add.call_args_list]
        assert len(added) == 1  # 仅审计行
        assert added[0].operation_type == "TASK_REVOKED"

    async def test_validate_for_commit_test_env_short_circuits(self) -> None:
        """CLPM_TEST_MODE=1（conftest 全局置位）→ 短路放行（真实链路集成测试覆盖）."""
        from app.services.authz_task_fence import validate_for_commit

        subj = TaskSubject(subject_user_id="u1", subject_role="IC", action="x")
        rev = await validate_for_commit(MagicMock(), subj, "L1")
        assert rev == -1


# ---------------------------------------------------------------------------
# 策略变更者与账本枚举一致性
# ---------------------------------------------------------------------------


class TestGuardContract:
    def test_domain_operations_in_ledger_constraint(self) -> None:
        """域外策略变更 operation 已入账本检查约束（迁移/引导 SQL/ORM 三方一致）."""
        from app.models.authz import SysAuthzPolicy

        constraint = next(
            c
            for c in SysAuthzPolicy.__table_args__
            if getattr(c, "name", "") == "ck_sys_authz_policy_operation"
        )
        sql = str(constraint.sqltext)
        for op in (OP_NODE_REBIND, OP_TAG_REBIND, OP_USER_STATE, OP_TOKEN_REVOKE):
            assert f"'{op}'" in sql

    def test_migration_and_bootstrap_contain_guard_table(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]  # backend/
        repo = Path(__file__).resolve().parents[2]
        migration = (
            root / "alembic" / "versions" / "p403scope01_policy_publication_guard.py"
        ).read_text(encoding="utf-8")
        schema = (repo / "db" / "postgresql" / "01_schema.sql").read_text(encoding="utf-8")
        seed = (repo / "db" / "postgresql" / "02_seed_data.sql").read_text(encoding="utf-8")
        assert "policy_publication_guard" in migration
        assert "CREATE TABLE IF NOT EXISTS policy_publication_guard" in schema
        assert "INSERT INTO policy_publication_guard" in seed

    def test_ws_subscribe_loop_resolver_scope_aware_signature(self) -> None:
        """scope 版回路解析存在且旧存在性版保留（单测 patch 目标兼容）."""
        assert hasattr(ws_realtime, "_resolve_loop_tags_scoped")
        assert hasattr(ws_realtime, "_resolve_loop_tags")

    def test_subject_revoked_error_carries_reason(self) -> None:
        exc = SubjectRevokedError("主体已禁用")
        assert exc.reason == "主体已禁用"


# ---------------------------------------------------------------------------
# async 测试事件循环适配
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


for _cls in (TestClassifyTags, TestWsRealtimeFilter, TestWsAlertFilter, TestTaskFence):
    # pytest-asyncio auto 模式（pyproject asyncio_mode=auto）下无需手动标记
    pass
