"""KPI capacity benchmark.

Runs real custom KPI tasks against the live API and replays the available
control loops across multiple complete hourly windows until the requested loop
count is covered. This matches the real deployment constraint when the source
system only exposes a limited number of loops.

Usage::

    cd backend && uv run python scripts/kpi_capacity_benchmark.py --loops 1000
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


DEFAULT_BASE_URL = "http://localhost:7101/api/v1"
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_PAGE_SIZE = 100
DEFAULT_RETRY_SECONDS = 60.0
DEFAULT_BATCH_SPACING_SECONDS = 2.0


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
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--retry-seconds", type=float, default=DEFAULT_RETRY_SECONDS)
    parser.add_argument("--batch-spacing", type=float, default=DEFAULT_BATCH_SPACING_SECONDS)
    return parser.parse_args()


def _load_loops(
    client: httpx.Client, base_url: str, headers: dict[str, str], page_size: int
) -> list[str]:
    """Fetch all available loop IDs using pagination."""
    loop_ids: list[str] = []
    page = 1
    while True:
        resp = client.get(
            f"{base_url}/loops",
            params={"page": page, "size": page_size},
            headers=headers,
        )
        resp.raise_for_status()
        payload = resp.json().get("data", {})
        items = payload.get("items", []) or []
        loop_ids.extend(str(item["loopId"]) for item in items)
        if len(items) < page_size:
            break
        page += 1
    return loop_ids


def _read_task_loops_done(task: dict) -> int:
    """Read the completed loop counter from the API payload."""
    return int(task.get("loopsDone") or task.get("loops_done") or 0)


def _replay_windows(requested_loops: int, loop_ids: list[str]) -> list[tuple[int, list[str]]]:
    """Split a loop-eval target into hourly replay batches."""
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

        loop_ids = _load_loops(client, args.base_url, headers, max(1, args.page_size))
        if not loop_ids:
            raise SystemExit("容量测试拒绝执行：当前没有可用回路。")

        requested_loops = args.loops
        available_loops = len(loop_ids)
        if available_loops < requested_loops:
            print(
                f"容量测试提示：仅发现 {available_loops} 个回路，将通过多窗口 replay "
                f"覆盖请求的 {requested_loops} 个 loop-evals。"
            )
            if requested_loops > available_loops * 4:
                print(
                    "容量测试提示：目标循环数远高于可用回路数，结果更适合作为 "
                    "吞吐回放基线，不应视为真实 1000 路径门禁。"
                )

        batches = _replay_windows(requested_loops, loop_ids)
        if not batches:
            raise SystemExit("容量测试拒绝执行：无法生成 replay 批次。")

        base_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        batch_timings: list[float] = []
        batch_statuses: list[str] = []
        total_loop_evals = 0

        started = time.perf_counter()
        for batch_index, batch_loop_ids in batches:
            ts_end = base_end - timedelta(hours=batch_index)
            ts_start = ts_end - timedelta(hours=1)
            submit_started = time.perf_counter()
            submit = _post_with_retry(
                client,
                f"{args.base_url}/tasks/custom/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "loopIds": batch_loop_ids,
                    "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                    "tsStart": ts_start.isoformat().replace("+00:00", "Z"),
                    "tsEnd": ts_end.isoformat().replace("+00:00", "Z"),
                },
                retry_seconds=args.retry_seconds,
            )
            task_id = submit.json()["data"]["taskId"]

            while True:
                if time.perf_counter() - started > args.timeout:
                    raise SystemExit(
                        f"容量测试超时：{args.timeout:.0f}s，已完成 {total_loop_evals} loop-evals"
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
        passed = (
            total_loop_evals >= requested_loops
            and all(status in {"SUCCESS", "COMPLETED"} for status in batch_statuses)
            and total_elapsed <= 600.0
        )

        print("KPI 容量测试结果")
        print(f"  requested_loop_evals: {requested_loops}")
        print(f"  available_loops: {available_loops}")
        print(f"  batches: {len(batches)}")
        print(f"  total_loop_evals: {total_loop_evals}")
        print(f"  total_seconds: {total_elapsed:.3f}")
        print(f"  batch_time_p50_seconds: {statistics.median(batch_timings):.3f}")
        print(f"  batch_time_p95_seconds: {_percentile(batch_timings, 0.95):.3f}")
        print(f"  SLO <=600s + all batches successful: {'通过' if passed else '失败'}")
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
