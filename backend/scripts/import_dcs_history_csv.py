#!/usr/bin/env python3
"""DCS 导出 CSV 历史数据 → TDengine 宽表（st_loop_data 子表）导入脚本。

用法（在 backend/ 目录下执行）::

    uv run python scripts/import_dcs_history_csv.py ../CLPM-engine/回路_20260820.csv
    uv run python scripts/import_dcs_history_csv.py <csv> --dry-run      # 只解析映射不写数
    uv run python scripts/import_dcs_history_csv.py <csv> --limit 100   # 冒烟验证

CSV 格式约定（DCS 导出）：
- 首列 ``timestamp``：时间戳字符串（如 ``2026-08-17 00:00:00.0``），**原样写入**，
  不做任何时区换算——DCS 导出为 +8 墙钟，与库内既有存储口径一致
  （TDengine 服务器按 +8 解释 naive 时间字符串，见 tdengine_provider 文件头注释）
- 其余列为 ``<回路位号>_<角色>``，角色 ∈ PV/OP/OUT/SP/MODE/KP/TI/TD
  （OP 与 OUT 均映射到宽表 op 列；KP→pid_p、TI→pid_i、TD→pid_d；
  pv_quality 恒写 1=Good，CSV 无质量码信息）

子表与 TAGS 口径（与应用读路径严格一致，否则 KPI/诊断读不到数据）：
- 子表名复刻 ``tdengine_provider._resolve_subtable``：取该回路 loop_tag_mapping
  自然序首行 tag → ``SELECT * FROM tag_registry WHERE id IN (...)`` 首行 tag_name
  （无点号 → 整个 tag 名，如 ``41LIC40103_PIDA_SP``）→ ``make_subtable_name``
- TAGS 写真实 loop_id / unit_id（子表首建时生效，TAG 错误无法后补）
- 子表由 ``INSERT ... USING st_loop_data TAGS(...)`` 首笔写入时自动创建

其他行为：
- 台账（loop_ledger）中不存在的回路位号跳过并在报告中列出
- 同 ts 重复写入按 TDengine upsert 语义覆盖（脚本可安全重跑）
- 写入复用 app.core.tdengine_native.batch_insert（含批切分与转义）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 让 app.* 可导入（脚本位于 backend/scripts/，包根在 backend/）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import AsyncSessionLocal  # noqa: E402
from app.core.tdengine import make_subtable_name  # noqa: E402
from app.core.tdengine_native import batch_insert, execute_native  # noqa: E402
from app.models.loop import LoopLedger, LoopTagMapping  # noqa: E402
from app.models.tag import TagRegistry  # noqa: E402

#: CSV 列名角色后缀集合
ROLE_SUFFIXES = {"PV", "OP", "OUT", "SP", "MODE", "KP", "TI", "TD"}

#: 宽表行 tuple 的 7 个数据角色（顺序固定，见 batch_insert 行格式）
DATA_ROLES = ("PV", "SP", "OP", "MODE", "KP", "TI", "TD")


@dataclass
class LoopTarget:
    """一个回路的导入目标（子表 + TAGS + CSV 列映射）。"""

    loop_tag: str  # 台账回路位号
    loop_id: str
    unit_id: str
    subtable: str
    cols: dict[str, str]  # 角色 → CSV 列名（OP/OUT 已归一到 OP）


def parse_header(header: list[str]) -> tuple[str, dict[str, dict[str, str]]]:
    """解析 CSV 表头 → (时间戳列名, {回路位号: {角色: 列名}})。"""
    ts_col = header[0].strip()
    if ts_col.lower() != "timestamp":
        print(f"[警告] 首列表头为「{ts_col}」而非 timestamp，仍按时间戳列处理")

    loop_cols: dict[str, dict[str, str]] = {}
    for name in header[1:]:
        loop_part, _, role = name.rpartition("_")
        if not loop_part or role not in ROLE_SUFFIXES:
            raise SystemExit(f"无法识别的列名「{name}」（期望 <回路位号>_<角色>）")
        # OP/OUT 归一为 OP（同一回路两者互斥，出现即报错）
        norm_role = "OP" if role == "OUT" else role
        cols = loop_cols.setdefault(loop_part, {})
        if norm_role in cols:
            raise SystemExit(f"回路 {loop_part} 的 {norm_role} 角色列重复/OP 与 OUT 并存: {name}")
        cols[norm_role] = name
    return ts_col, loop_cols


async def resolve_targets(
    csv_loops: dict[str, dict[str, str]],
) -> tuple[dict[str, LoopTarget], list[str], list[str]]:
    """解析每个 CSV 回路的子表名与 TAGS（复刻 provider 读路径口径）。

    Returns:
        (targets, 台账中不存在的回路, 无 tag 映射的回路)
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger))
        ledger = {loop.tag_name: loop for loop in result.scalars().all()}

        skipped_no_ledger = sorted(set(csv_loops) - set(ledger))
        skipped_no_mapping: list[str] = []
        targets: dict[str, LoopTarget] = {}

        for tag_name in sorted(set(csv_loops) & set(ledger)):
            loop = ledger[tag_name]
            # —— 与 tdengine_provider._resolve_subtable 完全相同的两步查询 ——
            m_res = await db.execute(
                select(LoopTagMapping).where(LoopTagMapping.loop_id == loop.id)
            )
            mappings = list(m_res.scalars().all())
            if not mappings:
                skipped_no_mapping.append(tag_name)
                continue
            tag_ids = [str(m.tag_id) for m in mappings]
            t_res = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
            tags = list(t_res.scalars().all())
            if not tags:
                skipped_no_mapping.append(tag_name)
                continue

            first_tag = tags[0].tag_name
            loop_part = first_tag.rsplit(".", 1)[0] if "." in first_tag else first_tag
            targets[tag_name] = LoopTarget(
                loop_tag=tag_name,
                loop_id=str(loop.id),
                unit_id=str(loop.unit_id) if loop.unit_id else "",
                subtable=make_subtable_name(loop_part),
                cols=csv_loops[tag_name],
            )
    return targets, skipped_no_ledger, skipped_no_mapping


