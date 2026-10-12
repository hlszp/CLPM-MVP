"""P4-01 真实 PG 集成测试：两路径迁移一致 / 并发 409 / 审计 / 回退 / downgrade 幂等.

与 mock 单测（tests/test_p4_01_authz_catalog.py）的区别：本文件在真实
PostgreSQL 上验证——

1. **新装路径**：bootstrap SQL（01_schema+02_seed）→ alembic stamp head →
   零漂移（alembic autogenerate 无差异）+ 种子三方一致
   （引导 SQL == services.authz_catalog.CATALOG/ROLE_SEED）
2. **存量升级路径**：alembic upgrade（head-1）→ 插入存量用户 →
   upgrade head → 矩阵/目录/存量用户 scope=ALL 与新装路径完全一致
   （核验问题 1：迁移前后有效授权一致）
3. **并发 409 双会话**：两个独立 AsyncSession 同 expectedRevision 真实
   并发 update_role_grants——恰一方成功、恰一方 409、账本 revision 唯一
   （主键约束实跑，非进程内 mock）
4. **审计/回退**：变更落 SysAuditLog + sys_authz_policy；回退=发布逆操作
   恢复 before 快照；二次回退拒绝
5. **管理员保护真实链**：移除 protected 码→403 且矩阵零变更（事务回滚）
6. **downgrade 幂等**：upgrade head → downgrade → upgrade → downgrade
   两轮升降干净成功，最终 upgrade head 后种子完好

安全边界：复用 ``CLPM_BOOTSTRAP_TEST_ADMIN_DSN``（loopback+管理库护栏，
与 test_production_bootstrap.py 同款）；每测试一次性 scratch 库，finally
DROP。alembic 子进程经 POSTGRES_* 环境变量指向 scratch 库。

运行方式（实验/验收时）：
    cd backend
    CLPM_BOOTSTRAP_TEST_ADMIN_DSN=postgresql://clpm:...@127.0.0.1:17102/postgres \
        uv run pytest tests/integration/test_p4_01_real_pg.py -v -m integration

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text

_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SCHEMA_SQL = _ROOT / "db" / "postgresql" / "01_schema.sql"
_SEED_SQL = _ROOT / "db" / "postgresql" / "02_seed_data.sql"
_ADMIN_ENV = "CLPM_BOOTSTRAP_TEST_ADMIN_DSN"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_REVISION = "p401authz01"
_PARENT = "p204engtpl01"

pytestmark = [pytest.mark.integration]


def _admin_dsn() -> str:
    dsn = os.getenv(_ADMIN_ENV)
    if not dsn:
        pytest.skip(f"{_ADMIN_ENV} 未设置；拒绝猜测或复用开发/生产数据库")
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        pytest.fail(f"{_ADMIN_ENV} 必须使用 postgresql:// DSN")
    if parsed.hostname not in _LOOPBACK_HOSTS:
        pytest.fail(f"{_ADMIN_ENV} 仅允许 loopback PostgreSQL，实际为 {parsed.hostname!r}")
    if parsed.path.lstrip("/") not in {"postgres", "template1"}:
        pytest.fail(f"{_ADMIN_ENV} 必须指向 postgres/template1 管理库")
    return dsn


def _pg_dsn(admin_dsn: str, database: str, scheme: str = "postgresql") -> str:
    parsed = urlsplit(admin_dsn)
    return urlunsplit(parsed._replace(path=f"/{database}", scheme=scheme, query="", fragment=""))


def _alembic(dsn: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """对 scratch 库执行 alembic 子命令（URL 经 POSTGRES_* 环境注入）。"""
    parsed = urlsplit(dsn)
    import urllib.parse

    env = {
        **os.environ,
        "POSTGRES_HOST": parsed.hostname or "127.0.0.1",
        "POSTGRES_PORT": str(parsed.port or 5432),
        "POSTGRES_USER": parsed.username or "clpm",
        "POSTGRES_PASSWORD": urllib.parse.unquote(parsed.password or ""),
        "POSTGRES_DB": parsed.path.lstrip("/"),
        "JWT_SECRET_KEY": os.environ.get(
            "JWT_SECRET_KEY", "ci-test-secret-key-at-least-32-characters-long!!!"
        ),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


# 引导 SQL 的 P4-01 段标记（剥离后=存量库"迁移前"的旧版 bootstrap 口径）
_P401_SCHEMA_MARKER = "-- §9 授权目录与持久策略"
_P401_SEED_MARKER = "-- 15. 授权目录与五角色矩阵种子"
_TAIL_MARKER = (
    "-- =============================================================================\n"
    "-- 脚本结束\n"
    "-- ============================================================================="
)


def _legacy_bootstrap_pair() -> tuple[str, str]:
    """旧版（P4-01 前）bootstrap SQL 对：剥离 P4-01 段的 01/02。

    存量升级路径的真实口径：存量库=旧版 bootstrap 完整 schema + stamp
    parent head（老迁移链从空库不可全链 upgrade——init schema 历史断裂，
    与 P2-02 存量演示口径一致：迁移前版本已在库中）。
    """
    schema = _SCHEMA_SQL.read_text(encoding="utf-8")
    seed = _SEED_SQL.read_text(encoding="utf-8")
    schema_legacy = schema.split(_P401_SCHEMA_MARKER)[0] + _TAIL_MARKER + "\n"
    seed_legacy = seed.split(_P401_SEED_MARKER)[0] + _TAIL_MARKER + "\n"
    return schema_legacy, seed_legacy


@pytest.fixture
async def scratch_db():
    """一次性 scratch 库：建库 → yield DSN → finally DROP。"""
    admin_dsn = _admin_dsn()
    database = f"clpm_p401_scratch_{uuid4().hex[:8]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{database}"')
    await admin.close()
    try:
        yield _pg_dsn(admin_dsn, database), _pg_dsn(admin_dsn, database, "postgresql+asyncpg")
    finally:
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def upgraded_db(scratch_db):
    """存量升级就绪库：旧版 bootstrap → stamp parent → 存量用户 → upgrade head。"""
    raw_dsn, sa_dsn = scratch_db
    schema_legacy, seed_legacy = _legacy_bootstrap_pair()
    conn = await asyncpg.connect(raw_dsn)
    await conn.execute(schema_legacy)
    await conn.execute(seed_legacy)
    await conn.close()

    proc = _alembic(raw_dsn, "stamp", _PARENT)
    assert proc.returncode == 0, proc.stderr

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.sys_user import SysUser

    engine = create_async_engine(sa_dsn)
    Session = async_sessionmaker(engine)
    async with Session() as session:
        session.add(
            SysUser(
                username="legacy_ic",
                password_hash="x",
                display_name="存量IC",
                role="IC_ENGINEER",
            )
        )
        session.add(
            SysUser(
                username="legacy_sponsor",
                password_hash="x",
                display_name="存量SPONSOR",
                role="SPONSOR",
            )
        )
        await session.commit()
    await engine.dispose()

    proc = _alembic(raw_dsn, "upgrade", "head")
    assert proc.returncode == 0, f"存量升级失败:\n{proc.stderr}"
    yield raw_dsn, sa_dsn


async def _seed_parity_assertions(sa_dsn: str, *, expect_users: int | None = None) -> dict:
    """两路径共用断言：矩阵==ROLE_SEED、目录==CATALOG、scope 全 ALL、账本=SEED+目录扩展链。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.authz_catalog import CATALOG, ROLE_SEED

    engine = create_async_engine(sa_dsn)
    try:
        async with engine.connect() as conn:
            perm_rows = await conn.execute(text("SELECT code FROM sys_permission"))
            catalog_in_db = {r[0] for r in perm_rows.fetchall()}
            assert catalog_in_db == set(CATALOG.keys()), (
                f"目录不一致: db-only={sorted(catalog_in_db - set(CATALOG))} "
                f"catalog-only={sorted(set(CATALOG) - catalog_in_db)}"
            )

            matrix_rows = await conn.execute(
                text("SELECT role, permission_code FROM sys_role_permission")
            )
            matrix: dict[str, set[str]] = {}
            for role, code in matrix_rows.fetchall():
                matrix.setdefault(role, set()).add(code)
            assert set(matrix.keys()) == set(ROLE_SEED.keys())
            for role, codes in ROLE_SEED.items():
                assert matrix.get(role, set()) == set(codes), (
                    f"{role} 矩阵不一致: db-only={sorted(matrix.get(role, set()) - set(codes))} "
                    f"seed-only={sorted(set(codes) - matrix.get(role, set()))}"
                )

            # 账本：revision=1 SEED + 后续目录扩展链（P4-02 起 revision=2/3...
            # CATALOG_EXTENSION 随迁移链延伸，断言按链形态而非硬编码号）
            ledger = await conn.execute(
                text("SELECT revision, operation FROM sys_authz_policy ORDER BY revision")
            )
            rows_ = ledger.fetchall()
            assert [r[0] for r in rows_] == list(range(1, len(rows_) + 1)), "revision 连续无洞"
            assert rows_[0][1] == "SEED"
            assert all(op == "CATALOG_EXTENSION" for _, op in rows_[1:])
            seed_row = await conn.execute(
                text("SELECT operation, operator FROM sys_authz_policy WHERE revision = 1")
            )
            op, operator = seed_row.fetchone()
            assert (op, operator) == ("SEED", "system:seed")

            scope_rows = await conn.execute(
                text(
                    "SELECT u.username, s.scope_type, s.node_id FROM sys_user u "
                    "LEFT JOIN sys_user_scope s ON s.user_id = u.id"
                )
            )
            scopes = scope_rows.fetchall()
            assert len(scopes) > 0
            for username, scope_type, node_id in scopes:
                assert scope_type == "ALL", f"{username} scope 应为 ALL 种子"
                assert node_id is None

            if expect_users is not None:
                assert len(scopes) == expect_users
            return {
                "roles": {r: len(matrix[r]) for r in matrix},
                "codes": len(catalog_in_db),
            }
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 核验问题 1：两路径迁移一致（新装零漂移 / 存量升级）
# ---------------------------------------------------------------------------


