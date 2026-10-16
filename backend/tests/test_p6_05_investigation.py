"""P6-05 后续分片一（E1）回路核查最小实体测试.

两层覆盖（对应任务书验证清单）：

1. **纯单测**（默认 ``-m 'not integration'`` 即跑，CLPM_TEST_MODE=1 由
   conftest 保证）：来源类型白名单；请求体无"按姓名分派"通道（旧姓名
   只作显示不作分派身份）；未登录 401、账户停用 403、非法入参
   400/404/422（端点级，校验在 DB 访问前触发）。
2. **真实 PG 集成**（``-m integration`` + 显式 ``TEST_DATABASE_URL``
   指向一次性 scratch 库，缺省跳过——防误写共享 dev 库）：阶段机非法
   迁移 422、来源+问题身份去重（不仅 loopId）、并发认领 revision CAS
   409、移交双记录原子（失败整体回滚）、取消≠回路正常（取消不阻塞同
   问题再现/不触碰回路状态）、撤权不泄露、旧姓名改名/删除后显示与
   待确认责任派生、WAITING 三要素 DB CHECK 兜底、HTTP 级端到端
   （camelCase 契约 + 路由接线）。

运行方式（实验/验收时）：
    cd backend
    uv run pytest tests/test_p6_05_investigation.py -q                 # 纯单测
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_test_p6 \
    uv run pytest tests/test_p6_05_investigation.py -v -m integration  # 集成
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
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.core.exceptions import BizError
from app.schemas.loop_investigation import InvestigationHandoffRequest
from app.services.loop_investigation import (
    claim_investigation,
    close_investigation,
    create_investigation,
    get_investigation,
    handoff_investigation,
    list_loop_investigations,
    resume_investigation,
    validate_source_kind,
    wait_investigation,
)
from tests.conftest import FakeRedis, mock_current_user

# ===========================================================================
# 纯单测：校验器与分派身份红线
# ===========================================================================


class TestValidators:
    """来源白名单 / 分派身份只认稳定 sys_user.id（不落 DB）."""

    def test_source_kind_whitelist(self):
        assert validate_source_kind("MANUAL") == "MANUAL"
        assert validate_source_kind("alert_event") == "ALERT_EVENT"  # 归一化大写
        assert validate_source_kind("DIAGNOSIS_RUN") == "DIAGNOSIS_RUN"
        assert validate_source_kind("ASSESSMENT") == "ASSESSMENT"

    def test_source_kind_unknown_400(self):
        with pytest.raises(BizError) as exc:
            validate_source_kind("HANDOFF")  # HANDOFF 不对客户端开放
        assert exc.value.status_code == 400
        assert exc.value.code == "ERR_INVESTIGATION_SOURCE_INVALID"
        with pytest.raises(BizError):
            validate_source_kind("random")

    def test_handoff_request_has_no_name_assignment_channel(self):
        """移交请求体没有"按姓名分派"字段：多余 ownerName 被忽略，不生效."""
        req = InvestigationHandoffRequest(
            expectedRevision=1,
            requestId="r-1",
            newOwnerId=str(uuid4()),
            ownerName="张三",  # 额外字段：忽略而非成为分派身份
        )
        dumped = req.model_dump()
        assert "owner_name" not in dumped and "ownerName" not in dumped


# ===========================================================================
# 纯单测：端点认证/校验（TestClient + mock DB；错误路径均先于 DB 访问触发）
# ===========================================================================

_LOOPS_URL = "/api/v1/loops"
_INV_URL = "/api/v1/investigations"


def _create_body(**over):
    body = {
        "sourceKind": "MANUAL",
        "problemKey": "oscillation",
        "title": "回路振荡待核查",
        "requestId": "r-1",
    }
    body.update(over)
    return body


class TestEndpointAuthAndValidation:
    """未登录 401 / 停用 403 / 非法入参 400·404·422（先于 DB 访问）."""

    def test_create_unauthenticated_401(self, client):
        resp = client.post(f"{_LOOPS_URL}/{uuid4()}/investigations", json=_create_body())
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"

    def test_list_unauthenticated_401(self, client):
        assert client.get(f"{_LOOPS_URL}/{uuid4()}/investigations").status_code == 401

    def test_detail_unauthenticated_401(self, client):
        assert client.get(f"{_INV_URL}/{uuid4()}").status_code == 401

    def test_claim_unauthenticated_401(self, client):
        resp = client.post(
            f"{_INV_URL}/{uuid4()}/claim",
            json={"expectedRevision": 1, "requestId": "r-1"},
        )
        assert resp.status_code == 401

    def test_disabled_account_403(self, client, mock_db):
        """账户停用拒绝访问（get_current_user 既有红线，核查端点继承）."""
        from app.core.security import create_access_token
        from tests.conftest import make_db_execute_return

        disabled = MagicMock()
        disabled.id = str(uuid4())
        disabled.username = "disabled_p605e1"
        disabled.role = "IC_ENGINEER"
        disabled.is_active = False
        token, _, _ = create_access_token(
            subject=disabled.id, username=disabled.username, role=disabled.role
        )
        mock_db.execute.return_value = make_db_execute_return(disabled)
        resp = client.post(
            f"{_LOOPS_URL}/{uuid4()}/investigations",
            json=_create_body(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_ACCOUNT_DISABLED"

    def test_create_missing_fields_422(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_LOOPS_URL}/{uuid4()}/investigations",
                json={"sourceKind": "MANUAL"},  # 缺 problemKey/title/requestId
            )
        assert resp.status_code == 422

    def test_create_unknown_source_kind_400(self, client, admin_user):
        """来源类型白名单在 DB 访问前校验."""
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_LOOPS_URL}/{uuid4()}/investigations",
                json=_create_body(sourceKind="HANDOFF"),
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVESTIGATION_SOURCE_INVALID"

    def test_create_non_manual_missing_source_id_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_LOOPS_URL}/{uuid4()}/investigations",
                json=_create_body(sourceKind="ALERT_EVENT"),  # 缺 sourceId
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVESTIGATION_SOURCE_INVALID"

    def test_create_malformed_loop_id_404(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_LOOPS_URL}/not-a-uuid/investigations",
                json=_create_body(),
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_detail_malformed_id_404(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.get(f"{_INV_URL}/not-a-uuid")
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_INVESTIGATION_NOT_FOUND"

    def test_claim_malformed_id_404(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_INV_URL}/not-a-uuid/claim",
                json={"expectedRevision": 1, "requestId": "r-1"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_INVESTIGATION_NOT_FOUND"

    def test_claim_missing_fields_422(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.post(f"{_INV_URL}/{uuid4()}/claim", json={})
        assert resp.status_code == 422

    def test_wait_missing_three_elements_422(self, client, admin_user):
        """WAITING 三要素 schema 必填（缺复查条件/责任）."""
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_INV_URL}/{uuid4()}/wait",
                json={
                    "expectedRevision": 2,
                    "requestId": "r-1",
                    "waitReason": "等待工艺确认",
                },
            )
        assert resp.status_code == 422

    def test_close_direct_handed_off_422(self, client, admin_user):
        """HANDED_OFF 只能经 /handoff 双记录路径，close 直传 422."""
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_INV_URL}/{uuid4()}/close",
                json={
                    "expectedRevision": 2,
                    "requestId": "r-1",
                    "outcome": "HANDED_OFF",
                },
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_INVESTIGATION_STAGE_INVALID"

    def test_close_invalid_outcome_422(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_INV_URL}/{uuid4()}/close",
                json={
                    "expectedRevision": 2,
                    "requestId": "r-1",
                    "outcome": "LOOP_NORMAL",  # 非法结果（也不允许借结束表达"回路正常"）
                },
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_INVESTIGATION_OUTCOME_INVALID"

    def test_close_cancel_without_note_422(self, client, admin_user):
        """取消必须填理由（取消≠回路正常，理由强制留痕）."""
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_INV_URL}/{uuid4()}/close",
                json={
                    "expectedRevision": 2,
                    "requestId": "r-1",
                    "outcome": "CANCELLED",
                },
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_INVESTIGATION_OUTCOME_INVALID"

    def test_handoff_malformed_new_owner_id_400(self, client, admin_user):
        """接手人只接受稳定 sys_user.id（UUID），姓名字符串 400."""
        with mock_current_user(admin_user):
            resp = client.post(
                f"{_INV_URL}/{uuid4()}/handoff",
                json={
                    "expectedRevision": 2,
                    "requestId": "r-1",
                    "newOwnerId": "张三",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVESTIGATION_TARGET_INVALID"


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
    """真实 PG（scratch 库）：建表 + 四用户/工厂树/两回路种子，收尾清行."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base
    from app.models.loop import LoopLedger
    from app.models.loop_investigation import LoopInvestigation
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
        "user_c": str(uuid4()),
        "user_d": str(uuid4()),
        "factory": str(uuid4()),
        "area": str(uuid4()),
        "unit_a": str(uuid4()),
        "unit_b": str(uuid4()),
        "loop_a1": str(uuid4()),
        "loop_b1": str(uuid4()),
    }

    async with sf() as s:
        for key, name, role, active in (
            ("user_a", f"p605e1a-{suffix}", "IC_ENGINEER", True),
            ("user_b", f"p605e1b-{suffix}", "PE_ENGINEER", True),
            ("user_c", f"p605e1c-{suffix}", "EXPERT", True),
            ("user_d", f"p605e1d-{suffix}", "IC_ENGINEER", False),
        ):
            s.add(
                SysUser(
                    id=ids[key],
                    username=name,
                    password_hash="p605e1-test",
                    display_name=name,
                    role=role,
                    is_active=active,
                )
            )
            await s.commit()
        for key, name, ntype, parent in (
            ("factory", f"P605E1-F-{suffix}", "FACTORY", None),
            ("area", f"P605E1-A-{suffix}", "AREA", ids["factory"]),
            ("unit_a", f"P605E1-UA-{suffix}", "UNIT", ids["area"]),
            ("unit_b", f"P605E1-UB-{suffix}", "UNIT", ids["area"]),
        ):
            s.add(PlantNode(id=ids[key], name=name, type=ntype, parent_id=parent))
            await s.commit()
        for key, tag, unit in (
            ("loop_a1", f"TIC-P605E1A-{suffix}", ids["unit_a"]),
            ("loop_b1", f"TIC-P605E1B-{suffix}", ids["unit_b"]),
        ):
            s.add(LoopLedger(id=ids[key], tag_name=tag, unit_id=unit, is_active=True))
            await s.commit()

    try:
        yield SimpleNamespace(sf=sf, engine=engine, **ids)
    finally:
        async with sf() as s:
            for lid in (ids["loop_a1"], ids["loop_b1"]):
                await s.execute(
                    sa_delete(LoopInvestigation).where(LoopInvestigation.loop_id == lid)
                )
            for lid in (ids["loop_a1"], ids["loop_b1"]):
                await s.execute(sa_delete(LoopLedger).where(LoopLedger.id == lid))
            for nid in (ids["unit_a"], ids["unit_b"], ids["area"], ids["factory"]):
                await s.execute(sa_delete(PlantNode).where(PlantNode.id == nid))
            for uid in (ids["user_a"], ids["user_b"], ids["user_c"], ids["user_d"]):
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


