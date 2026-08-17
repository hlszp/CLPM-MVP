#!/usr/bin/env python3
"""TDengine 全位号质量码审计：检查「PV 有值/空值 × 质量码」交叉分布。

用途：验证质量码与 PV 值是否错位（正常语义：有值=GOOD 为主、
空值/坏点=UNCERTAIN/BAD；错位表现：有值行 100% UNCERTAIN、
空值行 100% GOOD——曾于 2026-08-17 在 41FIC20021_PIDA 上发现）。

用法（在 backend/ 目录下）：
    uv run python scripts/td_quality_audit.py   # 默认：今日北京时间 00:00 ~ 12:00
    uv run python scripts/td_quality_audit.py --start "2026-08-17 00:00" --end "2026-08-17 12:00"

说明：
- 超表 st_loop_data 单条条件聚合 SQL 一次拿全部子表（27 回路秒级完成）；
- 时间参数按北京时间解释，内部转 UTC（TDengine ts 按 UTC 存储）；
- 只读审计，不写任何数据。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# ---- 配置（从 backend/.env 读取，避免硬编码凭据） ----
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def _rest_sql(sql: str, cfg: dict[str, str]) -> list[list]:
    host = cfg.get("TDENGINE_HOST", "localhost")
    port = int(cfg.get("TDENGINE_PORT", "7104")) + 11
    user = cfg.get("TDENGINE_USER", "root")
    password = cfg.get("TDENGINE_PASSWORD", "")
    url = f"http://{host}:{port}/rest/sql"
    req = urllib.request.Request(url, data=sql.encode("utf-8"), method="POST")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"TDengine 错误: {payload.get('code')} {payload.get('desc')}")
    return payload["data"]


def _to_utc_z(beijing_str: str) -> str:
    """北京时间字符串 → UTC Z 后缀串（TDengine ts 为 UTC 存储）。"""
    dt = datetime.fromisoformat(beijing_str).replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description="TDengine 全位号质量码审计")
    parser.add_argument("--start", default=None, help="开始时间（北京时间，如 2026-08-17 00:00）")
    parser.add_argument("--end", default=None, help="结束时间（北京时间，如 2026-08-17 12:00）")
    args = parser.parse_args()

    today = datetime.now(timezone(timedelta(hours=8))).date()
    start_bj = args.start or f"{today} 00:00"
    end_bj = args.end or f"{today} 12:00"
    start_utc, end_utc = _to_utc_z(start_bj), _to_utc_z(end_bj)

    cfg = _load_env()
    db = cfg.get("TDENGINE_DB", "clpm_ts")
    print(f"窗口（北京）: {start_bj} ~ {end_bj}   （UTC: {start_utc} ~ {end_utc}）\n")

    sql = f"""
    SELECT tbname,
      COUNT(*) AS total,
      COUNT(pv) AS pv_cnt,
      SUM(CASE WHEN pv IS NOT NULL AND pv_quality = 0 THEN 1 ELSE 0 END) AS val_good,
      SUM(CASE WHEN pv IS NOT NULL AND pv_quality = 1 THEN 1 ELSE 0 END) AS val_uncertain,
      SUM(CASE WHEN pv IS NOT NULL AND pv_quality = 2 THEN 1 ELSE 0 END) AS val_bad,
      SUM(CASE WHEN pv IS NULL AND pv_quality = 0 THEN 1 ELSE 0 END) AS null_good,
      SUM(CASE WHEN pv IS NULL AND pv_quality = 1 THEN 1 ELSE 0 END) AS null_uncertain,
      SUM(CASE WHEN pv IS NULL AND pv_quality = 2 THEN 1 ELSE 0 END) AS null_bad
    FROM {db}.st_loop_data
    WHERE ts >= '{start_utc}' AND ts < '{end_utc}'
    GROUP BY tbname ORDER BY tbname
    """
    rows = _rest_sql(" ".join(sql.split()), cfg)
    if not rows:
        print("窗口内无数据")
        return 1

    header = (
        f"{'位号':<20}{'行数':>7}{'PV非空':>8}{'非空率':>8}"
        f"{'有值行G/U/B':>16}{'空值行G/U/B':>16}{'判定':>8}"
    )
    print(header)
    print("-" * len(header))

    inverted: list[str] = []
    normal: list[str] = []
    no_good: list[str] = []  # 有值行 GOOD 占比为 0（质量码语义全局异常）
    for r in rows:
        (
            tbname,
            total,
            pv_cnt,
            val_good,
            val_uncertain,
            val_bad,
            null_good,
            null_uncertain,
            null_bad,
        ) = (r[0], r[1], r[2], r[3] or 0, r[4] or 0, r[5] or 0, r[6] or 0, r[7] or 0, r[8] or 0)
        tag = tbname.removeprefix("d_loop_").upper()
        pv_rate = pv_cnt / total * 100 if total else 0
        # 错位判定：有值行 UNCERTAIN 占比 >90% 且存在空值行标 GOOD
        val_total = val_good + val_uncertain + val_bad
        is_inverted = val_total > 0 and val_uncertain / val_total > 0.9 and null_good > 0
        (inverted if is_inverted else normal).append(tag)
        if val_total > 0 and val_good == 0:
            no_good.append(tag)
        verdict = "⚠️ 错位" if is_inverted else "正常"
        print(
            f"{tag:<20}{total:>7}{pv_cnt:>8}{pv_rate:>7.1f}%"
            f"{f'{val_good}/{val_uncertain}/{val_bad}':>16}"
            f"{f'{null_good}/{null_uncertain}/{null_bad}':>16}{verdict:>8}"
        )

    print("-" * len(header))
    print(
        f"汇总：{len(rows)} 个位号（有数据 {len([r for r in rows if r[1] > 0])} 个）\n"
        f"  1) 空值错位（有值行U>90% 且存在空值行标GOOD）：{len(inverted)} 个"
        + (f" → {'、'.join(inverted)}" if inverted else "")
        + "\n"
        f"  2) 有值行 GOOD 占比为 0（质量码语义异常，全部标 UNCERTAIN）：{len(no_good)} 个"
        + (f" → {'、'.join(no_good)}" if no_good else "")
        + "\n"
        "  G/U/B = GOOD/UNCERTAIN/BAD；「有值行」与「空值行」分别为 PV 非空/为空的行"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