class TestFreshInstallPath:
    async def test_bootstrap_stamp_zero_drift_and_seed_parity(self, scratch_db) -> None:
        """新装：bootstrap SQL → stamp head → alembic check 0 + 种子三方一致。"""
        raw_dsn, sa_dsn = scratch_db
        conn = await asyncpg.connect(raw_dsn)
        await conn.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
        # bootstrap 重放幂等（deploy 重试场景）
        await conn.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
        await conn.execute(_SEED_SQL.read_text(encoding="utf-8"))
        await conn.close()

        proc = _alembic(raw_dsn, "stamp", "head")
        assert proc.returncode == 0, proc.stderr

        proc = _alembic(raw_dsn, "check")
        assert proc.returncode == 0, f"新装零漂移失败:\n{proc.stdout}\n{proc.stderr}"

        summary = await _seed_parity_assertions(sa_dsn)
        # 规模与目录常量同源（P4-02 起随目录增量演进，不再硬编码）
        from app.services.authz_catalog import CATALOG as _C  # noqa: E402
        from app.services.authz_catalog import ROLE_SEED as _RS  # noqa: E402

        assert summary["codes"] == len(_C)
        assert summary["roles"]["ADMIN"] == len(_RS["ADMIN"])
        assert summary["roles"]["SPONSOR"] == len(_RS["SPONSOR"])


