#!/usr/bin/env python3
"""TDengine 三级降采样部署脚本.

创建 Stream（流式计算）实现秒级→分钟级→小时级三级降采样：
    秒级原始数据（signal_sim.st_loop_data）      KEEP 35d  → KPI 评估与辨识
        ↓ stream_loop_1min（每分钟聚合）
    分钟级数据（signal_sim_agg.st_loop_data_1min） KEEP 5y   → 趋势分析
        ↓ stream_loop_1h（每小时聚合）
    小时级数据（signal_sim_agg.st_loop_data_1h）   KEEP 5y   → 年度报表

存储预估（1000 回路 × 1 年）：
    秒级 35 天:  ~112 GB
    分钟级 1 年: ~19 GB
    小时级 5 年: ~1.6 GB
    合计:        ~133 GB（vs 秒级全留 1.19 TB，节省 89%）

使用方式：
    # 本地开发环境（使用 .env 中的 TDengine 配置）
    cd backend && uv run python scripts/tdengine_downsampling.py

    # 指定 TDengine 连接
    TDENGINE_HOST=localhost TDENGINE_PORT=7115 \
    TDENGINE_USER=root TDENGINE_PASSWORD=taosdata \
    TDENGINE_DB=signal_sim \
    uv run python scripts/tdengine_downsampling.py

    # 仅检查状态（不创建）
    uv run python scripts/tdengine_downsampling.py --check-only

依赖：requests（backend 已安装）

注意：
    - TDengine 3.3.0+ 支持 CREATE STREAM 语法
    - FILL_HISTORY 1 会回填历史数据（从源表最早数据开始）
    - 回填大量历史数据可能需要较长时间（27 回路 × 6 天 ≈ 2 分钟）
    - 脚本幂等：重复执行不会创建重复的 stream
"""

from __future__ import annotations

import argparse
import os
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

TDENGINE_HOST = os.getenv("TDENGINE_HOST", "localhost")
# REST API 端口（不是 native 协议端口）
TDENGINE_PORT = int(os.getenv("TDENGINE_REST_PORT", os.getenv("TDENGINE_PORT", "7115")))
TDENGINE_USER = os.getenv("TDENGINE_USER", "root")
TDENGINE_PASSWORD = os.getenv("TDENGINE_PASSWORD", "taosdata")
TDENGINE_DB = os.getenv("TDENGINE_DB", "signal_sim")

# 聚合数据库名
AGG_DB = os.getenv("TDENGINE_AGG_DB", "signal_sim_agg")

# 秒级数据保留天数（KPI 窗口 30 天 + 5 天缓冲）
RAW_KEEP_DAYS = int(os.getenv("TDENGINE_RAW_KEEP_DAYS", "35"))
# 聚合数据保留天数（5 年）
AGG_KEEP_DAYS = int(os.getenv("TDENGINE_AGG_KEEP_DAYS", "1825"))

# Stream 名称
STREAM_1MIN = "stream_loop_1min"
STREAM_1H = "stream_loop_1h"

# 目标表名
TABLE_1MIN = "st_loop_data_1min"
TABLE_1H = "st_loop_data_1h"


# ---------------------------------------------------------------------------
# TDengine REST 客户端
# ---------------------------------------------------------------------------


class TDengineClient:
    """TDengine REST API 客户端。"""

    def __init__(self, host: str, port: int, user: str, password: str):
        self.base_url = f"http://{host}:{port}/rest/sql"
        self.auth = (user, password)

    def execute(self, sql: str, db: str | None = None) -> dict:
        """执行 SQL，返回 JSON 响应。"""
        url = f"{self.base_url}/{db}" if db else self.base_url
        resp = requests.post(url, data=sql, auth=self.auth, timeout=30)
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(
                f"TDengine error: {result.get('desc')} (code={result.get('code')})\nSQL: {sql}"
            )
        return result

    def execute_many(self, statements: list[tuple[str, str | None, str]]) -> None:
        """执行多条 SQL。每条为 (sql, db, description)。"""
        for sql, db, desc in statements:
            print(f"  → {desc}... ", end="", flush=True)
            try:
                self.execute(sql, db=db)
                print("OK")
            except RuntimeError as e:
                if "already exists" in str(e).lower() or "Duplicate" in str(e):
                    print("SKIP (already exists)")
                else:
                    print("FAILED")
                    raise

    def check_version(self) -> str:
        """检查 TDengine 版本（需 3.3.0+ 支持 CREATE STREAM）。"""
        result = self.execute("SELECT SERVER_VERSION()")
        version = result.get("data", [["0.0.0.0"]])[0][0]
        print(f"TDengine version: {version}")
        parts = version.split(".")
        if len(parts) >= 2:
            major, minor = int(parts[0]), int(parts[1])
            if major < 3 or (major == 3 and minor < 3):
                print(f"WARNING: CREATE STREAM requires TDengine 3.3.0+, current is {version}")
        return version