async def _create_todo(s, pg, *, request_id: str, problem_key: str = "oscillation"):
    return await create_investigation(
        s,
        user_id=pg.user_a,
        ctx=_ctx_all(pg.user_a),
        loop_id=pg.loop_a1,
        source_kind="ALERT_EVENT",
        source_id="alert-001",
        problem_key=problem_key,
        title="回路振荡待核查",
        detail=None,
        request_id=request_id,
    )


class TestRealPG:  # noqa: D101
    pytestmark = _REQUIRES_PG

    # ------------------------------------------------------------------
    # 去重：来源身份+问题身份（不仅 loopId）
    # ------------------------------------------------------------------

    async def test_create_dedup_by_source_and_problem_identity(self, pg):
        async with pg.sf() as s:
            r1 = await _create_todo(s, pg, request_id="d-1")
            assert r1["deduped"] is False and r1["replayed"] is False
            assert r1["item"]["stage"] == "TODO" and r1["item"]["revision"] == 1
            assert r1["item"]["owner_id"] is None  # 新建未认领

            # 同 requestId 重试 → 幂等
            replay = await _create_todo(s, pg, request_id="d-1")
            assert replay["replayed"] is True
            assert replay["item"]["id"] == r1["item"]["id"]

            # 同来源+同问题（新 requestId）→ 去重返回既有记录，不新建
            dup = await _create_todo(s, pg, request_id="d-2")
            assert dup["deduped"] is True
            assert dup["item"]["id"] == r1["item"]["id"]

            # 同回路不同问题身份 → 独立记录（不仅按 loopId 合并）
            other = await _create_todo(s, pg, request_id="d-3", problem_key="stiction")
            assert other["deduped"] is False
            assert other["item"]["id"] != r1["item"]["id"]

            # 同问题不同来源身份 → 独立记录
            other_src = await create_investigation(
                s,
                user_id=pg.user_a,
                ctx=_ctx_all(pg.user_a),
                loop_id=pg.loop_a1,
                source_kind="DIAGNOSIS_RUN",
                source_id="run-001",
                problem_key="oscillation",
                title="诊断来源振荡",
                detail=None,
                request_id="d-4",
            )
            assert other_src["deduped"] is False

            listed = await list_loop_investigations(
                s, ctx=_ctx_all(pg.user_a), loop_id=pg.loop_a1, stage=None, page=1, page_size=20
            )
            assert listed["total"] == 3
            todos = await list_loop_investigations(
                s,
                ctx=_ctx_all(pg.user_a),
                loop_id=pg.loop_a1,
                stage="TODO",
                page=1,
                page_size=20,
            )
            assert todos["total"] == 3
            closed = await list_loop_investigations(
                s,
                ctx=_ctx_all(pg.user_a),
                loop_id=pg.loop_a1,
                stage="CLOSED",
                page=1,
                page_size=20,
            )
            assert closed["total"] == 0

    async def test_active_identity_unique_index_db_level(self, pg):
        """未结束记录同身份部分唯一索引：DB 级硬保证（并发去重底线）."""
        from app.models.loop_investigation import LoopInvestigation

        async with pg.sf() as s:
            await _create_todo(s, pg, request_id="uq-1")
            s.add(
                LoopInvestigation(
                    id=str(uuid4()),
                    loop_id=pg.loop_a1,
                    source_kind="ALERT_EVENT",
                    source_id="alert-001",
                    problem_key="oscillation",
                    title="并发抢建",
                    stage="TODO",
                    revision=1,
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()

    async def test_concurrent_create_dedup(self, pg):
        """并发首建同身份：恰一方新建，败方命中去重返回同一记录."""

        async def _attempt(req_id: str):
            async with pg.sf() as s:
                return await _create_todo(s, pg, request_id=req_id)

        r1, r2 = await asyncio.gather(_attempt("cc-1"), _attempt("cc-2"))
        assert r1["item"]["id"] == r2["item"]["id"]
        flags = sorted([r1["deduped"], r2["deduped"]])
        assert flags == [False, True]  # 一方新建一方去重
        async with pg.sf() as s:
            listed = await list_loop_investigations(
                s, ctx=_ctx_all(pg.user_a), loop_id=pg.loop_a1, stage=None, page=1, page_size=20
            )
            assert listed["total"] == 1

    # ------------------------------------------------------------------
    # 阶段机：非法迁移 422
    # ------------------------------------------------------------------

    async def test_stage_machine_illegal_transitions_422(self, pg):
        uid = pg.user_a
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            created = await _create_todo(s, pg, request_id="sm-1")
            inv = created["item"]

            # TODO 直接等待 → 422
            with pytest.raises(BizError) as e1:
                await wait_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx,
                    investigation_id=inv["id"],
                    expected_revision=1,
                    request_id="sm-2",
                    wait_reason="等工艺",
                    wait_review_condition="明早复查",
                    wait_responsible="工艺班组",
                )
            assert e1.value.status_code == 422
            assert e1.value.code == "ERR_INVESTIGATION_STAGE_INVALID"

            # TODO 直接恢复 → 422
            with pytest.raises(BizError) as e2:
                await resume_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx,
                    investigation_id=inv["id"],
                    expected_revision=1,
                    request_id="sm-3",
                )
            assert e2.value.status_code == 422

            # TODO 移交（未认领无责任可交）→ 422
            with pytest.raises(BizError) as e3:
                await handoff_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx,
                    investigation_id=inv["id"],
                    expected_revision=1,
                    request_id="sm-4",
                    new_owner_id=pg.user_c,
                    note=None,
                )
            assert e3.value.status_code == 422

            # 认领 → IN_PROGRESS；重复认领 → 422
            claimed = await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="sm-5",
            )
            assert claimed["item"]["stage"] == "IN_PROGRESS"
            assert claimed["item"]["owner_id"] == uid
            with pytest.raises(BizError) as e4:
                await claim_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx,
                    investigation_id=inv["id"],
                    expected_revision=2,
                    request_id="sm-6",
                )
            assert e4.value.status_code == 422

            # 结束后任何操作 → 422
            closed = await close_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="sm-7",
                outcome="ANSWERED",
                note="已答复班组",
                close_target_kind=None,
                close_target_id=None,
            )
            assert closed["item"]["stage"] == "CLOSED"
            assert closed["item"]["outcome"] == "ANSWERED"
            assert closed["item"]["closed_at"] is not None
            with pytest.raises(BizError) as e5:
                await close_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx,
                    investigation_id=inv["id"],
                    expected_revision=3,
                    request_id="sm-8",
                    outcome="ANSWERED",
                    note=None,
                    close_target_kind=None,
                    close_target_id=None,
                )
            assert e5.value.status_code == 422

    async def test_wait_resume_cycle_and_owner_enforcement(self, pg):
        uid, other = pg.user_a, pg.user_b
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="wr-1"))["item"]
            await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="wr-2",
            )
            # 非负责人等待 → 403
            with pytest.raises(BizError) as denied:
                await wait_investigation(
                    s,
                    user_id=other,
                    role="PE_ENGINEER",
                    ctx=_ctx_all(other),
                    investigation_id=inv["id"],
                    expected_revision=2,
                    request_id="wr-3",
                    wait_reason="越权等待",
                    wait_review_condition="x",
                    wait_responsible="y",
                )
            assert denied.value.status_code == 403
            assert denied.value.code == "ERR_INVESTIGATION_FORBIDDEN"

            waited = await wait_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="wr-4",
                wait_reason="等待工艺确认进料",
                wait_review_condition="次日早会复查",
                wait_responsible="工艺班组",
            )
            assert waited["item"]["stage"] == "WAITING"
            assert waited["item"]["wait_reason"] == "等待工艺确认进料"
            assert waited["item"]["wait_review_condition"] == "次日早会复查"
            assert waited["item"]["wait_responsible"] == "工艺班组"

            resumed = await resume_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=3,
                request_id="wr-5",
            )
            assert resumed["item"]["stage"] == "IN_PROGRESS"
            assert resumed["item"]["wait_reason"] is None
            assert resumed["item"]["wait_review_condition"] is None
            assert resumed["item"]["wait_responsible"] is None

    async def test_waiting_three_elements_db_check(self, pg):
        """DB CHECK 兜底：WAITING 缺三要素直接写库被拒；非 WAITING 残留要素被拒."""
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="ck-1"))["item"]
            with pytest.raises(IntegrityError):
                await s.execute(
                    text("UPDATE loop_investigation SET stage='WAITING' WHERE id=:iid"),
                    {"iid": inv["id"]},
                )
            await s.rollback()
            with pytest.raises(IntegrityError):
                await s.execute(
                    text("UPDATE loop_investigation SET wait_reason='x' WHERE id=:iid"),
                    {"iid": inv["id"]},
                )
            await s.rollback()
            # CLOSED 无结果直接写库同样被拒（结束结果 CHECK）
            with pytest.raises(IntegrityError):
                await s.execute(
                    text(
                        "UPDATE loop_investigation SET stage='CLOSED', "
                        "closed_at=now() WHERE id=:iid"
                    ),
                    {"iid": inv["id"]},
                )
            await s.rollback()

    # ------------------------------------------------------------------
    # 并发认领：revision CAS 409
    # ------------------------------------------------------------------

    async def test_concurrent_claim_cas_409(self, pg):
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="cl-0"))["item"]

        async def _attempt(uid: str, req_id: str):
            async with pg.sf() as s:
                return await claim_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=_ctx_all(uid),
                    investigation_id=inv["id"],
                    expected_revision=1,
                    request_id=req_id,
                )

        results = await asyncio.gather(
            _attempt(pg.user_a, "cl-1"),
            _attempt(pg.user_b, "cl-2"),
            return_exceptions=True,
        )
        oks = [r for r in results if not isinstance(r, Exception)]
        conflicts = [r for r in results if isinstance(r, BizError)]
        assert len(oks) == 1 and oks[0]["item"]["revision"] == 2
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409
        assert conflicts[0].code == "ERR_REVISION_CONFLICT"
        assert conflicts[0].data["currentRevision"] == 2
        assert conflicts[0].data["stage"] == "IN_PROGRESS"
        assert conflicts[0].data["ownerId"] in (pg.user_a, pg.user_b)

    async def test_claim_stale_revision_409_and_replay(self, pg):
        uid = pg.user_a
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="rp-1"))["item"]
            claimed = await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="rp-2",
            )
            assert claimed["item"]["revision"] == 2

            # 同 requestId 重放 → 幂等不推进版本
            replay = await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="rp-2",
            )
            assert replay["replayed"] is True
            assert replay["item"]["revision"] == 2

            # 过期版本（新 requestId）→ 409 携当前版本
            with pytest.raises(BizError) as stale:
                await claim_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx,
                    investigation_id=inv["id"],
                    expected_revision=1,
                    request_id="rp-3",
                )
            assert stale.value.status_code == 409
            assert stale.value.data["currentRevision"] == 2

    # ------------------------------------------------------------------
    # 移交：双记录原子
    # ------------------------------------------------------------------

    async def test_handoff_dual_record_atomic(self, pg):
        uid, successor = pg.user_a, pg.user_c
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="ho-1"))["item"]
            await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="ho-2",
            )
            result = await handoff_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="ho-3",
                new_owner_id=successor,
                note="转专家核查",
            )
            assert result["replayed"] is False
            source, target = result["source"], result["target"]
            # 源记录：CLOSED + HANDED_OFF + 目标回链
            assert source["stage"] == "CLOSED" and source["outcome"] == "HANDED_OFF"
            assert source["close_target_kind"] == "loop_investigation"
            assert source["close_target_id"] == target["id"]
            assert source["outcome_note"] == "转专家核查"
            assert source["revision"] == 3
            # 目标记录：TODO + 接续责任真实落位 + 回指源记录 + 身份延续
            assert target["stage"] == "TODO"
            assert target["owner_id"] == successor
            assert target["handoff_source_id"] == source["id"]
            assert target["source_kind"] == "ALERT_EVENT"
            assert target["source_id"] == "alert-001"
            assert target["problem_key"] == "oscillation"
            assert target["revision"] == 1
            # 移交预留：他人不可截胡认领
            with pytest.raises(BizError) as poach:
                await claim_investigation(
                    s,
                    user_id=pg.user_b,
                    role="PE_ENGINEER",
                    ctx=_ctx_all(pg.user_b),
                    investigation_id=target["id"],
                    expected_revision=1,
                    request_id="ho-4",
                )
            assert poach.value.status_code == 403
            # 接手人本人认领 → 正常
            taken = await claim_investigation(
                s,
                user_id=successor,
                role="EXPERT",
                ctx=_ctx_all(successor),
                investigation_id=target["id"],
                expected_revision=1,
                request_id="ho-5",
            )
            assert taken["item"]["stage"] == "IN_PROGRESS"
            assert taken["item"]["owner_id"] == successor

    async def test_handoff_replay_returns_same_target(self, pg):
        uid, successor = pg.user_a, pg.user_c
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="hr-1"))["item"]
            await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="hr-2",
            )
            first = await handoff_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="hr-3",
                new_owner_id=successor,
                note=None,
            )
            replay = await handoff_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="hr-3",
                new_owner_id=successor,
                note=None,
            )
            assert replay["replayed"] is True
            assert replay["target"]["id"] == first["target"]["id"]
            # 重放未制造第三条记录
            listed = await list_loop_investigations(
                s, ctx=ctx, loop_id=pg.loop_a1, stage=None, page=1, page_size=20
            )
            assert listed["total"] == 2

    async def test_handoff_invalid_target_rolls_back_atomically(self, pg):
        """接手人非法（不存在/停用/为自己）→ 400 且源记录零改动（原子性）."""
        uid = pg.user_a
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="hx-1"))["item"]
            await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="hx-2",
            )
            for bad_owner, req in (
                (str(uuid4()), "hx-3"),  # 不存在
                (pg.user_d, "hx-4"),  # 已停用
                (uid, "hx-5"),  # 为自己
            ):
                with pytest.raises(BizError) as exc:
                    await handoff_investigation(
                        s,
                        user_id=uid,
                        role="IC_ENGINEER",
                        ctx=ctx,
                        investigation_id=inv["id"],
                        expected_revision=2,
                        request_id=req,
                        new_owner_id=bad_owner,
                        note=None,
                    )
                assert exc.value.status_code == 400
                assert exc.value.code == "ERR_INVESTIGATION_TARGET_INVALID"
            # 源记录零改动（无半成品）
            detail = await get_investigation(s, ctx=ctx, investigation_id=inv["id"])
            assert detail["stage"] == "IN_PROGRESS"
            assert detail["revision"] == 2
            assert detail["outcome"] is None
            listed = await list_loop_investigations(
                s, ctx=ctx, loop_id=pg.loop_a1, stage=None, page=1, page_size=20
            )
            assert listed["total"] == 1

    # ------------------------------------------------------------------
    # 取消 ≠ 回路正常
    # ------------------------------------------------------------------

    async def test_cancel_not_mark_loop_normal(self, pg):
        from app.models.loop import LoopLedger

        uid = pg.user_a
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="cn-1"))["item"]
            await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="cn-2",
            )
            cancelled = await close_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="cn-3",
                outcome="CANCELLED",
                note="重复登记，取消",
                close_target_kind=None,
                close_target_id=None,
            )
            assert cancelled["item"]["outcome"] == "CANCELLED"
            assert cancelled["item"]["outcome_note"] == "重复登记，取消"

            # 回路行零改动（取消不改任何回路状态，≠回路正常）
            loop = (
                await s.execute(select(LoopLedger).where(LoopLedger.id == pg.loop_a1))
            ).scalar_one()
            assert loop.is_active is True

            # 取消（CLOSED）不阻塞同问题再现——取消≠问题解决
            recreated = await _create_todo(s, pg, request_id="cn-4")
            assert recreated["deduped"] is False
            assert recreated["item"]["id"] != inv["id"]
            listed = await list_loop_investigations(
                s, ctx=ctx, loop_id=pg.loop_a1, stage=None, page=1, page_size=20
            )
            assert listed["total"] == 2

    async def test_close_target_backlink_only_reference(self, pg):
        """结束保存目标回链：只存引用；非法类型/非 UUID id 400."""
        uid = pg.user_a
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="bt-1"))["item"]
            await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="bt-2",
            )
            order_id = str(uuid4())
            closed = await close_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=2,
                request_id="bt-3",
                outcome="NO_FURTHER_ACTION",
                note="已转处置工单跟进",
                close_target_kind="handling_order",
                close_target_id=order_id,
            )
            assert closed["item"]["close_target_kind"] == "handling_order"
            assert closed["item"]["close_target_id"] == order_id

            for kind, tid, req in (
                ("loop_investigation", order_id, "bt-4"),  # 服务端保留类型
                ("random_kind", order_id, "bt-5"),
                ("handling_order", "not-a-uuid", "bt-6"),
            ):
                with pytest.raises(BizError) as exc:
                    await close_investigation(
                        s,
                        user_id=uid,
                        role="IC_ENGINEER",
                        ctx=ctx,
                        investigation_id=inv["id"],
                        expected_revision=3,
                        request_id=req,
                        outcome="ANSWERED",
                        note=None,
                        close_target_kind=kind,
                        close_target_id=tid,
                    )
                assert exc.value.status_code == 400
                assert exc.value.code == "ERR_INVESTIGATION_TARGET_INVALID"

    # ------------------------------------------------------------------
    # 撤权不泄露
    # ------------------------------------------------------------------

    async def test_revocation_no_leak(self, pg):
        """撤权（scope 收窄）后：读/写均 403 通用拒绝，不回显任何对象详情."""
        uid = pg.user_a
        ctx_all = _ctx_all(uid)
        ctx_revoked = _ctx_unit_b_only(uid, pg.unit_b)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="rv-1"))["item"]

            with pytest.raises(BizError) as e_get:
                await get_investigation(s, ctx=ctx_revoked, investigation_id=inv["id"])
            assert e_get.value.status_code == 403
            assert e_get.value.code == "ERR_SCOPE_FORBIDDEN"

            with pytest.raises(BizError) as e_list:
                await list_loop_investigations(
                    s, ctx=ctx_revoked, loop_id=pg.loop_a1, stage=None, page=1, page_size=20
                )
            assert e_list.value.status_code == 403

            with pytest.raises(BizError) as e_claim:
                await claim_investigation(
                    s,
                    user_id=uid,
                    role="IC_ENGINEER",
                    ctx=ctx_revoked,
                    investigation_id=inv["id"],
                    expected_revision=1,
                    request_id="rv-2",
                )
            assert e_claim.value.status_code == 403
            # 拒绝报文不泄露标题/问题身份
            assert "振荡" not in json.dumps(e_claim.value.message, ensure_ascii=False)

            # 恢复 scope 后正常读取（撤权不删数据）
            detail = await get_investigation(s, ctx=ctx_all, investigation_id=inv["id"])
            assert detail["title"] == "回路振荡待核查"

    # ------------------------------------------------------------------
    # 旧姓名只作显示：改名刷新 / 删除派生待确认责任
    # ------------------------------------------------------------------

    async def test_owner_name_display_only_and_responsibility_pending(self, pg):
        from app.models.sys_user import SysUser

        uid = pg.user_a
        ctx = _ctx_all(uid)
        async with pg.sf() as s:
            inv = (await _create_todo(s, pg, request_id="nm-1"))["item"]
            claimed = await claim_investigation(
                s,
                user_id=uid,
                role="IC_ENGINEER",
                ctx=ctx,
                investigation_id=inv["id"],
                expected_revision=1,
                request_id="nm-2",
            )
            original_name = claimed["item"]["owner_display_name"]
            assert original_name.startswith("p605e1a-")
            assert claimed["item"]["responsibility_pending"] is False

            # 改名：稳定身份不变，显示名以 sys_user 当前值为准
            user_row = (await s.execute(select(SysUser).where(SysUser.id == uid))).scalar_one()
            user_row.display_name = "张工（改名）"
            await s.commit()
            renamed = await get_investigation(s, ctx=ctx, investigation_id=inv["id"])
            assert renamed["owner_id"] == uid
            assert renamed["owner_display_name"] == "张工（改名）"
            assert renamed["responsibility_pending"] is False

            # 删除用户：owner_id SET NULL，姓名回退分派时刻快照（旧姓名只作
            # 显示——快照不随改名追溯），派生"待确认责任"，不做同名猜测关联
            await s.execute(sa_delete(SysUser).where(SysUser.id == uid))
            await s.commit()
            orphan = await get_investigation(s, ctx=ctx, investigation_id=inv["id"])
            assert orphan["owner_id"] is None
            assert orphan["owner_display_name"] == original_name
            assert orphan["responsibility_pending"] is True


