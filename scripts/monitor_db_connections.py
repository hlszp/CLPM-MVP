#!/usr/bin/env python3
"""PG 连接池监控脚本（P2-018）— 定时轮询 + 趋势记录 + 阈值告警.

默认通过后端 ``/health/db-connections`` 端点获取连接数（不依赖 asyncpg，
后端服务运行时即可监控）。可选 ``--dsn`` 直连 PG（用于后端不可用时的诊断）。

用法::

    # 默认：通过后端端点轮询，每 5s，持续 5 分钟
    uv run python scripts/monitor_db_connections.py

    # 自定义间隔和持续时长
    uv run python scripts/monitor_db_connections.py --interval 2 --duration 600

    # 直连 PG 模式（后端不可用时诊断）
    uv run python scripts/monitor_db_connections.py --dsn "postgresql://user:pass@host/db"

输出:
    - 控制台实时输出（时间戳 + 总连接/最大/利用率 + WARN/CRITICAL 告警）
    - CSV 趋势文件 scripts/db_connections_trend.csv
    - 退出码：0=正常，1=曾出现 CRITICAL

告警阈值:
    - >80% max_connections: WARN（黄色）
    - >95% max_connections: CRITICAL（红色，退出码 1）
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# 告警阈值
_WARN_THRESHOLD = 0.80  # 80%
_CRITICAL_THRESHOLD = 0.95  # 95%

# CSV 输出路径
_CSV_PATH = Path(__file__).parent / "db_connections_trend.csv"

# 后端 API 地址
_API_BASE = "http://localhost:7101"

# ANSI 颜色
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def query_via_api(api_base: str) -> dict:
    """通过后端 /health/db-connections 端点查询连接数（无需 asyncpg）。"""
    url = f"{api_base}/health/db-connections"
    with urllib.request.urlopen(url, timeout=5) as resp:
        import json

        data = json.loads(resp.read())
        # 端点返回 {total, max, byApp, utilization}，统一为 by_app 键名
        return {
            "total": data["total"],
            "max": data["max"],
            "by_app": data.get("byApp", {}),
            "utilization": data.get("utilization", 0.0),
        }


async def query_via_asyncpg(dsn: str) -> dict:
    """直连 PG 查询连接数（后端不可用时诊断用）。"""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT COALESCE(application_name, 'unknown') AS app,
                   count(*) AS cnt
            FROM pg_stat_activity
            WHERE datname = current_database()
            GROUP BY application_name
            ORDER BY count(*) DESC
            """
        )
        by_app = {row["app"]: row["cnt"] for row in rows}
        total = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        )
        max_conn = await conn.fetchval("SHOW max_connections")
        return {
            "total": total,
            "max": max_conn,
            "by_app": by_app,
            "utilization": round(total / max_conn * 100, 1) if max_conn else 0.0,
        }
    finally:
        await conn.close()


def format_output(data: dict, timestamp: datetime) -> str:
    """格式化单次轮询输出."""
    total = data["total"]
    max_conn = data["max"]
    util = data["utilization"]
    ts_str = timestamp.strftime("%H:%M:%S")

    # 告警级别
    ratio = total / max_conn if max_conn else 0
    if ratio >= _CRITICAL_THRESHOLD:
        level = f"{_RED}CRITICAL{_RESET}"
        bar_color = _RED
    elif ratio >= _WARN_THRESHOLD:
        level = f"{_YELLOW}WARN{_RESET}"
        bar_color = _YELLOW
    else:
        level = f"{_GREEN}OK{_RESET}"
        bar_color = _GREEN

    # 连接数明细
    app_detail = " ".join(
        f"{k}={v}" for k, v in sorted(data["by_app"].items(), key=lambda x: -x[1])
    )

    # 进度条
    bar_width = 30
    filled = int(bar_width * ratio) if ratio < 1 else bar_width
    bar = f"{bar_color}{'█' * filled}{'░' * (bar_width - filled)}{_RESET}"

    return (
        f"[{ts_str}] {level} {total:>3}/{max_conn} ({util:>5.1f}%) "
        f"|{bar}| {app_detail}"
    )


def write_csv(data: dict, timestamp: datetime) -> None:
    """追加一行到 CSV 趋势文件."""
    is_new = not _CSV_PATH.exists()
    with _CSV_PATH.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "total", "max", "utilization", "by_app"])
        app_summary = ";".join(f"{k}:{v}" for k, v in data["by_app"].items())
        writer.writerow(
            [
                timestamp.isoformat(),
                data["total"],
                data["max"],
                data["utilization"],
                app_summary,
            ]
        )


async def monitor_loop(
    *, api_base: str, dsn: str | None, interval: float, duration: float
) -> int:
    """主监控循环。返回退出码（0=正常，1=曾出现 CRITICAL）。"""
    mode = "direct PG" if dsn else f"API {api_base}"
    print(
        f"PG 连接池监控启动 | 模式 {mode} | 间隔 {interval}s | 持续 {duration}s"
    )
    print(f"CSV 趋势文件: {_CSV_PATH}")
    print("-" * 80)

    had_critical = False
    start = time.monotonic()

    while time.monotonic() - start < duration:
        ts = datetime.now()
        try:
            if dsn:
                data = await query_via_asyncpg(dsn)
            else:
                data = query_via_api(api_base)
            print(format_output(data, ts))
            write_csv(data, ts)

            ratio = data["total"] / data["max"] if data["max"] else 0
            if ratio >= _CRITICAL_THRESHOLD:
                had_critical = True

        except Exception as exc:
            print(f"[{ts.strftime('%H:%M:%S')}] {_RED}ERROR{_RESET} 查询失败: {exc}")

        await asyncio.sleep(interval)

    print("-" * 80)
    print(f"监控结束 | CSV: {_CSV_PATH}")
    return 1 if had_critical else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PG 连接池监控脚本（P2-018）")
    parser.add_argument(
        "--interval", type=float, default=5.0, help="轮询间隔（秒，默认 5）"
    )
    parser.add_argument(
        "--duration", type=float, default=300.0, help="持续时长（秒，默认 300）"
    )
    parser.add_argument(
        "--api-base", type=str, default=_API_BASE, help="后端 API 地址（默认 localhost:7101）"
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=None,
        help="直连 PG DSN（可选，后端不可用时诊断用）",
    )
    args = parser.parse_args()

    return asyncio.run(
        monitor_loop(
            api_base=args.api_base,
            dsn=args.dsn,
            interval=args.interval,
            duration=args.duration,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
