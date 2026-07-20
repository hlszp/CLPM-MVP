"""端到端性能测试 — 10 次完整测试验证 16 秒目标.

运行方式::

    cd backend && uv run python scripts/e2e_perf_test.py
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
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

        # 获取所有回路
        resp = client.get(f"{base_url}/loops", params={"page": 1, "size": 100}, headers=headers)
        items = resp.json()["data"]["items"]
        loop_ids = [str(item["loopId"]) for item in items]
        print(f"找到 {len(loop_ids)} 个回路\n")

        # 时间窗口：使用上一个完整小时（与标准任务一致，可能有缓存）
        now = datetime.now(UTC)
        ts_end = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        ts_start = ts_end - timedelta(hours=1)
        ts_start_str = ts_start.replace(tzinfo=None).isoformat() + "Z"
        ts_end_str = ts_end.replace(tzinfo=None).isoformat() + "Z"
        print(f"时间窗口: {ts_start_str} ~ {ts_end_str}\n")

        results = []
        for i in range(num_tests):
            print(f"=== 第 {i + 1}/{num_tests} 次测试 ===")
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
                print(f"  任务提交失败: {resp.status_code}")
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
            avg = elapsed / len(loop_ids)
            result = {
                "test": i + 1,
                "status": status,
                "total_time": elapsed,
                "avg_per_loop": avg,
                "loops": len(loop_ids),
            }
            results.append(result)
            print(f"  状态: {status}, 总耗时: {elapsed:.2f}s, 平均: {avg:.3f}s/回路\n")

        # 汇总报告
        print("=" * 60)
        print("性能测试汇总报告")
        print("=" * 60)
        print(f"测试次数: {len(results)}")
        print(f"回路数量: {len(loop_ids)}")
        print(f"时间窗口: {ts_start_str} ~ {ts_end_str}")
        print()

        if results:
            times = [r["total_time"] for r in results if r["status"] in ("SUCCESS", "COMPLETED")]
            if times:
                print(f"成功次数: {len(times)}")
                print(f"最快: {min(times):.2f}s")
                print(f"最慢: {max(times):.2f}s")
                print(f"平均: {sum(times) / len(times):.2f}s")
                print(f"中位数: {sorted(times)[len(times) // 2]:.2f}s")
                print(f"16秒目标: {'✓ 达标' if max(times) <= 16 else '✗ 未达标'}")
            else:
                print("所有测试均失败！")

        print()
        print("详细数据:")
        print(f"{'序号':>4} {'状态':>10} {'总耗时':>8} {'平均/回路':>10}")
        for r in results:
            print(
                f"{r['test']:>4} {r['status']:>10} {r['total_time']:>7.2f}s "
                f"{r['avg_per_loop']:>9.3f}s"
            )


if __name__ == "__main__":
    main()
