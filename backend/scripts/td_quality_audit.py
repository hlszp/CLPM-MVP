#!/usr/bin/env python3
"""TDengine 全位号质量码审计：检查「PV 有值/空值 × 质量码」交叉分布。

质量码语义（项目约束，见 app/services/preprocessing/quality_code.py）：
    TDengine schema: 1 = Good, 0 = Bad（当前主数据源）；
    其他值 = Unknown/UNCERTAIN（OPC UA 语义仅作参考）。
    注意勿与 OPC UA（0=Good/1=Uncertain/2=Bad）混淆——2026-08-17 首版
    审计脚本曾误用 OPC UA 语义，把全库 Good(1) 误判为 UNCERTAIN。

用途：
    - 验证有值行质量码是否健康（应为 1=Good 为主）；
    - 定位 PV 空值行（远端 Bad 段，pv_quality=0）的分布；
    - 输出每行存储率与 PV 非空率，识别断流位号。

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

#: 北京时区
_BJ = timezone(timedelta(hours=8))


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
    dt = datetime.fromisoformat(beijing_str).replace(tzinfo=_BJ)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description="TDengine 全位号质量码审计")
    parser.add_argument("--start", default=None, help="开始时间（北京时间，如 2026-08-17 00:00）")
    parser.add_argument("--end", default=None, help="结束时间（北京时间，如 2026-08-17 12:00）")
    args = parser.parse_args()

    today = datetime.now(_BJ).date()
    start_bj = args.start or f"{today} 00:00"
    end_bj = args.end or f"{today} 12:00"
    start_utc, end_utc = _to_utc_z(start_bj), _to_utc_z(end_bj)

    cfg = _load_env()
    db = cfg.get("TDENGINE_DB", "clpm_ts")
    print(f"窗口（北京）: {start_bj} ~ {end_bj}   （UTC: {start_utc} ~ {end_utc}）")
    print("质量码语义：1=Good, 0=Bad, 其他=Unknown（项目 TDengine schema）\n")

    sql = f"""
    SELECT tbname,
      COUNT(*) AS total,
      COUNT(pv) AS pv_cnt,
      SUM(CASE WHEN pv IS NOT NULL AND pv_quality = 1 THEN 1 ELSE 0 END) AS val_good,
      SUM(CASE WHEN pv IS NOT NULL AND pv_quality = 0 THEN 1 ELSE 0 END) AS val_bad,
      SUM(CASE WHEN pv IS NULL AND pv_quality = 1 THEN 1 ELSE 0 END) AS null_good,
      SUM(CASE WHEN pv IS NULL AND pv_quality = 0 THEN 1 ELSE 0 END) AS null_bad
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
        f"{'有值行G/B/O':>14}{'空值行G/B/O':>14}{'异常':>10}"
    )
    print(header)
    print("-" * len(header))

    abnormal: list[str] = []
    empty_tags: list[str] = []
    for r in rows:
        (
            tbname,
            total,
            pv_cnt,
            val_good,
            val_bad,
            null_good,
            null_bad,
        ) = (
            r[0],
            r[1],
            r[2],
            r[3] or 0,
            r[4] or 0,
            r[5] or 0,
            r[6] or 0,
        )
        # Other（非 0/1 质量码）按总量差值推导（TDengine CASE WHEN 不支持 NOT IN）
        val_other = max(0, pv_cnt - val_good - val_bad)
        null_other = max(0, (total - pv_cnt) - null_good - null_bad)
        tag = tbname.removeprefix("d_loop_").upper()
        pv_rate = pv_cnt / total * 100 if total else 0
        issues: list[str] = []
        if total == 0:
            empty_tags.append(tag)
        else:
            val_total = val_good + val_bad + val_other
            if val_total > 0 and val_bad + val_other > 0:
                issues.append(f"有值行坏质量{val_bad + val_other}")
            # 空值行标 Bad(0)=远端真实坏段（正常）；标 Good(1)=语义错位（异常）；
            # 无质量码(NULL/Other)=该秒远端未上报 PV，仅其他角色驱动成行（中性）
            if null_good > 0:
                issues.append(f"空值行误标Good {null_good}")
            if null_other > 0:
                issues.append(f"空值行无质量码{null_other}")
            if pv_rate < 95:
                issues.append(f"非空率{pv_rate:.1f}%")
        if issues:
            abnormal.append(tag)
        verdict = " ".join(issues) if issues else ("无数据" if total == 0 else "健康")
        print(
            f"{tag:<20}{total:>7}{pv_cnt:>8}{pv_rate:>7.1f}%"
            f"{f'{val_good}/{val_bad}/{val_other}':>14}"
            f"{f'{null_good}/{null_bad}/{null_other}':>14}{verdict:>10}"
        )

    print("-" * len(header))
    data_tags = len(rows) - len(empty_tags)
    print(
        f"汇总：{len(rows)} 个位号（有数据 {data_tags} 个，无数据 {len(empty_tags)} 个）\n"
        f"  异常 {len(abnormal)} 个" + (f" → {'、'.join(abnormal)}" if abnormal else "") + "\n"
        "  G/B/O = Good(1)/Bad(0)/Other(NULL 等)；空值行标Bad=远端真实坏段（正常语义），\n"
        "  空值行无质量码=该秒远端未上报 PV（中性），空值行标Good 才是错位异常。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
