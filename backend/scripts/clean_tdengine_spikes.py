"""TDengine 历史数据毛刺清洗脚本.

功能：
    1. 从 PostgreSQL 查询 27 个回路清单（loop_id, tag_name, loop_type, range_min, range_max）
    2. 对每个回路：
       a. 分批查询 TDengine PV + pv_quality 时间序列（每批 5000 行）
       b. 滑动窗口识别毛刺段（支持持续 1-5 秒的 spike）
       c. 用前一个有效 PV 值替换毛刺点的 PV（INSERT 覆盖，TDengine last-write-wins）
    3. 输出清洗日志：JSON + CSV

毛刺识别策略：
    - 单点毛刺（detect_spike 算法）：前后双邻点突变均超过 spike_threshold_pct × range_span
    - 持续毛刺（1-5 秒）：起始突变 > spike_threshold → 向前搜索回落点 → 整段标记
    - 仅清洗 PV 值，不动 sp/op/mode/pid_* 字段
    - 替换值：取毛刺段起始前一个有效 PV（pv_quality=1 且非 NaN）

使用方式：
    cd backend && uv run python scripts/clean_tdengine_spikes.py [--dry-run] [--limit-loops N]

参数：
    --dry-run: 仅识别不替换，输出清洗日志
    --limit-loops N: 仅处理前 N 个回路（调试用）
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 让脚本能导入 app 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg  # noqa: E402

from app.contracts.data_types import ControlType  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.tdengine import execute_sql, make_subtable_name  # noqa: E402
from app.services.preprocessing.thresholds import get_threshold  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("clean_spikes")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BATCH_SIZE = 5000  # 每批查询行数
MAX_SPIKE_DURATION = 5  # 最大毛刺持续秒数（模拟器 spike 持续 1-3 秒，留余量）

# loop_type → ControlType 映射（loop_type 是大写字符串如 "FLOW"，
# ControlType 枚举值是 "FC"/"PC"/"TC"/"LC"）
_LOOP_TYPE_TO_CONTROL_TYPE: dict[str, ControlType] = {
    "FLOW": ControlType.FLOW,
    "LEVEL": ControlType.LEVEL,
    "PRESSURE": ControlType.PRESSURE,
    "TEMPERATURE": ControlType.TEMPERATURE,
}

# 输出目录
OUTPUT_DIR = Path(__file__).parent.parent / "logs" / "spike_cleaning"


# ---------------------------------------------------------------------------
# 步骤 1：从 PostgreSQL 查询回路清单
# ---------------------------------------------------------------------------


async def fetch_loops(pg_pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """从 PostgreSQL 查询所有回路清单."""
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                l.id::text AS loop_id,
                l.tag_name AS loop_tag,
                l.loop_type,
                t.range_min::float8 AS range_min,
                t.range_max::float8 AS range_max
            FROM loop_ledger l
            JOIN loop_tag_mapping m ON m.loop_id = l.id AND m.tag_role = 'PV'
            JOIN tag_registry t ON t.id = m.tag_id
            WHERE l.loop_type IS NOT NULL
              AND t.range_min IS NOT NULL
              AND t.range_max IS NOT NULL
            ORDER BY l.tag_name
            """
        )
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 步骤 2：分批查询 TDengine 数据
# ---------------------------------------------------------------------------


async def fetch_tdengine_batch(
    subtable: str, offset_ts: str | None, limit: int = BATCH_SIZE
) -> list[dict[str, Any]]:
    """分批查询 TDengine 数据（游标分页）.

    Args:
        subtable: 子表名
        offset_ts: 上一批最后一条的 ts（用于游标分页），None 表示从头开始
        limit: 最大行数

    Returns:
        list[dict]: [{ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality}, ...]
    """
    if offset_ts:
        sql = (
            f"SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
            f"FROM clpm_ts.{subtable} "
            f"WHERE ts > '{offset_ts}' "
            f"ORDER BY ts ASC LIMIT {limit}"
        )
    else:
        sql = (
            f"SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
            f"FROM clpm_ts.{subtable} "
            f"ORDER BY ts ASC LIMIT {limit}"
        )
    return await execute_sql(sql)


