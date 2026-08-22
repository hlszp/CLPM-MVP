#!/usr/bin/env python3
"""处置模块流转闭环 mock 数据构造（v2.0 双实体：建议 → 工单 PENDING → EXECUTING → VERIFYING）。

用途：本地验证处置状态机与 KPI 前后对比逻辑（08-处置模块设计方案 v2.0 §4）。
做法：
1. 经真实 API 驱动双实体流转（建议 accept → convert 转工单 → 工单 start → submit），
   端到端验证状态机与守卫；
2. 直插 kpi_snapshot_hourly 前后窗口 mock 快照：
   - 前窗 [started_at−24h, started_at]：较差指标（score≈71，振荡高，可信度 C）
   - 后窗 [submitted_at, submitted_at+24h]：改善指标（score≈89，可信度 B）
   mock 行 data_lineage 带 {"mock": "handling-flow"} 标记，ts 取 :30 分偏移
   避开真实整点快照的唯一约束（uq_kpi_snapshot_hourly_loop_ts）；
3. 拉取工单 kpi-comparison 打印对比结果。

用法（后端需在 17101 运行）::

    cd backend && uv run python scripts/mock_handling_flow.py
    cd backend && uv run python scripts/mock_handling_flow.py --loop-keyword 90PIC51212A
    cd backend && uv run python scripts/mock_handling_flow.py --suggestion-id <uuid>
    cd backend && uv run python scripts/mock_handling_flow.py --cleanup   # 仅清理 mock 快照

执行后工单停在 VERIFYING，可在前端 /handling 工单清单打开详情抽屉查看
KPI 对比卡并人工点击「有效·闭环 / 无效·重开」完成最后一步。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx

# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="处置模块流转闭环 mock 数据构造")
parser.add_argument("--base-url", default="http://localhost:17101", help="后端 API 根地址")
parser.add_argument("--username", default="admin")
parser.add_argument("--password", default="admin123")
parser.add_argument("--suggestion-id", default=None, help="指定建议 ID（缺省取第一条 PENDING）")
parser.add_argument("--loop-keyword", default=None, help="按回路位号模糊筛选 PENDING 建议")
parser.add_argument(
    "--cleanup",
    action="store_true",
    help="仅清理 mock 快照（data_lineage->>'mock'='handling-flow'），不构造新数据",
)
args = parser.parse_args()

MOCK_TAG = "handling-flow"


# ---------------------------------------------------------------------------
# API 辅助
# ---------------------------------------------------------------------------


def _must_ok(resp: httpx.Response, step: str) -> dict:
    if resp.status_code != 200:
        print(f"[失败] {step}: HTTP {resp.status_code} {resp.text[:300]}")
        sys.exit(1)
    body = resp.json()
    if body.get("code") not in ("0", 0):
        print(f"[失败] {step}: {body.get('code')} {body.get('message')}")
        sys.exit(1)
    return body["data"]


def login(client: httpx.Client) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": args.username, "password": args.password},
    )
    data = _must_ok(resp, "登录")
    return data["accessToken"]


def _naive(iso_z: str) -> datetime:
    """API 返回的 ISO+Z → naive UTC（DB 列口径）。"""
    return datetime.fromisoformat(iso_z.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# KPI mock 快照注入
# ---------------------------------------------------------------------------

#: 前窗指标（处置前"病情"：评分偏低、振荡高、可信度 C）
BEFORE_METRICS = {
    "score": 71.2,
    "good_value_rate": 96.5,
    "auto_mode_rate": 82.0,
    "steady_rate": 63.5,
    "accuracy_rate": 74.8,
    "oscillation_rate": 38.6,
    "saturation_rate": 9.4,
    "fast_rate": 70.1,
    "effective_auto_rate": 66.3,
    "confidence_level": "C",
}
#: 后窗指标（处置后"疗效"：评分回升、振荡收敛、可信度 B）
AFTER_METRICS = {
    "score": 89.4,
    "good_value_rate": 98.2,
    "auto_mode_rate": 95.0,
    "steady_rate": 91.6,
    "accuracy_rate": 92.3,
    "oscillation_rate": 8.9,
    "saturation_rate": 2.1,
    "fast_rate": 88.7,
    "effective_auto_rate": 93.5,
    "confidence_level": "B",
}


async def insert_mock_snapshots(
    loop_id: str, order_id: str, started_at: datetime, submitted_at: datetime
) -> tuple[int, int]:
    """前窗 6 条（:30 偏移，末条贴 started_at 前 30min）+ 后窗 12 条。

    返回 (前窗插入数, 后窗插入数)。已存在同 ts_start 的行跳过（幂等可重跑）。
    """
    from app.core.db import AsyncSessionLocal
    from app.models.metric import KpiSnapshotHourly

    def _row(loop_id: str, ts_start: datetime, metrics: dict) -> KpiSnapshotHourly:
        return KpiSnapshotHourly(
            id=str(uuid4()),
            loop_id=loop_id,
            ts_start=ts_start,
            ts_end=ts_start + timedelta(minutes=30),
            score=Decimal(str(metrics["score"])),
            good_value_rate=Decimal(str(metrics["good_value_rate"])),
            auto_mode_rate=Decimal(str(metrics["auto_mode_rate"])),
            steady_rate=Decimal(str(metrics["steady_rate"])),
            accuracy_rate=Decimal(str(metrics["accuracy_rate"])),
            oscillation_rate=Decimal(str(metrics["oscillation_rate"])),
            saturation_rate=Decimal(str(metrics["saturation_rate"])),
            fast_rate=Decimal(str(metrics["fast_rate"])),
            effective_auto_rate=Decimal(str(metrics["effective_auto_rate"])),
            status="SUCCESS",
            algorithm_version="mock-handling-flow",
            confidence_level=metrics["confidence_level"],
            data_lineage={"mock": MOCK_TAG, "orderId": order_id},
        )

    # 前窗：末条贴 started_at−1min（压过窗口内真实整点快照，保证 mock 被"最新一条"选中），
    # 其余按 1h 间隔往前铺 6 条
    before_rows = [
        _row(loop_id, started_at - timedelta(minutes=1 + 60 * i), BEFORE_METRICS) for i in range(6)
    ]
    # 后窗：末条贴 submitted_at+23h30m（演示期内不被未来真实整点快照反超），
    # 其余按 1h 间隔往前铺 12 条
    after_rows = [
        _row(loop_id, submitted_at + timedelta(minutes=23 * 60 + 30 - 60 * i), AFTER_METRICS)
        for i in range(12)
    ]

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        inserted_before = inserted_after = 0
        for rows, tag in ((before_rows, "before"), (after_rows, "after")):
            for r in rows:
                exists = (
                    await session.execute(
                        select(KpiSnapshotHourly.id).where(
                            KpiSnapshotHourly.loop_id == loop_id,
                            KpiSnapshotHourly.ts_start == r.ts_start,
                        )
                    )
                ).scalar_one_or_none()
                if exists:
                    print(f"  [跳过] {tag} {r.ts_start} 已存在快照（唯一约束）")
                    continue
                session.add(r)
                if tag == "before":
                    inserted_before += 1
                else:
                    inserted_after += 1
        await session.commit()
    return inserted_before, inserted_after


async def cleanup_mock_snapshots() -> None:
    from sqlalchemy import delete, text

    from app.core.db import AsyncSessionLocal
    from app.models.metric import KpiSnapshotHourly

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(KpiSnapshotHourly).where(
                text("kpi_snapshot_hourly.data_lineage->>'mock' = :tag")
            ),
            {"tag": MOCK_TAG},
        )
        await session.commit()
        print(f"[清理完成] 删除 mock 快照 {result.rowcount} 行")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def amain() -> None:
    if args.cleanup:
        await cleanup_mock_snapshots()
        return

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        token = login(client)
        client.headers["Authorization"] = f"Bearer {token}"
        print(f"[OK] 登录 {args.username}")

        # 1. 选 PENDING 建议（审核对象）
        if args.suggestion_id:
            sug = _must_ok(
                client.get(
                    "/api/v1/handling/suggestions",
                    params={"status": "PENDING", "pageSize": 100},
                ),
                "查询建议清单",
            )
            hit = next((i for i in sug["items"] if i["id"] == args.suggestion_id), None)
            if hit is None or hit["status"] != "PENDING":
                print(f"[失败] 建议 {args.suggestion_id} 不存在或非 PENDING")
                sys.exit(1)
            suggestion_id = args.suggestion_id
            loop_id = hit["loopId"]
            print(f"[OK] 选中建议 {suggestion_id}（回路 {hit['loopTagName']}）")
        else:
            params: dict = {"status": "PENDING", "pageSize": 50}
            if args.loop_keyword:
                params["keyword"] = args.loop_keyword
            data = _must_ok(
                client.get("/api/v1/handling/suggestions", params=params), "查询建议清单"
            )
            if not data["items"]:
                print("[失败] 没有可选的 PENDING 建议（可先跑诊断生成建议）")
                sys.exit(1)
            suggestion_id = data["items"][0]["id"]
            loop_id = data["items"][0]["loopId"]
            print(f"[OK] 选中建议 {suggestion_id}（回路 {data['items'][0]['loopTagName']}）")

        # 2. 建议审核：PENDING → ACCEPTED
        accepted = _must_ok(
            client.post(f"/api/v1/handling/suggestions/{suggestion_id}/accept"), "接受建议 accept"
        )
        assert accepted["status"] == "ACCEPTED", accepted["status"]
        print("[OK] accept → ACCEPTED")

        # 3. 转工单：ACCEPTED → CONVERTED（生成工单）
        order = _must_ok(
            client.post(
                "/api/v1/handling/suggestions/convert",
                json={
                    "suggestionIds": [suggestion_id],
                    "actionType": "TUNING",
                    "handler": "mock-仪控班",
                },
            ),
            "转工单 convert",
        )
        assert order["status"] == "PENDING", order["status"]
        order_id = order["id"]
        print(f"[OK] convert → 工单 {order['orderNo']}（PENDING）")

        # 4. 开工：PENDING → EXECUTING
        started = _must_ok(
            client.post(
                f"/api/v1/handling/orders/{order_id}/start",
                json={
                    "handler": "mock-仪控班",
                    "pidBefore": {"p": 1.2, "i": 20, "d": 0},
                    "actionDetail": {"method": "Lambda 整定法（mock）"},
                },
            ),
            "开工 start",
        )
        assert started["status"] == "EXECUTING", started["status"]
        started_at = _naive(started["startedAt"])
        print(f"[OK] start → EXECUTING（started_at={started_at}）")

        # 5. 执行反馈（自环追加，状态不变）
        feedback = _must_ok(
            client.post(
                f"/api/v1/handling/orders/{order_id}/feedback",
                json={"content": "参数已按整定建议下发，观察 24h（mock）"},
            ),
            "执行反馈 feedback",
        )
        assert feedback["status"] == "EXECUTING", feedback["status"]
        print(f"[OK] feedback → 反馈 {len(feedback['feedbackLog'])} 条")

        # 6. 提交验证：EXECUTING → VERIFYING
        submitted = _must_ok(
            client.post(
                f"/api/v1/handling/orders/{order_id}/submit",
                json={
                    "actionDetail": {
                        "pidAfter": {"p": 0.8, "i": 35, "d": 0},
                        "method": "Lambda 整定法（mock）",
                    }
                },
            ),
            "提交验证 submit",
        )
        assert submitted["status"] == "VERIFYING", submitted["status"]
        submitted_at = _naive(submitted["submittedAt"])
        print(f"[OK] submit → VERIFYING（submitted_at={submitted_at}）")

        # 7. 注入前后窗 KPI mock 快照
        nb, na = await insert_mock_snapshots(loop_id, order_id, started_at, submitted_at)
        print(f"[OK] mock 快照注入：前窗 {nb} 条（score≈71/C），后窗 {na} 条（score≈89/B）")

        # 8. 工单 kpi-comparison 预览验证
        cmp_data = _must_ok(
            client.post(f"/api/v1/handling/orders/{order_id}/kpi-comparison"), "KPI 对比预览"
        )
        before = cmp_data["kpiBefore"] or {}
        after = cmp_data["kpiAfter"] or {}
        print("\n===== KPI 前后对比（预览，verify 时固化）=====")
        print(f"{'指标':<12}{'处置前':>10}{'处置后':>10}")
        for label, key in (
            ("综合评分", "score"),
            ("有效自控率", "effectiveAutoRate"),
            ("平稳率", "steadyRate"),
            ("准确率", "accuracyRate"),
            ("快速率", "fastRate"),
            ("振荡率", "oscillationRate"),
            ("饱和率", "saturationRate"),
            ("好值率", "goodValueRate"),
        ):
            b = before.get(key)
            a = after.get(key)
            bs = f"{b:.1f}" if isinstance(b, int | float) else "—"
            as_ = f"{a:.1f}" if isinstance(a, int | float) else "—"
            print(f"{label:<12}{bs:>10}{as_:>10}")
        cl_b = before.get("confidenceLevel") or "—"
        cl_a = after.get("confidenceLevel") or "—"
        print(f"可信度: 前={cl_b} 后={cl_a}")
        print(
            f"窗口: 前[{cmp_data['window']['beforeStart']} ~ {cmp_data['window']['beforeEnd']}]"
            f" 后[{cmp_data['window']['afterStart']} ~ {cmp_data['window']['afterEnd']}]"
        )

        print(
            f"""
[完成] 工单 {order["orderNo"]}（{order_id}）已停在 VERIFYING。
前端验证：打开 /handling 工单 Tab → 点击该行 → 抽屉「验证中」区可见 KPI 对比卡，
点击「有效·闭环 / 无效·重开」时服务端将固化上述快照到 kpi_before/after。
清理 mock 快照：uv run python scripts/mock_handling_flow.py --cleanup
"""
        )


if __name__ == "__main__":
    if not args.cleanup:
        print(json.dumps({"base_url": args.base_url}, ensure_ascii=False))
    asyncio.run(amain())
