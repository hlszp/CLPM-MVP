"""将 db/postgresql 引导 SQL 应用到目标 PostgreSQL（CI 与本地验证用）。

背景（整改 S1-a / 新发现 G51）
--------------------------------
本仓库的 PostgreSQL schema 有两个来源：

1. initdb 引导脚本 db/postgresql/01_schema.sql + 02_seed_data.sql
   （生产与开发容器的实际路径，由 docker-entrypoint-initdb.d 执行）；
2. alembic 迁移链（alembic upgrade head，部署脚本 deploy/lib-migrate.sh 调用）。

关键事实：迁移链不能从空库构建。早期迁移以 01_schema.sql 已建好的表为前提
（如 ALTER TABLE loop_ledger ADD COLUMN ...），在全新空库上执行
alembic upgrade head 会以 UndefinedTableError: relation "loop_ledger" does not
exist 失败。因此正确的验证口径是「引导 SQL -> stamp head -> 漂移检查」，
而不是「空库 -> upgrade head」。

本脚本用 asyncpg 直接执行引导 SQL（不依赖 psql 客户端，CI runner 通用），
遇错即中止并回显上下文，模拟 initdb 的 ON_ERROR_STOP 语义。

用法::

    uv run python scripts/apply_bootstrap_sql.py                 # 应用 01 + 02
    uv run python scripts/apply_bootstrap_sql.py --schema-only   # 仅 01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

from app.core.config import settings

_DB_DIR = Path(__file__).resolve().parent.parent.parent / "db" / "postgresql"
_SCHEMA_SQL = _DB_DIR / "01_schema.sql"
_SEED_SQL = _DB_DIR / "02_seed_data.sql"


async def _apply(conn: asyncpg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    try:
        await conn.execute(sql)
    except Exception as exc:  # noqa: BLE001 - 需回显文件名与原始错误
        print(f"引导 SQL 执行失败: {path.name}", file=sys.stderr)
        print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    print(f"  已应用 {path.name}（{len(sql)} 字符）")


async def main(schema_only: bool) -> int:
    targets = [_SCHEMA_SQL] if schema_only else [_SCHEMA_SQL, _SEED_SQL]
    for t in targets:
        if not t.exists():
            print(f"缺少引导 SQL: {t}", file=sys.stderr)
            return 2

    conn = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
    )
    try:
        for t in targets:
            await _apply(conn, t)
    finally:
        await conn.close()
    print("引导 SQL 应用完成")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="应用 db/postgresql 引导 SQL")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="只应用 01_schema.sql，跳过种子数据",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.schema_only)))
