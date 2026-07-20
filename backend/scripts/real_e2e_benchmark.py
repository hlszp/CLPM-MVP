"""真实端到端基准测试 — 通过 API 触发评估并等待完成.

测量从触发任务到任务完成的实际总耗时，包括：
1. 任务提交（API）
2. Celery 执行（KPI 计算）
3. 任务完成（状态轮询）

运行方式::

    cd backend && uv run python scripts/real_e2e_benchmark.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


def main() -> None:
    base_url = "http://localhost:7101/api/v1"

    with httpx.Client() as client:
        # 登录
        print("=== 登录 ===")
        resp = client.post(
            f"{base_url}/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        if resp.status_code != 200:
            print(f"登录失败: {resp.status_code}")
            return
        token = resp.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}
        print("登录成功\n")

        # 获取所有回路 ID
        print("=== 获取回路列表 ===")
        resp = client.get(f"{base_url}/loops", params={"page": 1, "size": 100}, headers=headers)
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        loop_ids = [str(item["loopId"]) for item in items]
        print(f"找到 {len(loop_ids)} 个回路\n")

        # 测试 1：单回路评估
        print("=== 测试 1: 单回路评估 ===")
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        ts_end = now.replace(minute=0, second=0, microsecond=0)
        ts_start = ts_end - timedelta(hours=1)
        ts_start_str = ts_start.isoformat() + "Z"
        ts_end_str = ts_end.isoformat() + "Z"

        t0 = time.perf_counter()
        resp = client.post(
            f"{base_url}/tasks/custom/evaluate",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "loopIds": [loop_ids[0]],
                "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                "tsStart": ts_start_str,
                "tsEnd": ts_end_str,
            },
        )
        data = resp.json()
        if resp.status_code != 200:
            print(f"任务提交失败: {resp.status_code}, {data}")
            return
        task_id = data.get("data", {}).get("taskId", "")
        print(f"任务已提交: task_id={task_id[:10]}...")

        # 轮询任务状态
        poll_count = 0
        status = "PENDING"
        while status not in ("COMPLETED", "FAILED", "CANCELLED"):
            time.sleep(0.5)
            resp = client.get(f"{base_url}/tasks/{task_id}", headers=headers)
            data = resp.json()
            status = data.get("data", {}).get("status", "UNKNOWN")
            poll_count += 1

        elapsed = time.perf_counter() - t0
        print(f"任务完成: status={status}, 耗时={elapsed:.2f}s, 轮询次数={poll_count}")
        print(f"单回路平均耗时: {elapsed:.2f}s\n")

        # 测试 2：5 回路批量评估
        if len(loop_ids) >= 5:
            print("=== 测试 2: 5 回路批量评估 ===")
            t0 = time.perf_counter()
            resp = client.post(
                f"{base_url}/tasks/custom/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "loopIds": loop_ids[:5],
                    "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                    "tsStart": ts_start_str,
                    "tsEnd": ts_end_str,
                },
            )
            data = resp.json()
            if resp.status_code != 200:
                print(f"任务提交失败: {resp.status_code}, {data}")
                return
            task_id = data.get("data", {}).get("taskId", "")
            print(f"任务已提交: task_id={task_id[:10]}...")

            poll_count = 0
            status = "PENDING"
            while status not in ("COMPLETED", "FAILED", "CANCELLED"):
                time.sleep(1.0)
                resp = client.get(f"{base_url}/tasks/{task_id}", headers=headers)
                data = resp.json()
                status = data.get("data", {}).get("status", "UNKNOWN")
                progress = data.get("data", {}).get("progress", "")
                poll_count += 1
                if poll_count % 5 == 0:
                    print(f"  进度: {progress}")

            elapsed = time.perf_counter() - t0
            avg_per_loop = elapsed / 5
            print(f"任务完成: status={status}, 总耗时={elapsed:.2f}s, 轮询次数={poll_count}")
            print(f"5 回路平均耗时: {avg_per_loop:.2f}s/回路\n")

        # 测试 3：10 回路批量评估
        if len(loop_ids) >= 10:
            print("=== 测试 3: 10 回路批量评估 ===")
            t0 = time.perf_counter()
            resp = client.post(
                f"{base_url}/tasks/custom/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "loopIds": loop_ids[:10],
                    "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                    "tsStart": ts_start_str,
                    "tsEnd": ts_end_str,
                },
            )
            data = resp.json()
            if resp.status_code != 200:
                print(f"任务提交失败: {resp.status_code}, {data}")
                return
            task_id = data.get("data", {}).get("taskId", "")
            print(f"任务已提交: task_id={task_id[:10]}...")

            poll_count = 0
            status = "PENDING"
            while status not in ("COMPLETED", "FAILED", "CANCELLED"):
                time.sleep(2.0)
                resp = client.get(f"{base_url}/tasks/{task_id}", headers=headers)
                data = resp.json()
                status = data.get("data", {}).get("status", "UNKNOWN")
                progress = data.get("data", {}).get("progress", "")
                poll_count += 1
                if poll_count % 5 == 0:
                    print(f"  进度: {progress}")

            elapsed = time.perf_counter() - t0
            avg_per_loop = elapsed / 10
            print(f"任务完成: status={status}, 总耗时={elapsed:.2f}s, 轮询次数={poll_count}")
            print(f"10 回路平均耗时: {avg_per_loop:.2f}s/回路\n")

        print("=== 测试完成 ===")


if __name__ == "__main__":
    main()
