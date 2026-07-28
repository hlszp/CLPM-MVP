"""alembic 收敛迁移集成测试 — 真实 PG 验证 schema 漂移收敛（P2 数据正确性整改）.

覆盖：
1. 收敛迁移 ``d4e5f6a7b8c9`` 的 upgrade/downgrade 往返无错；
2. upgrade 后 6 个时间戳列在库中为 NOT NULL；
3. autogen 结构性差异为零（约束/索引/类型/可空性），
   仅余 comment/server_default 噪声（既有 env.py 配置
   ``compare_server_default=True`` + alembic 注释比对所致，非结构漂移）。

测试前提：
- 本地开发 PG 可达（默认读取 app settings 的 postgres_dsn，
  可用环境变量 TEST_DATABASE_URL 覆盖）
- 库已 ``alembic upgrade head``（含 d4e5f6a7b8c9）

运行方式：
    cd backend && uv run pytest tests/integration/test_alembic_convergence.py \
        -v -m integration --no-header

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

#: 收敛迁移补齐 NOT NULL 的（表, 列）
_NOT_NULL_TARGETS: tuple[tuple[str, str], ...] = tuple(
    (table, column)
    for table in ("loop_ledger", "plant_node", "sys_user")
    for column in ("created_at", "updated_at")
)


def _database_url() -> str:
    """测试库连接串：优先 TEST_DATABASE_URL，否则取 app settings。"""
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    from app.core.config import settings

    return settings.postgres_dsn


def _pg_reachable() -> bool:
    """快速探测 PG 是否可达，不可达则 skip 而不是报错。"""
    url = _database_url()
    host = url.split("@")[-1].split("/")[0].split(":")[0]
    port = int(url.split("@")[-1].split("/")[0].split(":")[1])
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="本地开发 PG 不可达"),
]


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """在 backend/ 目录下执行 alembic 子命令。"""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_convergence_migration_roundtrip() -> None:
    """upgrade/downgrade 往返无错，结束后回到 head。"""
    downgrade = _run_alembic("downgrade", "-1")
    assert downgrade.returncode == 0, f"downgrade 失败: {downgrade.stderr}"
    try:
        current = _run_alembic("current")
        assert "c3d4e5f6a7b8" in current.stdout, current.stdout
    finally:
        upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, f"upgrade 失败: {upgrade.stderr}"
    current = _run_alembic("current")
    assert "d4e5f6a7b8c9" in current.stdout, current.stdout


async def test_timestamp_columns_not_null_in_db() -> None:
    """upgrade 后 6 个时间戳列在 information_schema 中为 NOT NULL。"""
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:
            for table, column in _NOT_NULL_TARGETS:
                result = await conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = :column"
                    ),
                    {"table": table, "column": column},
                )
                is_nullable = result.scalar_one()
                assert is_nullable == "NO", f"{table}.{column} 在库中应为 NOT NULL"
    finally:
        await engine.dispose()


async def test_no_structural_autogen_drift() -> None:
    """autogen 不再产生结构性 ops（约束/索引/类型/可空性变更）。

    comment/server_default 噪声除外：既有 env.py 配置
    ``compare_server_default=True`` 且 alembic 默认比对列注释，
    库中历史迁移未写注释/服务端默认值，属于已知非结构噪声。
    """
    from alembic.autogenerate import produce_migrations
    from alembic.migration import MigrationContext
    from alembic.operations.ops import (
        AddConstraintOp,
        AlterColumnOp,
        CreateIndexOp,
        CreateTableOp,
        DropConstraintOp,
        DropIndexOp,
        DropTableOp,
    )

    from app.models import Base

    structural_types = (
        AddConstraintOp,
        CreateIndexOp,
        CreateTableOp,
        DropConstraintOp,
        DropIndexOp,
        DropTableOp,
    )

    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as conn:

            def _check(connection):  # noqa: ANN202
                ctx = MigrationContext.configure(
                    connection,
                    opts={"compare_server_default": False, "compare_type": True},
                )
                return produce_migrations(ctx, Base.metadata).upgrade_ops.ops

            ops = await conn.run_sync(_check)
    finally:
        await engine.dispose()

    violations: list[str] = []

    def _walk(op) -> None:  # noqa: ANN001, ANN202
        if isinstance(op, structural_types):
            violations.append(repr(op))
        elif isinstance(op, AlterColumnOp):
            # 仅允许注释变更；类型/可空性/默认值变更视为结构漂移
            if (
                op.modify_type is not None
                or op.modify_nullable is not None
                or op.modify_server_default is not False
            ):
                violations.append(repr(op))
        for sub in getattr(op, "ops", None) or []:
            _walk(sub)

    for op in ops:
        _walk(op)
    assert not violations, "autogen 仍存在结构性漂移:\n" + "\n".join(violations)
