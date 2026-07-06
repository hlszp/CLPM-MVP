#!/usr/bin/env python3
"""Range-based PV cleaning: replace all out-of-range PV values.

The original spike cleaning (clean_tdengine_spikes.py) only caught single-point
spikes where both front and back differences exceed the threshold. However, the
simulator's spike events caused PV to jump to extreme values and then decay
exponentially over 10-15 seconds. The decay tail was NOT detected as a spike
because the back difference was below the threshold.

This script performs a simpler but more thorough cleaning:
    For each loop, any PV value outside [range_min, range_max] is replaced
    with the previous in-range PV value.

Usage:
    cd backend && uv run python scripts/clean_out_of_range_pv.py
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.tdengine import make_subtable_name

REST_PORT = settings.TDENGINE_PORT + 11
REST_BASE = f"http://{settings.TDENGINE_HOST}:{REST_PORT}/rest/sql"
REST_DB_URL = f"{REST_BASE}/{settings.TDENGINE_DB}"
AUTH = (settings.TDENGINE_USER, settings.TDENGINE_PASSWORD)

BATCH_QUERY = 10000  # rows per query
INSERT_BATCH = 500  # rows per INSERT batch

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "logs" / "spike_cleaning"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


async def load_loops() -> list[dict]:
    async with AsyncSessionLocal() as s:
        r = await s.execute(
            text("""
            SELECT l.tag_name, t.range_min, t.range_max
            FROM loop_ledger l
            JOIN loop_tag_mapping m ON l.id = m.loop_id AND m.tag_role = 'PV'
            JOIN tag_registry t ON t.id = m.tag_id
            WHERE l.is_active = TRUE
            ORDER BY l.tag_name
            """)
        )
        return [
            {"tag_name": row[0], "range_min": float(row[1]), "range_max": float(row[2])}
            for row in r.fetchall()
        ]


def query_out_of_range(sub: str, rmin: float, rmax: float) -> list[tuple]:
    """Query all out-of-range PV rows, ordered by ts ASC."""
    sql = (
        f"SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
        f"FROM {sub} WHERE pv > {rmax} OR pv < {rmin} "
        f"ORDER BY ts ASC"
    )
    resp = httpx.post(REST_DB_URL, data=sql, auth=AUTH, timeout=120.0)
    data = resp.json()
    if "data" not in data:
        raise RuntimeError(f"Query failed for {sub}: {data}")
    return data["data"]


def query_previous_valid_pv(sub: str, ts: str, rmin: float, rmax: float) -> float:
    """Get the last in-range PV value before the given timestamp."""
    sql = (
        f"SELECT pv FROM {sub} "
        f"WHERE ts < '{ts}' AND pv >= {rmin} AND pv <= {rmax} "
        f"ORDER BY ts DESC LIMIT 1"
    )
    resp = httpx.post(REST_DB_URL, data=sql, auth=AUTH, timeout=30.0)
    data = resp.json()
    if "data" in data and data["data"]:
        return float(data["data"][0][0])
    # Fallback: use range midpoint
    return (rmin + rmax) / 2.0


def insert_corrected_rows(sub: str, rows: list[tuple]) -> int:
    """Insert corrected rows (INSERT overwrites in TDengine 3.x by default).

    NOTE: Do NOT use ``USING st_loop_data TAGS('','')`` — when the subtable
    already exists, mismatched TAGS cause TDengine to silently return
    affected_rows=0 (but HTTP 200, no error in body), so the script would
    mistakenly count those rows as inserted. Use plain INSERT into the
    existing subtable instead.

    NOTE: TDengine REST API returns timestamps in UTC ISO format
    (e.g. ``2026-06-30T09:20:36.673Z``). The ts string MUST be passed
    through unchanged — stripping ``T``/``Z`` would treat it as local
    time and INSERT into a non-existent timestamp, leaving the original
    out-of-range value untouched.
    """
    if not rows:
        return 0
    inserted = 0
    for i in range(0, len(rows), INSERT_BATCH):
        batch = rows[i : i + INSERT_BATCH]
        values_sql = []
        for ts, pv, sp, op, mode, pid_p, pid_i, pid_d, q in batch:
            # ts is already in UTC ISO format from REST API; pass as-is
            values_sql.append(f"('{ts}', {pv}, {sp}, {op}, {mode}, {pid_p}, {pid_i}, {pid_d}, {q})")
        sql = (
            f"INSERT INTO {sub} "
            f"(ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality) VALUES "
            + ", ".join(values_sql)
        )
        resp = httpx.post(REST_DB_URL, data=sql, auth=AUTH, timeout=60.0)
        affected = _parse_affected_rows(resp)
        if affected < 0:
            print(f"    INSERT batch {i // INSERT_BATCH} failed: {resp.text[:200]}")
            continue
        if affected != len(batch):
            print(
                f"    INSERT batch {i // INSERT_BATCH} partial: "
                f"expected {len(batch)}, got {affected}"
            )
        inserted += affected
    return inserted


def _parse_affected_rows(resp: httpx.Response) -> int:
    """Return affected_rows from TDengine REST response, or -1 on error."""
    if resp.status_code != 200:
        return -1
    try:
        data = resp.json()
    except Exception:
        return -1
    if data.get("code", 0) != 0:
        return -1
    if "data" in data and data["data"]:
        try:
            return int(data["data"][0][0])
        except (IndexError, ValueError, TypeError):
            return -1
    return -1


async def clean_loop(loop: dict, log_writer: csv.writer, report: dict) -> None:
    tag = loop["tag_name"]
    sub = make_subtable_name(tag)
    rmin = loop["range_min"]
    rmax = loop["range_max"]
    print(f"  {tag} (range=[{rmin},{rmax}]): querying out-of-range rows...")

    oor_rows = query_out_of_range(sub, rmin, rmax)
    total_oor = len(oor_rows)
    if total_oor == 0:
        print(f"  {tag}: 0 out-of-range values, skip")
        report["loops"].append(
            {
                "loop_tag": tag,
                "out_of_range_count": 0,
                "replaced": 0,
            }
        )
        return

    print(f"  {tag}: {total_oor} out-of-range values, cleaning...")

    # Group consecutive out-of-range rows into segments
    segments: list[list[tuple]] = []
    current_seg: list[tuple] = []
    prev_ts = None
    for row in oor_rows:
        ts = row[0]
        if prev_ts is None:
            current_seg.append(row)
        else:
            # Check if consecutive (within 2 seconds)
            # Parse timestamps and compare
            if _ts_diff_seconds(prev_ts, ts) <= 2:
                current_seg.append(row)
            else:
                if current_seg:
                    segments.append(current_seg)
                current_seg = [row]
        prev_ts = ts
    if current_seg:
        segments.append(current_seg)

    print(f"  {tag}: {len(segments)} segments (avg {total_oor // max(len(segments), 1)} pts/seg)")

    # For each segment, get the previous valid PV and replace all points
    corrected_rows: list[tuple] = []
    replaced_count = 0
    for seg in segments:
        first_ts = seg[0][0]
        prev_valid = query_previous_valid_pv(sub, first_ts, rmin, rmax)

        for row in seg:
            ts, orig_pv, sp, op, mode, pid_p, pid_i, pid_d, q = row
            # Replace PV with previous valid value
            new_pv = round(prev_valid, 4)
            corrected_rows.append((ts, new_pv, sp, op, mode, pid_p, pid_i, pid_d, q))
            # Log the replacement
            log_writer.writerow(
                [
                    tag,
                    sub,
                    ts,
                    orig_pv,
                    new_pv,
                    q,
                ]
            )
            replaced_count += 1

        # Update prev_valid to the corrected value for subsequent segments
        # (already done since we use the same prev_valid within a segment)

    # Insert corrected rows
    inserted = insert_corrected_rows(sub, corrected_rows)
    print(f"  {tag}: replaced {replaced_count} values, inserted {inserted} rows")

    report["loops"].append(
        {
            "loop_tag": tag,
            "out_of_range_count": total_oor,
            "segments": len(segments),
            "replaced": replaced_count,
            "inserted": inserted,
        }
    )


def _ts_diff_seconds(ts1: str, ts2: str) -> float:
    """Approximate time difference between two ISO timestamps."""
    try:
        from datetime import datetime

        d1 = datetime.fromisoformat(ts1.replace("Z", "+00:00"))
        d2 = datetime.fromisoformat(ts2.replace("Z", "+00:00"))
        return abs((d2 - d1).total_seconds())
    except Exception:
        return 999.0


async def main() -> None:
    print("=" * 70)
    print("Range-based PV Cleaning: Replace all out-of-range PV values")
    print("=" * 70)

    loops = await load_loops()
    print(f"Loaded {len(loops)} active loops")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    log_path = OUTPUT_DIR / f"range_clean_log_{timestamp}.csv"
    report_path = OUTPUT_DIR / f"range_clean_report_{timestamp}.json"

    report = {
        "cleaned_at": datetime.now(UTC).isoformat(),
        "total_loops": len(loops),
        "loops": [],
    }

    with open(log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "loop_tag",
                "subtable",
                "ts",
                "original_pv",
                "replaced_pv",
                "pv_quality",
            ]
        )
        for loop in loops:
            await clean_loop(loop, writer, report)
            f.flush()

    # Summary
    total_replaced = sum(lp.get("replaced", 0) for lp in report["loops"])
    report["total_replaced"] = total_replaced
    print(f"\nTotal replaced: {total_replaced} values")

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report: {report_path}")
    print(f"Log: {log_path}")

    # Verify
    print("\n=== Verification ===")
    for loop in loops:
        sub = make_subtable_name(loop["tag_name"])
        rmin, rmax = loop["range_min"], loop["range_max"]
        sql = f"SELECT COUNT(*) FROM {sub} WHERE pv > {rmax} OR pv < {rmin}"
        resp = httpx.post(REST_DB_URL, data=sql, auth=AUTH, timeout=30.0)
        data = resp.json()
        if "data" in data:
            remaining = data["data"][0][0]
            status = "OK" if remaining == 0 else f"REMAINING={remaining}"
            print(f"  {loop['tag_name']}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