# ---------------------------------------------------------------------------
# 降采样 DDL
# ---------------------------------------------------------------------------


def get_ddl_statements(
    raw_db: str, agg_db: str, raw_keep: int, agg_keep: int
) -> list[tuple[str, str | None, str]]:
    """生成降采样所需的全部 DDL 语句。"""
    return [
        # 1. 创建聚合数据库（5 年保留）
        (
            f"CREATE DATABASE IF NOT EXISTS {agg_db} KEEP {agg_keep}d DURATION 100d PRECISION 'ms'",
            None,
            f"创建聚合数据库 {agg_db}（KEEP={agg_keep}d）",
        ),
        # 2. 调整原始数据库保留策略
        (
            f"ALTER DATABASE {raw_db} KEEP {raw_keep}",
            None,
            f"调整 {raw_db} 保留策略（KEEP={raw_keep}d）",
        ),
        # 3. 删除旧 stream（幂等）
        (
            f"DROP STREAM IF EXISTS {STREAM_1MIN}",
            agg_db,
            f"删除旧 stream {STREAM_1MIN}",
        ),
        (
            f"DROP STREAM IF EXISTS {STREAM_1H}",
            agg_db,
            f"删除旧 stream {STREAM_1H}",
        ),
        # 4. 删除旧目标表（幂等，stream 会自动重建）
        (
            f"DROP STABLE IF EXISTS {TABLE_1MIN}",
            agg_db,
            f"删除旧目标表 {TABLE_1MIN}",
        ),
        (
            f"DROP STABLE IF EXISTS {TABLE_1H}",
            agg_db,
            f"删除旧目标表 {TABLE_1H}",
        ),
        # 5. 创建分钟级 stream（FILL_HISTORY 回填历史数据）
        (
            f"""CREATE STREAM IF NOT EXISTS {STREAM_1MIN}
TRIGGER AT_ONCE FILL_HISTORY 1
INTO {TABLE_1MIN}
SUBTABLE(CONCAT(tbname, '_1m'))
AS SELECT
    _wstart AS ts,
    AVG(pv) AS pv_avg, MIN(pv) AS pv_min, MAX(pv) AS pv_max, COUNT(pv) AS pv_cnt,
    AVG(sp) AS sp_avg, MIN(sp) AS sp_min, MAX(sp) AS sp_max,
    AVG(op) AS op_avg, MIN(op) AS op_min, MAX(op) AS op_max,
    AVG(pid_p) AS pid_p_avg, AVG(pid_i) AS pid_i_avg, AVG(pid_d) AS pid_d_avg,
    COUNT(*) AS quality_total_cnt
FROM {raw_db}.st_loop_data
PARTITION BY tbname
INTERVAL(1m)""",
            agg_db,
            f"创建分钟级 stream {STREAM_1MIN}",
        ),
        # 6. 创建小时级 stream
        (
            f"""CREATE STREAM IF NOT EXISTS {STREAM_1H}
TRIGGER AT_ONCE FILL_HISTORY 1
INTO {TABLE_1H}
SUBTABLE(CONCAT(tbname, '_1h'))
AS SELECT
    _wstart AS ts,
    AVG(pv_avg) AS pv_avg, MIN(pv_min) AS pv_min, MAX(pv_max) AS pv_max, SUM(pv_cnt) AS pv_cnt,
    AVG(sp_avg) AS sp_avg, MIN(sp_min) AS sp_min, MAX(sp_max) AS sp_max,
    AVG(op_avg) AS op_avg, MIN(op_min) AS op_min, MAX(op_max) AS op_max,
    AVG(pid_p_avg) AS pid_p_avg, AVG(pid_i_avg) AS pid_i_avg, AVG(pid_d_avg) AS pid_d_avg,
    SUM(quality_total_cnt) AS quality_total_cnt
FROM {agg_db}.{TABLE_1MIN}
PARTITION BY tbname
INTERVAL(1h)""",
            agg_db,
            f"创建小时级 stream {STREAM_1H}",
        ),
    ]


