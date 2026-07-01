#!/usr/bin/env python3
"""按小时窗口批量回填 KPI 性能指标快照（通过 Celery worker 执行）。

支持四种运行模式：

1. 手动模式（默认）::

    cd backend && uv run python scripts/backfill_kpi.py --start "2026-06-27 00:00:00" --end "2026-06-30 10:00:00"

2. 自动检测缺失快照::

    cd backend && uv run python scripts/backfill_kpi.py --auto --lookback-hours 48

    查询 PostgreSQL kpi_snapshot_hourly，找出回溯窗口内快照数不足的小时，
    自动回填。

3. 快速回填最近 N 小时::

    cd backend && uv run python scripts/backfill_kpi.py --last-hours 6

4. 仅检测 TDengine 数据空档（不回填）::

    cd backend && uv run python scripts/backfill_kpi.py --gap-detect --lookback-hours 48

    查询 TDengine 各子表数据，找出数据空档（采样间隔 > 5 秒），
    用于排查模拟程序崩溃期间的数据丢失。

设计说明：
    通过 Celery worker 执行而非脚本进程直接调用，原因：
    脚本进程 asyncio.run 环境下 httpx client 与 TDengine REST 交互异常，
    导致查询全部返回空数组（valid_rate=0）。Celery worker 的 event loop
    管理由 AsyncTask 处理，httpx client 正常工作。

幂等性：相同 (loop_id, ts_start) 的快照会被 UPSERT 覆盖，可重复执行。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按小时窗口批量回填 KPI 性能指标快照（Celery 触发）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # 模式互斥
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--start",
        help='手动模式：起始时间（UTC），格式 "YYYY-MM-DD HH:MM:SS"',
    )
    mode.add_argument(
        "--auto",
        action="store_true",
        help="自动检测模式：查询缺失快照并回填",
    )
    mode.add_argument(
        "--last-hours",
        type=int,
        metavar="N",
        help="快速模式：回填最近 N 小时",
    )
    mode.add_argument(
        "--gap-detect",
        action="store_true",
        help="仅检测模式：查询 TDengine 数据空档（不回填）",
    )

    # 可选参数
    p.add_argument(
        "--end",
        help='手动模式结束时间（UTC，不包含），格式 "YYYY-MM-DD HH:MM:SS"',
    )
    p.add_argument(
        "--lookback-hours",
        type=int,
        default=48,
        help="自动检测/空档检测的回溯小时数（默认 48）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划，不触发任务",
    )
    p.add_argument(
        "--poll",
        action="store_true",
        default=True,
        help="轮询 Celery 任务状态直到完成（默认开启）",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------


def parse_utc(s: str) -> datetime:
    """解析 naive datetime 字符串为 aware UTC datetime。"""
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=UTC)


def gen_hourly_windows(start: datetime, end: datetime) -> list[datetime]:
    """生成 [start, end) 范围内的每个完整小时窗口起始时刻列表。"""
    s = start.replace(minute=0, second=0, microsecond=0)
    windows: list[datetime] = []
    cur = s
    while cur < end:
        windows.append(cur)
        cur += timedelta(hours=1)
    return windows


def fmt_utc(dt: datetime) -> str:
    """格式化 UTC datetime 为 ISO 8601 字符串。"""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 模式 1: 自动检测缺失快照
# ---------------------------------------------------------------------------


async def detect_missing_snapshots(lookback_hours: int) -> list[datetime]:
    """查询 PostgreSQL，找出快照数不足的小时窗口。

    Args:
        lookback_hours: 回溯小时数

    Returns:
        缺失快照的小时窗口起始时刻列表（UTC）
    """
    from sqlalchemy import func, select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger
    from app.models.metric import KpiSnapshotHourly

    now = datetime.now(UTC)
    start = (now - timedelta(hours=lookback_hours)).replace(
        minute=0, second=0, microsecond=0
    )

    # 1. 查询活跃回路总数
    async with AsyncSessionLocal() as db:
        loop_count = await db.scalar(
            select(func.count()).select_from(LoopLedger).where(
                LoopLedger.is_active.is_(True),
                LoopLedger.status == "READY",
            )
        )
        expected = loop_count or 0
        print(f"[检测] 期望回路数: {expected}")
        print(f"[检测] 回溯范围: {fmt_utc(start)} ~ {fmt_utc(now)}")

        # 2. 查询已有快照按小时分组
        stmt = (
            select(
                KpiSnapshotHourly.ts_start,
                func.count().label("cnt"),
            )
            .where(KpiSnapshotHourly.ts_start >= start.replace(tzinfo=None))
            .group_by(KpiSnapshotHourly.ts_start)
            .order_by(KpiSnapshotHourly.ts_start)
        )
        result = await db.execute(stmt)
        existing: dict[datetime, int] = {}
        for row in result.all():
            # ts_start 在数据库中是 naive UTC，转为 aware
            ts = row.ts_start.replace(tzinfo=UTC) if row.ts_start.tzinfo is None else row.ts_start
            existing[ts] = row.cnt

    # 3. 对比找出缺失窗口
    all_windows = gen_hourly_windows(start, now)
    missing: list[datetime] = []
    incomplete: list[tuple[datetime, int]] = []

    for w in all_windows:
        cnt = existing.get(w, 0)
        if cnt == 0:
            missing.append(w)
        elif cnt < expected:
            incomplete.append((w, cnt))

    print(f"[检测] 小时窗口总数: {len(all_windows)}")
    print(f"[检测] 完整快照: {len(all_windows) - len(missing) - len(incomplete)}")
    print(f"[检测] 缺失快照（0 条）: {len(missing)}")
    print(f"[检测] 不完整快照（< {expected} 条）: {len(incomplete)}")

    if missing:
        print("\n  缺失窗口列表:")
        for w in missing:
            print(f"    {fmt_utc(w)} ~ {fmt_utc(w + timedelta(hours=1))}")

    if incomplete:
        print("\n  不完整窗口列表:")
        for w, cnt in incomplete:
            print(f"    {fmt_utc(w)} ~ {fmt_utc(w + timedelta(hours=1))}  已有 {cnt}/{expected}")

    # 缺失 + 不完整都需要回填
    return missing + [w for w, _ in incomplete]


# ---------------------------------------------------------------------------
# 模式 2: TDengine 数据空档检测
# ---------------------------------------------------------------------------


async def detect_tdengine_gaps(lookback_hours: int) -> list[dict[str, Any]]:
    """查询 TDengine 超表，找出数据空档（行数 < 90% 期望值）。

    使用 TDengine 超表 PARTITION BY TBNAME + 时间窗口过滤，
    一次查询获取所有子表每小时的行数，避免逐表查询。

    Args:
        lookback_hours: 回溯小时数

    Returns:
        空档列表，每项含 {tag_name, hour_start, row_count, expected, gap_ratio}
    """
    import httpx

    from app.core.config import settings

    now = datetime.now(UTC)
    start = (now - timedelta(hours=lookback_hours)).replace(
        minute=0, second=0, microsecond=0
    )
    td_port = settings.TDENGINE_PORT + 11
    td_url = f"http://{settings.TDENGINE_HOST}:{td_port}/rest/sql/{settings.TDENGINE_DB}"
    auth = httpx.BasicAuth(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD)

    table_count = 0
    gaps: list[dict[str, Any]] = []

    async with httpx.AsyncClient(auth=auth, timeout=60.0) as client:
        # 1. 查询子表数
        resp = await client.post(
            td_url,
            content="SHOW TABLES LIKE 'd_loop_%'".encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        tables_data = resp.json()
        if tables_data.get("code") != 0:
            print(f"[空档检测] 查询子表失败: {tables_data.get('desc')}")
            return []
        table_count = len(tables_data.get("data", []))
        print(f"[空档检测] 子表数: {table_count}")
        print(f"[空档检测] 回溯范围: {fmt_utc(start)} ~ {fmt_utc(now)}")

        # 2. 按 1 小时窗口查询超表（TBNAME + 时间过滤）
        # TDengine 支持 PARTITION BY TBNAME 获取子表名
        hourly_windows = gen_hourly_windows(start, now)

        for w in hourly_windows:
            w_end = w + timedelta(hours=1)
            # 查询该小时内每个子表的行数（一次查询所有子表）
            sql = (
                f"SELECT TBNAME as tbl, COUNT(*) as cnt "
                f"FROM st_loop_data "
                f"WHERE ts >= '{fmt_utc(w)}' AND ts < '{fmt_utc(w_end)}' "
                f"PARTITION BY TBNAME"
            )
            try:
                resp = await client.post(
                    td_url,
                    content=sql.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                )
                result = resp.json()
                if result.get("code") != 0:
                    continue
                data = result.get("data", [])
            except Exception:
                continue

            expected = 3600  # 1Hz × 3600s
            gap_count = 0
            min_rows = expected
            total_gap_ratio = 0.0

            for row in data:
                tbl_name, cnt = row[0], row[1]
                if cnt < expected * 0.9:
                    gap_ratio = 1.0 - (cnt / expected) if expected > 0 else 1.0
                    gaps.append({
                        "tag_name": tbl_name,
                        "hour_start": w,
                        "row_count": cnt,
                        "expected": expected,
                        "gap_ratio": gap_ratio,
                    })
                    gap_count += 1
                    if cnt < min_rows:
                        min_rows = cnt
                    total_gap_ratio += gap_ratio

            # 缺失子表（该小时完全无数据，不在结果中）
            missing_tables = table_count - len(data)
            if missing_tables > 0:
                gap_count += missing_tables
                if 0 < min_rows:
                    min_rows = 0
                total_gap_ratio += missing_tables  # 完全丢失 = 100%

            if gap_count > 0:
                avg_gap = total_gap_ratio / gap_count if gap_count > 0 else 0
                print(
                    f"  {fmt_utc(w)} ~ {fmt_utc(w_end)}  "
                    f"受影响子表={gap_count}/{table_count}  "
                    f"最少行数={min_rows}/3600  "
                    f"平均丢失率={avg_gap:.1%}"
                )

        if gaps:
            print(f"\n[空档检测] 发现 {len(gaps)} 个空档")
        else:
            print("\n[空档检测] 未发现数据空档，所有子表数据完整。")

        return gaps


# ---------------------------------------------------------------------------
# Celery 任务触发与轮询
# ---------------------------------------------------------------------------


def trigger_backfill(
    start: datetime, end: datetime, dry_run: bool, poll: bool
) -> None:
    """触发 backfill_kpi_range Celery 任务并轮询状态。"""
    windows = gen_hourly_windows(start, end)
    print(f"\n=== 回填计划 ===")
    print(f"时间范围（UTC）: {fmt_utc(start)} ~ {fmt_utc(end)}")
    print(f"小时窗口数: {len(windows)}")
    print(f"执行方式: 触发 Celery 任务 backfill_kpi_range（worker 内串行执行）")
    print()

    if dry_run:
        print("[DRY-RUN] 窗口列表：")
        for i, w in enumerate(windows, 1):
            print(f"  {i:3d}. {fmt_utc(w)} ~ {fmt_utc(w + timedelta(hours=1))}")
        print(f"\n共 {len(windows)} 个窗口，未触发任务。")
        return

    # 延迟导入，确保 --help / --dry-run 不触发 app 初始化
    from app.tasks.kpi_calc import backfill_kpi_range

    start_iso = fmt_utc(start)
    end_iso = fmt_utc(end)
    result = backfill_kpi_range.delay(start_iso, end_iso)
    task_id = result.id
    print(f"Celery 任务已触发: task_id={task_id}")
    print(f"参数: ts_start={start_iso}, ts_end={end_iso}")
    print()

    if not poll:
        print("任务已提交，不轮询状态。可通过 Celery worker 日志查看进度。")
        return

    print("轮询任务状态（每 10 秒）...")
    print("提示: 可通过 `celery -A app.tasks.celery_app worker` 日志查看详细进度")
    print()

    poll_interval = 10
    last_state: str | None = None
    t0 = time.monotonic()
    while True:
        try:
            state = result.state
        except Exception as exc:  # noqa: BLE001
            print(f"[{time.monotonic() - t0:.0f}s] 查询状态失败: {exc}")
            time.sleep(poll_interval)
            continue

        if state != last_state:
            elapsed = time.monotonic() - t0
            print(f"[{elapsed:.0f}s] 状态: {state}")
            last_state = state

        if state == "SUCCESS":
            print()
            print("=== 任务完成 ===")
            try:
                ret = result.result
                if isinstance(ret, dict):
                    print(f"窗口总数: {ret.get('total_windows')}")
                    print(f"失败窗口: {ret.get('failed_windows')}")
                    print(
                        f"回路级快照: success={ret.get('loop_success')}, "
                        f"inconclusive={ret.get('loop_inconclusive')}, "
                        f"failed={ret.get('loop_failed')}"
                    )
                    print(f"节点级快照: success={ret.get('node_success')}")
                    if ret.get("failed_window_list"):
                        print(f"失败窗口列表: {ret['failed_window_list']}")
                else:
                    print(f"返回值: {ret}")
            except Exception as exc:  # noqa: BLE001
                print(f"获取结果失败: {exc}")
            break
        elif state == "FAILURE":
            print()
            print("=== 任务失败 ===")
            try:
                print(f"异常: {result.result}")
            except Exception as exc:  # noqa: BLE001
                print(f"获取异常信息失败: {exc}")
            sys.exit(1)

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    # ── 模式 1: 手动 --start [--end] ──
    if args.start:
        try:
            start = parse_utc(args.start)
        except ValueError as e:
            print(f"起始时间解析失败: {e}", file=sys.stderr)
            sys.exit(1)

        if args.end:
            try:
                end = parse_utc(args.end)
            except ValueError as e:
                print(f"结束时间解析失败: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            # 未指定 --end，默认到当前时间的整点
            end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

        if end <= start:
            print(
                f"结束时间必须晚于起始时间: start={start}, end={end}",
                file=sys.stderr,
            )
            sys.exit(1)

        trigger_backfill(start, end, args.dry_run, args.poll)
        return

    # ── 模式 2: --last-hours N ──
    if args.last_hours is not None:
        if args.last_hours <= 0:
            print(f"--last-hours 必须 > 0", file=sys.stderr)
            sys.exit(1)
        now = datetime.now(UTC)
        end = now.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=args.last_hours)
        print(f"[快速模式] 回填最近 {args.last_hours} 小时")
        trigger_backfill(start, end, args.dry_run, args.poll)
        return

    # ── 模式 3: --gap-detect（仅检测，不回填）──
    if args.gap_detect:
        print("=== TDengine 数据空档检测 ===")
        print(f"回溯 {args.lookback_hours} 小时")
        gaps = asyncio.run(detect_tdengine_gaps(args.lookback_hours))
        if gaps:
            print(f"\n[建议] 检测到 {len(gaps)} 个空档，建议运行回填:")
            # 找出受影响的最早和最晚时间
            hours = sorted(set(g["hour_start"] for g in gaps))
            earliest = hours[0]
            latest = hours[-1] + timedelta(hours=1)
            print(
                f"  cd backend && uv run python scripts/backfill_kpi.py "
                f'--start "{earliest.strftime("%Y-%m-%d %H:%M:%S")}" '
                f'--end "{latest.strftime("%Y-%m-%d %H:%M:%S")}"'
            )
        return

    # ── 模式 4: --auto（自动检测缺失快照并回填）──
    if args.auto:
        print("=== 自动检测缺失 KPI 快照 ===")
        print(f"回溯 {args.lookback_hours} 小时")
        missing = asyncio.run(detect_missing_snapshots(args.lookback_hours))

        if not missing:
            print("\n[完成] 所有小时窗口快照完整，无需回填。")
            return

        # 合并连续的缺失窗口为时间范围
        missing.sort()
        start = missing[0]
        end = missing[-1] + timedelta(hours=1)

        print(f"\n[回填] 将回填 {len(missing)} 个缺失/不完整窗口")
        trigger_backfill(start, end, args.dry_run, args.poll)
        return


if __name__ == "__main__":
    main()
