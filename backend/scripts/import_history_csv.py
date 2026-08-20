"""CSV 历史数据导入 TDengine（按回路配置严格映射）.

背景（2026-08-20）：
    源 CSV 为宽表结构（timestamp + 875 个测点列，125 回路 × 7 测点），
    与 TDengine 超级表 st_loop_data（ts + pv/sp/op/mode/pid_p/pid_i/pid_d/pv_quality）
    结构不同。角色后缀与 CSV 列名不直接对应（如 PID_P 角色对应 `_KP` 列），
    必须以 PostgreSQL 回路配置（loop_ledger + loop_tag_mapping + tag_registry）
    为权威映射，将 CSV 测点列准确归入对应子表的对应信号列。

    子表名 = 回路台账 tag_name 规范化（d_loop_<loop_tag_name>，
    与 data_import/tdengine_provider 修复后的口径一致）。

用法：
    uv run python scripts/import_history_csv.py --csv <路径.csv|.csv.xz> [--dry-run]
        [--start "2026-08-17 00:00:00"] [--end "2026-08-19 23:59:59"]
        [--loops 41LIC30044_PIDA,05IC07135] [--no-drop]

数据契约：
    - 时间戳：CSV 本地时间（北京），写入保持本地时间字符串（与实时链路
      _STORED_TZ=UTC+8 口径一致）；REST 查询返回 UTC 属显示层行为
    - 数值：空串/NaN/Inf/解析失败 → NULL（与 _parse_float_val 口径一致）
    - mode：float 字符串 → int（TINYINT 列）
    - pv_quality：CSV 无质量码列 → 恒写 1（TDengine 语义 1=Good）
    - 冲突：默认先 DROP 子表再写（幂等重跑）；--no-drop 跳过
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import lzma
import math
import re
import sys
import time
from collections.abc import Iterator

# 角色 → 宽表列索引偏移（st_loop_data 列序：ts,pv,sp,op,mode,pid_p,pid_i,pid_d,pv_quality）
ROLES = ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")
BATCH_ROWS = 5000
INSERT_COLS = "ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality"


def norm_subtable(loop_tag_name: str) -> str:
    """回路台账 tag_name → 规范子表名（与 app.core.tdengine.make_subtable_name 一致）."""
    name = re.sub(r"[^a-z0-9_\-]", "_", loop_tag_name.lower())
    name = name.replace("-", "_").replace(".", "_")
    return "d_loop_" + re.sub(r"_+", "_", name)


def norm_ts(raw: str) -> str | None:
    """CSV 时间戳 → TDengine 毫秒格式字符串；解析失败返回 None（跳行）."""
    s = raw.strip()
    if not s:
        return None
    # '2026-08-17 00:00:00.0' / '2026-08-17 00:00:00' / ISO 'T' 分隔
    s = s.replace("T", " ")
    if "." in s:
        head, frac = s.split(".", 1)
        frac = "".join(ch for ch in frac if ch.isdigit())[:3].ljust(3, "0")
    else:
        head, frac = s, "000"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", head):
        return None
    return f"{head}.{frac}"


def parse_fval(raw: str | None) -> float | None:
    """CSV 字符串 → float；空/NaN/Inf/失败 → None（对齐 _parse_float_val）."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_ival(raw: str | None) -> int | None:
    """CSV 字符串 → int（MODE 等 TINYINT 列）；失败 → None."""
    v = parse_fval(raw)
    return int(v) if v is not None else None


async def load_loop_configs(loop_filter: set[str] | None) -> dict[str, dict]:
    """从 PG 加载回路配置：{loop_tag_name: {role→csv列名, loop_id, unit_id, subtable}}."""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.tag import TagRegistry

    async with AsyncSessionLocal() as db:
        loops = (
            await db.execute(
                select(LoopLedger.id, LoopLedger.tag_name, LoopLedger.unit_id).where(
                    LoopLedger.is_active.is_(True)
                )
            )
        ).all()
        if not loops:
            print("PG 无活跃回路，退出")
            return {}
        loop_ids = [str(r[0]) for r in loops]
        rows = (
            await db.execute(
                select(LoopTagMapping.loop_id, LoopTagMapping.tag_role, TagRegistry.tag_name)
                .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
                .where(LoopTagMapping.loop_id.in_(loop_ids))
            )
        ).all()

    lid_meta = {
        str(lid): (tag_name, str(lid), str(uid) if uid else "")
        for lid, tag_name, uid in loops
        if tag_name
    }
    role_map: dict[str, dict[str, str]] = {}
    for lid, role, tag_name in rows:
        if str(lid) in lid_meta and tag_name:
            role_map.setdefault(str(lid), {})[str(role).upper()] = tag_name

    configs: dict[str, dict] = {}
    for lid, (tag_name, _, unit_id) in lid_meta.items():
        if loop_filter and tag_name not in loop_filter:
            continue
        rm = role_map.get(lid, {})
        if "PV" not in rm:
            continue  # 无 PV 映射的回路无法对齐时间基准，跳过
        configs[tag_name] = {
            "roles": rm,
            "loop_id": lid,
            "unit_id": unit_id,
            "subtable": norm_subtable(tag_name),
        }
    return configs


