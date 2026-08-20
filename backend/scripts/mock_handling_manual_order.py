#!/usr/bin/env python3
"""处置模块「手工新增处置工单」功能 mock 模拟。

背景（设计缺口）：08-处置模块设计方案 §6.3 规定建议新增在诊断上下文
（POST /diagnosis/runs/{run_id}/actions），处置模块自身无独立新增入口；
且 loop_action_item.run_id 为 NOT NULL FK→diagnosis_run，纯手工工单
（现场处置不经诊断流程）无 run 可挂。

本脚本按「借用回路最近一次诊断 run」口径模拟手工新增（正式实现需
POST /handling/items 端点 + run_id 可空迁移或同口径借用，另行立项）：

1. 选定回路（缺省取清单中第一条有 PENDING 项的回路，--loop-keyword 可筛）；
2. DB 直插 MANUAL 工单：source=MANUAL / status=PENDING / suggested_by=当前用户 /
   basis='手工处置工单'（borrow 该回路最近 diagnosis_run.id 满足外键）；
3. 验证工单进入处置清单（GET /handling/items?loopId=）；
4. 真实 API 驱动流转：start（VALVE 类型 + 班组处置人）→ submit
   （结构化 action_detail）→ 停在 VERIFYING 供页面人工验证闭环。

用法（后端 17101 运行中）::

    cd backend && uv run python scripts/mock_handling_manual_order.py
    cd backend && uv run python scripts/mock_handling_manual_order.py --loop-keyword 90PIC
    cd backend && uv run python scripts/mock_handling_manual_order.py --cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

parser = argparse.ArgumentParser(description="处置模块手工新增工单 mock")
parser.add_argument("--base-url", default="http://localhost:17101")
parser.add_argument("--username", default="admin")
parser.add_argument("--password", default="admin123")
parser.add_argument("--loop-keyword", default=None, help="按回路位号模糊选回路")
parser.add_argument(
    "--cleanup", action="store_true", help="删除 basis 以'手工处置工单'开头的 mock 工单"
)
args = parser.parse_args()

#: 手工工单 basis 前缀（cleanup 匹配标记；正式实现后 basis 固定为"手工登记"）
MOCK_BASIS_PREFIX = "手工处置工单"

#: 演示工单内容（阀门类，贴合化工现场）
ORDER_CONTENT = "调节阀 FV-5121 填料函渗漏，工艺反映阀门动作迟滞，计划借停工窗口检修更换填料"


def _must_ok(resp: httpx.Response, step: str) -> dict:
    if resp.status_code != 200:
        print(f"[失败] {step}: HTTP {resp.status_code} {resp.text[:300]}")
        sys.exit(1)
    body = resp.json()
    if body.get("code") not in ("0", 0):
        print(f"[失败] {step}: {body.get('code')} {body.get('message')}")
        sys.exit(1)
    return body["data"]


async def insert_manual_order(loop_id: str, run_id: str, suggested_by: str) -> str:
    """直插手工工单（source=MANUAL / status=PENDING）。"""
    from app.core.db import AsyncSessionLocal
    from app.models.loop_action_item import LoopActionItem

    order_id = str(uuid4())
    async with AsyncSessionLocal() as session:
        session.add(
            LoopActionItem(
                id=order_id,
                run_id=run_id,
                loop_id=loop_id,
                source="MANUAL",
                category=None,
                content=ORDER_CONTENT,
                basis=f"{MOCK_BASIS_PREFIX}（mock：现场登记，不经诊断流程）",
                priority=None,
                status="PENDING",
                suggested_by=suggested_by,
                suggested_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()
    return order_id


async def cleanup_manual_orders() -> None:
    from sqlalchemy import delete, text

    from app.core.db import AsyncSessionLocal
    from app.models.loop_action_item import LoopActionItem

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(LoopActionItem).where(
                text("loop_action_item.basis LIKE :prefix"),
            ),
            {"prefix": f"{MOCK_BASIS_PREFIX}%"},
        )
        await session.commit()
        print(f"[清理完成] 删除手工工单 mock {result.rowcount} 条")


async def _pick_loop_with_run(keyword: str | None) -> tuple[str, str] | None:
    """选「有诊断 run」的回路（返回 loop_id, tag_name）；无则返回 None。"""
    from sqlalchemy import func, select, true

    from app.core.db import AsyncSessionLocal
    from app.models.diagnosis_run import DiagnosisRun
    from app.models.loop import LoopLedger

    async with AsyncSessionLocal() as session:
        stmt = (
            select(LoopLedger.id, LoopLedger.tag_name)
            .join(DiagnosisRun, DiagnosisRun.loop_id == LoopLedger.id)
            .where(LoopLedger.is_active == true())
            .group_by(LoopLedger.id, LoopLedger.tag_name)
            .order_by(func.max(DiagnosisRun.created_at).desc())
        )
        if keyword:
            stmt = stmt.where(LoopLedger.tag_name.ilike(f"%{keyword}%"))
        row = (await session.execute(stmt.limit(1))).first()
        return (str(row.id), row.tag_name) if row else None


async def _seed_minimal_dataset() -> tuple[str, str]:
    """空库自举：装置树（工厂→单元）+ 回路 + 一条诊断 run。

    幂等：回路按 tag_name 复用；返回 (loop_id, tag_name)。
    """
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.diagnosis_run import DiagnosisRun
    from app.models.loop import LoopLedger
    from app.models.plant_node import PlantNode

    tag = "90PIC51212A_PIDA"
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(LoopLedger).where(LoopLedger.tag_name == tag))
        ).scalar_one_or_none()
        if existing is None:
            # 装置树：工厂 → 单元（复用同名节点，避免唯一约束冲突）
            factory = (
                await session.execute(
                    select(PlantNode).where(
                        PlantNode.name == "EO 工厂", PlantNode.parent_id.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if factory is None:
                factory = PlantNode(id=str(uuid4()), name="EO 工厂", type="FACTORY")
                session.add(factory)
            unit = (
                await session.execute(select(PlantNode).where(PlantNode.name == "醛化反应单元"))
            ).scalar_one_or_none()
            if unit is None:
                unit = PlantNode(
                    id=str(uuid4()), name="醛化反应单元", type="UNIT", parent_id=factory.id
                )
                session.add(unit)
            loop = LoopLedger(
                id=str(uuid4()),
                tag_name=tag,
                description="辛醇罐TK521A顶部压力（mock 自举）",
                unit_id=unit.id,
                importance_level=1,
                control_type="FAST",
                loop_type="PRESSURE",
                status="PARTIAL",
                is_active=True,
            )
            session.add(loop)
        else:
            loop = existing
        await session.flush()

        has_run = (
            await session.execute(
                select(DiagnosisRun.id).where(DiagnosisRun.loop_id == loop.id).limit(1)
            )
        ).scalar_one_or_none()
        if not has_run:
            now = datetime.now(UTC).replace(tzinfo=None)
            session.add(
                DiagnosisRun(
                    id=str(uuid4()),
                    loop_id=loop.id,
                    triggered_by="admin",
                    trigger_type="MANUAL",
                    time_window_start=now - timedelta(hours=24),
                    time_window_end=now,
                    operator_group="full",
                    status="SUCCESS",
                    primary_category="VALVE",
                )
            )
        await session.commit()
    return str(loop.id), tag


async def _latest_run_id(loop_id: str) -> str | None:
    """回路最近一次诊断 run（手工工单借用其 id 满足 run_id 外键）。"""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.diagnosis_run import DiagnosisRun

    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(DiagnosisRun.id)
                .where(DiagnosisRun.loop_id == loop_id)
                .order_by(DiagnosisRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def amain() -> None:
    if args.cleanup:
        await cleanup_manual_orders()
        return

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        token = _must_ok(
            client.post(
                "/api/v1/auth/login",
                json={"username": args.username, "password": args.password},
            ),
            "登录",
        )["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"
        print(f"[OK] 登录 {args.username}")

        # 1. 选「有诊断 run」的回路；空库时自举最小数据集（装置树+回路+run）
        picked = await _pick_loop_with_run(args.loop_keyword)
        if picked:
            loop_id, loop_tag = picked
            print(f"[OK] 选定回路 {loop_tag}（{loop_id}）")
        else:
            print("[提示] 库中无可复用回路，自举最小数据集（EO 工厂/醛化反应单元/回路/诊断 run）")
            loop_id, loop_tag = await _seed_minimal_dataset()
            print(f"[OK] 自举完成，使用回路 {loop_tag}（{loop_id}）")

        # 2. 借用最近诊断 run（run_id NOT NULL 约束的 mock 口径）
        run_id = await _latest_run_id(loop_id)
        if not run_id:
            print("[失败] 该回路无诊断记录（run_id 外键无法满足），换一个回路重试")
            sys.exit(1)
        print(f"[OK] 借用最近诊断 run {run_id}（正式实现：run_id 可空迁移）")

        # 3. 直插手工工单
        order_id = await insert_manual_order(loop_id, run_id, args.username)
        print(f"[OK] 手工工单已登记 {order_id}")
        print(f"     内容：{ORDER_CONTENT}")

        # 4. 验证进入清单
        listed = _must_ok(
            client.get("/api/v1/handling/items", params={"loopId": loop_id, "pageSize": 50}),
            "清单复核",
        )
        hit = next((i for i in listed["items"] if i["id"] == order_id), None)
        if not hit:
            print("[失败] 工单未出现在处置清单")
            sys.exit(1)
        print(
            f"[OK] 清单可见：来源={hit['source']} "
            f"状态={hit['statusLabel']} 建议人={hit['suggestedBy']}"
        )

        # 5. 真实 API 流转：PENDING → HANDLING → VERIFYING
        started = _must_ok(
            client.post(
                f"/api/v1/handling/items/{order_id}/start",
                json={
                    "actionType": "VALVE",
                    "handler": "仪表班-李四",
                    "actionDetail": {"parts": "更换填料函", "downtimeHours": 4},
                },
            ),
            "开始处置 start",
        )
        print(f"[OK] start → {started['status']}（处置人={started['handledBy']}）")
        submitted = _must_ok(
            client.post(
                f"/api/v1/handling/items/{order_id}/submit",
                json={
                    "actionDetail": {
                        "parts": "更换填料函 + 阀芯研磨",
                        "downtimeHours": 6,
                        "vendor": "装置检修队",
                    }
                },
            ),
            "提交验证 submit",
        )
        print(f"[OK] submit → {submitted['status']}（actionDetail={submitted['actionDetail']}）")

        print(
            f"""
[完成] 手工工单 {order_id}（回路 {loop_tag}）已登记并流转至 VERIFYING。
前端验证：打开 /handling → 该回路下可见「人工新增」工单行（验证中），
点击行打开详情抽屉可继续「有效·闭环 / 无效·重开」。
清理：uv run python scripts/mock_handling_manual_order.py --cleanup
"""
        )


if __name__ == "__main__":
    asyncio.run(amain())