# ===========================================================================
# HTTP 级端到端（真实 PG + 路由接线 + camelCase 契约）
# ===========================================================================


@pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）")
@pytest.mark.integration
def test_http_end_to_end_investigation_endpoints(pg_http_seed):
    """七端点路由接线 + camelCase 请求/响应 + 全生命周期走查."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_scope_ctx
    from app.core.db import get_db
    from app.main import app

    seed = pg_http_seed
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
    user.username = "p605e1-http"
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

            # 1. 创建（camelCase 请求体）
            resp = client.post(
                f"/api/v1/loops/{loop_id}/investigations",
                json={
                    "sourceKind": "ALERT_EVENT",
                    "sourceId": "alert-900",
                    "problemKey": "oscillation",
                    "title": "HTTP 走查核查",
                    "requestId": "http-1",
                },
            )
            assert resp.status_code == 200, resp.text
            item = resp.json()["data"]["item"]
            inv_id = item["id"]
            assert item["stage"] == "TODO"
            assert item["sourceKind"] == "ALERT_EVENT"
            assert item["responsibilityPending"] is False
            assert "createdAt" in item  # camelCase 响应契约

            # 2. 去重：同身份再创建 → deduped
            resp = client.post(
                f"/api/v1/loops/{loop_id}/investigations",
                json={
                    "sourceKind": "ALERT_EVENT",
                    "sourceId": "alert-900",
                    "problemKey": "oscillation",
                    "title": "重复登记",
                    "requestId": "http-2",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["deduped"] is True
            assert resp.json()["data"]["item"]["id"] == inv_id

            # 3. 列表
            resp = client.get(f"/api/v1/loops/{loop_id}/investigations?stage=TODO")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 1 and "readAt" in data

            # 4. 认领 → 等待 → 恢复
            resp = client.post(
                f"/api/v1/investigations/{inv_id}/claim",
                json={"expectedRevision": 1, "requestId": "http-3"},
            )
            assert resp.status_code == 200, resp.text
            item = resp.json()["data"]["item"]
            assert item["stage"] == "IN_PROGRESS"
            assert item["ownerId"] == seed["user_a"]

            resp = client.post(
                f"/api/v1/investigations/{inv_id}/wait",
                json={
                    "expectedRevision": 2,
                    "requestId": "http-4",
                    "waitReason": "等待工艺确认",
                    "waitReviewCondition": "次日复查",
                    "waitResponsible": "工艺班组",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["item"]["stage"] == "WAITING"

            resp = client.post(
                f"/api/v1/investigations/{inv_id}/resume",
                json={"expectedRevision": 3, "requestId": "http-5"},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["item"]["stage"] == "IN_PROGRESS"

            # 5. 过期版本 409 携当前版本；版本正确但阶段非法 422
            resp = client.post(
                f"/api/v1/investigations/{inv_id}/resume",
                json={"expectedRevision": 3, "requestId": "http-6"},
            )
            assert resp.status_code == 409
            assert resp.json()["data"]["currentRevision"] == 4

            resp = client.post(
                f"/api/v1/investigations/{inv_id}/resume",
                json={"expectedRevision": 4, "requestId": "http-6b"},
            )
            assert resp.status_code == 422  # 非 WAITING 恢复 → 阶段机拒绝

            # 6. 移交（双记录原子）
            resp = client.post(
                f"/api/v1/investigations/{inv_id}/handoff",
                json={
                    "expectedRevision": 4,
                    "requestId": "http-7",
                    "newOwnerId": seed["user_c"],
                    "note": "转专家",
                },
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()["data"]
            assert payload["source"]["outcome"] == "HANDED_OFF"
            assert payload["target"]["stage"] == "TODO"
            assert payload["target"]["ownerId"] == seed["user_c"]
            target_id = payload["target"]["id"]

            # 7. 详情（目标记录回指源）
            resp = client.get(f"/api/v1/investigations/{target_id}")
            assert resp.status_code == 200
            detail = resp.json()["data"]
            assert detail["handoffSourceId"] == inv_id
            assert detail["problemKey"] == "oscillation"

            # 8. 取消必须带理由 + 取消≠回路正常（记录可再现）
            #    目标记录负责人=接手人：切换登录主体为接手人（负责人语义）
            user.id = seed["user_c"]
            user.role = "EXPERT"

            async def _override_ctx_c():
                return _ctx_all(seed["user_c"])

            app.dependency_overrides[get_scope_ctx] = _override_ctx_c
            resp = client.post(
                f"/api/v1/investigations/{target_id}/close",
                json={"expectedRevision": 1, "requestId": "http-8", "outcome": "CANCELLED"},
            )
            assert resp.status_code == 422
            resp = client.post(
                f"/api/v1/investigations/{target_id}/close",
                json={
                    "expectedRevision": 1,
                    "requestId": "http-9",
                    "outcome": "CANCELLED",
                    "note": "误登记取消",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["item"]["outcome"] == "CANCELLED"

            resp = client.post(
                f"/api/v1/loops/{loop_id}/investigations",
                json={
                    "sourceKind": "ALERT_EVENT",
                    "sourceId": "alert-900",
                    "problemKey": "oscillation",
                    "title": "问题再现",
                    "requestId": "http-10",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["deduped"] is False

            # 不存在核查 → 404
            assert client.get(f"/api/v1/investigations/{uuid4()}").status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def pg_http_seed():
    """HTTP 端到端种子（同步 fixture；复用与 pg 相同结构，收尾清行）."""
    if not _pg_reachable():
        pytest.skip("未显式提供 TEST_DATABASE_URL（scratch PG）")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base
    from app.models.loop import LoopLedger
    from app.models.loop_investigation import LoopInvestigation
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
        "user_c": str(uuid4()),
        "factory": str(uuid4()),
        "area": str(uuid4()),
        "unit_a": str(uuid4()),
        "loop_a1": str(uuid4()),
    }

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            for key, name, role in (
                ("user_a", f"p605e1ha-{suffix}", "IC_ENGINEER"),
                ("user_c", f"p605e1hc-{suffix}", "EXPERT"),
            ):
                s.add(
                    SysUser(
                        id=ids[key],
                        username=name,
                        password_hash="p605e1-test",
                        display_name=name,
                        role=role,
                        is_active=True,
                    )
                )
                await s.commit()
            for key, name, ntype, parent in (
                ("factory", f"P605E1H-F-{suffix}", "FACTORY", None),
                ("area", f"P605E1H-A-{suffix}", "AREA", ids["factory"]),
                ("unit_a", f"P605E1H-UA-{suffix}", "UNIT", ids["area"]),
            ):
                s.add(PlantNode(id=ids[key], name=name, type=ntype, parent_id=parent))
                await s.commit()
            s.add(
                LoopLedger(
                    id=ids["loop_a1"],
                    tag_name=f"TIC-P605E1A-{suffix}",
                    unit_id=ids["unit_a"],
                    is_active=True,
                )
            )
            await s.commit()

    async def _clean():
        async with sf() as s:
            await s.execute(
                sa_delete(LoopInvestigation).where(LoopInvestigation.loop_id == ids["loop_a1"])
            )
            await s.execute(sa_delete(LoopLedger).where(LoopLedger.id == ids["loop_a1"]))
            for nid in (ids["unit_a"], ids["area"], ids["factory"]):
                await s.execute(sa_delete(PlantNode).where(PlantNode.id == nid))
            for uid in (ids["user_a"], ids["user_c"]):
                await s.execute(sa_delete(SysUser).where(SysUser.id == uid))
            await s.commit()
        await engine.dispose()

    asyncio.run(_seed())
    ids["session_factory"] = sf
    try:
        yield ids
    finally:
        asyncio.run(_clean())