def _to_mode_int(val: object) -> int | None:
    """MODE 列值 → TINYINT（CSV 为 1.0/0.0 等浮点，空 → None）。"""
    if val is None:
        return None
    try:
        return int(float(val))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def run_import(
    csv_path: Path,
    targets: dict[str, LoopTarget],
    batch_size: int,
    limit: int | None,
) -> dict[str, int]:
    """流式读取 CSV（pandas 分块），按回路批量写入宽表。"""
    settings.TDENGINE_BATCH_SIZE = batch_size  # batch_insert 按该值切批
    written = dict.fromkeys(targets, 0)
    rows_done = 0
    invalid_mode = 0
    t0 = time.monotonic()

    reader = pd.read_csv(csv_path, chunksize=batch_size, dtype={"timestamp": str})
    for chunk in reader:
        if limit is not None and rows_done + len(chunk) > limit:
            chunk = chunk.iloc[: limit - rows_done]
        ts_list = chunk["timestamp"].tolist()
        n_rows = len(chunk)

        for tag, tgt in targets.items():
            # 各角色列 → Python list（NaN → None；缺失角色列 → 全 None）
            col_lists: dict[str, list] = {}
            for role in DATA_ROLES:
                col = tgt.cols.get(role)
                if col is None:
                    col_lists[role] = [None] * n_rows
                else:
                    series = chunk[col]
                    col_lists[role] = series.where(series.notna(), None).tolist()

            rows: list[tuple] = []
            for i in range(n_rows):
                mode = _to_mode_int(col_lists["MODE"][i])
                if col_lists["MODE"][i] is not None and mode is None:
                    invalid_mode += 1
                rows.append(
                    (
                        ts_list[i],
                        col_lists["PV"][i],
                        col_lists["SP"][i],
                        col_lists["OP"][i],
                        mode,
                        col_lists["KP"][i],
                        col_lists["TI"][i],
                        col_lists["TD"][i],
                        1,  # pv_quality = 1（Good，CSV 无质量码）
                    )
                )
            await batch_insert(tgt.subtable, rows, loop_id=tgt.loop_id, unit_id=tgt.unit_id)
            written[tag] += len(rows)

        rows_done += n_rows
        elapsed = time.monotonic() - t0
        rate = rows_done / elapsed if elapsed > 0 else 0.0
        print(f"  进度: {rows_done} 行 | {len(targets)} 回路 × {batch_size}/批 | {rate:.0f} 行/秒")
        if limit is not None and rows_done >= limit:
            break

    if invalid_mode:
        print(f"[警告] {invalid_mode} 个 MODE 值无法解析为整数，已置 NULL")
    return written


