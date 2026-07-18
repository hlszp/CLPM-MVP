"""端到端性能测试 — 使用标准评估任务（无并发限制），10 次测试.

运行方式::

    cd backend && uv run python scripts/e2e_perf_test_v2.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


def main() -> None:
    base_url = "http://localhost:7101/api/v1"
    num_tests = 10

    with httpx.Client() as client:
        # 登录
        resp = client.post(
            f"{base_url}/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        token = resp.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        print("=== 端到端性能测试（标准评估任务） ===")
        print(f"计划测试次数: {num_tests}\n")

        results = []
        for i in range(num_tests):
            print(f"--- 第 {i + 1}/{num_tests} 次测试 ---")
            t0 = time.perf_counter()

            # 使用标准评估任务（无并发限制）
            resp = client.post(
                f"{base_url}/tasks/standard/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={},
            )
            if resp.status_code != 200:
                print(f"  任务提交失败: {resp.status_code}, {resp.text[:200]}")
                # 等待之前的任务完成
                time.sleep(10)
                continue

            task_id = resp.json()["data"]["taskId"]

            # 轮询任务状态
            status = "PENDING"
            poll_count = 0
            while status not in ("COMPLETED", "SUCCESS", "FAILED", "CANCELLED"):
                time.sleep(0.5)
                resp = client.get(f"{base_url}/tasks/{task_id}", headers=headers)
                data = resp.json()
                status = data["data"]["status"]
                poll_count += 1

            elapsed = time.perf_counter() - t0
            result = {
                "test": i + 1,
                "status": status,
                "total_time": elapsed,
                "task_id": task_id[:10],
            }
            results.append(result)
            print(f"  状态: {status}, 总耗时: {elapsed:.2f}s\n")

        # 汇总报告
        print("=" * 60)
        print("性能测试汇总报告")
        print("=" * 60)
        print(f"测试次数: {len(results)}")
        print()

        if results:
            times = [r["total_time"] for r in results if r["status"] in ("SUCCESS", "COMPLETED")]
            if times:
                print(f"成功次数: {len(times)}")
                print(f"最快: {min(times):.2f}s")
                print(f"最慢: {max(times):.2f}s")
                print(f"平均: {sum(times) / len(times):.2f}s")
                print(f"中位数: {sorted(times)[len(times) // 2]:.2f}s")
                print(f"16秒目标: {'达标' if max(times) <= 16 else '未达标'}")
            else:
                print("所有测试均失败！")

        print()
        print("详细数据:")
        print(f"{'序号':>4} {'状态':>10} {'总耗时':>8}")
        for r in results:
            print(f"{r['test']:>4} {r['status']:>10} {r['total_time']:>7.2f}s")


if __name__ == "__main__":
    main()
