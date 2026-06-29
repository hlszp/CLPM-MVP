"""触发 27 回路诊断计算并验证诊断模块。

用法：
    cd backend && .venv/bin/python scripts/trigger_diagnosis_27loops.py

流程：
1. 查询 27 个 ACTIVE 回路
2. 对每个回路调用 run_loop_diagnosis.delay(loop_id, ts_start)
3. 等待任务完成（轮询 AsyncResult.status）
4. 验证诊断 API：GET /api/v1/diagnosis/list、/diagnosis/{loopId}、/diagnosis/tags
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 添加 backend 到 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.loop import LoopLedger
from app.tasks.diagnosis_engine import run_loop_diagnosis

# 诊断时间窗：与 KPI 验证保持一致
TS_START = "2026-06-26T09:00:00Z"

# API 基址
API_BASE = "http://localhost:8001/api/v1"

# 管理员账号
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


async def get_loop_ids() -> list[tuple[str, str]]:
    """获取 27 个回路的 (loop_id, tag_name) 列表。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LoopLedger.id, LoopLedger.tag_name)
            .where(LoopLedger.is_active.is_(True))
            .order_by(LoopLedger.tag_name)
        )
        return [(str(row[0]), row[1]) for row in result]


async def login() -> str:
    """登录获取 access_token。"""
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.post(
            "/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"]["accessToken"]


async def trigger_diagnosis(loops: list[tuple[str, str]]) -> dict[str, str]:
    """触发 27 回路诊断计算，等待结果。返回 loop_id → status 映射。"""
    print(f"\n=== 触发 {len(loops)} 回路诊断 (ts_start={TS_START}) ===")
    task_map: dict[str, tuple[str, str, object]] = {}  # loop_id → (tag_name, task_id, async_result)

    for loop_id, tag_name in loops:
        async_result = run_loop_diagnosis.delay(loop_id, TS_START)
        task_map[loop_id] = (tag_name, async_result.id, async_result)
        print(f"  {tag_name:30s} → task_id={async_result.id}")

    # 等待所有任务完成（最长 5 分钟）
    print("\n=== 等待任务完成 ===")
    deadline = time.time() + 300
    completed: dict[str, str] = {}  # loop_id → status
    while task_map and time.time() < deadline:
        for loop_id in list(task_map.keys()):
            tag_name, _task_id, async_result = task_map[loop_id]
            status = async_result.status
            if status in ("SUCCESS", "FAILURE", "REVOKED"):
                completed[loop_id] = status
                result_payload = None
                if status == "SUCCESS":
                    try:
                        result_payload = async_result.result
                    except Exception:  # noqa: BLE001
                        pass
                label_str = ""
                if isinstance(result_payload, dict):
                    labels = result_payload.get("labels", [])
                    label_str = f" labels={','.join(labels)}" if labels else ""
                    fused = result_payload.get("fusedConfidence")
                    if fused is not None:
                        label_str += f" fused={fused:.3f}"
                print(f"  {tag_name:30s} → {status}{label_str}")
                del task_map[loop_id]
        if task_map:
            await asyncio.sleep(2)

    if task_map:
        print(f"\n警告：{len(task_map)} 个任务超时未完成:")
        for tag_name, _tid, _ in task_map.values():
            print(f"  {tag_name}")

    # 统计结果
    success_count = sum(1 for s in completed.values() if s == "SUCCESS")
    failed_count = sum(1 for s in completed.values() if s == "FAILURE")
    print("\n=== 诊断任务结果 ===")
    print(f"  SUCCESS: {success_count}")
    print(f"  FAILURE: {failed_count}")
    print(f"  TIMEOUT: {len(task_map)}")
    return completed