class TestUpgradePath:
    async def test_upgrade_from_parent_seed_parity(self, scratch_db) -> None:
        """存量升级：旧版 bootstrap → stamp parent → 存量用户 → upgrade head
        → 与新装路径完全一致（矩阵/目录/scope/revision）。"""
        raw_dsn, sa_dsn = scratch_db
        schema_legacy, seed_legacy = _legacy_bootstrap_pair()
        conn = await asyncpg.connect(raw_dsn)
        await conn.execute(schema_legacy)
        await conn.execute(seed_legacy)

        # 迁移前快照：P4-01 四表不存在
        for tbl in ("sys_permission", "sys_role_permission", "sys_user_scope", "sys_authz_policy"):
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = $1)", tbl
            )
            assert not exists, f"迁移前不应存在 {tbl}"
        await conn.close()

        proc = _alembic(raw_dsn, "stamp", _PARENT)
        assert proc.returncode == 0, proc.stderr

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.sys_user import SysUser

        engine = create_async_engine(sa_dsn)
        Session = async_sessionmaker(engine)
        async with Session() as session:
            session.add(
                SysUser(
                    username="legacy_ic",
                    password_hash="x",
                    display_name="存量IC",
                    role="IC_ENGINEER",
                )
            )
            session.add(
                SysUser(
                    username="legacy_sponsor",
                    password_hash="x",
                    display_name="存量SPONSOR",
                    role="SPONSOR",
                )
            )
            await session.commit()
        await engine.dispose()

        proc = _alembic(raw_dsn, "upgrade", "head")
        assert proc.returncode == 0, f"存量升级失败:\n{proc.stderr}"

        proc = _alembic(raw_dsn, "check")
        assert proc.returncode == 0, f"升级后漂移:\n{proc.stdout}\n{proc.stderr}"

        # 存量 7 用户（5 种子 + 2 legacy）全部获得 scope=ALL；矩阵与新装一致
        summary = await _seed_parity_assertions(sa_dsn, expect_users=7)
        from app.services.authz_catalog import ROLE_SEED as _RS  # noqa: E402

        assert summary["roles"]["ADMIN"] == len(_RS["ADMIN"])