def open_csv(path: str) -> io.TextIOBase:
    """打开 CSV（自动识别 .xz 压缩）."""
    if path.endswith(".xz"):
        return lzma.open(path, "rt", encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def iter_rows(
    path: str, start: str | None, end: str | None
) -> Iterator[tuple[list[str], list[str]]]:
    """流式迭代 (规范化时间戳, 原始行)，按时间过滤."""
    with open_csv(path) as f:
        reader = csv.reader(f)
        next(reader)  # 表头
        for row in reader:
            if not row:
                continue
            ts = norm_ts(row[0]) if row[0] else None
            if ts is None:
                continue
            if start and ts < start:
                continue
            if end and ts > end:
                continue
            yield ts, row


async def main() -> int:
    parser = argparse.ArgumentParser(description="CSV 历史数据导入 TDengine（按回路配置映射）")
    parser.add_argument("--csv", required=True, help="CSV 路径（.csv 或 .csv.xz）")
    parser.add_argument("--dry-run", action="store_true", help="只做列映射预检，不写入")
    parser.add_argument("--start", default=None, help="起始时间（含），如 2026-08-17 00:00:00.000")
    parser.add_argument("--end", default=None, help="结束时间（含），如 2026-08-19 23:59:59.000")
    parser.add_argument("--loops", default=None, help="仅导入指定回路（逗号分隔回路位号）")
    parser.add_argument("--no-drop", action="store_true", help="不先删除既有子表（追加模式）")
    args = parser.parse_args()

    loop_filter = {x.strip() for x in args.loops.split(",")} if args.loops else None
    configs = await load_loop_configs(loop_filter)
    print(f"回路配置加载: {len(configs)} 个（含 PV 映射）")
    if not configs:
        return 1

    # ===== 列映射预检：回路角色 → CSV 列索引（严格匹配测点名称）=====
    with open_csv(args.csv) as f:
        header = next(csv.reader(f))
    col_idx = {name: i for i, name in enumerate(header)}
    # CSV 列名可能带空白
    col_idx = {k.strip(): v for k, v in col_idx.items()}

    missing_report: list[str] = []
    loop_cols: dict[str, dict[str, int | None]] = {}
    for tag_name, cfg in configs.items():
        cols: dict[str, int | None] = {}
        for role in ROLES:
            tag_col = cfg["roles"].get(role)
            if tag_col is None:
                cols[role] = None
                continue
            idx = col_idx.get(tag_col)
            if idx is None:
                # 严格匹配失败：角色有配置但 CSV 无对应测点列 → 该列写 NULL 并报告
                cols[role] = None
                missing_report.append(f"{tag_name}: 角色 {role} 的测点 {tag_col} 在 CSV 中无对应列")
            else:
                cols[role] = idx
        if cols.get("PV") is None:
            missing_report.append(f"{tag_name}: PV 测点列缺失，该回路整体跳过")
            continue
        loop_cols[tag_name] = cols

    print(f"列映射成功: {len(loop_cols)}/{len(configs)} 个回路")
    if missing_report:
        print(f"\n映射告警（{len(missing_report)} 条）：")
        for m in missing_report[:20]:
            print(f"  - {m}")
        if len(missing_report) > 20:
            print(f"  … 其余 {len(missing_report) - 20} 条省略")

    if args.dry_run:
        print("\n[dry-run] 预检完成，未写入任何数据")
        return 0

    # ===== 写入 =====
    from app.core.tdengine import execute_sql
    from app.core.tdengine_native import batch_insert

    # 默认先删子表（幂等重跑）；无 PV 列映射的回路一并清理孤儿子表
    if not args.no_drop:
        existing = {r[0] for r in (await execute_sql("SHOW TABLES"))}
        dropped = 0
        for tag_name, cfg in configs.items():
            if tag_name in loop_cols and cfg["subtable"] in existing:
                await execute_sql(f"DROP TABLE IF EXISTS {cfg['subtable']}")
                dropped += 1
        print(f"已删除 {dropped} 张既有子表（幂等重跑）")

    buffers: dict[str, list[tuple]] = {t: [] for t in loop_cols}
    stats: dict[str, int] = dict.fromkeys(loop_cols, 0)
    pending = 0
    t0 = time.monotonic()
    total_written = 0

    async def flush(force: bool = False) -> int:
        """将各回路缓冲写入 TDengine；force=True 时全量 flush。"""
        nonlocal pending, total_written
        written = 0
        for tag_name, buf in buffers.items():
            if not buf:
                continue
            if force or len(buf) >= BATCH_ROWS:
                cfg = configs[tag_name]
                n = await batch_insert(
                    cfg["subtable"], buf, loop_id=cfg["loop_id"], unit_id=cfg["unit_id"]
                )
                stats[tag_name] += n
                written += n
                buf.clear()
        pending = sum(len(b) for b in buffers.values())
        return written

    rows_read = 0
    for ts, row in iter_rows(args.csv, args.start, args.end):
        rows_read += 1
        for tag_name, cols in loop_cols.items():
            vals = []
            for role in ROLES:
                idx = cols[role]
                if idx is None or idx >= len(row):
                    vals.append(None)
                    continue
                raw = row[idx]
                vals.append(parse_ival(raw) if role == "MODE" else parse_fval(raw))
            # pv_quality：CSV 无质量码列，恒写 1（TDengine 1=Good）
            buffers[tag_name].append((ts, *vals, 1))
        pending += len(loop_cols)
        if pending >= BATCH_ROWS * len(loop_cols):
            total_written += await flush()

    total_written += await flush(force=True)
    elapsed = time.monotonic() - t0

    print(f"\nCSV 读取: {rows_read:,} 行 | 写入: {total_written:,} 行 | 耗时 {elapsed:.1f}s")
    zero = [t for t, n in stats.items() if n == 0]
    if zero:
        print(f"零写入回路（{len(zero)} 个）: {zero[:10]}")
    ok = sum(1 for n in stats.values() if n > 0)
    print(f"回路写入统计: {ok}/{len(loop_cols)} 个成功")
    print(f"INSERT 列序契约: {INSERT_COLS}")
    return 0 if not zero else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
