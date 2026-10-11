"""P2-04 真实 PG 集成回归：工程模板版本发布（REWORK 修复例）.

与 mock 单测（tests/test_p2_04_engineering_templates.py）的区别：本文件在
真实 PostgreSQL 上验证 ``publish_template_version()`` 的落库链——版本行 +
config_publication（operation=TEMPLATE_VERSION）+ **SysAuditLog 审计行**。

回归背景（REWORK）：初版审计行 ``target_id=f"{template.id}@v{new_version}"``
为 39~41 字符，超出 ``sys_audit_log.target_id VARCHAR(36)``——真实 PG 上
INSERT 报 StringDataRightTruncationError、发布端点一调即 500（mock 单测的
db.add 不触库所以漏拦；验收者在 scratch 库复现实锤，事务回滚零残留）。
修复后 target_id 为纯模板 UUID（版本号信息在 after_value envelope 与
operation_type 中不丢，与 rollback_template_version 同口径）。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（指向一次性 scratch 库），
否则跳过——避免误写共享 dev 库。

运行方式（实验/验收时）：
    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_p204_scratch \
        uv run pytest tests/integration/test_p2_04_engtpl_real_pg.py -v -m integration

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import os
import socket
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import engineering_template as et


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

_TEST_REASON = "TEST-P204-ENGTPL"
_TEST_OPERATOR = "tester"


@pytest.fixture
async def pg_context():
    """真实 PG（scratch 库）：建表 + 测试行清理上下文（零残留）.

    建表走 ``Base.metadata.create_all``（发布链经 count_loops_for_template
    触 loop_ledger、经审计触 sys_audit_log——依赖闭包在 ORM 元数据内自洽）；
    ``engine.begin()`` 显式提交——2.0 commit-as-you-go 下 ``engine.connect()``
    退出会回滚未提交的隐式事务，DDL 不落盘。
    """
    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base

    engine = create_async_engine(_database_url())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session = AsyncSession(engine)
    template_key = f"TEST-P204-TPL-{uuid4().hex[:8]}"
    try:
        yield session, template_key, engine
    finally:
        await session.close()
        async with engine.connect() as conn:
            # 按 operator / reason / template_key 前缀精确清理（零残留断言在用例内）
            await conn.execute(
                text(f"DELETE FROM sys_audit_log WHERE operator = '{_TEST_OPERATOR}'")
            )
            await conn.execute(
                text(f"DELETE FROM config_publication WHERE reason = '{_TEST_REASON}'")
            )
            await conn.execute(
                text(
                    f"DELETE FROM engineering_template_version WHERE template_id IN "
                    f"(SELECT id FROM engineering_template WHERE template_key = "
                    f"'{template_key}')"
                )
            )
            await conn.execute(
                text(f"DELETE FROM engineering_template WHERE template_key = '{template_key}'")
            )
            await conn.commit()
        await engine.dispose()


class TestTemplateVersionPublishRealPg:
    """工程模板版本发布：真实库成功落库 + 审计行长度 + 原子性 + 回拨."""

    @pytest.mark.asyncio
    async def test_publish_succeeds_and_audit_row_fits_column(self, pg_context) -> None:
        """REWORK 回归：publish 成功（不再 StringDataRightTruncationError）；
        审计 target_id=纯模板 UUID（≤36）；版本行/账本/审计三行同事务齐备."""
        session, template_key, _ = pg_context

        created = await et.create_template(
            session,
            template_key=template_key,
            name="TEST-P204 响应特征模板",
            category="RESPONSE",
            operator=_TEST_OPERATOR,
        )
        template_id = created["id"]

        from app.services import config_publish as cp

        rev0 = await cp.get_current_revision(session)
        r1 = await et.publish_template_version(
            session,
            template_id=template_id,
            profile={"settlingTau": 120},
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            expected_revision=rev0,
        )
        assert r1["version"] == 1
        assert r1["revision"] == rev0 + 1
        assert r1["revisionRef"] == f"engineering_template:{template_id}@v1"

        # 审计行真实落库且 target_id 在 VARCHAR(36) 内（回归点）
        audit = (
            await session.execute(
                text(
                    f"SELECT target_id, operation_type FROM sys_audit_log "
                    f"WHERE operator = '{_TEST_OPERATOR}' "
                    f"AND operation_type = 'TEMPLATE_VERSION_PUBLISH'"
                )
            )
        ).first()
        assert audit is not None, "审计行未落库（发布链不完整）"
        assert audit[0] == template_id
        assert len(audit[0]) <= 36

        # 账本行 operation=TEMPLATE_VERSION；版本行 v1 真实存在
        pub = (
            await session.execute(
                text(
                    f"SELECT revision, operation FROM config_publication "
                    f"WHERE reason = '{_TEST_REASON}' ORDER BY revision DESC LIMIT 1"
                )
            )
        ).first()
        assert pub == (rev0 + 1, "TEMPLATE_VERSION")
        v1 = await et.get_version(session, template_id, 1)
        assert v1 is not None and v1["profile"] == {"settlingTau": 120}

        # 追加 v2：v1 行不可变（零改动）、指针前进、pin v1 解析不变
        r2 = await et.publish_template_version(
            session,
            template_id=template_id,
            profile={"settlingTau": 60},
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            expected_revision=r1["revision"],
        )
        assert r2["version"] == 2
        v1_after = await et.get_version(session, template_id, 1)
        assert v1_after["profile"] == {"settlingTau": 120}

        # 指针回拨 v1：版本历史保持、指针回退（生成新 revision）
        rb = await et.rollback_template_version(
            session,
            template_id=template_id,
            version=1,
            reason=_TEST_REASON,
            operator=_TEST_OPERATOR,
            expected_revision=r2["revision"],
        )
        assert rb["revision"] == r2["revision"] + 1
        assert (await et.get_template_by_id(session, template_id)).current_version == 1

    @pytest.mark.asyncio
    async def test_publish_midway_failure_leaves_zero_residue(self, pg_context) -> None:
        """零残留反证：发布链中途失败（账本/审计构造阶段抛错）→ rollback 后
        三表均无新行——单事务原子性（初版 target_id 超长即在此位置真实触发
        StringDataRightTruncationError，验收者复现事务回滚零残留）."""
        session, template_key, _ = pg_context
        from unittest.mock import patch

        created = await et.create_template(
            session,
            template_key=template_key,
            name="TEST-P204 zero-residue",
            operator=_TEST_OPERATOR,
        )
        template_id = created["id"]

        from app.services import config_publish as cp

        rev0 = await cp.get_current_revision(session)
        # 在版本行 db.add 之后、审计行构造阶段注入失败（模拟当初的
        # INSERT 超长失败位置：同事务内已有未提交的版本行+账本行）
        with (
            patch(
                "app.services.config_publish._audit_envelope",
                side_effect=RuntimeError("injected midway failure"),
            ),
            pytest.raises(RuntimeError, match="injected midway failure"),
        ):
            await et.publish_template_version(
                session,
                template_id=template_id,
                profile={"x": 1},
                reason=_TEST_REASON,
                operator=_TEST_OPERATOR,
                expected_revision=rev0,
            )
        await session.rollback()

        counts = (
            await session.execute(
                text(
                    f"SELECT "
                    f"(SELECT COUNT(*) FROM engineering_template_version "
                    f" WHERE template_id = '{template_id}'), "
                    f"(SELECT COUNT(*) FROM config_publication WHERE reason = '{_TEST_REASON}'), "
                    f"(SELECT COUNT(*) FROM sys_audit_log WHERE operator = '{_TEST_OPERATOR}' "
                    f" AND operation_type = 'TEMPLATE_VERSION_PUBLISH')"
                )
            )
        ).first()
        assert counts == (0, 0, 0), f"失败路径应零残留（版本行/账本/审计），实际 {counts}"