# ---------------------------------------------------------------------------
# 状态检查
# ---------------------------------------------------------------------------


def check_status(client: TDengineClient, raw_db: str, agg_db: str) -> None:
    """检查降采样状态。"""
    print("\n=== 降采样状态检查 ===")

    # Stream 状态
    result = client.execute("SHOW STREAMS", db=agg_db)
    streams = result.get("data", [])
    if not streams:
        print("⚠️  无 stream")
    else:
        for row in streams:
            print(f"  stream: {row[0]}, status: {row[1]}")

    # 聚合数据统计
    for table, label in [(TABLE_1MIN, "分钟级"), (TABLE_1H, "小时级")]:
        try:
            result = client.execute(
                f"SELECT COUNT(*), FIRST(ts), LAST(ts) FROM {table}",
                db=agg_db,
            )
            row = result.get("data", [[0, "N/A", "N/A"]])[0]
            print(f"  {label}数据: {row[0]:,} 行, 范围 {row[1]} ~ {row[2]}")
        except RuntimeError:
            print(f"  {label}数据: 表不存在")

    # 原始数据统计
    try:
        result = client.execute(
            "SELECT COUNT(*), FIRST(ts), LAST(ts) FROM st_loop_data",
            db=raw_db,
        )
        row = result.get("data", [[0, "N/A", "N/A"]])[0]
        print(f"  秒级数据: {row[0]:,} 行, 范围 {row[1]} ~ {row[2]}")
    except RuntimeError:
        print("  秒级数据: 表不存在")

    # 数据库 KEEP
    for db_name in [raw_db, agg_db]:
        result = client.execute(
            f"SELECT name, `keep`, `duration` "
            f"FROM information_schema.ins_databases WHERE name='{db_name}'"
        )
        row = result.get("data", [["?", "?", "?"]])[0]
        print(f"  数据库 {db_name}: KEEP={row[1]}, DURATION={row[2]}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="TDengine 三级降采样部署")
    parser.add_argument("--check-only", action="store_true", help="仅检查状态，不执行 DDL")
    parser.add_argument("--host", default=TDENGINE_HOST, help="TDengine 主机")
    parser.add_argument("--port", type=int, default=TDENGINE_PORT, help="TDengine REST API 端口")
    parser.add_argument("--user", default=TDENGINE_USER, help="TDengine 用户名")
    parser.add_argument("--password", default=TDENGINE_PASSWORD, help="TDengine 密码")
    parser.add_argument("--raw-db", default=TDENGINE_DB, help="原始数据库名")
    parser.add_argument("--agg-db", default=AGG_DB, help="聚合数据库名")
    parser.add_argument("--raw-keep", type=int, default=RAW_KEEP_DAYS, help="秒级数据保留天数")
    parser.add_argument("--agg-keep", type=int, default=AGG_KEEP_DAYS, help="聚合数据保留天数")
    args = parser.parse_args()

    client = TDengineClient(args.host, args.port, args.user, args.password)

    print("=== TDengine 三级降采样部署 ===")
    print(f"连接: http://{args.host}:{args.port}")
    print(f"原始库: {args.raw_db} (KEEP={args.raw_keep}d)")
    print(f"聚合库: {args.agg_db} (KEEP={args.agg_keep}d)")

    # 检查版本
    client.check_version()

    if args.check_only:
        check_status(client, args.raw_db, args.agg_db)
        return 0

    # 执行 DDL
    print("\n=== 执行 DDL ===")
    ddl = get_ddl_statements(args.raw_db, args.agg_db, args.raw_keep, args.agg_keep)
    client.execute_many(ddl)

    # 等待 stream 启动
    print("\n等待 stream 启动（3s）...")
    time.sleep(3)

    # 检查状态
    check_status(client, args.raw_db, args.agg_db)

    print("\n=== 部署完成 ===")
    print("秒级数据 → signal_sim.st_loop_data (KEEP 35d)")
    print("分钟级   → signal_sim_agg.st_loop_data_1min (KEEP 5y)")
    print("小时级   → signal_sim_agg.st_loop_data_1h (KEEP 5y)")
    print("\n注意：FILL_HISTORY 正在回填历史数据，大规模数据可能需要数分钟。")
    print("使用 --check-only 查看回填进度。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
