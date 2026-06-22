"""数据库性能测试（PERF-DB-001 ~ PERF-DB-003）.

独立 Python 脚本（非 Locust），直接连接数据库测试查询性能。

用例:
    PERF-DB-001: TDengine 查询 1 万点（< 200ms）
    PERF-DB-002: TDengine 查询 24 小时波形（LTTB 降采样后 < 500ms）
    PERF-DB-003: PostgreSQL 回路列表（1200 回路 < 100ms）

环境变量（覆盖默认值，与 backend/.env 对齐）:
    POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
    TDENGINE_HOST / TDENGINE_PORT / TDENGINE_USER / TDENGINE_PASSWORD / TDENGINE_DB

运行:
    cd perf/scenarios
    python db_perf.py                    # 运行全部 3 个用例
    python db_perf.py --case db-001      # 仅运行 PERF-DB-001
    python db_perf.py --case db-002      # 仅运行 PERF-DB-002
    python db_perf.py --case db-003      # 仅运行 PERF-DB-003
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

PG_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "user": os.environ.get("POSTGRES_USER", "clpm"),
    "password": os.environ.get("POSTGRES_PASSWORD", "clpm_dev_2026"),
    "dbname": os.environ.get("POSTGRES_DB", "clpm"),
}

TD_CONFIG = {
    "host": os.environ.get("TDENGINE_HOST", "localhost"),
    "port": int(os.environ.get("TDENGINE_PORT", "6030")),
    "user": os.environ.get("TDENGINE_USER", "root"),
    "password": os.environ.get("TDENGINE_PASSWORD", "taosdata"),
    "database": os.environ.get("TDENGINE_DB", "clpm_ts"),
}

# 重复次数（用于取平均/P95）
REPEAT = 20

# 已知子表名（来自 db/tdengine/01_supertable.sql）
TD_SUBTABLES = ["d_loop_hds_rx_tic_101", "d_loop_hds_fr_fic_201", "d_loop_hdc_rx_tic_301"]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _percentile(values: list[float], p: float) -> float:
    """简单百分位计算（p 取 0~100）。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _print_result(case_id: str, name: str, threshold_ms: float, latencies: list[float]) -> bool:
    """打印测试结果，返回是否通过."""
    avg = statistics.mean(latencies) if latencies else 0.0
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    passed = p95 <= threshold_ms
    status = "PASS" if passed else "FAIL"
    print(f"\n{'=' * 70}")
    print(f"{case_id}: {name}")
    print(f"{'=' * 70}")
    print(f"  重复次数:  {len(latencies)}")
    print(f"  平均:      {avg:.2f} ms")
    print(f"  P95:       {p95:.2f} ms  (阈值 {threshold_ms} ms)")
    print(f"  P99:       {p99:.2f} ms")
    print(f"  最小/最大: {min(latencies):.2f} / {max(latencies):.2f} ms")
    print(f"  结果:      {status}")
    return passed


def _lttb_downsample(data: list, threshold: int) -> list:
    """简化版 LTTB（Largest-Triangle-Three-Buckets）降采样.

    仅用于性能测试计时，生产实现见 backend/app/services/waveform.py。
    """
    if len(data) <= threshold:
        return data
    bucket_size = len(data) / threshold
    sampled = []
    for i in range(threshold):
        idx = int(i * bucket_size)
        sampled.append(data[idx])
    return sampled


# ---------------------------------------------------------------------------
# PERF-DB-001: TDengine 查询 1 万点（< 200ms）
# ---------------------------------------------------------------------------


async def perf_db_001() -> bool:
    """TDengine 查询约 1 万个数据点，验收 P95 < 200ms.

    使用 SELECT * FROM <子表> LIMIT 10000 模拟 1 万点查询。
    """
    print("\n[PERF-DB-001] TDengine 查询 1 万点...")
    try:
        import taosws  # type: ignore
    except ImportError:
        print("  [WARN] taosws 未安装，跳过。请: pip install taospy")
        return False

    dsn = f"ws://{TD_CONFIG['host']}:{TD_CONFIG['port'] + 1000}/rest/ws"
    latencies: list[float] = []
    try:
        async with await taosws.connect(
            url=dsn,
            user=TD_CONFIG["user"],
            password=TD_CONFIG["password"],
            database=TD_CONFIG["database"],
        ) as conn:
            table = TD_SUBTABLES[0]
            for _ in range(REPEAT):
                sql = f"SELECT ts, pv, sp, op FROM {TD_CONFIG['database']}.{table} LIMIT 10000"
                t0 = time.perf_counter()
                result = await conn.query(sql)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed_ms)
                # 验证返回行数
                _ = sum(1 for _ in result)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 连接/查询失败: {exc}")
        return False

    return _print_result("PERF-DB-001", "TDengine 查询 1 万点", 200.0, latencies)


# ---------------------------------------------------------------------------
# PERF-DB-002: TDengine 查询 24 小时波形（LTTB 降采样后 < 500ms）
# ---------------------------------------------------------------------------