# ---------------------------------------------------------------------------
# downgrade 幂等（两轮升降干净）
# ---------------------------------------------------------------------------


class TestDowngradeIdempotent:
    async def test_two_round_up_down_grade(self, upgraded_db) -> None:
        raw_dsn, sa_dsn = upgraded_db
        for round_no in (1, 2):
            # 每轮：upgrade head（首轮已就绪，幂等重入）→ 种子完好 →
            # downgrade → 四表不存在（幂等 DROP IF EXISTS，重复执行无害）
            proc = _alembic(raw_dsn, "upgrade", "head")
            assert proc.returncode == 0, f"round{round_no} upgrade 失败:\n{proc.stderr}"
            await _seed_parity_assertions(sa_dsn, expect_users=7)
            proc = _alembic(raw_dsn, "downgrade", _PARENT)
            assert proc.returncode == 0, f"round{round_no} downgrade 失败:\n{proc.stderr}"
            conn = await asyncpg.connect(raw_dsn)
            for tbl in (
                "sys_authz_policy",
                "sys_user_scope",
                "sys_role_permission",
                "sys_permission",
            ):
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = $1)", tbl
                )
                assert not exists, f"round{round_no} downgrade 后 {tbl} 仍存在"
            await conn.close()
        # 最终回到 head（upgrade 重新播种幂等）
        proc = _alembic(raw_dsn, "upgrade", "head")
        assert proc.returncode == 0, proc.stderr
        await _seed_parity_assertions(sa_dsn, expect_users=7)


# ---------------------------------------------------------------------------
# 核验问题 3：并发 409 双会话 + 审计完整
# ---------------------------------------------------------------------------


class TestConcurrentRevision409:
    async def test_two_sessions_one_wins_one_409(self, upgraded_db) -> None:
        raw_dsn, sa_dsn = upgraded_db

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.exceptions import BizError
        from app.services import authz_catalog as az

        engine = create_async_engine(sa_dsn)
        Session = async_sessionmaker(engine)
        s1, s2 = Session(), Session()
        results: list = [None, None]
        base_rev = await az.get_current_revision(s1)  # 账本基线（SEED+目录扩展链）

        async def _put(session, idx: int, action_codes: list[str]) -> None:
            try:
                results[idx] = await az.update_role_grants(
                    session,
                    role="SPONSOR",
                    expected_revision=base_rev,
                    page_codes=["page:monitor.loops"],
                    action_codes=action_codes,
                    reason=f"TEST-P401 并发-{idx}",
                    operator=f"tester{idx}",
                )
            except BizError as exc:
                results[idx] = exc

        await asyncio.gather(_put(s1, 0, ["loop:view"]), _put(s2, 1, ["metric:view"]))

        outcomes = sorted(
            "409"
            if isinstance(r, BizError) and r.status_code == 409
            else ("ok" if isinstance(r, dict) else f"unexpected:{r!r}")
            for r in results
        )
        assert outcomes == ["409", "ok"], f"期望恰一方成功恰一方 409，实际 {outcomes}"

        ok_idx = 0 if isinstance(results[0], dict) else 1
        ok_actions = results[ok_idx]["actions"]
        async with Session() as check:
            rows = await check.execute(
                text("SELECT revision, operation FROM sys_authz_policy ORDER BY revision")
            )
            ledger = rows.fetchall()
            assert [r[0] for r in ledger] == list(range(1, base_rev + 2))
            assert ledger[-1][1] == "ROLE_GRANT"
            # 矩阵=成功方全量写入（输方零残留）：收紧后 loop:%/metric:%
            # 恰剩成功方那一个动作码
            grants = await check.execute(
                text(
                    "SELECT permission_code FROM sys_role_permission "
                    "WHERE role = 'SPONSOR' AND (permission_code LIKE 'loop:%' "
                    "OR permission_code LIKE 'metric:%')"
                )
            )
            codes = {r[0] for r in grants.fetchall()}
            assert codes == set(ok_actions)
            # 审计完整：成功方落 SysAuditLog
            audit = await check.execute(
                text(
                    "SELECT operation_type, operator FROM sys_audit_log "
                    "WHERE operation_type = 'AUTHZ_ROLE_GRANT'"
                )
            )
            audit_rows = audit.fetchall()
            assert len(audit_rows) == 1
            assert audit_rows[0][1] == f"tester{ok_idx}"

        await s1.close()
        await s2.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# 回退链 + 管理员保护真实链
