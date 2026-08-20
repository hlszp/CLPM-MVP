#!/usr/bin/env python3
"""处置模块「手工处置工单」mock 模拟（v2.0：验证 POST /handling/orders 手动新建）。

v2.0 双实体后（08-处置模块设计方案 §6.2）：处置工单（handling_order）为独立
执行对象，支持手动新建（source=MANUAL，不依赖诊断 run）——本脚本原 v1.x 的
「借用回路最近诊断 run 满足 run_id NOT NULL」缺口已消除。

流程：
1. 选定回路（缺省取最新有活动回路，--loop-keyword 可筛；空库自举最小数据集）；
2. 真实 API 手动新建工单：POST /handling/orders（source=MANUAL，order_no 自动生成）；
3. 验证工单进入清单（GET /handling/orders?loopId=）；
4. 真实 API 驱动流转：start（补 handler）→ feedback（执行反馈）→ submit
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
from uuid import uuid4

import httpx

parser = argparse.ArgumentParser(description="处置模块手工工单 mock")
parser.add_argument("--base-url", default="http://localhost:17101")
parser.add_argument("--username", default="admin")
parser.add_argument("--password", default="admin123")
parser.add_argument("--loop-keyword", default=None, help="按回路位号模糊选回路")
parser.add_argument(
    "--cleanup", action="store_true", help="删除 title 以'调节阀 FV-5121'开头的 mock 工单"
)
args = parser.parse_args()

#: 手工工单标题前缀（cleanup 匹配标记）
MOCK_TITLE_PREFIX = "调节阀 FV-5121"

#: 演示工单内容（阀门类，贴合化工现场；title 缺省取前 50 字）
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


async def cleanup_manual_orders() -> None:
    from sqlalchemy import delete, text

    from app.core.db import AsyncSessionLocal
    from app.models.handling_order import HandlingOrder

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(HandlingOrder).where(text("handling_order.title LIKE :prefix")),
            {"prefix": f"{MOCK_TITLE_PREFIX}%"},
        )
        await session.commit()
        print(f"[清理完成] 删除手工工单 mock {result.rowcount} 条")


async def _pick_loop(keyword: str | None) -> tuple[str, str] | None:
    """选任意活动回路（v2.0 手动工单无 run 依赖）；返回 (loop_id, tag_name)。"""
    from sqlalchemy import func, select, true

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger

    async with AsyncSessionLocal() as session:
        stmt = select(LoopLedger.id, LoopLedger.tag_name).where(LoopLedger.is_active == true())
        if keyword:
            stmt = stmt.where(LoopLedger.tag_name.ilike(f"%{keyword}%"))
        stmt = stmt.order_by(LoopLedger.updated_at.desc().nulls_last(), func.random())
        row = (await session.execute(stmt.limit(1))).first()
        return (str(row.id), row.tag_name) if row else None


async def _seed_minimal_dataset() -> tuple[str, str]:
    """空库自举：装置树（工厂→单元）+ 回路（手动工单无 run 依赖，不再建诊断 run）。

    幂等：回路按 tag_name 复用；返回 (loop_id, tag_name)。
    """
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
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
        await session.commit()
    return str(loop.id), tag


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

        # 1. 选回路；空库时自举最小数据集
        picked = await _pick_loop(args.loop_keyword)
        if picked:
            loop_id, loop_tag = picked
            print(f"[OK] 选定回路 {loop_tag}（{loop_id}）")
        else:
            print("[提示] 库中无可复用回路，自举最小数据集（EO 工厂/醛化反应单元/回路）")
            loop_id, loop_tag = await _seed_minimal_dataset()
            print(f"[OK] 自举完成，使用回路 {loop_tag}（{loop_id}）")

        # 2. 手动新建工单（POST /handling/orders，source=MANUAL，run 无关）
        order = _must_ok(
            client.post(
                "/api/v1/handling/orders",
                json={
                    "loopId": loop_id,
                    "actionType": "VALVE",
                    "content": ORDER_CONTENT,
                    "handler": "仪表班-李四",
                },
            ),
            "手动新建工单",
        )
        order_id = order["id"]
        print(f"[OK] 手工工单已登记 {order['orderNo']}（{order_id}）")
        print(f"     标题：{order['title']}")
        print(f"     内容：{ORDER_CONTENT}")

        # 3. 验证进入清单
        listed = _must_ok(
            client.get("/api/v1/handling/orders", params={"loopId": loop_id, "pageSize": 50}),
            "清单复核",
        )
        hit = next((i for i in listed["items"] if i["id"] == order_id), None)
        if not hit:
            print("[失败] 工单未出现在处置工单清单")
            sys.exit(1)
        print(
            f"[OK] 清单可见：编号={hit['orderNo']} 来源={hit['source']} "
            f"状态={hit['statusLabel']} 处置人={hit['handler']}"
        )

        # 4. 真实 API 流转：PENDING → EXECUTING →（feedback）→ VERIFYING
        started = _must_ok(
            client.post(
                f"/api/v1/handling/orders/{order_id}/start",
                json={
                    "handler": "仪表班-李四",
                    "actionDetail": {"parts": "更换填料函", "downtimeHours": 4},
                },
            ),
            "开工 start",
        )
        print(f"[OK] start → {started['status']}（处置人={started['handler']}）")
        feedback = _must_ok(
            client.post(
                f"/api/v1/handling/orders/{order_id}/feedback",
                json={"content": "旧填料已拆除，待新填料到货回装（mock）"},
            ),
            "执行反馈 feedback",
        )
        print(f"[OK] feedback → 反馈 {len(feedback['feedbackLog'])} 条")
        submitted = _must_ok(
            client.post(
                f"/api/v1/handling/orders/{order_id}/submit",
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
[完成] 手工工单 {order["orderNo"]}（回路 {loop_tag}）已登记并流转至 VERIFYING。
前端验证：打开 /handling 工单 Tab → 该回路下可见「手动新建」工单行（验证中），
点击行打开详情抽屉可继续「有效·闭环 / 无效·重开」。
清理：uv run python scripts/mock_handling_manual_order.py --cleanup
"""
        )


if __name__ == "__main__":
    asyncio.run(amain())
