"""Production PostgreSQL bootstrap validation on a disposable database.

This test is intentionally opt-in. It never uses the application's normal
``POSTGRES_DB`` and refuses non-loopback PostgreSQL servers. The supplied
administrator DSN is used only to create a uniquely named
``clpm_bootstrap_test_*`` database, execute the production bootstrap SQL, and
drop that database in a ``finally`` block.

Run:

    CLPM_BOOTSTRAP_TEST_ADMIN_DSN=postgresql://user:pass@127.0.0.1:7102/postgres \
      uv run pytest tests/integration/test_production_bootstrap.py \
      -v -m integration --no-header
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_SQL = _ROOT / "db" / "postgresql" / "01_schema.sql"
_SEED_SQL = _ROOT / "db" / "postgresql" / "02_seed_data.sql"
_ADMIN_ENV = "CLPM_BOOTSTRAP_TEST_ADMIN_DSN"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_CONVERGED_TABLES = {
    "algorithm_parameter",
    "clpm_metric_data_requirement",
    "dcs_mode_mapping",
    "dcs_model",
    "dcs_pid_structure",
    "dcs_vendor",
    "diagnosis_config_change",
    "diagnosis_rule",
    "diagnosis_tag",
    "diagnosis_task",
    "diagnosis_threshold_override",
    "kpi_snapshot_custom",
    "loop_confidence_latest",
    "mode_definition",
    "report_config",
    "unit_kpi_summary",
}


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
        pytest.fail(f"{_ADMIN_ENV} 必须指向 postgres/template1 管理库，不得指向业务库")
    return dsn


def _database_dsn(admin_dsn: str, database: str) -> str:
    parsed = urlsplit(admin_dsn)
    return urlunsplit(parsed._replace(path=f"/{database}", query="", fragment=""))


@pytest.mark.integration
async def test_production_bootstrap_creates_complete_disposable_schema() -> None:
    """01_schema must create the complete ORM schema from empty and replay safely."""
    admin_dsn = _admin_dsn()
    database = f"clpm_bootstrap_test_{uuid4().hex}"
    admin = await asyncpg.connect(admin_dsn)
    target = None
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        target = await asyncpg.connect(_database_dsn(admin_dsn, database))
        schema_sql = _SCHEMA_SQL.read_text(encoding="utf-8")
        await target.execute(schema_sql)
        # deploy/lib-migrate.sh may retry bootstrap after an interrupted
        # deployment. Replaying the DDL must therefore be harmless.
        await target.execute(schema_sql)

        await target.execute(_SEED_SQL.read_text(encoding="utf-8"))

        rows = await target.fetch(
            "SELECT tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        )
        assert {row["tablename"] for row in rows} == set(Base.metadata.tables)
        assert await target.fetchval("SELECT COUNT(*) FROM sys_user") >= 5

        # Alembic autogenerate does not compare PostgreSQL CHECK expressions.
        # Guard the named checks of the 16 convergence tables explicitly.
        expected_checks = {
            constraint.name
            for table_name in _CONVERGED_TABLES
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint) and constraint.name
        }
        actual_checks = {
            row["conname"]
            for row in await target.fetch(
                "SELECT con.conname "
                "FROM pg_catalog.pg_constraint con "
                "JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace "
                "WHERE ns.nspname = 'public' AND con.contype = 'c' "
                "AND rel.relname = ANY($1::text[])",
                sorted(_CONVERGED_TABLES),
            )
        }
        assert expected_checks <= actual_checks

        # Validate final columns, types, nullability, indexes and constraints
        # against ORM metadata. Server defaults/comments are intentionally
        # ignored, matching alembic/env.py convergence policy.
        await target.close()
        target = None
        parsed_target_dsn = urlsplit(_database_dsn(admin_dsn, database))
        sqlalchemy_dsn = urlunsplit(parsed_target_dsn._replace(scheme="postgresql+asyncpg"))
        engine = create_async_engine(sqlalchemy_dsn)
        try:
            async with engine.connect() as conn:

                def _schema_diff(connection):  # noqa: ANN202
                    from alembic.autogenerate import produce_migrations
                    from alembic.migration import MigrationContext

                    context = MigrationContext.configure(
                        connection,
                        opts={"compare_server_default": False, "compare_type": True},
                    )
                    return produce_migrations(context, Base.metadata).upgrade_ops.ops

                operations = await conn.run_sync(_schema_diff)
        finally:
            await engine.dispose()

        from alembic.operations.ops import (
            AddColumnOp,
            AddConstraintOp,
            AlterColumnOp,
            CreateIndexOp,
            CreateTableOp,
            DropColumnOp,
            DropConstraintOp,
            DropIndexOp,
            DropTableOp,
        )

        structural_types = (
            AddColumnOp,
            AddConstraintOp,
            CreateIndexOp,
            CreateTableOp,
            DropColumnOp,
            DropConstraintOp,
            DropIndexOp,
            DropTableOp,
        )
        violations: list[str] = []

        def _describe(operation) -> str:  # noqa: ANN001, ANN202
            payload = operation.to_diff_tuple()
            return repr(payload)

        def _walk(operation) -> None:  # noqa: ANN001, ANN202
            if isinstance(operation, structural_types):
                violations.append(_describe(operation))
            elif isinstance(operation, AlterColumnOp):
                # Match alembic/env.py: defaults/comments are intentionally
                # outside the structural convergence gate.
                if operation.modify_type is not None or operation.modify_nullable is not None:
                    violations.append(_describe(operation))
            for child in getattr(operation, "ops", None) or []:
                _walk(child)

        for operation in operations:
            _walk(operation)
        assert not violations, "生产 bootstrap 与 ORM 仍有结构漂移:\n" + "\n".join(violations)
    finally:
        if target is not None:
            await target.close()
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()
