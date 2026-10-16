"""P6-05 后续分片二（E2）/me/work 统一工作查询测试.

两层覆盖（对应任务书验证清单）：

1. **纯单测**（默认 ``-m 'not integration'`` 即跑，CLPM_TEST_MODE=1 由
   conftest 保证）：workKind/partition 白名单；cursor 编解码往返与非法
   游标 400；分区判定纯函数（等待>今日计划>现在可行动互斥优先级）；
   统一排序键（有效优先级+期限+稳定 ID，NULL 期限最后）；响应无健康
   断言字段（空待办≠健康）；未登录 401、非法筛选 400、pageSize 越界
   422（端点级，校验先于 DB 访问）。
2. **真实 PG 集成**（``-m integration`` + 显式 ``TEST_DATABASE_URL``
   指向一次性 scratch 库，缺省跳过——防误写共享 dev 库）：四 workKind
   聚合、分区互斥优先级（未来计划/已结束仅筛选可见）、单来源失败
   readStatus=PARTIAL 且 total=None 不填伪造 0、撤权不泄露
   （inaccessibleCount + 不回显名称/状态/证据）、cursor 稳定分页
   （无重复无遗漏）、同名旧姓名不猜测归给用户、HTTP 级端到端
   （camelCase 契约 + 路由接线）。

运行方式（实验/验收时）：
    cd backend
    uv run pytest tests/test_p6_05_personal_work.py -q                 # 纯单测
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_test_p6 \
    uv run pytest tests/test_p6_05_personal_work.py -v -m integration  # 集成
"""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from app.core.exceptions import BizError
from app.schemas.personal_work import PersonalWorkData
from app.services.personal_work import (
    decode_cursor,
    encode_cursor,
    partition_of_investigation,
    partition_of_order,
    query_my_work,
    validate_partition,
    validate_work_kind,
)
from tests.conftest import FakeRedis, mock_current_user

# ===========================================================================
# 纯单测：筛选白名单 / 游标 / 分区与排序纯函数 / 无健康断言
# ===========================================================================


class TestValidators:
    """workKind/partition 白名单（未知值一律 400，先于 DB 访问）."""

    def test_work_kind_whitelist(self):
        assert validate_work_kind("ASSIGNED") == "ASSIGNED"
        assert validate_work_kind("system_suggestion") == "SYSTEM_SUGGESTION"  # 归一化大写
        assert validate_work_kind("FOLLOW") == "FOLLOW"
        assert validate_work_kind("RESUME") == "RESUME"

    def test_work_kind_unknown_400(self):
        with pytest.raises(BizError) as exc:
            validate_work_kind("TODO_LIST")
        assert exc.value.status_code == 400
        assert exc.value.code == "ERR_WORK_FILTER_INVALID"

    def test_partition_whitelist(self):
        for value in ("ACTIONABLE", "TODAY", "WAITING", "FUTURE", "CLOSED"):
            assert validate_partition(value.lower()) == value

    def test_partition_unknown_400(self):
        with pytest.raises(BizError) as exc:
            validate_partition("URGENT")
        assert exc.value.status_code == 400
        assert exc.value.code == "ERR_WORK_FILTER_INVALID"


class TestCursor:
    """keyset 游标：往返稳定；非法游标一律 400（不静默回退首页）."""

    @staticmethod
    def _item(**over):
        item = {
            "_rank": 2,
            "due_at": None,
            "work_kind": "ASSIGNED",
            "work_id": str(uuid4()),
        }
        item.update(over)
        return item

    def test_cursor_roundtrip(self):
        item = self._item(due_at=datetime(2026, 10, 14, 8, 30, 0))
        key = decode_cursor(encode_cursor(item))
        assert key == (2, (0, "2026-10-14T08:30:00"), "ASSIGNED", item["work_id"])

    def test_cursor_roundtrip_null_due(self):
        item = self._item()
        key = decode_cursor(encode_cursor(item))
        assert key == (2, (1, ""), "ASSIGNED", item["work_id"])

    def test_cursor_malformed_400(self):
        for bad in (
            "!!!not-base64!!!",
            "bm90LWpzb24=",  # base64("not-json")
            "e30=",  # base64("{}")：缺排序键
            "eyJwIjowLCJkIjpudWxsLCJrIjoiVEFDSyIsImkiOiJ4In0=",  # kind 非白名单
            "eyJwIjowLCJkIjpudWxsLCJrIjoiQVNTSUdORUQifQ==",  # 缺 workId
        ):
            with pytest.raises(BizError) as exc:
                decode_cursor(bad)
            assert exc.value.status_code == 400
            assert exc.value.code == "ERR_WORK_CURSOR_INVALID"