async def verify_diagnosis_api(token: str, loops: list[tuple[str, str]]) -> None:
    """验证诊断 API。"""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=30.0) as client:
        # 1. 诊断列表
        print("\n=== GET /diagnosis/list ===")
        resp = await client.get("/diagnosis/list", params={"page": 1, "pageSize": 100})
        if resp.status_code == 200:
            data = resp.json()["data"]
            items = data.get("items", [])
            total = data.get("total", 0)
            print(f"  total: {total}, page items: {len(items)}")
            label_counter: dict[str, int] = {}
            for item in items:
                labels = item.get("diagLabels", []) or []
                for lab in labels:
                    label_counter[lab] = label_counter.get(lab, 0) + 1
                tag_name = item.get("tagName", "?")
                score = item.get("score")
                labels_str = ",".join(labels) if labels else "-"
                print(f"  {tag_name:30s} score={score!s:8s} labels={labels_str}")
            print(f"\n  标签分布: {label_counter}")
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")

        # 2. 诊断详情（取第一个回路）
        if loops:
            loop_id, tag_name = loops[0]
            print(f"\n=== GET /diagnosis/{loop_id} ({tag_name}) ===")
            resp = await client.get(f"/diagnosis/{loop_id}")
            if resp.status_code == 200:
                detail = resp.json()["data"]
                print(f"  loopId: {detail.get('loopId')}")
                print(f"  tagName: {detail.get('tagName')}")
                tags = detail.get("diagnosisTags", []) or detail.get("tags", [])
                print(f"  diagnosisTags count: {len(tags)}")
                for t in tags[:5]:
                    print(
                        f"    - {t.get('tagType') or t.get('label')}: "
                        f"confidence={t.get('confidence')} severity={t.get('severity')}"
                    )
                evidence_chain = detail.get("evidenceChain", [])
                print(f"  evidenceChain count: {len(evidence_chain)}")
            else:
                print(f"  ERROR {resp.status_code}: {resp.text[:200]}")

        # 3. 诊断标签列表
        print("\n=== GET /diagnosis/tags ===")
        resp = await client.get("/diagnosis/tags", params={"page": 1, "pageSize": 100})
        if resp.status_code == 200:
            data = resp.json()["data"]
            items = data.get("items", [])
            total = data.get("total", 0)
            print(f"  total: {total}, page items: {len(items)}")
            severity_counter: dict[str, int] = {}
            tag_type_counter: dict[str, int] = {}
            for item in items:
                sev = item.get("severity", "?")
                tt = item.get("tagType", "?")
                severity_counter[sev] = severity_counter.get(sev, 0) + 1
                tag_type_counter[tt] = tag_type_counter.get(tt, 0) + 1
            print(f"  严重等级分布: {severity_counter}")
            print(f"  标签类型分布: {tag_type_counter}")
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")

        # 4. 诊断指标配置
        print("\n=== GET /diagnosis/metrics ===")
        resp = await client.get("/diagnosis/metrics")
        if resp.status_code == 200:
            metrics = resp.json()["data"]
            print(f"  诊断指标配置数: {len(metrics)}")
            for m in metrics[:5]:
                print(
                    f"    - {m.get('diagCode')}: {m.get('diagName')} (enabled={m.get('isEnabled')})"
                )
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")

        # 5. 诊断统计报表
        print("\n=== GET /diagnosis/analytics ===")
        end_time = datetime.now(UTC).isoformat()
        start_time = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        resp = await client.get(
            "/diagnosis/analytics",
            params={
                "startTime": start_time,
                "endTime": end_time,
                "granularity": "day",
            },
        )
        if resp.status_code == 200:
            analytics = resp.json()["data"]
            print(f"  统计报表 keys: {list(analytics.keys())}")
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")


async def main() -> None:
    loops = await get_loop_ids()
    print(f"找到 {len(loops)} 个 ACTIVE 回路")
    if not loops:
        print("无回路可诊断，退出")
        return

    # 触发诊断
    await trigger_diagnosis(loops)

    # 登录
    token = await login()
    print(f"\n登录成功: {ADMIN_USERNAME}")

    # 验证 API
    await verify_diagnosis_api(token, loops)


if __name__ == "__main__":
    asyncio.run(main())
