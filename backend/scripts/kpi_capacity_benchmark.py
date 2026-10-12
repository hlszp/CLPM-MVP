"""KPI capacity benchmark（P5 批 α 重写：standard-hourly 独立模式 + custom 单列）.

两种互斥模式（03 号文 §8 P5-05 / P0-03 v2 §3）：

1. ``--mode standard-hourly``（唯一计入标准小时容量 SLO 的模式）
   经既有 Celery 注册任务 ``app.tasks.kpi_calc.calculate_hourly_kpi`` 投递
   冻结窗口（``.delay(ts_start=...)``，复用唯一 lifespan Worker，不新增开放
   触发 API）；完成屏障为 **DB 回查**（父任务终态不作为屏障）：
   - 小时账本：kpi_snapshot_hourly 中 eligible 集行数 = 预期，且
     source ∈ {SCHEDULED, MANUAL_STANDARD}、source_task_id 与投递一致或
     为既有同窗行（幂等 upsert）；
   - latest 投影：loop_confidence_latest 中预定回路行更新至该窗口
     （DEC-09 迟到窗口不覆盖当前投影——历史冻结窗可能不满足本条件，
     报告单列，可用 --barrier-loop-prefix 限定 PERF- 生成集）；
   - 节点结果：预定 nodeIds（is_kpi_enabled 且子树含本窗快照回路）在
     kpi_node_snapshot_hourly 有该窗行；
   等待期间轮询 ``celery inspect active`` 记录 calculate_node_kpi_hourly
   子任务 ID（尽力取证，错过即如实登记 not-observed）。

2. ``--mode custom``（loop-evals 吞吐回放，独立单列，**不计 600s 标准 SLO**）
   走 POST /tasks/custom/evaluate；少量回路多窗口重放仅在显式 ``--allow-replay``
   下放行，报告必须标注 unique_loops / loop_evals，不得表述为标准小时容量。

修复（P0-03 §3.4）：分页参数名 pageSize（API le=100，逐页拉全量+断言取回数）、
默认端口 17101、standard-hourly 1000 独立 loopId 断言（禁止重放凑数）、
custom 重放单列。

Usage::

    cd backend && uv run python scripts/kpi_capacity_benchmark.py \
        --mode standard-hourly --ts-start 2026-10-13T08:00:00 \
        --expect-eligible 1000 --barrier-loop-prefix PERF- --dry-run

    cd backend && uv run python scripts/kpi_capacity_benchmark.py --mode custom --loops 1000
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_BASE_URL = "http://localhost:17101/api/v1"  # MVP 隔离端口（原 7101 是另一项目）
DEFAULT_TIMEOUT_SECONDS = 3600.0
DEFAULT_SLO_SECONDS = 600.0
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_PAGE_SIZE = 100  # API /loops pageSize le=100
DEFAULT_RETRY_SECONDS = 60.0
DEFAULT_BATCH_SPACING_SECONDS = 2.0

#: 屏障允许的快照来源（.delay 直投=MANUAL_STANDARD 语境下任务自建记录为
#: SCHEDULED；API 手动=MANUAL_STANDARD。同窗 Beat 先跑 → SCHEDULED+task_id=NULL）
_ALLOWED_SOURCES = ("SCHEDULED", "MANUAL_STANDARD")


def _percentile(values: list[float], percentile: float) -> float:
    """Return nearest-rank percentile for a non-empty sample."""
    ordered = sorted(values)
    index = max(0, int(len(ordered) * percentile + 0.999999) - 1)
    return ordered[index]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KPI 容量基准（standard-hourly / custom 单列）")
    parser.add_argument(
        "--mode",
        choices=("standard-hourly", "custom"),
        default="standard-hourly",
        help="standard-hourly=经 Celery 入口的标准小时容量（唯一计 SLO）；"
        "custom=loop-evals 回放单列",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--loops", type=int, default=1000, help="custom 模式目标 loop-evals 数")
    parser.add_argument(
        "--ts-start",
        default=None,
        help="standard-hourly 冻结窗口起点（ISO 8601，naive UTC，整点）；缺省=上一个完整小时",
    )
    parser.add_argument(
        "--expect-eligible",
        type=int,
        default=1000,
        help="standard-hourly 前置断言：三条件参评回路数（is_active+READY+include_in_evaluation）",
    )
    parser.add_argument(
        "--barrier-loop-prefix",
        default=None,
        help="屏障核算限定回路位号前缀（如 PERF-，混合 dev 环境下只对生成集对账）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--slo-seconds", type=float, default=DEFAULT_SLO_SECONDS)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--retry-seconds", type=float, default=DEFAULT_RETRY_SECONDS)
    parser.add_argument("--batch-spacing", type=float, default=DEFAULT_BATCH_SPACING_SECONDS)
    parser.add_argument(
        "--allow-replay",
        action="store_true",
        help="custom 模式：可用回路数 < 目标时允许少 ID 多窗口重放（报告显式标注 loop-evals）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做前置检查与计划打印，不投递任何任务/不产生任何写入",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# HTTP helpers（custom 模式 + preflight 交叉核对）
# ---------------------------------------------------------------------------


def _post_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    json: dict,
    retry_seconds: float,
) -> httpx.Response:
    """POST with 429 backoff handling."""
    deadline = time.perf_counter() + retry_seconds
    while True:
        response = client.post(url, headers=headers, json=json)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if time.perf_counter() >= deadline:
            response.raise_for_status()
        sleep_for = min(5.0, max(0.5, float(response.headers.get("Retry-After", "1") or 1)))
        time.sleep(sleep_for)


def _get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    retry_seconds: float,
) -> httpx.Response:
    """GET with 429 backoff handling."""
    deadline = time.perf_counter() + retry_seconds
    while True:
        response = client.get(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if time.perf_counter() >= deadline:
            response.raise_for_status()
        sleep_for = min(5.0, max(0.5, float(response.headers.get("Retry-After", "1") or 1)))
        time.sleep(sleep_for)


def _load_loops(
    client: httpx.Client, base_url: str, headers: dict[str, str], page_size: int
) -> list[str]:
    """Fetch all loop IDs via pagination.

    P5 批 α 修复：参数名 ``pageSize``（原 ``size`` 被 API 静默忽略回退默认 20，
    且 ``len(items)<page_size`` 早停——实际只拉到约 20 个回路）；单页上限
    le=100，逐页拉全量并断言非空页与去重计数。
    """
    page_size = max(1, min(page_size, 100))  # API le=100 硬上限
    loop_ids: list[str] = []
    seen: set[str] = set()
    page = 1
    while True:
        resp = client.get(
            f"{base_url}/loops",
            params={"page": page, "pageSize": page_size},
            headers=headers,
        )
        resp.raise_for_status()
        payload = resp.json().get("data", {})
        items = payload.get("items", []) or []
        if not items:
            break
        for item in items:
            lid = str(item["loopId"])
            if lid not in seen:
                seen.add(lid)
                loop_ids.append(lid)
        total = payload.get("total")
        if total is not None and len(loop_ids) >= int(total):
            break
        if len(items) < page_size:
            break
        page += 1
    return loop_ids


def _read_task_loops_done(task: dict) -> int:
    """Read the completed loop counter from the API payload."""
    return int(task.get("loopsDone") or task.get("loops_done") or 0)


# ---------------------------------------------------------------------------
# standard-hourly 模式（Celery 入口 + DB 完成屏障）
# ---------------------------------------------------------------------------


def _frozen_window(ts_start_arg: str | None) -> datetime:
    """冻结窗口起点（naive UTC 整点）。"""
    if ts_start_arg:
        raw = ts_start_arg.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        dt = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if dt.minute or dt.second or dt.microsecond:
        raise SystemExit(f"--ts-start 必须为整点小时（收到 {dt.isoformat()}）")
    return dt


async def _preflight_eligible(prefix: str | None) -> tuple[list[str], int]:
    """三条件参评回路（DB 权威）+ 总活跃回路数。

    Returns:
        (eligible_loop_ids, total_active)
    """
    from sqlalchemy import func, select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger

    async with AsyncSessionLocal() as db:
        stmt = (
            select(LoopLedger.id, LoopLedger.tag_name)
            .where(
                LoopLedger.is_active.is_(True),
                LoopLedger.status == "READY",
                LoopLedger.include_in_evaluation.is_(True),
            )
            .order_by(LoopLedger.tag_name)
        )
        total_stmt = (
            select(func.count()).select_from(LoopLedger).where(LoopLedger.is_active.is_(True))
        )
        if prefix:
            stmt = stmt.where(LoopLedger.tag_name.like(f"{prefix}%"))
            total_stmt = total_stmt.where(LoopLedger.tag_name.like(f"{prefix}%"))
        rows = (await db.execute(stmt)).all()
        total_active = await db.scalar(total_stmt)
    return [str(r[0]) for r in rows], int(total_active or 0)


_PREDICTED_NODES_SQL = """
WITH RECURSIVE node_tree(root_id, descendant_id) AS (
    SELECT id, id FROM plant_node
    UNION ALL
    SELECT nt.root_id, c.id FROM plant_node c JOIN node_tree nt ON c.parent_id = nt.descendant_id
)
SELECT DISTINCT n.id
FROM plant_node n
JOIN node_tree nt ON nt.root_id = n.id
JOIN loop_ledger l ON l.unit_id = nt.descendant_id
JOIN kpi_snapshot_hourly s ON s.loop_id = l.id AND s.ts_start = :ts_start
WHERE n.is_kpi_enabled = true
"""


async def _barrier_snapshot(ts_start: datetime, eligible_ids: list[str]) -> dict[str, object]:
    """一次 DB 回查取全部屏障条件计数（不依赖任务状态字段）。"""
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    eligible_set = set(eligible_ids)
    async with AsyncSessionLocal() as db:
        # 条件 1：小时账本（行数 + 来源断言样本）
        ledger_rows = (
            (
                await db.execute(
                    text(
                        "SELECT loop_id, source, source_task_id FROM kpi_snapshot_hourly "
                        "WHERE ts_start = :ts AND loop_id = ANY(:ids)"
                    ),
                    {"ts": ts_start, "ids": sorted(eligible_set)},
                )
            ).all()
            if eligible_set
            else []
        )
        source_breakdown: dict[str, int] = {}
        foreign_source_rows = 0
        for _loop_id, src, _tid in ledger_rows:
            source_breakdown[src or "NULL"] = source_breakdown.get(src or "NULL", 0) + 1
            if (src or "") not in _ALLOWED_SOURCES:
                foreign_source_rows += 1
        # 条件 2：latest 投影（数据窗 = 冻结窗）
        latest_rows = (
            await db.execute(
                text(
                    "SELECT count(*) FROM loop_confidence_latest "
                    "WHERE data_ts_start = :ts AND loop_id = ANY(:ids)"
                ),
                {"ts": ts_start, "ids": sorted(eligible_set)},
            )
        ).scalar()
        # 条件 3：预定 nodeIds 与其节点结果
        predicted_nodes = [
            str(r[0])
            for r in (await db.execute(text(_PREDICTED_NODES_SQL), {"ts_start": ts_start})).all()
        ]
        node_rows = 0
        if predicted_nodes:
            node_rows = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM kpi_node_snapshot_hourly "
                        "WHERE ts_start = :ts AND plant_node_id = ANY(:nodes)"
                    ),
                    {"ts": ts_start, "nodes": predicted_nodes},
                )
            ).scalar()
    return {
        "ledger_rows": len(ledger_rows),
        "source_breakdown": source_breakdown,
        "foreign_source_rows": foreign_source_rows,
        "latest_rows": int(latest_rows or 0),
        "predicted_node_count": len(predicted_nodes),
        "node_rows": int(node_rows or 0),
    }


async def _observe_child_task_ids() -> list[dict]:
    """尽力取证：worker 当前活跃的 calculate_node_kpi_hourly 子任务。"""
    try:
        from app.tasks.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=2.0)
        active = inspect.active() or {}
    except Exception:  # noqa: BLE001 — 取证失败不影响屏障
        return []
    out: list[dict] = []
    for _worker, tasks in active.items():
        for t in tasks or []:
            if t.get("name") == "app.tasks.kpi_calc.calculate_node_kpi_hourly":
                out.append(
                    {
                        "id": t.get("id"),
                        "args": t.get("args"),
                        "kwargs": t.get("kwargs"),
                        "worker": _worker,
                    }
                )
    return out


async def run_standard_hourly(args: argparse.Namespace) -> int:
    """standard-hourly 模式主流程。"""
    ts_start = _frozen_window(args.ts_start)
    ts_end = ts_start + timedelta(hours=1)
    prefix = args.barrier_loop_prefix

    eligible_ids, total_active = await _preflight_eligible(prefix)
    print("[preflight] 三条件参评回路数检查（DB 权威口径）")
    print(f"  window: {ts_start.isoformat()} ~ {ts_end.isoformat()} (naive UTC)")
    print(f"  barrier_loop_prefix: {prefix or '(none)'}")
    print(f"  eligible(is_active+READY+include_in_evaluation): {len(eligible_ids)}")
    print(f"  total_active(同前缀范围): {total_active}")
    if len(set(eligible_ids)) != len(eligible_ids):
        raise SystemExit("[preflight] 断言失败：eligible loop id 存在重复（不应发生）")
    if len(eligible_ids) != args.expect_eligible:
        raise SystemExit(
            f"[preflight] 断言失败：三条件参评回路数 {len(eligible_ids)} ≠ 预期 "
            f"{args.expect_eligible}（standard-hourly 必须为独立回路集，禁止重放凑数）"
        )
    print(f"  独立 loopId 断言: {len(eligible_ids)} unique = expect ✓")

    barrier0 = await _barrier_snapshot(ts_start, eligible_ids)
    print(
        f"[preflight] 投递前既有同窗行: ledger={barrier0['ledger_rows']} "
        f"latest={barrier0['latest_rows']} node={barrier0['node_rows']} "
        f"(同窗已被计算过时这些行非 0；SETNX 锁 TTL 15000s 内重投会 HourlyWindowBusy)"
    )

    if args.dry_run:
        print("[dry-run] 计划（未投递任何任务、未写入任何数据）：")
        print(f"  submit: calculate_hourly_kpi.delay(ts_start='{ts_start.isoformat()}')")
        print(
            f"  屏障: 小时账本 {len(eligible_ids)} 行 + latest {len(eligible_ids)} 行 + "
            f"预定节点(投递后动态预测)节点行"
        )
        print(f"  SLO: {args.slo_seconds:.0f}s（计时=投递提交开始 → 全部屏障条件满足）")
        print("[dry-run] preflight 通过，standard-hourly 就绪")
        return 0

    from app.tasks.kpi_calc import calculate_hourly_kpi

    print(
        f"[submit] calculate_hourly_kpi.delay(ts_start='{ts_start.isoformat()}') "
        f"（经既有 Celery 入口，复用唯一 lifespan Worker，不新增开放触发 API）"
    )
    submit_started = time.perf_counter()
    result = calculate_hourly_kpi.delay(ts_start=ts_start.isoformat())
    parent_task_id = result.id
    print(f"[submit] parent_task_id={parent_task_id}")

    child_ids: list[dict] = []
    child_observed_at: str | None = None
    barrier: dict[str, object] = {}
    first_met: dict[str, float] = {}
    done = False
    while time.perf_counter() - submit_started < args.timeout:
        # 子任务 ID 尽力取证（父任务终态≠屏障；错过即 not-observed）
        if not child_ids:
            child_ids = await _observe_child_task_ids()
            if child_ids:
                child_observed_at = datetime.now(UTC).isoformat()
        barrier = await _barrier_snapshot(ts_start, eligible_ids)
        expected_nodes = int(barrier["predicted_node_count"])
        conditions = {
            "ledger": int(barrier["ledger_rows"]) >= len(eligible_ids),
            "source": int(barrier["foreign_source_rows"]) == 0,
            "latest": int(barrier["latest_rows"]) >= len(eligible_ids),
            "nodes": expected_nodes == 0 or int(barrier["node_rows"]) >= expected_nodes,
        }
        for name, ok in conditions.items():
            if ok and name not in first_met:
                first_met[name] = time.perf_counter() - submit_started
        if all(conditions.values()):
            done = True
            break
        await asyncio.sleep(args.poll_interval)

    elapsed = time.perf_counter() - submit_started
    print("[barrier] 完成屏障判定（全部 DB 回查，父任务终态不作为屏障）：")
    print(
        f"  小时账本行: {barrier.get('ledger_rows')}/{len(eligible_ids)} "
        f"(source 分布: {barrier.get('source_breakdown')})"
    )
    print(
        f"  latest 投影行: {barrier.get('latest_rows')}/{len(eligible_ids)} "
        f"(DEC-09 迟到窗不覆盖当前投影——历史冻结窗对既有回路可能不增长)"
    )
    print(f"  预定节点: {barrier.get('node_rows')}/{barrier.get('predicted_node_count')}")
    print(f"  各条件首次满足时刻(s): { {k: round(v, 1) for k, v in sorted(first_met.items())} }")
    print(f"  parent_task_id: {parent_task_id}")
    print(
        f"  节点子任务取证: {child_ids if child_ids else 'not-observed'}"
        f"{f' @ {child_observed_at}' if child_observed_at else ''}"
    )
    print(f"  total_seconds: {elapsed:.3f}")
    slo_passed = done and elapsed <= args.slo_seconds
    print(f"  SLO <= {args.slo_seconds:.0f}s: {'通过' if slo_passed else '失败'}")
    if not done:
        print("  [timeout] 屏障未闭合（上方计数即缺口；不缩分母、不改判）")
        return 1
    return 0 if elapsed <= args.slo_seconds else 1


# ---------------------------------------------------------------------------
# custom 模式（loop-evals 回放，独立单列）
# ---------------------------------------------------------------------------


def _replay_windows(requested_loops: int, loop_ids: list[str]) -> list[tuple[int, list[str]]]:
    """Split a loop-eval target into hourly replay batches（仅 custom 模式）。"""
    if not loop_ids:
        return []
    batch_size = len(loop_ids)
    batches = math.ceil(requested_loops / batch_size)
    result: list[tuple[int, list[str]]] = []
    remaining = requested_loops
    for batch_index in range(batches):
        chunk_size = min(batch_size, remaining)
        result.append((batch_index, loop_ids[:chunk_size]))
        remaining -= chunk_size
    return result


def run_custom(args: argparse.Namespace) -> int:
    """custom 模式主流程（loop-evals 回放，不计标准小时 SLO）。"""
    with httpx.Client(timeout=30.0) as client:
        login = client.post(
            f"{args.base_url}/auth/login",
            json={"username": args.username, "password": args.password},
        )
        login.raise_for_status()
        token = login.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        loop_ids = _load_loops(client, args.base_url, headers, args.page_size)
        if not loop_ids:
            raise SystemExit("custom 模式拒绝执行：当前没有可用回路。")
        unique_loops = len(set(loop_ids))
        replay = unique_loops < args.loops
        if replay and not args.allow_replay:
            raise SystemExit(
                f"custom 模式：仅 {unique_loops} 个独立回路 < 目标 {args.loops} loop-evals；"
                "少 ID 多窗口重放需显式 --allow-replay（结果只作吞吐回放基线，"
                "不得视为真实 1000 路径门禁）"
            )

        batches = _replay_windows(args.loops, loop_ids)
        if not batches:
            raise SystemExit("custom 模式拒绝执行：无法生成 replay 批次。")

        base_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        batch_timings: list[float] = []
        batch_statuses: list[str] = []
        total_loop_evals = 0

        started = time.perf_counter()
        for batch_index, batch_loop_ids in batches:
            ts_end = base_end - timedelta(hours=batch_index + 1)
            ts_start = ts_end - timedelta(hours=1)
            submit_started = time.perf_counter()
            submit = _post_with_retry(
                client,
                f"{args.base_url}/tasks/custom/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "loopIds": batch_loop_ids,
                    # P0-03 §3.3：metrics 仅非空校验、不进任务参数（恒为全量
                    # MetricConfig）——报告不得声称"仅 3 项指标"
                    "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                    "tsStart": ts_start.isoformat().replace("+00:00", "Z"),
                    "tsEnd": ts_end.isoformat().replace("+00:00", "Z"),
                },
                retry_seconds=args.retry_seconds,
            )
            task_id = submit.json()["data"]["taskId"]

            while True:
                if time.perf_counter() - started > args.timeout:
                    done_evals = total_loop_evals
                    raise SystemExit(
                        f"custom 模式超时：{args.timeout:.0f}s，已完成 {done_evals} loop-evals"
                    )
                task_response = _get_with_retry(
                    client,
                    f"{args.base_url}/tasks/{task_id}",
                    headers=headers,
                    retry_seconds=args.retry_seconds,
                )
                task = task_response.json()["data"]
                status = task["status"]
                if status in {"SUCCESS", "COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(args.poll_interval)

            elapsed = time.perf_counter() - submit_started
            loops_done = _read_task_loops_done(task)
            total_loop_evals += loops_done
            batch_timings.append(elapsed)
            batch_statuses.append(status)
            if batch_index < len(batches) - 1:
                time.sleep(args.batch_spacing)

        total_elapsed = time.perf_counter() - started
        all_ok = all(s in {"SUCCESS", "COMPLETED"} for s in batch_statuses)

        print("KPI custom 容量回放结果（loop-evals 口径——不计 standard-hourly 600s SLO）")
        print(f"  mode: custom (replay={replay})")
        print(f"  unique_loops: {unique_loops}")
        print(f"  requested_loop_evals: {args.loops}")
        print(f"  batches: {len(batches)}")
        print(f"  total_loop_evals: {total_loop_evals}")
        print(f"  total_seconds: {total_elapsed:.3f}")
        if batch_timings:
            print(f"  batch_time_p50_seconds: {statistics.median(batch_timings):.3f}")
            print(f"  batch_time_p95_seconds: {_percentile(batch_timings, 0.95):.3f}")
        print(f"  all_batches_successful: {all_ok}")
        if replay:
            print(
                "  ⚠ 重放口径：少量独立回路多窗口重放凑 loop-evals，仅作吞吐参考，"
                "不得表述为标准小时容量或 1000 独立回路容量"
            )
        return 0 if (all_ok and total_loop_evals >= args.loops) else 1


def main() -> int:
    args = _parse_args()
    if args.mode == "standard-hourly":
        return asyncio.run(run_standard_hourly(args))
    return run_custom(args)


if __name__ == "__main__":
    raise SystemExit(main())
