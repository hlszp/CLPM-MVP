"""运维入口：python -m scripts.ten_minute_shadow --help（默认只读）。"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import AsyncSessionLocal
from app.core.redis import close_redis, redis_client
from app.models.loop import LoopLedger
from app.models.sys_config import SysConfig
from app.services import ten_minute_control as control
from app.services.result_ledger import canonical_json


def parser():
    p = argparse.ArgumentParser(description="10 分钟影子验证：不发布正式评分")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="只读：开关与当前影子 cohort")
    loops = sub.add_parser("loops", help="只读：查询指定回路 UUID")
    loops.add_argument("--tags", required=True, help="逗号分隔位号")
    config = sub.add_parser("configure", help="明确更改影子开关与白名单")
    config.add_argument("--enabled", choices=["true", "false"], required=True)
    config.add_argument("--loop-ids", help="逗号分隔 UUID；省略则保留既有白名单")
    run = sub.add_parser("run", help="投递低优先级手工影子试验")
    run.add_argument("--start", required=True, help="带时区 ISO 时间")
    run.add_argument("--end", required=True, help="带时区 ISO 时间")
    reports = sub.add_parser("reports", help="只读：最近报告（完整单份用 --id）")
    reports.add_argument("--limit", type=int, choices=range(1, 101), default=20)
    reports.add_argument("--id")
    return p


async def main(args):
    if args.command == "loops":
        tags = list(dict.fromkeys(args.tags.split(",")))
        if not 1 <= len(tags) <= control.MAX_LOOPS:
            raise ValueError("每次查询 1~24 个明确位号")
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(LoopLedger.id, LoopLedger.tag_name).where(LoopLedger.tag_name.in_(tags))
                )
            ).all()
        return {
            "loops": [{"loopId": str(r[0]), "tag": r[1]} for r in rows],
            "missing": sorted(set(tags) - {r[1] for r in rows}),
        }
    if args.command == "reports":
        active = await redis_client.get(control.ACTIVE_KEY)
        active_id = json.loads(active)["id"] if active else None

        def observed(row):
            if row.get("status") == "QUEUED" and row.get("id") != active_id:
                return {
                    **row,
                    "observedStatus": "STALLED",
                    "notice": "排队过期或交接中断，未完成；重新 run 发起新一轮",
                }
            return row

        if args.id:
            return observed(json.loads(control.report_path(args.id).read_text()))
        root = control.report_root()
        files = sorted(root.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        rows = [observed(json.loads(p.read_text())) for p in files[: args.limit]]
        return {"total": len(files), "shown": len(rows), "reports": rows}
    cfg = await control.read_config()
    if args.command == "configure":
        if args.loop_ids is not None:
            cfg["loopIds"] = args.loop_ids.split(",") if args.loop_ids else []
        cfg["enabled"] = args.enabled == "true"
        cfg = control.parse_config(canonical_json(cfg))
        async with AsyncSessionLocal() as db:
            found = set(
                (
                    await db.execute(select(LoopLedger.id).where(LoopLedger.id.in_(cfg["loopIds"])))
                ).scalars()
            )
            if set(cfg["loopIds"]) != {str(x) for x in found}:
                raise ValueError("白名单包含不存在的回路，未保存配置")
            stmt = insert(SysConfig).values(
                key=control.CONFIG_KEY,
                value=canonical_json(cfg),
                description="10分钟影子验证开关与白名单",
            )
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=[SysConfig.key], set_={"value": stmt.excluded.value}
                )
            )
            await db.commit()
        return cfg
    if args.command == "run":
        start, end = (datetime.fromisoformat(v) for v in (args.start, args.end))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("必须显式携带时区，例如 2026-10-21T01:00:00Z")
        return await control.enqueue(
            redis_client,
            loop_ids=cfg["loopIds"],
            start=start,
            end=end,
            kind="MANUAL_SHADOW",
            source_task_id=str(uuid4()),
        )
    active = await redis_client.get(control.ACTIVE_KEY)
    return {
        "config": cfg,
        "active": json.loads(active) if active else None,
        "notice": "无活跃 cohort 不代表历史试验成功，请读取 reports",
    }


async def run_cli():
    try:
        print(json.dumps(await main(parser().parse_args()), ensure_ascii=False, indent=2))
    finally:
        await close_redis()


if __name__ == "__main__":
    asyncio.run(run_cli())
