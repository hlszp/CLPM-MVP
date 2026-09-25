#!/usr/bin/env python3
"""登记本地历史读取路由（history_layout_manifest）—— 运维显式动作。

背景（2026-09-25 生产排查）：
写入侧布局（sys_config: history.storage_mode）与读取路由（manifest）是两套
独立开关。趋势图与评估按 manifest 决定读哪张表；无 manifest 段时恒按 legacy
读宽表 st_loop_data。因此把写入切成 point（只写点表）却没有登记 manifest 时，
表现就是"实时数据在采、趋势图全空"，且此前没有任何入口能完成这一步
（set_layout 是无调用方的服务函数，UI/API 都没有）。

用法（默认 dry-run，不写库）：
    cd backend && uv run python scripts/register_layout_manifest.py --layout point
    cd backend && uv run python scripts/register_layout_manifest.py --layout point --apply
    # 指定生效时刻（默认立即）；把历史窗口留给旧布局时用 --valid-from
    cd backend && uv run python scripts/register_layout_manifest.py --layout point \
        --valid-from 2026-09-25T00:00:00+08:00 --apply

只有 --apply 才写库；写库前会打印当前自检结论，写库后再打印一次对比。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

LAYOUTS = ("legacy", "shadow", "point")
SCOPES = ("global", "loop", "source")


async def main() -> int:
    parser = argparse.ArgumentParser(description="登记历史读取路由（manifest）")
    parser.add_argument("--layout", required=True, choices=LAYOUTS, help="目标读取布局")
    parser.add_argument("--scope", default="global", choices=SCOPES, help="作用域，默认 global")
    parser.add_argument("--scope-id", default=None, help="scope=loop/source 时的对象 ID")
    parser.add_argument(
        "--valid-from",
        default=None,
        help="生效时刻（ISO 8601，默认立即）。历史窗口需要保留旧布局时显式指定",
    )
    parser.add_argument("--basis", default=None, help="登记依据（写进 manifest，便于回溯）")
    parser.add_argument(
        "--apply", action="store_true", help="真正写库（缺省为 dry-run，只打印将要执行的动作）"
    )
    args = parser.parse_args()

    if args.scope != "global" and not args.scope_id:
        print("scope=loop/source 时必须提供 --scope-id")
        return 2

    valid_from = (
        datetime.fromisoformat(args.valid_from.replace("Z", "+00:00"))
        if args.valid_from
        else datetime.now(UTC)
    )

    from app.core.db import AsyncSessionLocal
    from app.services.data_source.history_layout import get_layout_selfcheck
    from app.services.data_source.point_history_metadata import set_layout

    async with AsyncSessionLocal() as db:
        before = await get_layout_selfcheck(db)
        print("--- 登记前 ---")
        print(f"  写入侧 storage_mode = {before['writeMode']}")
        print(f"  读取侧 readLayout   = {before['readLayout']}")
        print(f"  一致性 selfcheck    = {before['severity']} / consistent={before['consistent']}")
        print(f"  结论: {before['diagnosis']}")

        print("--- 将执行 ---")
        print(
            f"  插入 manifest 段：scope={args.scope}"
            + (f":{args.scope_id}" if args.scope_id else "")
            + f" layout={args.layout} valid_from={valid_from.isoformat()}"
        )
        if not args.apply:
            print("  （dry-run，未写库；确认无误后加 --apply 重跑）")
            return 0

        entry = await set_layout(
            db,
            layout=args.layout,
            valid_from=valid_from,
            scope_type=args.scope,
            scope_id=args.scope_id,
            basis=args.basis or "register_layout_manifest.py 手工登记（切读）",
        )
        await db.commit()
        print(f"  已写入 manifest id={entry.id}")

        after = await get_layout_selfcheck(db)
        print("--- 登记后 ---")
        print(f"  读取侧 readLayout = {after['readLayout']}")
        print(f"  一致性 selfcheck  = {after['severity']} / consistent={after['consistent']}")
        print(f"  结论: {after['diagnosis']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
