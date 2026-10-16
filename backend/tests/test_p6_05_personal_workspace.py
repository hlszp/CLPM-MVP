"""P6-05 个人关系分片（/me/*）测试.

两层覆盖（对应任务书验证清单）：

1. **纯单测**（默认 ``-m 'not integration'`` 即跑，CLPM_TEST_MODE=1 由
   conftest 保证）：偏好/最近继续校验器白名单与凭据/URL 拒绝；未登录
   401、账户停用 403、非法 key/kind/loopId 400/404（端点级，校验在
   DB 访问前触发）。
2. **真实 PG 集成**（``-m integration`` + 显式 ``TEST_DATABASE_URL``
   指向一次性 scratch 库，缺省跳过——防误写共享 dev 库）：双用户隔离、
   CAS 409 携当前版本、同 requestId 幂等重放、取消墓碑 + 迟到旧关注
   拒绝复活、清空 generation 栅栏（旧代写/删 409）、撤权不泄露
   （inaccessibleCount + PARTIAL，不回显名称/证据）、唯一约束并发裁决、
   HTTP 级端到端（camelCase 契约 + 路由接线）。

运行方式（实验/验收时）：
    cd backend
    uv run pytest tests/test_p6_05_personal_workspace.py -q                 # 纯单测
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_test_p6 \
    uv run pytest tests/test_p6_05_personal_workspace.py -v -m integration  # 集成
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.core.exceptions import BizError
from app.services.personal_workspace import (
    clear_work_resume,
    delete_work_resume,
    list_loop_follows,
    list_preferences,
    list_work_resume,
    put_preference,
    put_work_resume,
    set_loop_follow,
    validate_preference_key,
    validate_preference_payload,
    validate_resource_kind,
    validate_resume_context,
)
from tests.conftest import FakeRedis, mock_current_user

# ===========================================================================
# 纯单测：校验器
# ===========================================================================


class TestValidators:
    """白名单/保留键/凭据/任意 URL/大小与深度约束（不落 DB）."""

    def test_preference_key_whitelist(self):
        validate_preference_key("default_home")
        validate_preference_key("filter_presets")
        validate_preference_key("sort_presets")

    def test_preference_key_unknown_400(self):
        with pytest.raises(BizError) as exc:
            validate_preference_key("evil_key")
        assert exc.value.status_code == 400
        assert exc.value.code == "ERR_PREFERENCE_KEY_INVALID"

    def test_preference_key_reserved_resume_generation_400(self):
        """服务端保留键 resume_generation 不对客户端开放写."""
        with pytest.raises(BizError) as exc:
            validate_preference_key("resume_generation")
        assert exc.value.status_code == 400
        assert exc.value.code == "ERR_PREFERENCE_KEY_INVALID"

    def test_default_home_payload_ok(self):
        validate_preference_payload("default_home", {"path": "/cockpit"})

    def test_default_home_rejects_external_url(self):
        with pytest.raises(BizError) as exc:
            validate_preference_payload("default_home", {"path": "https://evil.example/x"})
        assert exc.value.status_code == 400

    def test_default_home_rejects_unknown_field(self):
        with pytest.raises(BizError):
            validate_preference_payload("default_home", {"path": "/cockpit", "onClick": "x"})

    def test_payload_rejects_credential_keys(self):
        with pytest.raises(BizError) as exc:
            validate_preference_payload(
                "filter_presets", {"monitor": {"accessToken": "abc", "status": ["OPEN"]}}
            )
        assert exc.value.status_code == 400

    def test_payload_rejects_arbitrary_url_nested(self):
        with pytest.raises(BizError) as exc:
            validate_preference_payload(
                "filter_presets", {"monitor": {"link": "http://evil.example"}, "page": 1}
            )
        assert exc.value.status_code == 400

    def test_payload_rejects_oversize(self):
        big = {"monitor": {"note": "x" * (9 * 1024)}}
        with pytest.raises(BizError) as exc:
            validate_preference_payload("filter_presets", big)
        assert exc.value.status_code == 400

    def test_payload_rejects_too_deep(self):
        deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
        with pytest.raises(BizError):
            validate_preference_payload("filter_presets", deep)

    def test_resource_kind_whitelist(self):
        validate_resource_kind("loop")
        with pytest.raises(BizError) as exc:
            validate_resource_kind("loop_investigation")
        assert exc.value.status_code == 400
        assert exc.value.code == "ERR_RESOURCE_KIND_INVALID"

    def test_resume_context_rejects_url_and_credentials(self):
        validate_resume_context({"section": "diagnosis", "window": {"hours": 24}})
        with pytest.raises(BizError):
            validate_resume_context({"evidenceUrl": "https://evil.example/e/1"})
        with pytest.raises(BizError):
            validate_resume_context({"apiSecret": "s3cr3t"})
        with pytest.raises(BizError):
            validate_resume_context(["not", "a", "dict"])


# ===========================================================================
# 纯单测：端点认证/校验（TestClient + mock DB；错误路径均先于 DB 访问触发）
# ===========================================================================

_ME_URL = "/api/v1/me"


class TestEndpointAuthAndValidation:
    """未登录 401 / 停用 403 / 非法入参 400·404·422（先于 DB 访问）."""

    def test_list_follows_unauthenticated_401(self, client):
        resp = client.get(f"{_ME_URL}/loop-follows")
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"

    def test_put_follow_unauthenticated_401(self, client):
        resp = client.put(
            f"{_ME_URL}/loop-follows/{uuid4()}",
            json={"isFollowed": True, "expectedRevision": 0, "requestId": "r-1"},
        )
        assert resp.status_code == 401

    def test_list_preferences_unauthenticated_401(self, client):
        assert client.get(f"{_ME_URL}/workspace-preferences").status_code == 401

    def test_work_resume_unauthenticated_401(self, client):
        assert client.get(f"{_ME_URL}/work-resume").status_code == 401
        assert client.delete(f"{_ME_URL}/work-resume").status_code == 401

    def test_disabled_account_403(self, client, mock_db):
        """账户停用拒绝访问（get_current_user 既有红线，/me 全端点继承）."""
        from app.core.security import create_access_token
        from tests.conftest import make_db_execute_return

        disabled = MagicMock()
        disabled.id = str(uuid4())
        disabled.username = "disabled_p605"
        disabled.role = "IC_ENGINEER"
        disabled.is_active = False
        token, _, _ = create_access_token(
            subject=disabled.id, username=disabled.username, role=disabled.role
        )
        mock_db.execute.return_value = make_db_execute_return(disabled)
        resp = client.get(f"{_ME_URL}/loop-follows", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_ACCOUNT_DISABLED"

    def test_put_follow_body_missing_fields_422(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/loop-follows/{uuid4()}",
                json={"isFollowed": True},  # 缺 expectedRevision/requestId
            )
        assert resp.status_code == 422

    def test_put_follow_malformed_loop_id_404(self, client, admin_user):
        """非法 UUID 不得直撞 PG 类型报错（先于 DB 访问 404）."""
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/loop-follows/not-a-uuid",
                json={"isFollowed": True, "expectedRevision": 0, "requestId": "r-1"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_put_preference_reserved_key_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/workspace-preferences/resume_generation",
                json={"payload": {"generation": 99}, "expectedRevision": 0, "requestId": "r-1"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PREFERENCE_KEY_INVALID"

    def test_put_preference_unknown_key_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/workspace-preferences/not_allowed",
                json={"payload": {}, "expectedRevision": 0, "requestId": "r-1"},
            )
        assert resp.status_code == 400

    def test_put_preference_bad_payload_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/workspace-preferences/default_home",
                json={
                    "payload": {"path": "javascript:alert(1)"},
                    "expectedRevision": 0,
                    "requestId": "r-1",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PREFERENCE_PAYLOAD_INVALID"

    def test_put_work_resume_invalid_kind_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/work-resume/unknown-kind/{uuid4()}",
                json={
                    "context": {},
                    "expectedRevision": 0,
                    "generation": 1,
                    "requestId": "r-1",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_RESOURCE_KIND_INVALID"

    def test_put_work_resume_invalid_resource_id_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.put(
                f"{_ME_URL}/work-resume/loop/not-a-uuid",
                json={
                    "context": {},
                    "expectedRevision": 0,
                    "generation": 1,
                    "requestId": "r-1",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_RESOURCE_ID_INVALID"


# ===========================================================================
# 真实 PG 集成（scratch 库；缺省 skip）
# ===========================================================================


def _database_url() -> str:
    return os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable() -> bool:
    url = _database_url()
    if not url:
        return False
    try:
        hostport = url.split("@")[-1].split("/")[0]
        host, port = hostport.split(":")[0], int(hostport.split(":")[1])
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False


_REQUIRES_PG = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）"),
]


@pytest.fixture
async def pg():
    """真实 PG（scratch 库）：确保建表 + 两用户/工厂树/两回路种子，收尾清行."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base
    from app.models.loop import LoopLedger
    from app.models.personal_workspace import (
        UserLoopFollow,
        UserWorkResume,
        UserWorkspacePreference,
    )
    from app.models.plant_node import PlantNode
    from app.models.sys_user import SysUser

    engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
        poolclass=NullPool,
    )
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    suffix = uuid4().hex[:8]
    ids = {
        "user_a": str(uuid4()),
        "user_b": str(uuid4()),
        "factory": str(uuid4()),
        "area": str(uuid4()),
        "unit_a": str(uuid4()),
        "unit_b": str(uuid4()),
        "loop_a1": str(uuid4()),
        "loop_b1": str(uuid4()),
    }

    async with sf() as s:
        for key, name, role in (
            ("user_a", f"p605ua-{suffix}", "IC_ENGINEER"),
            ("user_b", f"p605ub-{suffix}", "PE_ENGINEER"),
        ):
            s.add(
                SysUser(
                    id=ids[key],
                    username=name,
                    password_hash="p605-test",
                    display_name=name,
                    role=role,
                    is_active=True,
                )
            )
            await s.commit()
        for key, name, ntype, parent in (
            ("factory", f"P605-F-{suffix}", "FACTORY", None),
            ("area", f"P605-A-{suffix}", "AREA", ids["factory"]),
            ("unit_a", f"P605-UA-{suffix}", "UNIT", ids["area"]),
            ("unit_b", f"P605-UB-{suffix}", "UNIT", ids["area"]),
        ):
            s.add(PlantNode(id=ids[key], name=name, type=ntype, parent_id=parent))
            await s.commit()
        for key, tag, unit in (
            ("loop_a1", f"TIC-P605A-{suffix}", ids["unit_a"]),
            ("loop_b1", f"TIC-P605B-{suffix}", ids["unit_b"]),
        ):
            s.add(LoopLedger(id=ids[key], tag_name=tag, unit_id=unit, is_active=True))
            await s.commit()

    try:
        yield SimpleNamespace(sf=sf, engine=engine, **ids)
    finally:
        async with sf() as s:
            for uid in (ids["user_a"], ids["user_b"]):
                await s.execute(sa_delete(UserWorkResume).where(UserWorkResume.user_id == uid))
                await s.execute(sa_delete(UserLoopFollow).where(UserLoopFollow.user_id == uid))
                await s.execute(
                    sa_delete(UserWorkspacePreference).where(UserWorkspacePreference.user_id == uid)
                )
            for lid in (ids["loop_a1"], ids["loop_b1"]):
                await s.execute(sa_delete(LoopLedger).where(LoopLedger.id == lid))
            for nid in (ids["unit_a"], ids["unit_b"], ids["area"], ids["factory"]):
                await s.execute(sa_delete(PlantNode).where(PlantNode.id == nid))
            for uid in (ids["user_a"], ids["user_b"]):
                await s.execute(sa_delete(SysUser).where(SysUser.id == uid))
            await s.commit()
        await engine.dispose()