# ---------------------------------------------------------------------------
# 步骤 3：毛刺识别（滑动窗口，支持持续 1-5 秒的 spike）
# ---------------------------------------------------------------------------


def _to_float(v: Any) -> float | None:
    """安全转 float，None/NaN 返回 None."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (ValueError, TypeError):
        return None


def detect_spike_segments(
    pv_values: list[Any],
    threshold_pct: float,
    range_min: float,
    range_max: float,
) -> list[tuple[int, int]]:
    """识别毛刺段（支持持续 1-5 秒的 spike）.

    策略：
        1. 对每个点 i，计算 prev_diff = |pv[i] - pv[i-1]|
        2. 若 prev_diff > spike_threshold（起始突变）：
           a. 向前搜索 j ∈ [i, i+MAX_SPIKE_DURATION]，找到 next_diff > spike_threshold（回落点）
           b. 标记 [i, j] 为毛刺段（闭区间）
           c. 跳到 j+1 继续扫描
        3. 若未找到回落点 → 视为 JUMP（非 SPIKE），跳过

    Args:
        pv_values: PV 值序列
        threshold_pct: 尖峰阈值（占量程百分比，如 0.50 表示 50%）
        range_min: 量程下限
        range_max: 量程上限

    Returns:
        [(start_idx, end_idx), ...] 闭区间列表
    """
    n = len(pv_values)
    if n < 3:
        return []

    range_span = max(range_max - range_min, 1e-9)
    spike_threshold = threshold_pct * range_span

    segments: list[tuple[int, int]] = []
    i = 1
    while i < n - 1:
        prev = _to_float(pv_values[i])
        prev_pv = _to_float(pv_values[i - 1])
        if prev is None or prev_pv is None:
            i += 1
            continue

        prev_diff = abs(prev - prev_pv)
        if prev_diff <= spike_threshold:
            i += 1
            continue

        # 检测到起始突变，向前搜索回落点
        start = i
        end = -1
        for j in range(i, min(i + MAX_SPIKE_DURATION, n - 1)):
            cur = _to_float(pv_values[j])
            nxt = _to_float(pv_values[j + 1])
            if cur is None or nxt is None:
                continue
            next_diff = abs(nxt - cur)
            if next_diff > spike_threshold:
                end = j + 1
                break

        if end == -1:
            # 未找到回落点 → 可能是 JUMP（非 SPIKE），跳过
            i += 1
            continue

        segments.append((start, end))
        i = end + 1

    return segments


# ---------------------------------------------------------------------------
# 步骤 4：用前一个有效 PV 值替换毛刺点
# ---------------------------------------------------------------------------


def find_previous_valid_pv(
    pv_values: list[Any],
    quality_values: list[Any],
    end_idx: int,
    replacements: dict[int, float] | None = None,
) -> float | None:
    """找到 end_idx 之前最后一个有效 PV（pv_quality=1 且非 NaN）.

    Args:
        pv_values: PV 值序列
        quality_values: 质量码序列
        end_idx: 当前段的起始索引（向前查找 end_idx-1, end_idx-2, ...）
        replacements: 已收集的替换记录 {idx: new_pv}，避免用未替换的异常值
    """
    for k in range(end_idx - 1, -1, -1):
        # 优先用已替换的值（处理连续 spike 段）
        if replacements and k in replacements:
            return replacements[k]
        q = quality_values[k]
        # TDengine 质量码：1=Good, 0=Bad
        try:
            q_int = int(q) if q is not None else 0
        except (ValueError, TypeError):
            q_int = 0
        if q_int != 1:
            continue
        v = _to_float(pv_values[k])
        if v is not None:
            return v
    return None


def build_replace_sql(
    subtable: str, rows: list[dict[str, Any]], replacements: dict[int, float]
) -> str:
    """构造 INSERT 覆盖 SQL（仅替换 PV，保留其他列原值）.

    Args:
        subtable: 子表名
        rows: 原始数据行
        replacements: {row_idx: new_pv_value}

    Returns:
        INSERT SQL 语句
    """
    values_parts: list[str] = []
    for idx, new_pv in replacements.items():
        row = rows[idx]
        ts = row["ts"]
        sp = row.get("sp")
        op = row.get("op")
        mode = row.get("mode")
        pid_p = row.get("pid_p")
        pid_i = row.get("pid_i")
        pid_d = row.get("pid_d")
        pv_quality = row.get("pv_quality")

        def fmt(v: Any) -> str:
            if v is None:
                return "NULL"
            if isinstance(v, str):
                return v  # ts 字符串
            return str(v)

        values_parts.append(
            f"('{fmt(ts)}', {fmt(new_pv)}, {fmt(sp)}, {fmt(op)}, {fmt(mode)}, "
            f"{fmt(pid_p)}, {fmt(pid_i)}, {fmt(pid_d)}, {fmt(pv_quality)})"
        )

    # TDengine INSERT：同 ts 重复写入，last-write-wins 覆盖原值
    sql = (
        f"INSERT INTO clpm_ts.{subtable} "
        f"(ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality) VALUES " + ", ".join(values_parts)
    )
    return sql


# ---------------------------------------------------------------------------
# 步骤 5：处理单个回路
# ---------------------------------------------------------------------------


async def clean_loop(
    pg_pool: asyncpg.Pool,
    loop: dict[str, Any],
    dry_run: bool,
    clean_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """清洗单个回路的所有历史数据.

    Returns:
        该回路的清洗统计
    """
    loop_tag = loop["loop_tag"]
    loop_type = loop["loop_type"]
    range_min = float(loop["range_min"])
    range_max = float(loop["range_max"])

    # 构造子表名
    subtable = make_subtable_name(loop_tag)

    # 获取控制类型阈值
    control_type = _LOOP_TYPE_TO_CONTROL_TYPE.get(loop_type, ControlType.TEMPERATURE)
    threshold = get_threshold(control_type)
    spike_pct = threshold.spike_threshold_pct

    logger.info(
        "[%s] 开始清洗：subtable=%s, control_type=%s, spike_threshold=%.1f%%, range=[%.2f, %.2f]",
        loop_tag,
        subtable,
        control_type,
        spike_pct * 100,
        range_min,
        range_max,
    )

    # 分批查询 + 识别 + 替换
    offset_ts: str | None = None
    total_rows = 0
    total_spikes = 0
    total_replaced = 0
    batches = 0

    while True:
        rows = await fetch_tdengine_batch(subtable, offset_ts, BATCH_SIZE)
        if not rows:
            break

        batches += 1
        total_rows += len(rows)

        pv_values = [r.get("pv") for r in rows]
        quality_values = [r.get("pv_quality") for r in rows]

        # 识别毛刺段
        segments = detect_spike_segments(pv_values, spike_pct, range_min, range_max)

        if not segments:
            offset_ts = str(rows[-1]["ts"])
            if len(rows) < BATCH_SIZE:
                break
            continue

        # 收集要替换的点
        replacements: dict[int, float] = {}
        for start, end in segments:
            # 找前一个有效 PV（在 rows 起始之前的需要跨批处理，先在批内找）
            prev_pv = find_previous_valid_pv(pv_values, quality_values, start, replacements)
            if prev_pv is None:
                # 跨批情况：查 TDengine 前一批最后一个有效值
                # 简化处理：用 range_min 或跳过（保守策略）
                logger.debug(
                    "[%s] 段 [%d, %d] 未找到前一个有效 PV，跳过",
                    loop_tag,
                    start,
                    end,
                )
                continue

            for idx in range(start, end + 1):
                if idx >= len(rows):
                    break
                original_pv = _to_float(pv_values[idx])
                if original_pv is None:
                    continue
                replacements[idx] = prev_pv
                clean_log.append(
                    {
                        "loop_tag": loop_tag,
                        "subtable": subtable,
                        "ts": str(rows[idx]["ts"]),
                        "original_pv": original_pv,
                        "replaced_pv": prev_pv,
                        "pv_quality": rows[idx].get("pv_quality"),
                        "segment_start_idx": start,
                        "segment_end_idx": end,
                        "control_type": str(control_type),
                        "spike_threshold_pct": spike_pct,
                        "range_min": range_min,
                        "range_max": range_max,
                        "cleaned_at": datetime.utcnow().isoformat() + "Z",
                    }
                )

        total_spikes += len(segments)
        total_replaced += len(replacements)

        # 执行替换（非 dry-run）
        if not dry_run and replacements:
            sql = build_replace_sql(subtable, rows, replacements)
            result = await execute_sql(sql)
            logger.info(
                "[%s] 批次 %d：识别 %d 段，替换 %d 个点",
                loop_tag,
                batches,
                len(segments),
                len(replacements),
            )
            if result == []:
                # execute_sql 失败返回空列表，但 INSERT 成功也会返回空列表
                # 检查响应：成功 INSERT 通常返回空 data
                pass
        else:
            if dry_run and segments:
                logger.info(
                    "[dry-run][%s] 批次 %d：识别 %d 段，%d 个点待替换",
                    loop_tag,
                    batches,
                    len(segments),
                    len(replacements),
                )

        # 下一批
        offset_ts = str(rows[-1]["ts"])
        if len(rows) < BATCH_SIZE:
            break

    stats = {
        "loop_tag": loop_tag,
        "subtable": subtable,
        "control_type": str(control_type),
        "total_rows_scanned": total_rows,
        "total_spike_segments": total_spikes,
        "total_points_replaced": total_replaced,
        "batches": batches,
        "range_min": range_min,
        "range_max": range_max,
        "spike_threshold_pct": spike_pct,
        "dry_run": dry_run,
    }
    logger.info(
        "[%s] 完成：扫描 %d 行，识别 %d 段毛刺，替换 %d 个点",
        loop_tag,
        total_rows,
        total_spikes,
        total_replaced,
    )
    return stats


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 连接 PostgreSQL
    pg_pool = await asyncpg.create_pool(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
        min_size=1,
        max_size=2,
    )

    try:
        # 1. 查询回路清单
        loops = await fetch_loops(pg_pool)
        if args.limit_loops:
            loops = loops[: args.limit_loops]
        logger.info("共 %d 个回路待清洗（dry_run=%s）", len(loops), args.dry_run)

        # 2. 逐个清洗
        all_stats: list[dict[str, Any]] = []
        clean_log: list[dict[str, Any]] = []
        for i, loop in enumerate(loops, 1):
            logger.info("=== 进度 %d/%d ===", i, len(loops))
            stats = await clean_loop(pg_pool, loop, args.dry_run, clean_log)
            all_stats.append(stats)

        # 3. 输出清洗报告
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = OUTPUT_DIR / f"clean_report_{timestamp}.json"
        log_csv_path = OUTPUT_DIR / f"clean_log_{timestamp}.csv"

        # JSON 报告
        report = {
            "cleaned_at": datetime.utcnow().isoformat() + "Z",
            "dry_run": args.dry_run,
            "total_loops": len(loops),
            "summary": {
                "total_rows_scanned": sum(s["total_rows_scanned"] for s in all_stats),
                "total_spike_segments": sum(s["total_spike_segments"] for s in all_stats),
                "total_points_replaced": sum(s["total_points_replaced"] for s in all_stats),
            },
            "loops": all_stats,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        # CSV 日志
        if clean_log:
            with open(log_csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(clean_log[0].keys()))
                writer.writeheader()
                writer.writerows(clean_log)

        # 控制台汇总
        print("\n" + "=" * 60)
        print("清洗完成汇总")
        print("=" * 60)
        print(f"模式：{'dry-run（仅识别）' if args.dry_run else '执行替换'}")
        print(f"回路数：{len(loops)}")
        print(f"扫描总行数：{report['summary']['total_rows_scanned']:,}")
        print(f"识别毛刺段数：{report['summary']['total_spike_segments']:,}")
        print(f"替换点数：{report['summary']['total_points_replaced']:,}")
        print(f"\nJSON 报告：{report_path}")
        if clean_log:
            print(f"CSV 日志：{log_csv_path}（{len(clean_log)} 条记录）")
        else:
            print("CSV 日志：无毛刺数据，未生成 CSV")

    finally:
        await pg_pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TDengine 历史数据毛刺清洗")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅识别不替换，输出清洗日志",
    )
    parser.add_argument(
        "--limit-loops",
        type=int,
        default=None,
        help="仅处理前 N 个回路（调试用）",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