async def perf_db_002() -> bool:
    """TDengine 查询 24 小时波形数据，验收 P95 < 500ms.

    模拟后端 waveform 服务：查询 24 小时原始数据 + 应用 LTTB 降采样到 5000 点。
    """
    print("\n[PERF-DB-002] TDengine 查询 24 小时波形（LTTB 降采样）...")
    try:
        import taosws  # type: ignore
    except ImportError:
        print("  [WARN] taosws 未安装，跳过。请: pip install taospy")
        return False

    dsn = f"ws://{TD_CONFIG['host']}:{TD_CONFIG['port'] + 1000}/rest/ws"
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=24)
    latencies: list[float] = []

    try:
        async with await taosws.connect(
            url=dsn,
            user=TD_CONFIG["user"],
            password=TD_CONFIG["password"],
            database=TD_CONFIG["database"],
        ) as conn:
            table = TD_SUBTABLES[0]
            for _ in range(REPEAT):
                sql = (
                    f"SELECT ts, pv, pv_quality FROM {TD_CONFIG['database']}.{table} "
                    f"WHERE ts >= '{_iso(start_time)}' AND ts <= '{_iso(end_time)}' "
                    f"ORDER BY ts ASC"
                )
                t0 = time.perf_counter()
                result = await conn.query(sql)
                rows = list(result)
                # 模拟 LTTB 降采样到 5000 点
                _lttb_downsample(rows, 5000)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed_ms)
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 连接/查询失败: {exc}")
        return False

    return _print_result("PERF-DB-002", "TDengine 24h 波形 + LTTB 降采样", 500.0, latencies)


# ---------------------------------------------------------------------------
# PERF-DB-003: PostgreSQL 回路列表（1200 回路 < 100ms）
# ---------------------------------------------------------------------------


def perf_db_003() -> bool:
    """PostgreSQL 查询回路列表，验收 P95 < 100ms.

    假设 loop_ledger 表有 1200 条回路（生产规模），查询分页第一页 20 条。
    若实际数据不足，脚本仍可运行，但需在 1200 条数据规模下验证。
    """
    print("\n[PERF-DB-003] PostgreSQL 回路列表查询（目标 1200 回路规模）...")
    try:
        import psycopg2  # type: ignore
    except ImportError:
        print("  [WARN] psycopg2 未安装，跳过。请: pip install psycopg2-binary")
        return False

    latencies: list[float] = []
    conn = None
    try:
        conn = psycopg2.connect(**PG_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()

        # 先统计回路总数
        cur.execute("SELECT COUNT(*) FROM loop_ledger")
        total = cur.fetchone()[0]
        print(f"  当前 loop_ledger 记录数: {total}（目标规模 1200）")

        for _ in range(REPEAT):
            sql = (
                "SELECT id, tag_name, description, unit_id, control_mode, "
                "is_active, status, score, updated_at "
                "FROM loop_ledger ORDER BY created_at DESC LIMIT 20 OFFSET 0"
            )
            t0 = time.perf_counter()
            cur.execute(sql)
            rows = cur.fetchall()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            _ = len(rows)

        # 带筛选条件查询（模拟真实业务）
        for _ in range(REPEAT):
            sql = (
                "SELECT id, tag_name, description, unit_id, control_mode, "
                "is_active, status, score, updated_at "
                "FROM loop_ledger WHERE is_active = TRUE AND control_mode = 'Auto' "
                "ORDER BY score ASC LIMIT 20 OFFSET 0"
            )
            t0 = time.perf_counter()
            cur.execute(sql)
            cur.fetchall()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)

        cur.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 连接/查询失败: {exc}")
        return False
    finally:
        if conn:
            conn.close()

    return _print_result("PERF-DB-003", "PostgreSQL 回路列表（1200 规模）", 100.0, latencies)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def _run_async(cases: list[str]) -> dict[str, bool]:
    """运行异步用例（TDengine）."""
    results: dict[str, bool] = {}
    if "db-001" in cases:
        results["PERF-DB-001"] = await perf_db_001()
    if "db-002" in cases:
        results["PERF-DB-002"] = await perf_db_002()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="CLPM 数据库性能测试")
    parser.add_argument(
        "--case",
        choices=["db-001", "db-002", "db-003", "all"],
        default="all",
        help="选择运行的用例（默认 all）",
    )
    args = parser.parse_args()

    cases = ["db-001", "db-002", "db-003"] if args.case == "all" else [args.case]

    results: dict[str, bool] = {}

    # 异步用例（TDengine）
    async_cases = [c for c in cases if c in ("db-001", "db-002")]
    if async_cases:
        results.update(asyncio.run(_run_async(async_cases)))

    # 同步用例（PostgreSQL）
    if "db-003" in cases:
        results["PERF-DB-003"] = perf_db_003()

    # 汇总
    print(f"\n{'=' * 70}")
    print("数据库性能测试汇总")
    print(f"{'=' * 70}")
    for case_id, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {case_id}: {status}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