def _ctx_all(user_id: str):
    from app.services.authz_scope import ScopeContext

    return ScopeContext(
        user_id=user_id,
        role="IC_ENGINEER",
        scope_type="ALL",
        node_id=None,
        allowed_node_ids=None,
        revision=0,
    )


def _ctx_unit_b_only(user_id: str, unit_b: str):
    from app.services.authz_scope import ScopeContext

    return ScopeContext(
        user_id=user_id,
        role="IC_ENGINEER",
        scope_type="NODE_SUBTREE",
        node_id=unit_b,
        allowed_node_ids=frozenset({unit_b}),
        revision=1,
    )


class TestRealPG:  # noqa: D101
    pytestmark = _REQUIRES_PG

    # ------------------------------------------------------------------
    # 双用户隔离
    # ------------------------------------------------------------------

    async def test_dual_user_isolation(self, pg):
        """账号 A 的关注/偏好/最近继续对账号 B 完全不可见."""
        async with pg.sf() as s:
            await set_loop_follow(
                s,
                user_id=pg.user_a,
                ctx=_ctx_all(pg.user_a),
                loop_id=pg.loop_a1,
                is_followed=True,
                expected_revision=0,
                request_id="iso-a-1",
            )
            await put_preference(
                s,
                user_id=pg.user_a,
                key="default_home",
                payload={"path": "/cockpit"},
                expected_revision=0,
                request_id="iso-a-2",
            )
            await put_work_resume(
                s,
                user_id=pg.user_a,
                ctx=_ctx_all(pg.user_a),
                kind="loop",
                resource_id=pg.loop_a1,
                context={"section": "diagnosis"},
                expected_revision=0,
                generation=1,
                request_id="iso-a-3",
            )

        async with pg.sf() as s:
            follows_b = await list_loop_follows(
                s, user_id=pg.user_b, ctx=_ctx_all(pg.user_b), page=1, page_size=20
            )
            prefs_b = await list_preferences(s, user_id=pg.user_b)
            resume_b = await list_work_resume(s, user_id=pg.user_b, ctx=_ctx_all(pg.user_b))
        assert follows_b["total"] == 0 and follows_b["items"] == []
        assert prefs_b["items"] == {}
        assert resume_b["items"] == []

        async with pg.sf() as s:
            follows_a = await list_loop_follows(
                s, user_id=pg.user_a, ctx=_ctx_all(pg.user_a), page=1, page_size=20
            )
        assert follows_a["total"] == 1
        assert follows_a["items"][0]["tag_name"].startswith("TIC-P605A-")

    # ------------------------------------------------------------------
    # CAS 409 + requestId 幂等 + 取消墓碑 + 迟到旧关注拒绝
    # ------------------------------------------------------------------

    async def test_follow_cas_replay_tombstone(self, pg):
        uid, lid = pg.user_a, pg.loop_a1
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            r1 = await set_loop_follow(
                s,
                user_id=uid,
                ctx=ctx,
                loop_id=lid,
                is_followed=True,
                expected_revision=0,
                request_id="f-1",
            )
            assert r1["revision"] == 1 and r1["is_followed"] is True and r1["replayed"] is False

            # 同 requestId 重试 → 幂等返回已有结果，不推进版本
            replay = await set_loop_follow(
                s,
                user_id=uid,
                ctx=ctx,
                loop_id=lid,
                is_followed=True,
                expected_revision=0,
                request_id="f-1",
            )
            assert replay["replayed"] is True and replay["revision"] == 1

            # 过期版本（新 requestId）→ 409 携当前版本
            with pytest.raises(BizError) as stale:
                await set_loop_follow(
                    s,
                    user_id=uid,
                    ctx=ctx,
                    loop_id=lid,
                    is_followed=True,
                    expected_revision=0,
                    request_id="f-2",
                )
            assert stale.value.status_code == 409
            assert stale.value.data["currentRevision"] == 1

            # 取消关注 → 版本墓碑（行不物理删除）
            r2 = await set_loop_follow(
                s,
                user_id=uid,
                ctx=ctx,
                loop_id=lid,
                is_followed=False,
                expected_revision=1,
                request_id="f-3",
            )
            assert r2["revision"] == 2 and r2["is_followed"] is False

        # 墓碑行仍在库中
        from app.models.personal_workspace import UserLoopFollow

        async with pg.sf() as s:
            row = (
                await s.execute(
                    select(UserLoopFollow).where(
                        UserLoopFollow.user_id == uid, UserLoopFollow.loop_id == lid
                    )
                )
            ).scalar_one()
            assert row.is_followed is False and row.revision == 2

        async with pg.sf() as s:
            # 取消后迟到的旧关注请求（旧版本号）→ 409 不得复活
            with pytest.raises(BizError) as late_old:
                await set_loop_follow(
                    s,
                    user_id=uid,
                    ctx=ctx,
                    loop_id=lid,
                    is_followed=True,
                    expected_revision=1,
                    request_id="f-4",
                )
            assert late_old.value.status_code == 409
            assert late_old.value.data["currentRevision"] == 2

            # 取消后迟到的"当作新建"请求（expectedRevision=0）→ 墓碑拦截 409
            with pytest.raises(BizError) as late_new:
                await set_loop_follow(
                    s,
                    user_id=uid,
                    ctx=ctx,
                    loop_id=lid,
                    is_followed=True,
                    expected_revision=0,
                    request_id="f-5",
                )
            assert late_new.value.status_code == 409

            # 携带当前版本重新关注 → 正常复活（用户显式新操作）
            r3 = await set_loop_follow(
                s,
                user_id=uid,
                ctx=ctx,
                loop_id=lid,
                is_followed=True,
                expected_revision=2,
                request_id="f-6",
            )
            assert r3["revision"] == 3 and r3["is_followed"] is True

            follows = await list_loop_follows(s, user_id=uid, ctx=ctx, page=1, page_size=20)
            assert follows["total"] == 1

    async def test_follow_loop_not_found_and_scope_forbidden(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            # 不存在的回路 → 404
            with pytest.raises(BizError) as nf:
                await set_loop_follow(
                    s,
                    user_id=uid,
                    ctx=_ctx_all(uid),
                    loop_id=str(uuid4()),
                    is_followed=True,
                    expected_revision=0,
                    request_id="nf-1",
                )
            assert nf.value.status_code == 404
            assert nf.value.code == "ERR_LOOP_NOT_FOUND"

            # scope 外回路 → 403 通用拒绝（不回显对象详情）
            with pytest.raises(BizError) as denied:
                await set_loop_follow(
                    s,
                    user_id=uid,
                    ctx=_ctx_unit_b_only(uid, pg.unit_b),
                    loop_id=pg.loop_a1,
                    is_followed=True,
                    expected_revision=0,
                    request_id="nf-2",
                )
            assert denied.value.status_code == 403
            assert denied.value.code == "ERR_SCOPE_FORBIDDEN"

    async def test_concurrent_first_create_unique_constraint(self, pg):
        """并发首建由唯一约束裁决：恰一方成功 rev=1，败方 409 携当前版本."""
        uid, lid = pg.user_a, pg.loop_a1

        async def _attempt(req_id: str):
            async with pg.sf() as s:
                return await set_loop_follow(
                    s,
                    user_id=uid,
                    ctx=_ctx_all(uid),
                    loop_id=lid,
                    is_followed=True,
                    expected_revision=0,
                    request_id=req_id,
                )

        results = await asyncio.gather(_attempt("cc-1"), _attempt("cc-2"), return_exceptions=True)
        oks = [r for r in results if not isinstance(r, Exception)]
        conflicts = [r for r in results if isinstance(r, BizError)]
        assert len(oks) == 1 and oks[0]["revision"] == 1
        assert len(conflicts) == 1 and conflicts[0].status_code == 409
        assert conflicts[0].data["currentRevision"] == 1

    async def test_unique_constraint_db_level(self, pg):
        """(user_id, loop_id) 重复插入被数据库拒绝（重试不重复关系的硬保证）."""
        from app.models.personal_workspace import UserLoopFollow

        async with pg.sf() as s:
            s.add(
                UserLoopFollow(
                    user_id=pg.user_a,
                    loop_id=pg.loop_a1,
                    is_followed=True,
                    revision=1,
                    last_request_id="uq-1",
                )
            )
            await s.commit()
        async with pg.sf() as s:
            s.add(
                UserLoopFollow(
                    user_id=pg.user_a,
                    loop_id=pg.loop_a1,
                    is_followed=True,
                    revision=1,
                    last_request_id="uq-2",
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()

    # ------------------------------------------------------------------
    # 偏好：按 key 独立版本 + 白名单 + CAS + 幂等
    # ------------------------------------------------------------------

    async def test_preference_independent_keys_and_cas(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            home = await put_preference(
                s,
                user_id=uid,
                key="default_home",
                payload={"path": "/cockpit"},
                expected_revision=0,
                request_id="p-1",
            )
            assert home["revision"] == 1

            presets = await put_preference(
                s,
                user_id=uid,
                key="filter_presets",
                payload={"monitor": {"status": ["OPEN"]}},
                expected_revision=0,
                request_id="p-2",
            )
            assert presets["revision"] == 1

            # 同 requestId 重放
            replay = await put_preference(
                s,
                user_id=uid,
                key="filter_presets",
                payload={"monitor": {"status": ["OPEN"]}},
                expected_revision=0,
                request_id="p-2",
            )
            assert replay["replayed"] is True and replay["revision"] == 1

            # 更新 default_home 不影响 filter_presets（按 key 独立版本）
            home2 = await put_preference(
                s,
                user_id=uid,
                key="default_home",
                payload={"path": "/monitor/loops"},
                expected_revision=1,
                request_id="p-3",
            )
            assert home2["revision"] == 2

            # 过期版本 409 携当前版本
            with pytest.raises(BizError) as stale:
                await put_preference(
                    s,
                    user_id=uid,
                    key="default_home",
                    payload={"path": "/cockpit"},
                    expected_revision=1,
                    request_id="p-4",
                )
            assert stale.value.status_code == 409
            assert stale.value.data["currentRevision"] == 2

            listed = await list_preferences(s, user_id=uid)
            assert listed["items"]["default_home"]["payload"] == {"path": "/monitor/loops"}
            assert listed["items"]["default_home"]["revision"] == 2
            assert listed["items"]["filter_presets"]["revision"] == 1
            # 服务端保留键不对外返回
            assert "resume_generation" not in listed["items"]

    # ------------------------------------------------------------------
    # 最近继续：generation 清空栅栏
    # ------------------------------------------------------------------

    async def test_resume_generation_fence(self, pg):
        uid, lid = pg.user_a, pg.loop_a1
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            listed0 = await list_work_resume(s, user_id=uid, ctx=ctx)
            assert listed0["current_generation"] == 1

            r1 = await put_work_resume(
                s,
                user_id=uid,
                ctx=ctx,
                kind="loop",
                resource_id=lid,
                context={"section": "diagnosis", "window": {"hours": 24}},
                expected_revision=0,
                generation=1,
                request_id="w-1",
            )
            assert r1["revision"] == 1 and r1["generation"] == 1

            replay = await put_work_resume(
                s,
                user_id=uid,
                ctx=ctx,
                kind="loop",
                resource_id=lid,
                context={"section": "diagnosis", "window": {"hours": 24}},
                expected_revision=0,
                generation=1,
                request_id="w-1",
            )
            assert replay["replayed"] is True and replay["revision"] == 1

            # 清空：先原子增代际再清旧代
            cleared = await clear_work_resume(s, user_id=uid)
            assert cleared["current_generation"] == 2
            assert cleared["cleared_count"] == 1

            listed1 = await list_work_resume(s, user_id=uid, ctx=ctx)
            assert listed1["items"] == [] and listed1["current_generation"] == 2

            # 旧代迟到写入 → 409 不得复活
            with pytest.raises(BizError) as stale_put:
                await put_work_resume(
                    s,
                    user_id=uid,
                    ctx=ctx,
                    kind="loop",
                    resource_id=lid,
                    context={"section": "tuning"},
                    expected_revision=0,
                    generation=1,
                    request_id="w-2",
                )
            assert stale_put.value.status_code == 409
            assert stale_put.value.code == "ERR_GENERATION_STALE"
            assert stale_put.value.data["currentGeneration"] == 2

            # 新代写入 → 正常
            r2 = await put_work_resume(
                s,
                user_id=uid,
                ctx=ctx,
                kind="loop",
                resource_id=lid,
                context={"section": "tuning"},
                expected_revision=0,
                generation=2,
                request_id="w-3",
            )
            assert r2["revision"] == 1 and r2["generation"] == 2

            # 旧代迟到单删 → 409
            with pytest.raises(BizError) as stale_del:
                await delete_work_resume(s, user_id=uid, kind="loop", resource_id=lid, generation=1)
            assert stale_del.value.status_code == 409

            # 新代单删 → 版本墓碑（保留版本链）
            deleted = await delete_work_resume(
                s, user_id=uid, kind="loop", resource_id=lid, generation=2
            )
            assert deleted["deleted"] is True and deleted["revision"] == 2

            # 重复删除幂等
            again = await delete_work_resume(
                s, user_id=uid, kind="loop", resource_id=lid, generation=2
            )
            assert again["deleted"] is True and again["revision"] == 2

            # 迟到旧版本写 → 409（墓碑防复活）
            with pytest.raises(BizError) as late:
                await put_work_resume(
                    s,
                    user_id=uid,
                    ctx=ctx,
                    kind="loop",
                    resource_id=lid,
                    context={"section": "handling"},
                    expected_revision=1,
                    generation=2,
                    request_id="w-4",
                )
            assert late.value.status_code == 409
            assert late.value.data["currentRevision"] == 2

            listed2 = await list_work_resume(s, user_id=uid, ctx=ctx)
            assert listed2["items"] == []

    async def test_resume_clear_isolated_per_user(self, pg):
        """清空只影响本人：A 清空后 B 的最近继续仍在、B 的代际不变."""
        async with pg.sf() as s:
            await put_work_resume(
                s,
                user_id=pg.user_a,
                ctx=_ctx_all(pg.user_a),
                kind="loop",
                resource_id=pg.loop_a1,
                context={"section": "diagnosis"},
                expected_revision=0,
                generation=1,
                request_id="c-a",
            )
            await put_work_resume(
                s,
                user_id=pg.user_b,
                ctx=_ctx_all(pg.user_b),
                kind="loop",
                resource_id=pg.loop_b1,
                context={"section": "tuning"},
                expected_revision=0,
                generation=1,
                request_id="c-b",
            )
            await clear_work_resume(s, user_id=pg.user_a)

            resume_b = await list_work_resume(s, user_id=pg.user_b, ctx=_ctx_all(pg.user_b))
            assert len(resume_b["items"]) == 1
            assert resume_b["current_generation"] == 1

    # ------------------------------------------------------------------
    # 撤权不泄露
    # ------------------------------------------------------------------

    async def test_revocation_no_leak(self, pg):
        """撤权（scope 收窄）后：关系保留但不返回名称/状态/证据."""
        uid, lid = pg.user_a, pg.loop_a1
        ctx_all = _ctx_all(uid)
        ctx_revoked = _ctx_unit_b_only(uid, pg.unit_b)
        async with pg.sf() as s:
            await set_loop_follow(
                s,
                user_id=uid,
                ctx=ctx_all,
                loop_id=lid,
                is_followed=True,
                expected_revision=0,
                request_id="rv-1",
            )
            await put_work_resume(
                s,
                user_id=uid,
                ctx=ctx_all,
                kind="loop",
                resource_id=lid,
                context={"section": "diagnosis", "evidenceRefs": ["kpi:123"]},
                expected_revision=0,
                generation=1,
                request_id="rv-2",
            )

            visible = await list_loop_follows(s, user_id=uid, ctx=ctx_all, page=1, page_size=20)
            assert visible["total"] == 1 and visible["read_status"] == "COMPLETE"

            hidden = await list_loop_follows(s, user_id=uid, ctx=ctx_revoked, page=1, page_size=20)
            assert hidden["total"] == 0 and hidden["items"] == []
            assert hidden["inaccessible_count"] == 1
            assert hidden["read_status"] == "PARTIAL"
            # 不泄露回路名称/描述
            assert "TIC-P605A" not in json.dumps(hidden, ensure_ascii=False, default=str)

            resume_hidden = await list_work_resume(s, user_id=uid, ctx=ctx_revoked)
            assert resume_hidden["items"] == []
            assert resume_hidden["inaccessible_count"] == 1
            # 不泄露最近继续 context（证据引用）
            assert "evidenceRefs" not in json.dumps(resume_hidden, ensure_ascii=False, default=str)

            # 撤权后写入同回路 → 403；关系本身保留（恢复 scope 仍可见）
            with pytest.raises(BizError) as denied:
                await put_work_resume(
                    s,
                    user_id=uid,
                    ctx=ctx_revoked,
                    kind="loop",
                    resource_id=lid,
                    context={"section": "tuning"},
                    expected_revision=1,
                    generation=1,
                    request_id="rv-3",
                )
            assert denied.value.status_code == 403

            restored = await list_loop_follows(s, user_id=uid, ctx=ctx_all, page=1, page_size=20)
            assert restored["total"] == 1

    async def test_resume_write_needs_existing_object(self, pg):
        """失败打开不能生成最近项：不存在的回路 404，不制造条目."""
        uid = pg.user_a
        ghost = str(uuid4())
        async with pg.sf() as s:
            with pytest.raises(BizError) as nf:
                await put_work_resume(
                    s,
                    user_id=uid,
                    ctx=_ctx_all(uid),
                    kind="loop",
                    resource_id=ghost,
                    context={"section": "diagnosis"},
                    expected_revision=0,
                    generation=1,
                    request_id="gx-1",
                )
            assert nf.value.status_code == 404
            listed = await list_work_resume(s, user_id=uid, ctx=_ctx_all(uid))
            assert listed["items"] == []


# ===========================================================================
# HTTP 级端到端（真实 PG + 路由接线 + camelCase 契约）
# ===========================================================================


@pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）")
@pytest.mark.integration
def test_http_end_to_end_me_endpoints(pg_sync_seed):
    """六端点路由接线 + camelCase 请求/响应 + 401 越权参数无效."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_scope_ctx
    from app.core.db import get_db
    from app.main import app

    seed = pg_sync_seed
    sf = seed["session_factory"]

    async def _override_db():
        async with sf() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    user = MagicMock()
    user.id = seed["user_a"]
    user.username = "p605-http"
    user.role = "IC_ENGINEER"

    async def _override_user():
        return user

    async def _override_ctx():
        return _ctx_all(seed["user_a"])

    fake_redis = FakeRedis()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_scope_ctx] = _override_ctx
    try:
        with (
            patch("app.core.redis.redis_client", fake_redis),
            patch("app.middleware.idempotency.redis_client", fake_redis),
            patch("app.middleware.rate_limit.redis_client", fake_redis),
            TestClient(app) as client,
        ):
            loop_id = seed["loop_a1"]

            # 1. PUT 关注（camelCase 请求体）
            resp = client.put(
                f"/api/v1/me/loop-follows/{loop_id}",
                json={"isFollowed": True, "expectedRevision": 0, "requestId": "http-1"},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["isFollowed"] is True and data["revision"] == 1
            assert "updatedAt" in data  # camelCase 响应契约

            # 2. GET 关注列表（携带自由 userId 参数也无效——只认登录主体）
            resp = client.get(f"/api/v1/me/loop-follows?userId={uuid4()}")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 1
            assert data["items"][0]["tagName"].startswith("TIC-P605A-")
            assert data["readStatus"] == "COMPLETE"
            assert "readAt" in data

            # 3. 过期版本 409 携当前版本
            resp = client.put(
                f"/api/v1/me/loop-follows/{loop_id}",
                json={"isFollowed": False, "expectedRevision": 0, "requestId": "http-2"},
            )
            assert resp.status_code == 409
            assert resp.json()["data"]["currentRevision"] == 1

            # 4. 偏好读写
            resp = client.put(
                "/api/v1/me/workspace-preferences/default_home",
                json={
                    "payload": {"path": "/cockpit"},
                    "expectedRevision": 0,
                    "requestId": "http-3",
                },
            )
            assert resp.status_code == 200
            resp = client.get("/api/v1/me/workspace-preferences")
            assert resp.status_code == 200
            items = resp.json()["data"]["items"]
            assert items["default_home"]["payload"] == {"path": "/cockpit"}
            assert "resume_generation" not in items

            # 5. 最近继续：读代际 → 写入 → 单删 → 清空
            resp = client.get("/api/v1/me/work-resume")
            assert resp.status_code == 200
            gen = resp.json()["data"]["currentGeneration"]
            resp = client.put(
                f"/api/v1/me/work-resume/loop/{loop_id}",
                json={
                    "context": {"section": "diagnosis"},
                    "expectedRevision": 0,
                    "generation": gen,
                    "requestId": "http-4",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["generation"] == gen

            resp = client.delete(f"/api/v1/me/work-resume/loop/{loop_id}?generation={gen}")
            assert resp.status_code == 200
            assert resp.json()["data"]["deleted"] is True

            resp = client.delete("/api/v1/me/work-resume")
            assert resp.status_code == 200
            cleared = resp.json()["data"]
            assert cleared["currentGeneration"] == gen + 1

            # 旧代迟到写 → 409
            resp = client.put(
                f"/api/v1/me/work-resume/loop/{loop_id}",
                json={
                    "context": {"section": "tuning"},
                    "expectedRevision": 0,
                    "generation": gen,
                    "requestId": "http-5",
                },
            )
            assert resp.status_code == 409
            assert resp.json()["code"] == "ERR_GENERATION_STALE"

            # 不存在回路 → 404
            resp = client.put(
                f"/api/v1/me/loop-follows/{uuid4()}",
                json={"isFollowed": True, "expectedRevision": 0, "requestId": "http-6"},
            )
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def pg_sync_seed():
    """HTTP 端到端种子（同步 fixture；复用与 pg 相同结构，收尾清行）."""
    if not _pg_reachable():
        pytest.skip("未显式提供 TEST_DATABASE_URL（scratch PG）")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base
    from app.models.loop import LoopLedger
    from app.models.personal_workspace import (
        UserLoopFollow,
        UserWorkResume,
        UserWorkspacePreference,
    )
    from app.models.plant_node import PlantNode
    from app.models.sys_user import SysUser

    engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"timezone": "UTC"}},
        poolclass=NullPool,
    )
    sf = async_sessionmaker(engine, expire_on_commit=False)

    suffix = uuid4().hex[:8]
    ids = {
        "user_a": str(uuid4()),
        "factory": str(uuid4()),
        "area": str(uuid4()),
        "unit_a": str(uuid4()),
        "loop_a1": str(uuid4()),
    }

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            s.add(
                SysUser(
                    id=ids["user_a"],
                    username=f"p605http-{suffix}",
                    password_hash="p605-test",
                    display_name=f"p605http-{suffix}",
                    role="IC_ENGINEER",
                    is_active=True,
                )
            )
            await s.commit()
            for key, name, ntype, parent in (
                ("factory", f"P605H-F-{suffix}", "FACTORY", None),
                ("area", f"P605H-A-{suffix}", "AREA", ids["factory"]),
                ("unit_a", f"P605H-UA-{suffix}", "UNIT", ids["area"]),
            ):
                s.add(PlantNode(id=ids[key], name=name, type=ntype, parent_id=parent))
                await s.commit()
            s.add(
                LoopLedger(
                    id=ids["loop_a1"],
                    tag_name=f"TIC-P605A-{suffix}",
                    unit_id=ids["unit_a"],
                    is_active=True,
                )
            )
            await s.commit()

    async def _clean():
        async with sf() as s:
            uid = ids["user_a"]
            await s.execute(sa_delete(UserWorkResume).where(UserWorkResume.user_id == uid))
            await s.execute(sa_delete(UserLoopFollow).where(UserLoopFollow.user_id == uid))
            await s.execute(
                sa_delete(UserWorkspacePreference).where(UserWorkspacePreference.user_id == uid)
            )
            await s.execute(sa_delete(LoopLedger).where(LoopLedger.id == ids["loop_a1"]))
            for nid in (ids["unit_a"], ids["area"], ids["factory"]):
                await s.execute(sa_delete(PlantNode).where(PlantNode.id == nid))
            await s.execute(sa_delete(SysUser).where(SysUser.id == uid))
            await s.commit()
        await engine.dispose()

    asyncio.run(_seed())
    ids["session_factory"] = sf
    try:
        yield ids
    finally:
        asyncio.run(_clean())
