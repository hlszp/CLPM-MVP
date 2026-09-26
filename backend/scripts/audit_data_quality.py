#!/usr/bin/env python3
"""位号级数据质量体检 CLI（点表口径，只读）。

替代已删除的 scripts/td_quality_audit.py（原实现读已退役的宽表 st_loop_data）。
核心逻辑在 app/services/data_quality_audit.py，本脚本只负责参数、取窗口、打印/导出。

用法：
    cd backend && ./.venv/bin/python scripts/audit_data_quality.py --last-hours 6
    ... --start 2026-09-25T00:00:00 --end 2026-09-25T06:00:00 --loop <loop_id>
    ... --format json --out /tmp/dq.json
    ... --format csv  --out /tmp/dq.csv

时间参数：ISO 或 YYYY-MM-DD HH:MM（按 UTC 解释，与产品代码一致）；
工具内部一律用 ISO-Z 字面量查询（裸字面量会被 TDengine 按会话时区解释，静默偏移 8 小时）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

ISSUE_LABEL = {
    "no_data": "断流",
    "bad_quality": "质量码坏",
    "low_density": "密度不足",
}


def parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="位号级数据质量体检（点表口径，只读）")
    p.add_argument("--start", help="窗口起点（ISO 或 YYYY-MM-DD HH:MM，UTC）")
    p.add_argument("--end", help="窗口终点（同上）")
    p.add_argument(
        "--last-hours", type=float, default=24.0, help="未给 start/end 时的回看小时数（默认 24）"
    )
    p.add_argument("--loop", dest="loop_id", help="仅体检某条回路（loop_id）")
    p.add_argument(
        "--min-density-ratio",
        type=float,
        default=0.5,
        help="事件密度比阈值（相对等长前一窗口，默认 0.5）",
    )
    p.add_argument("--no-held", action="store_true", help="跳过 held_too_long 统计（更快）")
    p.add_argument("--format", choices=("table", "json", "csv"), default="table")
    p.add_argument("--out", help="导出文件路径（json/csv 时生效；缺省打印到 stdout）")
    p.add_argument("--top", type=int, default=50, help="table 模式下最多显示问题行数（默认 50）")
    return p


async def run(args: argparse.Namespace) -> int:
    from app.core.db import AsyncSessionLocal
    from app.services.data_quality_audit import audit_data_quality

    end = parse_dt(args.end) if args.end else datetime.now(UTC).replace(tzinfo=None)
    start = parse_dt(args.start) if args.start else end - timedelta(hours=args.last_hours)

    async with AsyncSessionLocal() as db:
        data = await audit_data_quality(
            db,
            start=start,
            end=end,
            loop_id=args.loop_id,
            min_density_ratio=args.min_density_ratio,
            with_held=not args.no_held,
        )

    if args.format == "json":
        _emit(json.dumps(data, ensure_ascii=False, indent=2), args.out)
        return 0

    if args.format == "csv":
        rows = [["位号", "回路", "角色", "行数", "坏质量", "密度比", "HELD槽", "PV覆盖率", "问题"]]
        for it in data["points"]:
            rows.append(
                [
                    it.get("tagName") or "",
                    it.get("loopId") or "",
                    it.get("role") or "",
                    str(it.get("rows") or 0),
                    str(it.get("badRows") or 0),
                    "" if it.get("densityRatio") is None else str(it["densityRatio"]),
                    str(it.get("heldTooLong") or 0),
                    "" if it.get("pvCoverage") is None else str(it["pvCoverage"]),
                    "/".join(ISSUE_LABEL.get(x, x) for x in it.get("issues") or []),
                ]
            )
        body = "\n".join(",".join('"' + c.replace('"', '""') + '"' for c in r) for r in rows)
        _emit(body, args.out)
        return 0

    s, w = data["summary"], data["window"]
    print("位号级数据质量体检（点表口径，只读）")
    print("  窗口     : " + w["start"] + " ~ " + w["end"])
    print(
        "  基线窗口 : "
        + w["reference"]["start"]
        + " ~ "
        + w["reference"]["end"]
        + "（等长前窗，用于事件密度比）"
    )
    print("  阈值     : 密度比 < " + str(data["minDensityRatio"]) + " 判为不足")
    print(
        f"  位号总数 : {s['points']} | 断流 {s['noData']} | 质量码坏 {s['badQuality']}"
        f" | 密度不足 {s['lowDensity']} | 含 HELD 填平 {s['withHeldTooLong']}"
    )
    shown = 0
    for it in sorted(
        data["points"], key=lambda x: (-len(x.get("issues") or []), str(x.get("tagName")))
    ):
        issues = it.get("issues") or []
        held = int(it.get("heldTooLong") or 0)
        if not issues and not held:
            continue
        if shown >= args.top:
            print("  ...（其余省略：用 --top 调整或 --format json 看全量）")
            break
        label = "/".join(ISSUE_LABEL.get(x, x) for x in issues) or "HELD填平"
        name = it.get("tagName") or it.get("pointId")
        lid = str(it.get("loopId") or "-")[:8]
        role = it.get("role") or "-"
        rows = int(it.get("rows") or 0)
        bad = int(it.get("badRows") or 0)
        print(
            f"  {name:<32} 回路={lid} 角色={role:<8} 行数={rows:>7} 坏={bad:>6}"
            f" 密度比={it.get('densityRatio')} HELD={held} → {label}"
        )
        shown += 1
    if not shown:
        print("  未发现问题：窗口内位号均有数据、质量码正常、密度不低于阈值。")
    if data["loops"]:
        print("  回路级（SeriesContext）：")
        for lp in data["loops"][:10]:
            print(
                f"    {str(lp['loopId'])[:8]} heldTooLong={lp.get('heldTooLong')}"
                f" gap={lp.get('gap')} pvCoverage={lp.get('pvCoverage')}"
            )
    return 0


def _emit(text: str, out: str | None) -> None:
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        print("已导出: " + out + "（" + str(len(text.splitlines())) + " 行）")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
