"""P7-03 回退演练（隔离 scratch 库，2026-10-16）。

脚本性质：一次性演练工具（不入 CI、不进运行时）。全程只读写自建 scratch 库，
共享 dev 库（clpm@17102/clpm）零写入；finally DROP 演练库。

步骤：
  A. 空库直接 ``alembic upgrade head``（记录链是否可从空库起步——运维口径）
  B. 空库 → 引导 SQL(01+02) → stamp head → downgrade -3
     （跨 p702perm01/p605invest01/p605person01）→ upgrade head →
     二轮 downgrade -3 → upgrade head；每步快照表清单 + 授权数据计数。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import urllib.parse
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _ROOT / "backend"
_SCHEMA_SQL = _ROOT / "db" / "postgresql" / "01_schema.sql"
_SEED_SQL = _ROOT / "db" / "postgresql" / "02_seed_data.sql"


def log(msg: str) -> None:
    print(f"[drill] {msg}", flush=True)


def alembic(db: str, *args: str) -> subprocess.CompletedProcess:
    """对 scratch 库执行 alembic 子命令，POSTGRES_* 环境注入（同集成测试口径）。"""
    dsn = os.environ["CLPM_BOOTSTRAP_TEST_ADMIN_DSN"]
    p = urlsplit(dsn)
    env = {
        **os.environ,
        "POSTGRES_HOST": p.hostname or "127.0.0.1",
        "POSTGRES_PORT": str(p.port or 5432),
        "POSTGRES_USER": p.username or "clpm",
        "POSTGRES_PASSWORD": urllib.parse.unquote(p.password or ""),
        "POSTGRES_DB": db,
        "JWT_SECRET_KEY": "ci-test-secret-key-at-least-32-characters-long!!!",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    log(f"$ alembic {' '.join(args)}  (db={db}) -> rc={proc.returncode}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-600:]
        log(f"  stderr/stdout tail:\n{tail}")
    return proc


async def snapshot(db: str) -> dict:
    dsn = os.environ["CLPM_BOOTSTRAP_TEST_ADMIN_DSN"]
    raw = urlunsplit(urlsplit(dsn)._replace(path=f"/{db}"))
    conn = await asyncpg.connect(raw)
    tables = {
        r[0]
        for r in (await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    }

    async def count(sql: str) -> int:
        try:
            return await conn.fetchval(sql)
        except Exception:
            return -1

    snaps = {
        "tables": tables,
        "sys_permission": await count("SELECT count(*) FROM sys_permission"),
        "sys_authz_policy": await count("SELECT count(*) FROM sys_authz_policy"),
        "sys_role_permission": await count("SELECT count(*) FROM sys_role_permission"),
        "loop_investigation": await count("SELECT count(*) FROM loop_investigation"),
        "user_loop_follow": await count("SELECT count(*) FROM user_loop_follow"),
    }
    ver = await conn.fetchval("SELECT version_num FROM alembic_version")
    snaps["alembic_version"] = ver
    await conn.close()
    return snaps


def diff(name_a: str, a: dict, name_b: str, b: dict) -> None:
    ta, tb = a["tables"], b["tables"]
    only_a, only_b = sorted(ta - tb), sorted(tb - ta)
    log(f"  diff {name_a} -> {name_b}: 表 -{only_a} +{only_b}")
    for k in (
        "alembic_version",
        "sys_permission",
        "sys_authz_policy",
        "sys_role_permission",
        "loop_investigation",
        "user_loop_follow",
    ):
        if a[k] != b[k]:
            log(f"    {k}: {a[k]} -> {b[k]}")


async def main() -> None:
    admin = await asyncpg.connect(os.environ["CLPM_BOOTSTRAP_TEST_ADMIN_DSN"])
    probe_a = f"clpm_p703_probeA_{uuid.uuid4().hex[:6]}"
    drill = f"clpm_p703_drill_{uuid.uuid4().hex[:6]}"
    await admin.execute(f'CREATE DATABASE "{probe_a}"')
    await admin.execute(f'CREATE DATABASE "{drill}"')
    log(f"scratch: probeA={probe_a} drill={drill}（finally 全 DROP）")

    try:
        # ---- A. 空库直接 upgrade head ----
        log("=== 步骤 A：空库直接 upgrade head（链完整性探测）===")
        proc = alembic(probe_a, "upgrade", "head")
        log(
            f"A 结论：rc={proc.returncode}"
            + (
                "（空库可全链 upgrade）"
                if proc.returncode == 0
                else "（空库不可全链 upgrade——生产口径仍需 bootstrap 起步）"
            )
        )

        # ---- B. 回退演练主链 ----
        log("=== 步骤 B0：空库 → 引导 SQL(01+02) → stamp head ===")
        dsn = os.environ["CLPM_BOOTSTRAP_TEST_ADMIN_DSN"]
        raw = urlunsplit(urlsplit(dsn)._replace(path=f"/{drill}"))
        conn = await asyncpg.connect(raw)
        await conn.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
        await conn.execute(_SEED_SQL.read_text(encoding="utf-8"))
        await conn.close()
        log("bootstrap 01+02 执行 rc=0（asyncpg execute 无异常）")
        assert alembic(drill, "stamp", "head").returncode == 0

        s0 = await snapshot(drill)
        log(
            f"S0(stamp head): version={s0['alembic_version']} tables={len(s0['tables'])} "
            f"perm={s0['sys_permission']} ledger={s0['sys_authz_policy']}"
        )

        log("=== 步骤 B1：downgrade -3（p702perm01 -> p504srvflt01）===")
        assert alembic(drill, "downgrade", "-3").returncode == 0
        s1 = await snapshot(drill)
        log(
            f"S1(downgrade -3): version={s1['alembic_version']} tables={len(s1['tables'])} "
            f"perm={s1['sys_permission']} ledger={s1['sys_authz_policy']}"
        )
        diff("S0", s0, "S1", s1)

        log("=== 步骤 B2：upgrade head（回退后重升级）===")
        assert alembic(drill, "upgrade", "head").returncode == 0
        s2 = await snapshot(drill)
        log(
            f"S2(upgrade head): version={s2['alembic_version']} tables={len(s2['tables'])} "
            f"perm={s2['sys_permission']} ledger={s2['sys_authz_policy']}"
        )
        diff("S1", s1, "S2", s2)
        assert alembic(drill, "check").returncode == 0, "S2 后 alembic check 非零"

        log("=== 步骤 B3：二轮 downgrade -3 → upgrade head（幂等）===")
        assert alembic(drill, "downgrade", "-3").returncode == 0
        assert alembic(drill, "upgrade", "head").returncode == 0
        s3 = await snapshot(drill)
        log(
            f"S3(二轮回): version={s3['alembic_version']} tables={len(s3['tables'])} "
            f"perm={s3['sys_permission']} ledger={s3['sys_authz_policy']}"
        )
        if s2["tables"] == s3["tables"] and all(
            s2[k] == s3[k] for k in ("sys_permission", "sys_authz_policy", "sys_role_permission")
        ):
            log("幂等结论：二轮 S3 与 S2 表集合与授权数据完全一致 PASS")
        else:
            diff("S2", s2, "S3", s3)
            raise SystemExit("S3 != S2 幂等失败")
        assert alembic(drill, "check").returncode == 0
        log("=== 演练全周期 PASS ===")
    finally:
        for db in (probe_a, drill):
            await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.close()
        log(f"finally：DROP {probe_a}, {drill} 完成（scratch 自清）")


if __name__ == "__main__":
    asyncio.run(main())