# ---------------------------------------------------------------------------


class TestRollbackAndAdminProtection:
    async def test_rollback_restores_before_and_double_rollback_rejected(self, upgraded_db) -> None:
        raw_dsn, sa_dsn = upgraded_db

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.exceptions import BizError
        from app.services import authz_catalog as az

        engine = create_async_engine(sa_dsn)
        Session = async_sessionmaker(engine)
        async with Session() as session:
            before = await az.get_role_grants(session, "SPONSOR")
            base_rev = await az.get_current_revision(session)
            updated = await az.update_role_grants(
                session,
                role="SPONSOR",
                expected_revision=base_rev,
                page_codes=["page:monitor.loops"],
                action_codes=["loop:view"],
                reason="TEST-P401 收紧演示",
                operator="tester",
            )
            assert updated["revision"] == base_rev + 1

            rolled = await az.rollback_revision(
                session,
                revision=base_rev + 1,
                expected_revision=base_rev + 1,
                reason="TEST-P401 回退演示",
                operator="tester",
            )
            assert rolled["operation"] == "ROLLBACK"
            assert rolled["rollbackOf"] == base_rev + 1
            assert rolled["revision"] == base_rev + 2

            restored = await az.get_role_grants(session, "SPONSOR")
            assert restored == before, "回退=发布逆操作应恢复 before 快照"

            with pytest.raises(BizError) as ei:
                await az.rollback_revision(
                    session,
                    revision=base_rev + 1,
                    expected_revision=base_rev + 2,
                    reason="二次回退",
                    operator="tester",
                )
            assert ei.value.code == "ERR_AUTHZ_ALREADY_ROLLED_BACK"

            ledger = await session.execute(
                text(
                    "SELECT revision, operation, rollback_of FROM sys_authz_policy "
                    "ORDER BY revision"
                )
            )
            rows = ledger.fetchall()
            assert [r[0] for r in rows] == list(range(1, base_rev + 3))
            assert rows[-1][1] == "ROLLBACK" and rows[-1][2] == base_rev + 1
            audit = await session.execute(
                text(
                    "SELECT operation_type FROM sys_audit_log "
                    "WHERE operation_type LIKE 'AUTHZ_%' ORDER BY operated_at"
                )
            )
            assert [r[0] for r in audit.fetchall()] == [
                "AUTHZ_ROLE_GRANT",
                "AUTHZ_ROLLBACK",
            ]
        await engine.dispose()

    async def test_admin_protection_real_transaction(self, upgraded_db) -> None:
        raw_dsn, sa_dsn = upgraded_db

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.exceptions import BizError
        from app.services import authz_catalog as az

        engine = create_async_engine(sa_dsn)
        Session = async_sessionmaker(engine)
        async with Session() as session:
            before = await az.get_role_grants(session, "ADMIN")
            codes = set(az.ROLE_SEED["ADMIN"]) - {"system:user-manage"}
            base_rev = await az.get_current_revision(session)
            with pytest.raises(BizError) as ei:
                await az.update_role_grants(
                    session,
                    role="ADMIN",
                    expected_revision=base_rev,
                    page_codes=sorted(c for c in codes if az.CATALOG[c].kind != "ACTION"),
                    action_codes=sorted(c for c in codes if az.CATALOG[c].kind == "ACTION"),
                    reason="尝试移除最后管理员关键权限",
                    operator="tester",
                )
            assert ei.value.code == "ERR_AUTHZ_ADMIN_PROTECTED"
            # 403 拒绝后矩阵零变更、revision 未推进
            after = await az.get_role_grants(session, "ADMIN")
            assert after == before
            rev = await az.get_current_revision(session)
            assert rev == base_rev
        await engine.dispose()
