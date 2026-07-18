#!/usr/bin/env python3
"""回填任务性能实测脚本：27 回路 × 24 小时墙钟耗时测量.

用途：KPI 回填链路性能优化（P0 并发 + P1 I/O 合并 + P2 fan-out）的前后对比实测。
流程：登录 → POST /tasks/backfill → POST /tasks/{id}/start → 轮询任务状态直到终态。

用法：
    .venv/bin/python scripts/measure_backfill_perf.py \
        [--base http://localhost:7101] \
        [--ts-start 2026-07-16T00:00:00Z] [--ts-end 2026-07-17T00:00:00Z]
"""

import argparse
import json
import sys
import time
import urllib.request


def req(base: str, method: str, path: str, body=None, token: str | None = None):
    r = urllib.request.Request(base + path, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(r, data, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:7101")
    ap.add_argument("--ts-start", default="2026-07-16T00:00:00Z")
    ap.add_argument("--ts-end", default="2026-07-17T00:00:00Z")
    ap.add_argument("--timeout", type=int, default=900, help="最长等待秒数")
    args = ap.parse_args()

    login = req(args.base, "POST", "/api/v1/auth/login", {"username": "admin", "password": "admin123"})
    token = login.get("accessToken") or login.get("access_token") or login.get("data", {}).get("accessToken")
    if not token:
        print("LOGIN_FAILED", json.dumps(login, ensure_ascii=False)[:300])
        sys.exit(1)
    print("login ok")

    created = req(args.base, "POST", "/api/v1/tasks/backfill", {
        "title": f"perf-measure-{int(time.time())}",
        "tsStart": args.ts_start,
        "tsEnd": args.ts_end,
    }, token)
    task_id = created.get("taskId") or created.get("task_id") or created.get("data", {}).get("taskId")
    if not task_id:
        print("CREATE_FAILED", json.dumps(created, ensure_ascii=False)[:300])
        sys.exit(1)
    print(f"task created: {task_id}")

    t0 = time.monotonic()
    started = req(args.base, "POST", f"/api/v1/tasks/{task_id}/start", None, token)
    print("started:", json.dumps(started, ensure_ascii=False)[:300], flush=True)

    while True:
        time.sleep(5)
        st = req(args.base, "GET", f"/api/v1/tasks/{task_id}", None, token)
        status = st.get("status") or st.get("data", {}).get("status")
        progress = st.get("progress") or st.get("data", {}).get("progress")
        elapsed = time.monotonic() - t0
        print(f"[{elapsed:7.1f}s] status={status} progress={progress}", flush=True)
        if status in ("SUCCESS", "FAILED", "CANCELLED", "PARTIAL_SUCCESS"):
            print("FINAL:", json.dumps(st, ensure_ascii=False)[:800])
            print(f"WALL_SECONDS={elapsed:.1f}")
            sys.exit(0 if status == "SUCCESS" else 2)
        if elapsed > args.timeout:
            print("TIMEOUT_WAITING")
            sys.exit(3)


if __name__ == "__main__":
    main()
