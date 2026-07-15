"""KPI 1000-loop capacity benchmark.

Runs a real custom KPI task and verifies terminal status plus snapshot count. The
benchmark never fabricates capacity by repeating the same loop: it requires at
least ``--loops`` distinct active loops in the target environment.

Usage::

    cd backend && uv run python scripts/kpi_capacity_benchmark.py --loops 1000
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


DEFAULT_BASE_URL = "http://localhost:7101/api/v1"
DEFAULT_TIMEOUT_SECONDS = 600.0


def _percentile(values: list[float], percentile: float) -> float:
    """Return nearest-rank percentile for a non-empty sample."""
    ordered = sorted(values)
    index = max(0, int(len(ordered) * percentile + 0.999999) - 1)
    return ordered[index]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real KPI capacity benchmark")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--loops", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.loops < 1:
        raise SystemExit("--loops 必须大于 0")

    with httpx.Client(timeout=30.0) as client:
        login = client.post(
            f"{args.base_url}/auth/login",
            json={"username": args.username, "password": args.password},
        )
        login.raise_for_status()
        token = login.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        loops_response = client.get(
            f"{args.base_url}/loops",
            params={"page": 1, "size": args.loops},
            headers=headers,
        )
        loops_response.raise_for_status()
        loops = loops_response.json().get("data", {}).get("items", [])
        loop_ids = [str(loop["loopId"]) for loop in loops]
        if len(loop_ids) < args.loops:
            raise SystemExit(
                f"容量测试拒绝执行：需要 {args.loops} 个不同回路，当前只有 {len(loop_ids)} 个。"
            )

        ts_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        ts_start = ts_end - timedelta(hours=1)
        submitted_at = time.perf_counter()
        submit = client.post(
            f"{args.base_url}/tasks/custom/evaluate",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "loopIds": loop_ids,
                "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                "tsStart": ts_start.isoformat().replace("+00:00", "Z"),
                "tsEnd": ts_end.isoformat().replace("+00:00", "Z"),
            },
        )
        submit.raise_for_status()
        task_id = submit.json()["data"]["taskId"]

        poll_latencies: list[float] = []
        while True:
            if time.perf_counter() - submitted_at > args.timeout:
                raise SystemExit(f"容量测试超时：{args.timeout:.0f}s，task_id={task_id}")
            poll_started = time.perf_counter()
            task_response = client.get(f"{args.base_url}/tasks/{task_id}", headers=headers)
            poll_latencies.append(time.perf_counter() - poll_started)
            task_response.raise_for_status()
            task = task_response.json()["data"]
            status = task["status"]
            if status in {"SUCCESS", "COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(args.poll_interval)

        elapsed = time.perf_counter() - submitted_at
        loops_done = int(task.get("loopsDone") or 0)
        passed = (
            status in {"SUCCESS", "COMPLETED"} and loops_done == args.loops and elapsed <= 600.0
        )
        print("KPI 容量测试结果")
        print(f"  task_id: {task_id}")
        print(f"  loops: {args.loops}")
        print(f"  status: {status}")
        print(f"  loops_done: {loops_done}")
        print(f"  total_seconds: {elapsed:.3f}")
        print(f"  task_poll_p50_seconds: {statistics.median(poll_latencies):.3f}")
        print(f"  task_poll_p95_seconds: {_percentile(poll_latencies, 0.95):.3f}")
        print(f"  SLO <=600s + all snapshots: {'通过' if passed else '失败'}")
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
