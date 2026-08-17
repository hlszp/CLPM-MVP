"""TDengine 历史数据 MODE 字段批量修正脚本.

功能：
    将宽表中所有 mode IS NULL OR mode = 0 的历史记录统一修正为 mode=1（自动）。
    MODE=2(串级)/3(远程)/4(先控) 为有效自动控制模式，不予修改。
    基于 TDengine last-write-wins 语义，通过 INSERT 同一 ts 的新行覆盖原值。

策略：
    1. 查询所有子表（d_loop_*）
    2. 按时间顺序分批拉取 mode IS NULL OR mode = 0 的行（每批 5000 行）
    3. 将 mode 改写为 1，其余列保持原值，INSERT 回同一子表
    4. 输出修正日志（每子表修正前后计数 + 总汇总）

使用方式：
    cd backend && uv run python scripts/fix_tdengine_mode.py [--dry-run] [--limit-tables N]

参数：
    --dry-run:      仅统计不修改，输出将被修正的行数
    --limit-tables N: 仅处理前 N 个子表（调试用）
    --batch-size N: 每批处理行数（默认 5000）

⚠️ 注意事项：
    - 本脚本直接覆盖 TDengine 历史数据，执行前建议备份
    - dry-run 模式会完整扫描但不写入，请先跑一次 dry-run 确认范围
    - TDengine INSERT 同 ts 覆盖是幂等操作，重复执行不会产生新行
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 让脚本能导入 app 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.tdengine import execute_sql  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("fix_mode")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE = 5000
OUTPUT_DIR = Path(__file__).parent.parent / "logs" / "mode_fix"
TARGET_MODE = 1  # 目标 MODE 值（自动）


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def fmt_val(v: Any) -> str:
    """将 Python 值转为 TDengine SQL 字面量."""
    if v is None:
        return "NULL"
    if isinstance(v, str):
        # ts 字符串已经带 Z/T 格式，加单引号；注意转义单引号
        return "'" + v.replace("'", "\\'") + "'"
    # 数值类型（int/float）
    return str(v)


def build_overwrite_sql(subtable: str, rows: list[dict[str, Any]]) -> str:
    """构造 INSERT 覆盖 SQL（仅修改 mode 列，其余列原样回写）.

    TDengine 对同一 ts 重复写入采用 last-write-wins 覆盖语义，
    因此我们只需重新 INSERT 这些行，将 mode 改为 TARGET_MODE 即可。
    """
    values_parts: list[str] = []
    for r in rows:
        values_parts.append(
            "("
            f"{fmt_val(r.get('ts'))}, "
            f"{fmt_val(r.get('pv'))}, "
            f"{fmt_val(r.get('sp'))}, "
            f"{fmt_val(r.get('op'))}, "
            f"{TARGET_MODE}, "  # 强制写入目标 MODE
            f"{fmt_val(r.get('pid_p'))}, "
            f"{fmt_val(r.get('pid_i'))}, "
            f"{fmt_val(r.get('pid_d'))}, "
            f"{fmt_val(r.get('pv_quality'))}"
            ")"
        )
    return (
        f"INSERT INTO {settings.TDENGINE_DB}.{subtable} "
        f"(ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality) VALUES " + ", ".join(values_parts)
    )


# ---------------------------------------------------------------------------
# 步骤 1：列出所有子表
# ---------------------------------------------------------------------------


async def list_subtables() -> list[str]:
    """查询 clpm_ts 库下所有 d_loop_ 开头的子表名."""
    sql = (
        "SELECT table_name FROM information_schema.ins_tables "
        f"WHERE db_name = '{settings.TDENGINE_DB}' "
        "AND table_name LIKE 'd_loop_%' "
        "ORDER BY table_name"
    )
    rows = await execute_sql(sql)
    return [str(r["table_name"]) for r in rows]


# ---------------------------------------------------------------------------
# 步骤 2：统计单个子表需修正的行数
# ---------------------------------------------------------------------------


async def count_bad_mode(subtable: str) -> int:
    """统计子表中 mode IS NULL OR mode = 0 的行数（仅修复已知错误值）.

    注意：MODE=2(串级)/3(远程)/4(先控) 是有效的自动控制模式，不应被改写。
    """
    sql = (
        f"SELECT COUNT(*) AS cnt FROM {settings.TDENGINE_DB}.{subtable} "
        "WHERE mode IS NULL OR mode = 0"
    )
    rows = await execute_sql(sql)
    return int(rows[0]["cnt"]) if rows else 0


async def count_total_rows(subtable: str) -> int:
    """统计子表总行数."""
    sql = f"SELECT COUNT(*) AS cnt FROM {settings.TDENGINE_DB}.{subtable}"
    rows = await execute_sql(sql)
    return int(rows[0]["cnt"]) if rows else 0


# ---------------------------------------------------------------------------
# 步骤 3：分批查询需修正的行
# ---------------------------------------------------------------------------


async def fetch_bad_mode_batch(
    subtable: str,
    offset_ts: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """游标分页查询 mode IS NULL OR mode = 0 的行（按 ts 升序）.

    Args:
        subtable: 子表名
        offset_ts: 上一批最后一条的 ts（None 表示从头开始）
        limit: 每批最大行数

    Returns:
        行列表，列为 ts/pv/sp/op/mode/pid_p/pid_i/pid_d/pv_quality
    """
    where = "WHERE mode IS NULL OR mode = 0"
    if offset_ts:
        sql = (
            "SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
            f"FROM {settings.TDENGINE_DB}.{subtable} "
            f"{where} "
            f"AND ts > '{offset_ts}' "
            "ORDER BY ts ASC "
            f"LIMIT {limit}"
        )
    else:
        sql = (
            "SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
            f"FROM {settings.TDENGINE_DB}.{subtable} "
            f"{where} "
            "ORDER BY ts ASC "
            f"LIMIT {limit}"
        )
    return await execute_sql(sql)


# ---------------------------------------------------------------------------
# 步骤 4：处理单个子表
# ---------------------------------------------------------------------------


async def fix_subtable(
    subtable: str,
    dry_run: bool,
    batch_size: int,
) -> dict[str, Any]:
    """修正单个子表中所有 NULL 或 mode=0 的值为 TARGET_MODE.

    Returns:
        该子表的修正统计
    """
    total_rows = await count_total_rows(subtable)
    bad_count = await count_bad_mode(subtable)

    logger.info(
        "[%s] 总 %d 行，需修正 %d 行（%.2f%%）",
        subtable,
        total_rows,
        bad_count,
        (bad_count / total_rows * 100) if total_rows else 0,
    )

    if bad_count == 0:
        return {
            "subtable": subtable,
            "total_rows": total_rows,
            "bad_rows_before": 0,
            "fixed_rows": 0,
            "batches": 0,
            "dry_run": dry_run,
        }

    if dry_run:
        return {
            "subtable": subtable,
            "total_rows": total_rows,
            "bad_rows_before": bad_count,
            "fixed_rows": 0,
            "batches": 0,
            "dry_run": True,
        }

    # ---- 实际修正：分批查询 + INSERT 覆盖 ----
    offset_ts: str | None = None
    fixed_total = 0
    batches = 0

    while True:
        rows = await fetch_bad_mode_batch(subtable, offset_ts, batch_size)
        if not rows:
            break

        batches += 1
        sql = build_overwrite_sql(subtable, rows)
        await execute_sql(sql)
        fixed_total += len(rows)

        if batches % 20 == 0 or len(rows) < batch_size:
            logger.info(
                "[%s] 批次 %d：已修正 %d / %d 行",
                subtable,
                batches,
                fixed_total,
                bad_count,
            )

        # 游标推进
        offset_ts = str(rows[-1]["ts"])
        if len(rows) < batch_size:
            break

    # ---- 验证：修正后再统计一次 ----
    remaining_sql = (
        f"SELECT COUNT(*) AS cnt FROM {settings.TDENGINE_DB}.{subtable} "
        "WHERE mode IS NULL OR mode = 0"
    )
    remaining_rows = await execute_sql(remaining_sql)
    remaining = int(remaining_rows[0]["cnt"]) if remaining_rows else 0
    logger.info(
        "[%s] 修正完成：处理 %d 行，修正后剩余NULL/0 mode %d 行",
        subtable,
        fixed_total,
        remaining,
    )

    return {
        "subtable": subtable,
        "total_rows": total_rows,
        "bad_rows_before": bad_count,
        "fixed_rows": fixed_total,
        "remaining_bad_after": remaining,
        "batches": batches,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("TDengine MODE 批量修正工具")
    logger.info("=" * 60)
    logger.info(
        "目标：将所有 mode IS NULL 或 mode=0 的记录修正为 %d（保留2/3/4）",
        TARGET_MODE,
    )
    logger.info("模式：%s", "DRY-RUN（仅统计）" if args.dry_run else "执行替换")
    logger.info("TDengine DB: %s", settings.TDENGINE_DB)

    # 1. 列出所有子表
    subtables = await list_subtables()
    logger.info("发现 %d 个 d_loop_* 子表", len(subtables))
    if args.limit_tables:
        subtables = subtables[: args.limit_tables]
        logger.info("按 --limit-tables=%d 截断，实际处理 %d 个", args.limit_tables, len(subtables))

    # 2. 逐个修正
    all_stats: list[dict[str, Any]] = []
    total_bad_before = 0
    total_fixed = 0
    total_rows_all = 0

    for i, subtable in enumerate(subtables, 1):
        logger.info("--- [%d/%d] %s ---", i, len(subtables), subtable)
        stats = await fix_subtable(subtable, args.dry_run, args.batch_size)
        all_stats.append(stats)
        total_bad_before += stats["bad_rows_before"]
        total_fixed += stats.get("fixed_rows", 0)
        total_rows_all += stats["total_rows"]

    # 3. 输出报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUTPUT_DIR / f"mode_fix_report_{timestamp}.json"
    report = {
        "fixed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "target_mode": TARGET_MODE,
        "dry_run": args.dry_run,
        "total_subtables": len(subtables),
        "summary": {
            "total_rows": total_rows_all,
            "total_bad_before": total_bad_before,
            "total_fixed": total_fixed,
            "bad_pct_before": (
                round(total_bad_before / total_rows_all * 100, 2) if total_rows_all else 0
            ),
        },
        "tables": all_stats,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. 控制台汇总
    print("\n" + "=" * 60)
    print("MODE 批量修正完成")
    print("=" * 60)
    print(f"模式：{'DRY-RUN（仅统计，未修改数据）' if args.dry_run else '执行替换'}")
    print(f"子表数：{len(subtables)}")
    print(f"历史总行数：{total_rows_all:,}")
    print(
        f"修正前NULL/0 MODE行数：{total_bad_before:,}"
        f"（占比 {report['summary']['bad_pct_before']:.2f}%）"
    )
    if not args.dry_run:
        print(f"实际修正行数：{total_fixed:,}")
        remaining_all = sum(s.get("remaining_bad_after", 0) for s in all_stats)
        print(f"修正后剩余NULL/0 MODE行数：{remaining_all:,}")
    print(f"\nJSON 报告：{report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TDengine MODE 字段批量修正（NULL/0→1，保留2/3/4）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计不修改，输出待修正行数",
    )
    parser.add_argument(
        "--limit-tables",
        type=int,
        default=None,
        help="仅处理前 N 个子表（调试用）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批处理行数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