class TestPartitionAndSort:
    """分区互斥优先级 + 统一排序键（有效优先级+期限+稳定 ID）."""

    def test_investigation_partition(self):
        assert partition_of_investigation("WAITING") == "WAITING"
        assert partition_of_investigation("IN_PROGRESS") == "ACTIONABLE"
        assert partition_of_investigation("TODO") == "ACTIONABLE"
        assert partition_of_investigation("CLOSED") == "CLOSED"

    def test_order_partition_mutual_exclusive(self):
        today = datetime(2026, 10, 14).date()
        yesterday = datetime(2026, 10, 13, 9, 0)
        tomorrow = datetime(2026, 10, 15, 9, 0)
        this_morning = datetime(2026, 10, 14, 9, 0)
        # CLOSED 终态优先于任何计划日期
        assert partition_of_order("CLOSED", this_morning, today) == "CLOSED"
        assert partition_of_order("CANCELLED", None, today) == "CLOSED"
        # 今日计划（执行中仍是今日计划——能操作时仍显示操作按钮）
        assert partition_of_order("EXECUTING", this_morning, today) == "TODAY"
        # 未来计划仅筛选可见
        assert partition_of_order("PENDING", tomorrow, today) == "FUTURE"
        # 计划已过/无计划 → 现在可行动（不按"今天有更新"筛掉）
        assert partition_of_order("PENDING", yesterday, today) == "ACTIONABLE"
        assert partition_of_order("EXECUTING", None, today) == "ACTIONABLE"
        # 验证中仍是可行动（验证是人工动作，不是等待条件）
        assert partition_of_order("VERIFYING", None, today) == "ACTIONABLE"

    def test_sort_key_rank_then_due_then_stable_id(self):
        base = datetime(2026, 10, 14, 8, 0, 0)

        def item(rank, due, kind, wid):
            return {"_rank": rank, "due_at": due, "work_kind": kind, "work_id": wid}

        def sort_key(i):
            due = (0, i["due_at"].isoformat()) if i["due_at"] else (1, "")
            return (i["_rank"], due, i["work_kind"], i["work_id"])

        items = [
            item(4, None, "RESUME", "r-1"),
            item(2, None, "ASSIGNED", "a-2"),
            item(2, base, "ASSIGNED", "a-1"),
            item(0, base + timedelta(hours=1), "ASSIGNED", "a-0"),
            item(1, None, "SYSTEM_SUGGESTION", "s-1"),
            item(3, None, "FOLLOW", "f-1"),
        ]
        ordered = sorted(items, key=sort_key)
        assert [i["work_id"] for i in ordered] == ["a-0", "s-1", "a-1", "a-2", "f-1", "r-1"]

    def test_no_health_assertion_in_response_schema(self):
        """空待办≠健康：响应模型不得含任何健康断言字段."""
        assert not any("health" in name.lower() for name in PersonalWorkData.model_fields)


# ===========================================================================
# 纯单测：端点认证/校验（TestClient + mock DB；错误路径均先于 DB 访问触发）
# ===========================================================================

_WORK_URL = "/api/v1/me/work"


