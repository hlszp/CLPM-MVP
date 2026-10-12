"""P2-02 C10 真实 PG 集成测试：分层发布 / 并发 409 / 重置解释 / 回退重放.

与 mock 单测（tests/test_p2_02_config_publish.py）的区别：本文件在真实
PostgreSQL 上验证唯一约束并发语义（expectedRevision 双方同持时仅一方成功）、
JSONB 行为与事务原子性——04 §2 C10 指定"配置服务与真实 PG"。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（指向一次性 scratch 库），
否则跳过——避免误写共享 dev 库的 algorithm_parameter / metric_config。

运行方式（实验/验收时）：
    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_p202_scratch \
        uv run pytest tests/integration/test_p2_02_c10_real_pg.py -v -m integration

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import asyncio
import os
import socket
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.exceptions import BizError
from app.models.algorithm_parameter import AlgorithmParameter
from app.models.audit import SysAuditLog
from app.models.config_publish import ConfigOverride, ConfigPublication
from app.models.metric import MetricConfig
from app.services import algorithm_config as ac
from app.services import config_publish as cp


def _database_url() -> str:
    """测试库连接串：必须显式指定（scratch 库），不回落共享 dev 库。"""
    return os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable() -> bool:
    if not _database_url():
        return False
    try:
        with socket.create_connection(("localhost", 17102), timeout=3):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）"),
]

_TEST_REASON = "TEST-P202-C10"
_TEST_OPERATOR = "tester"


class _Scopes:
    """每测试一组作用域：TEMPLATE=任意字符串键；NODE/LOOP=UUID 形态
    （plant_node.id / loop_ledger.id 语义，避免影响计数查询绑定）。"""

    def __init__(self) -> None:
        self.template = f"TEST-P202-TPL-{uuid4().hex[:8]}"
        self.node = str(uuid4())
        self.loop = str(uuid4())


@pytest.fixture
async def pg_context():
    """真实 PG（scratch 库）：建表 + 测试行清理上下文."""
    engine = create_async_engine(_database_url())
    async with engine.connect() as conn:
        await conn.run_sync(ConfigOverride.__table__.create, checkfirst=True)
        await conn.run_sync(ConfigPublication.__table__.create, checkfirst=True)
        await conn.run_sync(AlgorithmParameter.__table__.create, checkfirst=True)
        await conn.run_sync(MetricConfig.__table__.create, checkfirst=True)
        await conn.run_sync(SysAuditLog.__table__.create, checkfirst=True)

    session = AsyncSession(engine)
    try:
        yield session, _Scopes(), engine
    finally:
        await session.close()
        async with engine.connect() as conn:
            # 测试行按 operator / reason 前缀精确清理，不动其他数据
            await conn.execute(
                text(f"DELETE FROM config_override WHERE updated_by = '{_TEST_OPERATOR}'")
            )
            await conn.execute(
                text(f"DELETE FROM config_publication WHERE reason = '{_TEST_REASON}'")
            )
            await conn.execute(
                text("DELETE FROM algorithm_parameter WHERE metric_code = 'saturation_rate'")
            )
            await conn.execute(
                text(f"DELETE FROM sys_audit_log WHERE operator = '{_TEST_OPERATOR}'")
            )
            await conn.commit()
        await engine.dispose()


async def _cleanup_test_rows(session: AsyncSession) -> None:
    await session.execute(
        text(f"DELETE FROM config_override WHERE updated_by = '{_TEST_OPERATOR}'")
    )
    await session.execute(text(f"DELETE FROM config_publication WHERE reason = '{_TEST_REASON}'"))
    await session.commit()


class TestC10RealPg:
    """C10：逐层覆盖 / 并发 409 / 重置解释 / 回退重放（真实 PG）."""

    @pytest.mark.asyncio
    async def test_layer_chain_and_revision(self, pg_context) -> None:
        """发布 TEMPLATE<NODE<LOOP 三层 → 解析链逐层遮盖，revision 单调."""
        session, scopes, _ = pg_context
        await _cleanup_test_rows(session)

        # saturation_rate 单键指标（saturation_epsilon 0.0~10.0），值沿链递增
        metric = "saturation_rate"
        key = "saturation_epsilon"
        rev = await cp.get_current_revision(session)
        r1 = await cp.publish_override(
            session,
            layer="TEMPLATE",
            scope_id=scopes.template,
            metric_code=metric,
            params={key: 1.0},
            expected_revision=rev,
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        r2 = await cp.publish_override(
            session,
            layer="NODE",
            scope_id=scopes.node,
            metric_code=metric,
            params={key: 2.0},
            expected_revision=r1["revision"],
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        r3 = await cp.publish_override(
            session,
            layer="LOOP",
            scope_id=scopes.loop,
            metric_code=metric,
            params={key: 3.0},
            expected_revision=r2["revision"],
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        assert (r1["revision"], r2["revision"], r3["revision"]) == (
            rev + 1,
            rev + 2,
            rev + 3,
        )

        # 逐层解析：DEFAULT 0.0 → TEMPLATE 1.0 → NODE 2.0 → LOOP 3.0 → TASK 4.0
        resolved = await cp.resolve_effective_params(
            session,
            metric,
            "STABLE",
            template_key=scopes.template,
            node_id=scopes.node,
            loop_id=scopes.loop,
            task_overrides={key: 4.0},
        )
        assert resolved["params"][key] == 4.0
        chain = [(s["source"], s["shadowedBy"]) for s in resolved["shadowed"] if s["key"] == key]
        assert ("DEFAULT", "TEMPLATE") in chain
        assert ("TEMPLATE", "NODE") in chain
        assert ("NODE", "LOOP") in chain
        assert ("LOOP", "TASK") in chain

    @pytest.mark.asyncio
    async def test_concurrent_publish_exactly_one_wins(self, pg_context) -> None:
        """真实并发：两方同持一个 expectedRevision → 仅一方成功，另一方 409."""
        session, scopes, engine = pg_context
        await _cleanup_test_rows(session)

        metric = "saturation_rate"
        rev = await cp.get_current_revision(session)

        async def _publish(scope_suffix: str):
            async with AsyncSession(engine) as s:
                return await cp.publish_override(
                    s,
                    layer="NODE",
                    scope_id=f"{scopes.node[:24]}{scope_suffix}{scopes.node[25:]}",
                    metric_code=metric,
                    params={"saturation_epsilon": 1.5},
                    expected_revision=rev,
                    reason=_TEST_REASON,
                    operator=_TEST_OPERATOR,
                    control_type="STABLE",
                )

        results = await asyncio.gather(_publish("a"), _publish("b"), return_exceptions=True)
        successes = [r for r in results if not isinstance(r, BaseException)]
        conflicts = [r for r in results if isinstance(r, BizError)]
        assert len(successes) == 1, f"应恰有一方成功，实际 {results}"
        assert len(conflicts) == 1, f"应恰有一方 409，实际 {results}"
        assert conflicts[0].status_code == 409
        assert conflicts[0].data["currentRevision"] == rev + 1

        # 账本中 rev+1 仅一行（唯一约束语义）
        rows = (
            await session.execute(
                text(
                    f"SELECT COUNT(*) FROM config_publication "
                    f"WHERE reason = '{_TEST_REASON}' AND revision = {rev + 1}"
                )
            )
        ).scalar_one()
        assert rows == 1

    @pytest.mark.asyncio
    async def test_reset_lower_layer_higher_still_effective(self, pg_context) -> None:
        """重置 TEMPLATE 层 → NODE 层仍生效，解释逐键给出 NODE 来源."""
        session, scopes, _ = pg_context
        await _cleanup_test_rows(session)

        metric = "saturation_rate"
        rev = await cp.get_current_revision(session)
        p1 = await cp.publish_override(
            session,
            layer="TEMPLATE",
            scope_id=scopes.template,
            metric_code=metric,
            params={"saturation_epsilon": 1.0},
            expected_revision=rev,
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        await cp.publish_override(
            session,
            layer="NODE",
            scope_id=scopes.node,
            metric_code=metric,
            params={"saturation_epsilon": 2.0},
            expected_revision=p1["revision"],
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )

        tpl_rows = (
            (
                await session.execute(
                    text(f"SELECT id FROM config_override WHERE scope_id = '{scopes.template}'")
                )
            )
            .scalars()
            .all()
        )
        assert len(tpl_rows) == 1
        reset = await cp.reset_override(
            session,
            override_id=tpl_rows[0],
            expected_revision=p1["revision"] + 1,
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
        )
        assert reset["revision"] == p1["revision"] + 2

        # 重置后解析：键回落 NODE 层（2.0），不再是 TEMPLATE（1.0）
        resolved = await cp.resolve_effective_params(
            session,
            metric,
            "STABLE",
            template_key=scopes.template,
            node_id=scopes.node,
        )
        assert resolved["params"]["saturation_epsilon"] == 2.0

        # 解释：被重置层原值中，键仍被 NODE 层覆盖
        explanation = await cp.explain_reset(
            session,
            layer="TEMPLATE",
            scope_id=scopes.template,
            metric_code=metric,
            control_type="STABLE",
            keys=["saturation_epsilon"],
            node_id=scopes.node,
        )
        assert explanation == [
            {
                "key": "saturation_epsilon",
                "value": 2.0,
                "source": "NODE",
                "sourceId": scopes.node,
                "sourceRevision": str(p1["revision"] + 1),
            }
        ]

    @pytest.mark.asyncio
    async def test_rollback_is_inverse_replay(self, pg_context) -> None:
        """回退 = before 状态重放为新 revision，历史行不删."""
        session, scopes, _ = pg_context
        await _cleanup_test_rows(session)

        metric = "saturation_rate"
        rev = await cp.get_current_revision(session)
        p1 = await cp.publish_override(
            session,
            layer="LOOP",
            scope_id=scopes.loop,
            metric_code=metric,
            params={"saturation_epsilon": 1.0},
            expected_revision=rev,
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        p2 = await cp.publish_override(
            session,
            layer="LOOP",
            scope_id=scopes.loop,
            metric_code=metric,
            params={"saturation_epsilon": 5.0},
            expected_revision=p1["revision"],
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        rollback = await cp.rollback_publication(
            session,
            revision=p2["revision"],
            expected_revision=p2["revision"],
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
        )
        assert rollback["revision"] == p2["revision"] + 1
        # 覆盖恢复为 p2 的 before = p1 的 after
        resolved = await cp.resolve_effective_params(session, metric, "STABLE", loop_id=scopes.loop)
        assert resolved["params"]["saturation_epsilon"] == 1.0
        # 历史 p2 行仍在（追加式账本）
        count = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM config_publication WHERE reason = '{_TEST_REASON}'")
            )
        ).scalar_one()
        assert count == 3  # p1 + p2 + ROLLBACK

    @pytest.mark.asyncio
    async def test_pin_snapshot_and_legacy_sync(self, pg_context) -> None:
        """LEGACY_SYNC 推进 revision；pin 快照捕获 DEFAULT 层 + 覆盖层."""
        session, scopes, _ = pg_context
        await _cleanup_test_rows(session)
        saved_cache = dict(ac._merged_cache)
        saved_rev = cp._get_synced_revision()
        try:
            metric = "saturation_rate"
            # 全局通道写入（经发布服务登记 LEGACY_SYNC）
            new_rev = await cp.note_legacy_sync(
                session,
                metric_code=metric,
                before={"STABLE": None},
                after={"STABLE": {"saturation_epsilon": 1.5}},
                reason=_TEST_REASON,
                operator=_TEST_OPERATOR,
            )
            # 直接写全局层行 + 提交（note_legacy_sync 只登记账本不改行）
            session.add(
                AlgorithmParameter(
                    metric_code=metric,
                    control_type="STABLE",
                    params={"saturation_epsilon": 1.5},
                    description="TEST",
                    is_enabled=True,
                    updated_by=_TEST_OPERATOR,
                    version=1,
                )
            )
            await session.commit()

            # LOOP 层覆盖入账
            await cp.publish_override(
                session,
                layer="LOOP",
                scope_id=scopes.loop,
                metric_code=metric,
                params={"saturation_epsilon": 4.0},
                expected_revision=new_rev,
                reason=_TEST_REASON,
                operator=_TEST_OPERATOR,
                control_type="STABLE",
            )

            snapshot = await cp.pin_config_snapshot(session, metric_codes=[metric])
            assert snapshot["configRevision"] >= new_rev + 1
            assert snapshot["params"]["saturation_rate|STABLE"]["saturation_epsilon"] == 1.5
            assert snapshot["overrides"][f"LOOP|{scopes.loop}|{metric}"] == {
                "saturation_epsilon": 4.0
            }
            # 快照内解析：LOOP 遮盖 DEFAULT
            resolved = cp.resolve_from_snapshot(snapshot, metric, "STABLE", loop_id=scopes.loop)
            assert resolved["params"]["saturation_epsilon"] == 4.0
            assert resolved["sources"]["saturation_epsilon"] == "LOOP"
        finally:
            cp._set_synced_revision(saved_rev)
            ac._merged_cache = saved_cache
            await session.rollback()

    @pytest.mark.asyncio
    async def test_stale_expected_revision_409_real(self, pg_context) -> None:
        """发布后旧 expectedRevision 再用 → 409 + 携带最新 revision."""
        session, scopes, _ = pg_context
        await _cleanup_test_rows(session)

        metric = "saturation_rate"
        rev = await cp.get_current_revision(session)
        p1 = await cp.publish_override(
            session,
            layer="NODE",
            scope_id=scopes.node,
            metric_code=metric,
            params={"saturation_epsilon": 1.0},
            expected_revision=rev,
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            control_type="STABLE",
        )
        other_node = str(uuid4())
        with pytest.raises(BizError) as exc_info:
            await cp.publish_override(
                session,
                layer="NODE",
                scope_id=other_node,
                metric_code=metric,
                params={"saturation_epsilon": 2.0},
                expected_revision=rev,
                reason=_TEST_REASON,
                operator=_TEST_OPERATOR,
                control_type="STABLE",
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.data == {"currentRevision": p1["revision"]}