async def verify(targets: dict[str, LoopTarget]) -> None:
    """导入后校验：按子表统计行数与首末时间戳。"""
    sql = (
        f"SELECT TBNAME AS tb, COUNT(*) AS cnt, FIRST(ts) AS fst, LAST(ts) AS lst "
        f"FROM {settings.TDENGINE_DB}.st_loop_data GROUP BY TBNAME"
    )
    rows = await execute_native(sql)
    stats = {r["tb"]: r for r in rows}

    print("\n===== 导入校验（TDengine 实测）=====")
    print(f"{'回路位号':<24} {'子表':<40} {'行数':>9} {'首时间戳':<24} {'末时间戳'}")
    total = 0
    for tag, tgt in sorted(targets.items()):
        st = stats.get(tgt.subtable)
        cnt = int(st["cnt"]) if st else 0
        total += cnt
        fst = str(st["fst"])[:19] if st else "-"
        lst = str(st["lst"])[:19] if st else "-"
        print(f"{tag:<24} {tgt.subtable:<40} {cnt:>9} {fst:<24} {lst}")
    print(f"合计 {len(targets)} 回路 / {total} 行")


async def main() -> None:
    parser = argparse.ArgumentParser(description="DCS 导出 CSV 历史数据 → TDengine 宽表导入")
    parser.add_argument("csv", type=Path, help="CSV 文件路径")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="每批写入行数（默认 5000，SQL 约 300KB < REST 1MB 上限）",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅导入前 N 行（冒烟验证用）")
    parser.add_argument("--dry-run", action="store_true", help="只解析映射并打印计划，不写数据")
    args = parser.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"CSV 文件不存在: {args.csv}")

    # 1. 解析表头
    header = pd.read_csv(args.csv, nrows=0).columns.tolist()
    ts_col, loop_cols = parse_header([str(c) for c in header])
    print(f"CSV: {args.csv.name} | 时间戳列「{ts_col}」| {len(loop_cols)} 个回路")

    # 2. 解析回路映射（子表 + TAGS）
    targets, no_ledger, no_mapping = await resolve_targets(loop_cols)
    print(f"匹配台账回路: {len(targets)} 个")

    # 3. 报告异常项
    if no_ledger:
        print(f"[跳过] 台账中不存在（{len(no_ledger)} 个）: {', '.join(no_ledger)}")
    if no_mapping:
        print(f"[跳过] 无 tag 映射（{len(no_mapping)} 个）: {', '.join(no_mapping)}")
    missing_roles = {
        tag: sorted(set(DATA_ROLES) - set(t.cols)) for tag, t in targets.items() if len(t.cols) < 7
    }
    if missing_roles:
        for tag, roles in missing_roles.items():
            print(f"[提示] 回路 {tag} 缺少角色列 {roles}（对应列将写 NULL）")

    if not targets:
        raise SystemExit("没有可导入的回路，退出")

    # 4. 打印映射计划（dry-run 终点）
    print("\n===== 子表映射计划（与应用读路径 tdengine_provider 口径一致）=====")
    for tag, tgt in sorted(targets.items()):
        print(
            f"{tag:<24} → {tgt.subtable:<40} "
            f"TAGS(loop_id={tgt.loop_id[:8]}…, unit_id={tgt.unit_id[:8] or '空'}…)"
        )

    if args.dry_run:
        print("\n[dry-run] 未写入任何数据")
        return

    # 5. 写入
    print(
        f"\n开始导入（batch={args.batch_size}"
        f"{' , limit=' + str(args.limit) if args.limit else ''}）…"
    )
    t0 = time.monotonic()
    written = await run_import(args.csv, targets, args.batch_size, args.limit)
    elapsed = time.monotonic() - t0
    print(f"写入完成: {sum(written.values())} 行 / {len(targets)} 回路，耗时 {elapsed:.0f} 秒")

    # 6. 校验
    await verify(targets)


if __name__ == "__main__":
    asyncio.run(main())