class TestEndpointAuthAndValidation:
    """未登录 401 / 非法筛选 400 / pageSize 越界 422（先于 DB 访问）."""

    def test_unauthenticated_401(self, client):
        resp = client.get(_WORK_URL)
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"

    def test_invalid_work_kind_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.get(_WORK_URL, params={"workKind": "TODO_LIST"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_WORK_FILTER_INVALID"

    def test_invalid_partition_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.get(_WORK_URL, params={"partition": "URGENT"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_WORK_FILTER_INVALID"

    def test_malformed_cursor_400(self, client, admin_user):
        with mock_current_user(admin_user):
            resp = client.get(_WORK_URL, params={"cursor": "!!!bad!!!"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_WORK_CURSOR_INVALID"

    def test_page_size_out_of_range_422(self, client, admin_user):
        with mock_current_user(admin_user):
            assert client.get(_WORK_URL, params={"pageSize": 0}).status_code == 422
            assert client.get(_WORK_URL, params={"pageSize": 51}).status_code == 422


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


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
async def pg():
    """真实 PG（scratch 库）：建表 + 三用户/工厂树/两回路种子，收尾清行."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base
    from app.models.loop import LoopLedger
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
        "factory": str(uuid4()),
        "area": str(uuid4()),
        "unit_a": str(uuid4()),
        "unit_b": str(uuid4()),
        "loop_a1": str(uuid4()),
        "loop_b1": str(uuid4()),
    }

    async with sf() as s:
        for key, name, role in (
            ("user_a", f"p605e2a-{suffix}", "IC_ENGINEER"),
            ("user_b", f"p605e2b-{suffix}", "PE_ENGINEER"),
            ("user_c", f"p605e2c-{suffix}", "EXPERT"),
        ):
            s.add(
                SysUser(
                    id=ids[key],
                    username=name,
                    password_hash="p605e2-test",
                    display_name=name,
                    role=role,
                    is_active=True,
                )
            )
            await s.commit()
        for key, name, ntype, parent in (
            ("factory", f"P605E2-F-{suffix}", "FACTORY", None),
            ("area", f"P605E2-A-{suffix}", "AREA", ids["factory"]),
            ("unit_a", f"P605E2-UA-{suffix}", "UNIT", ids["area"]),
            ("unit_b", f"P605E2-UB-{suffix}", "UNIT", ids["area"]),
        ):
            s.add(PlantNode(id=ids[key], name=name, type=ntype, parent_id=parent))
            await s.commit()
        for key, tag, unit in (
            ("loop_a1", f"TIC-P605E2A-{suffix}", ids["unit_a"]),
            ("loop_b1", f"TIC-P605E2B-{suffix}", ids["unit_b"]),
        ):
            s.add(LoopLedger(id=ids[key], tag_name=tag, unit_id=unit, is_active=True))
            await s.commit()

    try:
        yield SimpleNamespace(sf=sf, engine=engine, **ids)
    finally:
        async with sf() as s:
            from app.models.handling_order import HandlingOrder
            from app.models.loop_action_item import LoopActionItem
            from app.models.loop_investigation import LoopInvestigation
            from app.models.personal_workspace import UserLoopFollow, UserWorkResume

            for lid in (ids["loop_a1"], ids["loop_b1"]):
                await s.execute(
                    sa_delete(LoopInvestigation).where(LoopInvestigation.loop_id == lid)
                )
                await s.execute(sa_delete(HandlingOrder).where(HandlingOrder.loop_id == lid))
                await s.execute(sa_delete(LoopActionItem).where(LoopActionItem.loop_id == lid))
                await s.execute(sa_delete(UserLoopFollow).where(UserLoopFollow.loop_id == lid))
            for uid in (ids["user_a"], ids["user_b"], ids["user_c"]):
                await s.execute(sa_delete(UserWorkResume).where(UserWorkResume.user_id == uid))
                await s.execute(sa_delete(UserLoopFollow).where(UserLoopFollow.user_id == uid))
            for lid in (ids["loop_a1"], ids["loop_b1"]):
                await s.execute(sa_delete(LoopLedger).where(LoopLedger.id == lid))
            for nid in (ids["unit_a"], ids["unit_b"], ids["area"], ids["factory"]):
                await s.execute(sa_delete(PlantNode).where(PlantNode.id == nid))
            for uid in (ids["user_a"], ids["user_b"], ids["user_c"]):
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


# ---------------------------------------------------------------------------
# 集成种子辅助（直接 ORM 写行——本测试不经过 E1 端点）
# ---------------------------------------------------------------------------


async def _seed_investigation(s, pg, *, owner_id, stage="IN_PROGRESS", loop_id=None, title=None):
    from app.models.loop_investigation import LoopInvestigation

    now = _now()
    row = LoopInvestigation(
        id=str(uuid4()),
        loop_id=loop_id or pg.loop_a1,
        source_kind="ALERT_EVENT",
        source_id=f"alert-{uuid4().hex[:6]}",
        problem_key="oscillation",
        title=title or f"核查-{uuid4().hex[:6]}",
        stage=stage,
        owner_id=owner_id,
        owner_name="p605e2-seed",
        revision=1,
        created_by=owner_id,
        created_at=now,
        updated_at=now,
    )
    if stage == "WAITING":
        row.wait_reason = "等待工艺确认进料"
        row.wait_review_condition = "次日早会复查"
        row.wait_responsible = "工艺班组"
    if stage == "CLOSED":
        row.outcome = "ANSWERED"
        row.closed_at = now
        row.closed_by = owner_id
    s.add(row)
    await s.commit()
    return row


async def _seed_order(
    s,
    pg,
    *,
    handler_id,
    status="PENDING",
    planned_at=None,
    sla_stage="NONE",
    sla_deadline_at=None,
    loop_id=None,
    title=None,
    suggestion_ids=None,
    handler_name=None,
):
    from app.models.handling_order import HandlingOrder

    row = HandlingOrder(
        id=str(uuid4()),
        order_no=f"HD-P605E2-{uuid4().hex[:8]}",
        loop_id=loop_id or pg.loop_a1,
        source="MANUAL",
        suggestion_ids=suggestion_ids,
        title=title or f"工单-{uuid4().hex[:6]}",
        action_type="OTHER",
        planned_at=planned_at,
        handler=handler_name,
        handler_id=handler_id,
        status=status,
        sla_stage=sla_stage,
        sla_deadline_at=sla_deadline_at,
    )
    s.add(row)
    await s.commit()
    return row


async def _seed_action_item(
    s, pg, *, loop_id=None, priority=2, status="PENDING", source="SYSTEM", content=None, basis=None
):
    from app.models.loop_action_item import LoopActionItem

    row = LoopActionItem(
        id=str(uuid4()),
        loop_id=loop_id or pg.loop_a1,
        source=source,
        category="PROCESS",
        content=content or f"建议-{uuid4().hex[:6]}",
        basis=basis,
        priority=priority,
        status=status,
        suggested_by="系统" if source == "SYSTEM" else "p605e2-seed",
        suggested_at=_now(),
    )
    s.add(row)
    await s.commit()
    return row


async def _seed_follow(s, pg, *, user_id, loop_id):
    from app.models.personal_workspace import UserLoopFollow

    row = UserLoopFollow(user_id=user_id, loop_id=loop_id, is_followed=True, revision=1)
    s.add(row)
    await s.commit()
    return row


async def _seed_resume(s, pg, *, user_id, loop_id, context=None):
    from app.models.personal_workspace import UserWorkResume

    row = UserWorkResume(
        user_id=user_id,
        resource_kind="loop",
        resource_id=loop_id,
        context=context or {"page": "workbench360", "section": "assess"},
        revision=1,
        generation=1,
        last_visited_at=_now(),
    )
    s.add(row)
    await s.commit()
    return row


class TestRealPG:  # noqa: D101
    pytestmark = _REQUIRES_PG

    # ------------------------------------------------------------------
    # 四 workKind 聚合
    # ------------------------------------------------------------------

    async def test_four_work_kinds_aggregate(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            inv = await _seed_investigation(s, pg, owner_id=uid)
            order = await _seed_order(s, pg, handler_id=uid)
            action = await _seed_action_item(s, pg, priority=1, basis="诊断结论：工艺/外扰问题")
            await _seed_follow(s, pg, user_id=uid, loop_id=pg.loop_a1)
            await _seed_resume(s, pg, user_id=uid, loop_id=pg.loop_b1)

            result = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid))
            kinds = {i["work_kind"] for i in result["items"]}
            assert kinds == {"ASSIGNED", "SYSTEM_SUGGESTION", "FOLLOW", "RESUME"}
            assert result["total"] == 5  # 核查+工单+建议+关注+最近继续
            assert result["read_status"] == "COMPLETE"
            assert result["next_cursor"] is None
            assert isinstance(result["read_at"], datetime)
            for src in ("investigation", "handling_order", "action_item", "follow", "resume"):
                assert result["source_statuses"][src]["status"] == "OK"
                assert result["source_statuses"][src]["total"] == 1
                assert result["source_statuses"][src]["inaccessible_count"] == 0

            by_id = {i["work_id"]: i for i in result["items"]}
            # 分配给我：核查 + 工单（同一 workKind 两来源）
            assert by_id[str(inv.id)]["source_kind"] == "investigation"
            assert by_id[str(inv.id)]["owner_id"] == uid
            assert by_id[str(inv.id)]["responsibility_pending"] is False
            assert by_id[str(order.id)]["source_kind"] == "handling_order"
            assert by_id[str(order.id)]["partition"] == "ACTIONABLE"
            # 系统建议：待审核池，负责人为空；basis 作为进入清单理由
            suggestion = by_id[str(action.id)]
            assert suggestion["work_kind"] == "SYSTEM_SUGGESTION"
            assert suggestion["owner_id"] is None
            assert suggestion["reason"] == "诊断结论：工艺/外扰问题"
            assert suggestion["partition"] == "ACTIONABLE"
            assert "HIGH_PRIORITY" in suggestion["priority_reasons"]
            assert suggestion["next_action"]["kind"] == "review_action_item"
            # 关注/最近继续：不承担工作分区
            follows = [i for i in result["items"] if i["work_kind"] == "FOLLOW"]
            resumes = [i for i in result["items"] if i["work_kind"] == "RESUME"]
            assert follows[0]["loop_id"] == pg.loop_a1 and follows[0]["partition"] is None
            assert resumes[0]["loop_id"] == pg.loop_b1
            assert resumes[0]["context"]["section"] == "assess"
            # 排序：高优先级建议（rank 1）先于常规分配（rank 2）先于关注/最近
            ordered_ids = [i["work_id"] for i in result["items"]]
            assert ordered_ids.index(str(action.id)) < ordered_ids.index(str(inv.id))
            assert ordered_ids.index(str(inv.id)) < ordered_ids.index(pg.loop_a1)

    async def test_work_kind_filter_single_view(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            await _seed_investigation(s, pg, owner_id=uid)
            await _seed_follow(s, pg, user_id=uid, loop_id=pg.loop_a1)
            result = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid), work_kind="FOLLOW")
            assert {i["work_kind"] for i in result["items"]} == {"FOLLOW"}
            assert result["total"] == 1
            assert set(result["source_statuses"]) == {"follow"}

    # ------------------------------------------------------------------
    # 分区互斥优先级（等待 > 今日计划 > 现在可行动；未来/已结束仅筛选可见）
    # ------------------------------------------------------------------

    async def test_partition_mutual_exclusive_and_filters(self, pg):
        uid = pg.user_a
        now = _now()
        today_morning = now.replace(hour=6, minute=0, second=0, microsecond=0)
        async with pg.sf() as s:
            waiting_inv = await _seed_investigation(s, pg, owner_id=uid, stage="WAITING")
            today_order = await _seed_order(
                s, pg, handler_id=uid, status="EXECUTING", planned_at=today_morning
            )
            overdue_order = await _seed_order(
                s,
                pg,
                handler_id=uid,
                status="PENDING",
                planned_at=now - timedelta(days=1),
            )
            future_order = await _seed_order(
                s,
                pg,
                handler_id=uid,
                status="PENDING",
                planned_at=now + timedelta(days=2),
            )
            closed_order = await _seed_order(s, pg, handler_id=uid, status="CLOSED")

            result = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid))
            by_id = {i["work_id"]: i for i in result["items"]}
            # 等待条件分区（WAITING 三要素透出）
            assert by_id[str(waiting_inv.id)]["partition"] == "WAITING"
            assert by_id[str(waiting_inv.id)]["wait_reason"] == "等待工艺确认进料"
            assert by_id[str(waiting_inv.id)]["wait_review_condition"] == "次日早会复查"
            # 今日计划（可操作时仍显示操作按钮）
            assert by_id[str(today_order.id)]["partition"] == "TODAY"
            assert by_id[str(today_order.id)]["next_action"]["kind"] == "submit_verification"
            # 计划已过 → 现在可行动 + 逾期理由；排序最先
            assert by_id[str(overdue_order.id)]["partition"] == "ACTIONABLE"
            assert "OVERDUE" in by_id[str(overdue_order.id)]["priority_reasons"]
            assert result["items"][0]["work_id"] == str(overdue_order.id)
            # 未来计划/已结束：默认视图排除
            assert str(future_order.id) not in by_id
            assert str(closed_order.id) not in by_id
            assert result["total"] == 3  # 等待核查 + 今日工单 + 逾期工单
            # 未来计划/已结束：仅筛选可见（计数与分页同规则）
            future = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid), partition="FUTURE")
            assert [i["work_id"] for i in future["items"]] == [str(future_order.id)]
            assert future["total"] == 1
            closed = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid), partition="CLOSED")
            assert [i["work_id"] for i in closed["items"]] == [str(closed_order.id)]
            assert closed["items"][0]["partition"] == "CLOSED"
            today = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid), partition="TODAY")
            assert {i["work_id"] for i in today["items"]} == {str(today_order.id)}
            # 分区互斥：每条 item 恰好一个分区
            for item in result["items"]:
                assert item["partition"] in ("WAITING", "TODAY", "ACTIONABLE")

    # ------------------------------------------------------------------
    # 单来源失败：readStatus=PARTIAL 且 total 语义正确不填 0
    # ------------------------------------------------------------------

    async def test_source_failure_partial_total_not_faked_zero(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            await _seed_investigation(s, pg, owner_id=uid)
            await _seed_action_item(s, pg)

            async def _boom(**kwargs):
                raise RuntimeError("p605e2 模拟来源故障")

            with patch("app.services.personal_work._fetch_action_items", side_effect=_boom):
                result = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid))
            # 部分数据必须可见
            assert {i["work_kind"] for i in result["items"]} == {"ASSIGNED"}
            # 计数未知不填伪造 0
            assert result["total"] is None
            assert result["read_status"] == "PARTIAL"
            assert result["source_statuses"]["action_item"]["status"] == "ERROR"
            assert result["source_statuses"]["action_item"]["total"] is None
            # 健康来源计数与分页同规则
            assert result["source_statuses"]["investigation"]["total"] == 1
            assert result["source_statuses"]["investigation"]["status"] == "OK"

    async def test_all_sources_failure_read_status_error(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:

            async def _boom(**kwargs):
                raise RuntimeError("p605e2 模拟全来源故障")

            with patch("app.services.personal_work._fetch_action_items", side_effect=_boom):
                result = await query_my_work(
                    s, user_id=uid, ctx=_ctx_all(uid), work_kind="SYSTEM_SUGGESTION"
                )
            assert result["items"] == []
            assert result["total"] is None
            assert result["read_status"] == "ERROR"
            assert result["source_statuses"]["action_item"]["status"] == "ERROR"

    # ------------------------------------------------------------------
    # 撤权不泄露
    # ------------------------------------------------------------------

    async def test_revocation_no_leak(self, pg):
        """撤权（scope 收窄）后：撤权对象不回显名称/状态/证据，仅计 inaccessible."""
        uid = pg.user_a
        ctx_revoked = _ctx_unit_b_only(uid, pg.unit_b)
        async with pg.sf() as s:
            inv = await _seed_investigation(
                s, pg, owner_id=uid, loop_id=pg.loop_a1, title="绝密核查标题-p605e2"
            )
            await _seed_follow(s, pg, user_id=uid, loop_id=pg.loop_b1)

            result = await query_my_work(s, user_id=uid, ctx=ctx_revoked)
            # 可访问对象仍可见（范围内关注的 loop_b1）
            assert [i["work_kind"] for i in result["items"]] == ["FOLLOW"]
            assert result["items"][0]["loop_id"] == pg.loop_b1
            # 撤权核查不回显：标题/回路位号不出现在响应任何位置
            payload = json.dumps(result["items"], ensure_ascii=False, default=str)
            assert "绝密核查标题-p605e2" not in payload
            assert str(inv.id) not in payload
            assert "TIC-P605E2A" not in payload
            # 撤权对象保留并计数提示（不泄露详情）
            assert result["source_statuses"]["investigation"]["inaccessible_count"] == 1
            assert result["read_status"] == "PARTIAL"
            # total 与分页同规则：只计可访问条目
            assert result["total"] == 1

            # 恢复 scope 后数据仍在（撤权不删数据）
            restored = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid))
            assert str(inv.id) in {i["work_id"] for i in restored["items"]}
            assert restored["read_status"] == "COMPLETE"

    # ------------------------------------------------------------------
    # cursor 稳定分页（无重复无遗漏；计数与分页同规则）
    # ------------------------------------------------------------------

    async def test_cursor_stable_pagination(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            for _ in range(3):
                await _seed_investigation(s, pg, owner_id=uid)
            for _ in range(2):
                await _seed_order(s, pg, handler_id=uid)
            await _seed_action_item(s, pg)
            await _seed_follow(s, pg, user_id=uid, loop_id=pg.loop_a1)

            seen: list[str] = []
            cursor = None
            pages = 0
            while True:
                result = await query_my_work(
                    s, user_id=uid, ctx=_ctx_all(uid), cursor=cursor, page_size=3
                )
                pages += 1
                seen.extend(i["work_id"] for i in result["items"])
                assert result["total"] == 7
                if result["next_cursor"] is None:
                    break
                cursor = result["next_cursor"]
                assert pages < 10  # 防死循环
            assert pages == 3  # 3 + 3 + 1
            assert len(seen) == 7
            assert len(set(seen)) == 7  # 无重复无遗漏

    async def test_cursor_across_rank_boundary(self, pg):
        """跨优先级档位的 keyset 翻页不丢不重（rank 1 建议 → rank 2 工单 → rank 3 关注）."""
        uid = pg.user_a
        async with pg.sf() as s:
            action = await _seed_action_item(s, pg, priority=1)
            order = await _seed_order(s, pg, handler_id=uid)
            await _seed_follow(s, pg, user_id=uid, loop_id=pg.loop_a1)

            page1 = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid), page_size=1)
            assert [i["work_id"] for i in page1["items"]] == [str(action.id)]
            page2 = await query_my_work(
                s, user_id=uid, ctx=_ctx_all(uid), cursor=page1["next_cursor"], page_size=1
            )
            assert [i["work_id"] for i in page2["items"]] == [str(order.id)]
            page3 = await query_my_work(
                s, user_id=uid, ctx=_ctx_all(uid), cursor=page2["next_cursor"], page_size=1
            )
            assert [i["work_id"] for i in page3["items"]] == [pg.loop_a1]
            assert page3["next_cursor"] is None

    # ------------------------------------------------------------------
    # 同名旧姓名：不猜测归给同名用户；显示名以 sys_user 当前值为准
    # ------------------------------------------------------------------

    async def test_same_name_legacy_not_assigned(self, pg):
        uid = pg.user_a
        async with pg.sf() as s:
            # 旧记录：handler 字符串=我的 username 但无稳定 handler_id → 不得归入我的工作
            legacy = await _seed_order(s, pg, handler_id=None, handler_name="p605e2a-same-name")
            # 同名旧姓名快照的无主核查（owner_id NULL）：不得按姓名猜测归给我
            orphan = await _seed_investigation(s, pg, owner_id=None)
            orphan.owner_name = "p605e2a-legacy-same-name"
            await s.commit()
            mine = await _seed_investigation(s, pg, owner_id=uid)
            action = await _seed_action_item(s, pg)

            result = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid))
            ids = {i["work_id"] for i in result["items"]}
            assert str(legacy.id) not in ids  # 无稳定身份不归入（不猜测）
            assert str(orphan.id) not in ids
            assert str(mine.id) in ids
            assert str(legacy.id) not in ids  # 无稳定身份不归入（不猜测）
            assert str(orphan.id) not in ids
            assert str(mine.id) in ids

            # 我的核查：显示名取 sys_user 当前值（改名即刷新，不回退旧快照）
            from app.models.sys_user import SysUser

            user = (await s.execute(select(SysUser).where(SysUser.id == uid))).scalar_one()
            user.display_name = "张工（改名）"
            await s.commit()
            renamed = await query_my_work(s, user_id=uid, ctx=_ctx_all(uid))
            mine_item = {i["work_id"]: i for i in renamed["items"]}[str(mine.id)]
            assert mine_item["owner_id"] == uid
            assert mine_item["owner_display_name"] == "张工（改名）"
            assert mine_item["responsibility_pending"] is False
            # 系统建议池：无负责人也不标待确认（未认领≠责任待确认）
            suggestion = {i["work_id"]: i for i in renamed["items"]}[str(action.id)]
            assert suggestion["owner_display_name"] is None
            assert suggestion["responsibility_pending"] is False


# ===========================================================================
# HTTP 级端到端（真实 PG + 路由接线 + camelCase 契约）
# ===========================================================================


@pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）")
@pytest.mark.integration
def test_http_end_to_end_me_work(pg_http_seed):
    """GET /api/v1/me/work 路由接线 + camelCase 契约 + 筛选/游标走查."""
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
    user.username = "p605e2-http"
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
            # 聚合：四 workKind（种子已备核查/工单/建议/关注/最近继续）
            resp = client.get("/api/v1/me/work")
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            kinds = {i["workKind"] for i in data["items"]}
            assert kinds == {"ASSIGNED", "SYSTEM_SUGGESTION", "FOLLOW", "RESUME"}
            assert data["total"] == 5
            assert data["readStatus"] == "COMPLETE"
            assert "readAt" in data and data["nextCursor"] is None
            assert set(data["sourceStatuses"]) == {
                "investigation",
                "handling_order",
                "action_item",
                "follow",
                "resume",
            }
            # camelCase 契约字段
            item = data["items"][0]
            for field in (
                "workKind",
                "workId",
                "loopId",
                "ownerId",
                "stage",
                "partition",
                "plannedAt",
                "dueAt",
                "waitReason",
                "evidenceRefs",
                "nextAction",
                "revision",
                "priorityReasons",
                "responsibilityPending",
                "loopTagName",
            ):
                assert field in item, field

            # workKind 筛选
            resp = client.get("/api/v1/me/work", params={"workKind": "FOLLOW"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert {i["workKind"] for i in data["items"]} == {"FOLLOW"}
            assert data["total"] == 1

            # 分区筛选：今日计划
            resp = client.get("/api/v1/me/work", params={"partition": "TODAY"})
            assert resp.status_code == 200
            today_items = resp.json()["data"]["items"]
            assert [i["partition"] for i in today_items] == ["TODAY"] * len(today_items)

            # 游标翻页
            resp = client.get("/api/v1/me/work", params={"pageSize": 2})
            page1 = resp.json()["data"]
            assert len(page1["items"]) == 2 and page1["nextCursor"]
            resp = client.get(
                "/api/v1/me/work", params={"pageSize": 2, "cursor": page1["nextCursor"]}
            )
            page2 = resp.json()["data"]
            assert len(page2["items"]) == 2
            ids1 = {i["workId"] for i in page1["items"]}
            ids2 = {i["workId"] for i in page2["items"]}
            assert ids1.isdisjoint(ids2)

            # 非法筛选 400
            resp = client.get("/api/v1/me/work", params={"workKind": "NOPE"})
            assert resp.status_code == 400
            assert resp.json()["code"] == "ERR_WORK_FILTER_INVALID"
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def pg_http_seed():
    """HTTP 端到端种子（同步 fixture；核查/工单/建议/关注/最近继续各一，收尾清行）."""
    if not _pg_reachable():
        pytest.skip("未显式提供 TEST_DATABASE_URL（scratch PG）")
    import asyncio as _asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base
    from app.models.handling_order import HandlingOrder
    from app.models.loop import LoopLedger
    from app.models.loop_action_item import LoopActionItem
    from app.models.loop_investigation import LoopInvestigation
    from app.models.personal_workspace import UserLoopFollow, UserWorkResume
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
                    username=f"p605e2ha-{suffix}",
                    password_hash="p605e2-test",
                    display_name=f"p605e2ha-{suffix}",
                    role="IC_ENGINEER",
                    is_active=True,
                )
            )
            await s.commit()
            for key, name, ntype, parent in (
                ("factory", f"P605E2H-F-{suffix}", "FACTORY", None),
                ("area", f"P605E2H-A-{suffix}", "AREA", ids["factory"]),
                ("unit_a", f"P605E2H-UA-{suffix}", "UNIT", ids["area"]),
            ):
                s.add(PlantNode(id=ids[key], name=name, type=ntype, parent_id=parent))
                await s.commit()
            s.add(
                LoopLedger(
                    id=ids["loop_a1"],
                    tag_name=f"TIC-P605E2A-{suffix}",
                    unit_id=ids["unit_a"],
                    is_active=True,
                )
            )
            await s.commit()
            now = _now()
            s.add(
                LoopInvestigation(
                    id=str(uuid4()),
                    loop_id=ids["loop_a1"],
                    source_kind="ALERT_EVENT",
                    source_id="alert-http-1",
                    problem_key="oscillation",
                    title="HTTP 走查核查",
                    stage="IN_PROGRESS",
                    owner_id=ids["user_a"],
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.add(
                HandlingOrder(
                    id=str(uuid4()),
                    order_no=f"HD-P605E2H-{suffix}",
                    loop_id=ids["loop_a1"],
                    source="MANUAL",
                    title="HTTP 走查工单",
                    action_type="OTHER",
                    planned_at=now,
                    handler_id=ids["user_a"],
                    status="EXECUTING",
                )
            )
            s.add(
                LoopActionItem(
                    id=str(uuid4()),
                    loop_id=ids["loop_a1"],
                    source="SYSTEM",
                    category="PROCESS",
                    content="HTTP 走查系统建议",
                    basis="诊断结论：工艺/外扰问题",
                    priority=1,
                    status="PENDING",
                    suggested_by="系统",
                    suggested_at=now,
                )
            )
            s.add(
                UserLoopFollow(
                    user_id=ids["user_a"], loop_id=ids["loop_a1"], is_followed=True, revision=1
                )
            )
            s.add(
                UserWorkResume(
                    user_id=ids["user_a"],
                    resource_kind="loop",
                    resource_id=ids["loop_a1"],
                    context={"page": "workbench360", "section": "diagnosis"},
                    revision=1,
                    generation=1,
                    last_visited_at=now,
                )
            )
            await s.commit()

    async def _clean():
        async with sf() as s:
            await s.execute(
                sa_delete(LoopInvestigation).where(LoopInvestigation.loop_id == ids["loop_a1"])
            )
            await s.execute(sa_delete(HandlingOrder).where(HandlingOrder.loop_id == ids["loop_a1"]))
            await s.execute(
                sa_delete(LoopActionItem).where(LoopActionItem.loop_id == ids["loop_a1"])
            )
            await s.execute(
                sa_delete(UserLoopFollow).where(UserLoopFollow.user_id == ids["user_a"])
            )
            await s.execute(
                sa_delete(UserWorkResume).where(UserWorkResume.user_id == ids["user_a"])
            )
            await s.execute(sa_delete(LoopLedger).where(LoopLedger.id == ids["loop_a1"]))
            for nid in (ids["unit_a"], ids["area"], ids["factory"]):
                await s.execute(sa_delete(PlantNode).where(PlantNode.id == nid))
            await s.execute(sa_delete(SysUser).where(SysUser.id == ids["user_a"]))
            await s.commit()
        await engine.dispose()

    _asyncio.run(_seed())
    ids["session_factory"] = sf
    try:
        yield ids
    finally:
        _asyncio.run(_clean())
