"""MW-P5-04 性能压测脚本：attention/summary API p95 响应时间 + 数据集生成/清理。

使用方法：
    cd backend && uv run python scripts/perf_test_attention_summary.py

流程：
1. 生成压测数据集（1000 回路 + 10000 关注项=alert events）
2. 对 /monitor/attention 和 /monitor/loops/{id}/summary 跑 50 轮 p95
3. 输出报告（JSON + 控制台摘要）
4. 清理压测数据（tag_name 以 PERF_ 前缀标识）

压测数据隔离：所有压测回路的 tag_name 以 PERF_ 前缀，清理时按前缀删除。
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# 让 backend 目录下的模块可导入
sys.path.insert(0, ".")

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.alert import AlertEvent  # noqa: E402
from app.models.loop import LoopLedger  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PERF_PREFIX = "PERF_"
NUM_LOOPS = 1000
NUM_ALERTS_PER_LOOP = 10  # 1000 × 10 = 10000 关注项
BASE_URL = "http://localhost:7101"
API_PREFIX = "/api/v1"
NUM_ROUNDS = 50  # 每个端点跑 50 轮取 p95
P95_TARGET_MS = 500  # 目标 p95 ≤ 500ms

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

# ---------------------------------------------------------------------------
# 数据集生成
# ---------------------------------------------------------------------------


async def generate_dataset(db: AsyncSession) -> tuple[list[str], str]:
    """生成压测数据集，返回 (loop_ids, cleanup_marker)。"""
    batch_id = str(uuid4())[:8]
    print(f"[生成] 开始生成 {NUM_LOOPS} 回路 × {NUM_ALERTS_PER_LOOP} 关注项 (batch={batch_id})...")

    loop_ids: list[str] = []
    loops: list[LoopLedger] = []
    now = datetime.now(UTC).replace(tzinfo=None)

    for i in range(NUM_LOOPS):
        loop = LoopLedger(
            id=str(uuid4()),
            tag_name=f"{PERF_PREFIX}{batch_id}_L{i:04d}",
            status="PARTIAL",
            loop_type="OTHER",
            importance_level=2,
            include_in_evaluation=True,
            created_at=now,
            updated_at=now,
        )
        loops.append(loop)
        loop_ids.append(loop.id)

    db.add_all(loops)
    await db.flush()
    print(f"[生成] {len(loops)} 回路已插入，开始生成关注项...")

    # 批量生成 alert events（关注项来源之一）
    severities = ["CRITICAL", "ERROR", "WARN", "INFO"]
    statuses = ["ACTIVE", "ACKNOWLEDGED", "SUPPRESSED"]
    alerts: list[AlertEvent] = []

    for loop_idx, loop_id in enumerate(loop_ids):
        for j in range(NUM_ALERTS_PER_LOOP):
            sev = severities[j % len(severities)]
            status = statuses[j % len(statuses)]
            alert = AlertEvent(
                id=str(uuid4()),
                rule_code="PERF_TEST_RULE",
                rule_version=1,
                loop_id=loop_id,
                severity=sev,
                status=status,
                trigger_condition_snapshot={"test": True, "batch": batch_id},
                rule_dsl_snapshot={"type": "THRESHOLD", "test": True},
                trigger_count=1,
                triggered_at=now - timedelta(minutes=loop_idx + j),
            )
            alerts.append(alert)

    # 分批 add 避免内存峰值
    BATCH_SIZE = 500
    for start in range(0, len(alerts), BATCH_SIZE):
        db.add_all(alerts[start : start + BATCH_SIZE])
        await db.flush()

    await db.commit()
    print(f"[生成] 完成：{len(loops)} 回路 + {len(alerts)} 关注项")
    return loop_ids, batch_id


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


async def cleanup_dataset(db: AsyncSession, batch_id: str) -> None:
    """清理压测数据（按 tag_name 前缀 + batch_id）。"""
    print(f"[清理] 开始清理 batch={batch_id}...")
    pattern = f"{PERF_PREFIX}{batch_id}%"

    # 先删 alert events（CASCADE 会自动处理，但显式删更安全）
    result = await db.execute(select(LoopLedger.id).where(LoopLedger.tag_name.like(pattern)))
    loop_ids = [row[0] for row in result.fetchall()]

    if loop_ids:
        await db.execute(delete(AlertEvent).where(AlertEvent.loop_id.in_(loop_ids)))
        await db.execute(delete(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
        await db.commit()

    print(f"[清理] 已删除 {len(loop_ids)} 回路及其关注项")


# ---------------------------------------------------------------------------
# 性能测试
# ---------------------------------------------------------------------------


async def login(client: httpx.AsyncClient) -> str:
    """登录获取 accessToken。"""
    resp = await client.post(
        f"{BASE_URL}{API_PREFIX}/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]["accessToken"]


async def measure_endpoint(
    client: httpx.AsyncClient,
    token: str,
    method: str,
    url: str,
    rounds: int = NUM_ROUNDS,
) -> dict:
    """对指定端点跑 N 轮，返回延迟统计。"""
    headers = {"Authorization": f"Bearer {token}"}
    latencies: list[float] = []

    for _ in range(rounds):
        start = time.perf_counter()
        if method == "GET":
            resp = await client.get(url, headers=headers, timeout=30)
        else:
            resp = await client.post(url, headers=headers, timeout=30)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)

        if resp.status_code != 200:
            print(f"  ⚠ HTTP {resp.status_code} @ {url}: {resp.text[:200]}")
            return {
                "url": url,
                "rounds": len(latencies),
                "error": f"HTTP {resp.status_code}",
                "latencies_ms": latencies,
            }

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]
    mean = statistics.mean(latencies)

    return {
        "url": url,
        "rounds": len(latencies),
        "mean_ms": round(mean, 1),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "p99_ms": round(p99, 1),
        "min_ms": round(min(latencies), 1),
        "max_ms": round(max(latencies), 1),
        "p95_pass": p95 <= P95_TARGET_MS,
        "latencies_ms": [round(x, 1) for x in latencies],
    }


async def run_perf_tests(loop_ids: list[str]) -> dict:
    """运行性能测试。"""
    async with httpx.AsyncClient() as client:
        token = await login(client)
        print(f"[压测] 登录成功，开始 {NUM_ROUNDS} 轮测试...")

        results: dict = {}

        # 1. attention 列表（默认分页）
        print("\n[压测] 1/4 GET /monitor/attention (page=1, pageSize=20)")
        results["attention_default"] = await measure_endpoint(
            client,
            token,
            "GET",
            f"{BASE_URL}{API_PREFIX}/monitor/attention?page=1&pageSize=20",
        )

        # 2. attention 列表（大分页）
        print("[压测] 2/4 GET /monitor/attention (page=1, pageSize=100)")
        results["attention_large_page"] = await measure_endpoint(
            client,
            token,
            "GET",
            f"{BASE_URL}{API_PREFIX}/monitor/attention?page=1&pageSize=100",
        )

        # 3. attention 列表（筛选 severity=CRITICAL）
        print("[压测] 3/4 GET /monitor/attention (筛选 source=ALERT)")
        results["attention_filtered"] = await measure_endpoint(
            client,
            token,
            "GET",
            f"{BASE_URL}{API_PREFIX}/monitor/attention?source=ALERT&page=1&pageSize=20",
        )

        # 4. summary（取第一个压测回路）
        test_loop_id = loop_ids[0]
        print(f"[压测] 4/4 GET /monitor/loops/{test_loop_id[:8]}.../summary")
        results["summary"] = await measure_endpoint(
            client,
            token,
            "GET",
            f"{BASE_URL}{API_PREFIX}/monitor/loops/{test_loop_id}/summary",
        )

        return results


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


def generate_report(results: dict, loop_count: int, alert_count: int) -> str:
    """生成 Markdown 性能测试报告。"""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# MW-P5-04 性能压测报告",
        "",
        f"**测试时间**: {now_str}",
        f"**数据规模**: {loop_count} 回路 / {alert_count} 关注项",
        f"**测试轮次**: {NUM_ROUNDS} 轮/端点",
        f"**p95 目标**: ≤ {P95_TARGET_MS}ms",
        "",
        "## 测试结果摘要",
        "",
        "| 端点 | mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | p95 达标 |",
        "|---|---|---|---|---|---|",
    ]

    all_pass = True
    for key, r in results.items():
        if "error" in r:
            lines.append(f"| {key} | ERROR | - | - | - | ❌ {r['error']} |")
            all_pass = False
            continue
        passed = "✅" if r["p95_pass"] else "❌"
        if not r["p95_pass"]:
            all_pass = False
        lines.append(
            f"| {key} | {r['mean_ms']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | {passed} |"
        )

    lines.extend(
        [
            "",
            f"**总体结论**: {'✅ 全部端点 p95 达标' if all_pass else '❌ 存在 p95 未达标端点'}",
            "",
            "## 详细数据",
            "",
        ]
    )

    for key, r in results.items():
        lines.append(f"### {key}")
        lines.append(f"- URL: `{r.get('url', 'N/A')}`")
        if "error" in r:
            lines.append(f"- 错误: {r['error']}")
        else:
            lines.append(f"- 轮次: {r['rounds']}")
            lines.append(f"- mean: {r['mean_ms']}ms")
            lines.append(f"- p50: {r['p50_ms']}ms")
            lines.append(f"- p95: {r['p95_ms']}ms")
            lines.append(f"- p99: {r['p99_ms']}ms")
            lines.append(f"- min/max: {r['min_ms']}ms / {r['max_ms']}ms")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def main():
    print("=" * 60)
    print("MW-P5-04 性能压测：attention/summary")
    print("=" * 60)

    # 1. 生成数据集
    async with AsyncSessionLocal() as db:
        loop_ids, batch_id = await generate_dataset(db)

    try:
        # 2. 运行性能测试
        results = await run_perf_tests(loop_ids)

        # 3. 生成报告
        report = generate_report(results, NUM_LOOPS, NUM_LOOPS * NUM_ALERTS_PER_LOOP)
        report_path = "/tmp/perf-report-MW-P5-04.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[报告] 已保存到 {report_path}")

        # 控制台摘要
        print("\n" + "=" * 60)
        print("压测结果摘要")
        print("=" * 60)
        for key, r in results.items():
            if "error" in r:
                print(f"  {key}: ERROR ({r['error']})")
            else:
                status = "✅" if r["p95_pass"] else "❌"
                print(f"  {key}: p95={r['p95_ms']}ms {status}")
        print("=" * 60)

    finally:
        # 4. 清理数据集
        async with AsyncSessionLocal() as db:
            await cleanup_dataset(db, batch_id)

        print("\n[完成] 压测数据已清理")


if __name__ == "__main__":
    asyncio.run(main())
