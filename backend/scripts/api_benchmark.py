"""API 接口性能基准测试 — 通过实际 HTTP 请求测量。

测试场景：
1. 回路列表查询
2. 标签列表查询
3. 指标仪表盘查询
4. 触发性能评估（单回路）
5. 触发性能评估（10 回路批量）
6. 实时值查询
7. 历史数据查询

运行方式::

    cd backend && uv run python scripts/api_benchmark.py
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
        t0 = time.perf_counter()
        resp = client.post(
            f"{base_url}/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        login_time = time.perf_counter() - t0
        if resp.status_code != 200:
            print(f"登录失败: {resp.status_code}")
            return
        token = resp.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"登录耗时: {login_time:.3f}s\n")

        # 1. 回路列表
        print("=== 1. 回路列表查询 ===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.get(f"{base_url}/loops", params={"page": 1, "size": 10}, headers=headers)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
            print(f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, loops={len(items)}")
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        # 2. 标签列表
        print("=== 2. 标签列表查询 ===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.get(f"{base_url}/tags", params={"page": 1, "size": 10}, headers=headers)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
            print(f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, tags={len(items)}")
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        # 3. 指标仪表盘
        print("=== 3. 指标仪表盘 ===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.get(f"{base_url}/performance/board", headers=headers)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            if resp.status_code == 200 and isinstance(data, dict):
                trend = data.get("data", {}).get("trend", [])
                print(
                    f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, "
                    f"trend_points={len(trend)}"
                )
            else:
                print(f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}")
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        # 获取回路 ID 列表
        resp = client.get(f"{base_url}/loops", params={"page": 1, "size": 20}, headers=headers)
        data = resp.json()
        items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
        loop_ids = [str(item["loopId"]) for item in items]
        print(f"可用回路 ID: {loop_ids[:5]}... (共 {len(loop_ids)} 个)\n")

        # 4. 触发评估（单回路）
        print("=== 4. 触发性能评估（单回路）===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.post(
                f"{base_url}/tasks/standard/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={"loop_ids": [loop_ids[0]], "time_range": "last_hour"},
            )
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            status = (
                data.get("data", {}).get("status", "UNKNOWN")
                if isinstance(data, dict)
                else "UNKNOWN"
            )
            print(
                f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, task_status={status}"
            )
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        # 5. 触发评估（10 回路）
        if len(loop_ids) >= 10:
            print("=== 5. 触发性能评估（10 回路批量）===")
            t0 = time.perf_counter()
            resp = client.post(
                f"{base_url}/tasks/standard/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={"loop_ids": loop_ids[:10], "time_range": "last_hour"},
            )
            elapsed = time.perf_counter() - t0
            data = resp.json()
            status = (
                data.get("data", {}).get("status", "UNKNOWN")
                if isinstance(data, dict)
                else "UNKNOWN"
            )
            task_id = data.get("data", {}).get("task_id", "")[:10] if isinstance(data, dict) else ""
            print(
                f"  耗时: {elapsed:.3f}s, status={resp.status_code}, "
                f"task_status={status}, task_id={task_id}"
            )
            print("  (注：异步任务，此时间为任务提交时间)\n")

        # 6. 实时值查询
        print("=== 6. 实时值查询 ===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.get(
                f"{base_url}/realtime",
                params={"tagCodes": ["TI10101", "TI10102", "TI10103"]},
                headers=headers,
            )
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
            print(
                f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, values={len(items)}"
            )
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        # 7. 历史数据查询
        print("=== 7. 历史数据查询（单回路波形）===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.get(
                f"{base_url}/timeseries/{loop_ids[0]}/waveform",
                params={"time_range": "last_hour"},
                headers=headers,
            )
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            if resp.status_code == 200 and isinstance(data, dict):
                points = len(data.get("data", {}).get("data", []))
                print(
                    f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, points={points}"
                )
            else:
                print(f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}")
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        # 8. KPI 快照查询
        print("=== 8. KPI 快照查询 ===")
        times = []
        for i in range(3):
            t0 = time.perf_counter()
            resp = client.get(
                f"{base_url}/performance/loops/snapshots",
                params={"page": 1, "size": 10},
                headers=headers,
            )
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            data = resp.json()
            items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
            print(
                f"  第 {i + 1} 次: {elapsed:.3f}s, status={resp.status_code}, "
                f"snapshots={len(items)}"
            )
        print(f"  平均: {sum(times) / len(times):.3f}s\n")

        print("=== 测试完成 ===")


if __name__ == "__main__":
    main()
