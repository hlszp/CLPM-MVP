"""简单端到端测试 — 测量实际耗时"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import UTC

import httpx


def main() -> None:
    base_url = "http://localhost:7101/api/v1"
    loop_count = 20

    with httpx.Client() as client:
        resp = client.post(
            f"{base_url}/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        token = resp.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"{base_url}/loops", params={"page": 1, "size": 100}, headers=headers)
        items = resp.json()["data"]["items"]
        loop_ids = [str(item["loopId"]) for item in items[:loop_count]]
        print(f"找到 {len(loop_ids)} 个回路")

        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        ts_end = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        ts_start = ts_end - timedelta(hours=1)
        ts_start_str = ts_start.replace(tzinfo=None).isoformat() + "Z"
        ts_end_str = ts_end.replace(tzinfo=None).isoformat() + "Z"

        print(f"\n=== 测试 {loop_count} 回路批量评估 ===")
        t0 = time.perf_counter()
        resp = client.post(
            f"{base_url}/tasks/custom/evaluate",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "loopIds": loop_ids,
                "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                "tsStart": ts_start_str,
                "tsEnd": ts_end_str,
            },
        )
        if resp.status_code != 200:
            print(f"任务提交失败: {resp.status_code}")
            return

        task_id = resp.json()["data"]["taskId"]
        print(f"任务已提交: {task_id[:10]}...")

        status = "PENDING"
        poll_count = 0
        while status not in ("COMPLETED", "FAILED", "CANCELLED"):
            time.sleep(1.0)
            resp = client.get(f"{base_url}/tasks/{task_id}", headers=headers)
            data = resp.json()
            status = data["data"]["status"]
            progress = data["data"].get("progress", "")
            poll_count += 1
            if poll_count % 5 == 0:
                elapsed = time.perf_counter() - t0
                print(f"  进度: {progress}, 已耗时: {elapsed:.2f}s")

        elapsed = time.perf_counter() - t0
        avg_per_loop = elapsed / loop_count
        print(f"\n任务完成: status={status}, 总耗时={elapsed:.2f}s")
        print(f"平均每回路耗时: {avg_per_loop:.3f}s")


if __name__ == "__main__":
    main()
