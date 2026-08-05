#!/usr/bin/env python3
"""TDengine 三级降采样运维监控脚本.

监控秒级→分钟级→小时级三级降采样的数据延迟与完整性，输出结构化健康报告，
适合 cron 定时执行或接入告警系统（退出码：0=OK / 1=WARN / 2=CRITICAL）。

三级链路：
    秒级原始（{RAW_DB}.st_loop_data）          KEEP 35d  → KPI 评估
        ↓ stream_loop_1min（AT_ONCE + FILL_HISTORY）
    分钟级（{AGG_DB}.st_loop_data_1min）        KEEP 5y   → 趋势分析
        ↓ stream_loop_1h（AT_ONCE + FILL_HISTORY）
    小时级（{AGG_DB}.st_loop_data_1h）          KEEP 5y   → 年度报表

监控项：
    1. Stream 状态：SHOW STREAMS，期望全部 status=running
    2. 数据延迟：每级 LAST(ts) 与当前时间差（秒级<2min / 分钟级<3min / 小时级<70min）
    3. 数据完整性：
       - 三级 subtable 数量一致性（每回路都应有 _1m / _1h 子表）
       - 最近 1 小时行数合理性（秒级≥预期×0.5 / 分钟级≥50 / 小时级≥1）
       - 降采样一致性抽样（秒级某分钟 AVG(pv) ≈ 分钟级 pv_avg，误差<5%）
    4. 数据库保留策略：KEEP 配置是否符合预期
    5. 原始→聚合回路边数：源 subtable 与聚合 subtable 应一一对应

使用方式：
    # 默认（使用 .env 中 TDengine 配置）
    cd backend && uv run python scripts/tdengine_downsampling_monitor.py

    # JSON 输出（接入告警系统）
    uv run python scripts/tdengine_downsampling_monitor.py --format json

    # 指定连接
    TDENGINE_HOST=localhost TDENGINE_REST_PORT=7115 \
    uv run python scripts/tdengine_downsampling_monitor.py

    # 仅检查不告警（CI 用）
    uv run python scripts/tdengine_downsampling_monitor.py --no-alert

cron 示例（每 10 分钟）：
    */10 * * * * cd /opt/clpm/backend && \
        uv run python scripts/tdengine_downsampling_monitor.py \
        >> /var/log/clpm/tdengine_monitor.log 2>&1

依赖：requests（backend 已安装）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

TDENGINE_HOST = os.getenv("TDENGINE_HOST", "localhost")
TDENGINE_PORT = int(os.getenv("TDENGINE_REST_PORT", os.getenv("TDENGINE_PORT", "7115")))
TDENGINE_USER = os.getenv("TDENGINE_USER", "root")
TDENGINE_PASSWORD = os.getenv("TDENGINE_PASSWORD", "taosdata")
TDENGINE_DB = os.getenv("TDENGINE_DB", "signal_sim")
AGG_DB = os.getenv("TDENGINE_AGG_DB", "signal_sim_agg")

RAW_KEEP_DAYS = int(os.getenv("TDENGINE_RAW_KEEP_DAYS", "35"))
AGG_KEEP_DAYS = int(os.getenv("TDENGINE_AGG_KEEP_DAYS", "1825"))

# 延迟阈值（秒）：超过即告警
LATENCY_THRESHOLDS = {
    "raw_1s": 120,  # 秒级：gap backfill ~1min，2min 内正常
    "agg_1min": 180,  # 分钟级：stream AT_ONCE，3min 内正常
    "agg_1h": 4200,  # 小时级：1h 聚合 + 10min 容差
}

# 最近窗口预期行数下限（完整性）
FRESH_ROW_FLOOR = {
    "raw_1s_per_loop_per_min": 30,  # 秒级 1 点/s，1 分钟 60 点，下限 30（50% 容差）
    "agg_1min_per_loop_per_h": 50,  # 分钟级 1 点/min，1 小时 60 点，下限 50
    "agg_1h_per_loop": 1,  # 小时级至少 1 点
}

# 降采样一致性抽样误差容忍（5%）
CONSISTENCY_TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """单项检查结果。"""

    name: str
    status: str  # OK / WARN / CRITICAL / SKIP
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitorReport:
    """监控报告。"""

    timestamp: str
    overall_status: str  # OK / WARN / CRITICAL
    checks: list[CheckResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        # 整体状态取最严重
        severity = {"OK": 0, "SKIP": 0, "WARN": 1, "CRITICAL": 2}
        cur = severity.get(self.overall_status, 0)
        new = severity.get(result.status, 0)
        if new > cur:
            self.overall_status = result.status


# ---------------------------------------------------------------------------
# TDengine 客户端
# ---------------------------------------------------------------------------


class TDengineClient:
    """TDengine REST API 客户端（容错查询）。"""

    def __init__(self, host: str, port: int, user: str, password: str):
        self.base_url = f"http://{host}:{port}/rest/sql"
        self.auth = (user, password)

    def query(self, sql: str, db: str | None = None) -> list[list[Any]]:
        """执行查询，返回 data 二维列表。失败抛 RuntimeError。"""
        url = f"{self.base_url}/{db}" if db else self.base_url
        resp = requests.post(url, data=sql, auth=self.auth, timeout=30)
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(
                f"TDengine error: {result.get('desc')} (code={result.get('code')})\nSQL: {sql}"
            )
        return result.get("data", []) or []

    def query_safe(self, sql: str, db: str | None = None) -> list[list[Any]]:
        """容错查询：失败返回空列表并打印警告。"""
        try:
            return self.query(sql, db=db)
        except RuntimeError as e:
            print(f"  [WARN] 查询失败: {e}", file=sys.stderr)
            return []


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def parse_tdengine_ts(ts: str | None) -> datetime | None:
    """解析 TDengine 返回的时间字符串（如 '2026-08-05T03:09:23.868Z'）。

    naive 串按 UTC 处理（补 Z），与 data_integrity 口径一致。
    """
    if not ts or ts == "NULL":
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def fmt_latency(seconds: float | None) -> str:
    """格式化延迟为可读串。"""
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds / 3600:.2f}h"


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def check_streams(client: TDengineClient, agg_db: str, report: MonitorReport) -> None:
    """检查 Stream 状态。

    SHOW STREAMS 在 TDengine 3.x 返回列：stream_name, create_time, stream_sql,
    其中 stream_sql 含 CREATE STREAM 语句本身。status 列在 3.3+ 才有，且常为空。
    本检查以"stream 是否存在"为主，状态字段无法解析时不报 CRITICAL
    （延迟检查 latency_* 才是 stream 真正存活的证据）。
    """
    expected = {"stream_loop_1min", "stream_loop_1h"}
    rows = client.query_safe("SHOW STREAMS", db=agg_db)
    found: dict[str, str] = {}
    for row in rows:
        if not row:
            continue
        name = str(row[0])
        # status 列位置因版本而异，扫描所有单元格找 running/paused/failed
        status_val = "unknown"
        for cell in row[1:]:
            if isinstance(cell, str) and cell.lower() in ("running", "paused", "failed"):
                status_val = cell.lower()
                break
        found[name] = status_val

    missing = expected - set(found.keys())
    # 仅明确 paused/failed 才算异常；unknown（版本不支持 status 列）不报 CRITICAL
    not_running = {n: s for n, s in found.items() if s in ("paused", "failed")}

    if missing:
        report.add(
            CheckResult(
                name="stream_status",
                status="CRITICAL",
                message=f"缺少 stream: {missing}",
                details={"found": found, "missing": list(missing)},
            )
        )
    elif not_running:
        report.add(
            CheckResult(
                name="stream_status",
                status="CRITICAL",
                message=f"stream 非运行态: {not_running}",
                details={"found": found},
            )
        )
    else:
        unknown = {n: s for n, s in found.items() if s == "unknown"}
        msg = f"全部 {len(found)} 个 stream 存在"
        if unknown:
            msg += f"（{len(unknown)} 个状态未知，延迟检查将验证存活性）"
        report.add(
            CheckResult(
                name="stream_status",
                status="OK",
                message=msg,
                details={"found": found},
            )
        )


def check_latency(
    client: TDengineClient,
    raw_db: str,
    agg_db: str,
    report: MonitorReport,
    table_1min: str,
    table_1h: str,
) -> dict[str, datetime | None]:
    """检查三级数据延迟。返回每级最新时间戳。"""
    now = datetime.now(UTC)
    last_ts: dict[str, datetime | None] = {}

    queries = [
        ("raw_1s", "SELECT LAST(ts) FROM st_loop_data", raw_db, "秒级原始"),
        ("agg_1min", f"SELECT LAST(ts) FROM {table_1min}", agg_db, "分钟级聚合"),
        ("agg_1h", f"SELECT LAST(ts) FROM {table_1h}", agg_db, "小时级聚合"),
    ]

    for key, sql, db, label in queries:
        rows = client.query_safe(sql, db=db)
        ts_str = rows[0][0] if rows else None
        ts = parse_tdengine_ts(ts_str)
        last_ts[key] = ts
        if ts is None:
            report.add(
                CheckResult(
                    name=f"latency_{key}",
                    status="CRITICAL",
                    message=f"{label} 无数据或查询失败",
                    details={"lastTs": ts_str},
                )
            )
            continue
        latency = (now - ts).total_seconds()
        threshold = LATENCY_THRESHOLDS.get(key, 300)
        details = {
            "lastTs": ts.isoformat(),
            "latencySec": round(latency, 1),
            "latencyHuman": fmt_latency(latency),
            "thresholdSec": threshold,
        }
        if latency > threshold:
            status = "CRITICAL" if latency > threshold * 3 else "WARN"
            report.add(
                CheckResult(
                    name=f"latency_{key}",
                    status=status,
                    message=f"{label} 延迟 {fmt_latency(latency)} 超阈值 {fmt_latency(threshold)}",
                    details=details,
                )
            )
        else:
            report.add(
                CheckResult(
                    name=f"latency_{key}",
                    status="OK",
                    message=f"{label} 延迟 {fmt_latency(latency)}",
                    details=details,
                )
            )

    return last_ts


def check_subtable_consistency(
    client: TDengineClient,
    raw_db: str,
    agg_db: str,
    report: MonitorReport,
    table_1min: str,
    table_1h: str,
) -> None:
    """检查三级 subtable 数量一致性（每回路应有 _1m / _1h 子表）。

    用 SELECT COUNT(*) FROM (SELECT DISTINCT tbname ...) 子查询计数
    （TDengine 不支持 COUNT(DISTINCT tbname) 聚合，直接返回 None）。
    """
    raw_cnt_rows = client.query_safe(
        "SELECT COUNT(*) FROM (SELECT DISTINCT tbname FROM st_loop_data)", db=raw_db
    )
    raw_count = int(raw_cnt_rows[0][0]) if raw_cnt_rows and raw_cnt_rows[0][0] else 0

    min_cnt_rows = client.query_safe(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT tbname FROM {table_1min})",
        db=agg_db,
    )
    min_count = int(min_cnt_rows[0][0]) if min_cnt_rows and min_cnt_rows[0][0] else 0

    hour_cnt_rows = client.query_safe(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT tbname FROM {table_1h})",
        db=agg_db,
    )
    hour_count = int(hour_cnt_rows[0][0]) if hour_cnt_rows and hour_cnt_rows[0][0] else 0

    details = {
        "rawSubtableCount": raw_count,
        "minuteSubtableCount": min_count,
        "hourSubtableCount": hour_count,
    }

    if raw_count == 0:
        report.add(
            CheckResult(
                name="subtable_consistency",
                status="WARN",
                message="秒级原始无 subtable（无活跃回路或未写入）",
                details=details,
            )
        )
        return

    # 容差：聚合级 subtable 数应 ≥ 秒级（FILL_HISTORY 回填中可能暂时偏少）
    issues: list[str] = []
    if min_count < raw_count:
        issues.append(f"分钟级 {min_count} < 秒级 {raw_count}")
    if hour_count < raw_count:
        issues.append(f"小时级 {hour_count} < 秒级 {raw_count}")

    if issues:
        report.add(
            CheckResult(
                name="subtable_consistency",
                status="WARN",
                message="；".join(issues) + "（可能 FILL_HISTORY 回填未完成）",
                details=details,
            )
        )
    else:
        report.add(
            CheckResult(
                name="subtable_consistency",
                status="OK",
                message=f"三级 subtable 一致（秒{raw_count}/分{min_count}/时{hour_count}）",
                details=details,
            )
        )


def check_fresh_rows(
    client: TDengineClient,
    raw_db: str,
    agg_db: str,
    report: MonitorReport,
    table_1min: str,
    table_1h: str,
) -> None:
    """检查最近窗口行数合理性（整体，非单回路）。"""
    now = datetime.now(UTC)
    one_min_ago = (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    one_hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    # 秒级最近 1 分钟总行数
    raw_rows = client.query_safe(
        f"SELECT COUNT(*) FROM st_loop_data WHERE ts >= '{one_min_ago}'",
        db=raw_db,
    )
    raw_cnt = int(raw_rows[0][0]) if raw_rows else 0

    # 分钟级最近 1 小时总行数
    min_rows = client.query_safe(
        f"SELECT COUNT(*) FROM {table_1min} WHERE ts >= '{one_hour_ago}'",
        db=agg_db,
    )
    min_cnt = int(min_rows[0][0]) if min_rows else 0

    # 小时级最近 2 小时总行数（小时级 1 点/h）
    two_hour_ago = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    hour_rows = client.query_safe(
        f"SELECT COUNT(*) FROM {table_1h} WHERE ts >= '{two_hour_ago}'",
        db=agg_db,
    )
    hour_cnt = int(hour_rows[0][0]) if hour_rows else 0

    # 估算预期：subtable 数（子查询计数，TDengine 不支持 COUNT(DISTINCT tbname)）
    sub_rows = client.query_safe(
        "SELECT COUNT(*) FROM (SELECT DISTINCT tbname FROM st_loop_data)", db=raw_db
    )
    sub_count = int(sub_rows[0][0]) if sub_rows and sub_rows[0][0] else 0

    details = {
        "rawLastMinRows": raw_cnt,
        "minuteLastHourRows": min_cnt,
        "hourLast2hRows": hour_cnt,
        "estimatedSubtables": sub_count,
    }

    issues: list[str] = []
    if sub_count > 0:
        if raw_cnt < sub_count * 30:
            issues.append(f"秒级最近1min仅 {raw_cnt} 行（预期≥{sub_count * 30}）")
        if min_cnt < sub_count * 50:
            issues.append(f"分钟级最近1h仅 {min_cnt} 行（预期≥{sub_count * 50}）")
        if hour_cnt < sub_count:
            issues.append(f"小时级最近2h仅 {hour_cnt} 行（预期≥{sub_count}）")

    if issues:
        report.add(
            CheckResult(
                name="fresh_rows",
                status="WARN",
                message="；".join(issues),
                details=details,
            )
        )
    else:
        report.add(
            CheckResult(
                name="fresh_rows",
                status="OK",
                message=f"最近窗口行数正常（秒{raw_cnt}/分{min_cnt}/时{hour_cnt}）",
                details=details,
            )
        )


def check_downsampling_consistency(
    client: TDengineClient,
    raw_db: str,
    agg_db: str,
    report: MonitorReport,
    table_1min: str,
) -> None:
    """抽样验证降采样一致性：秒级某分钟 AVG(pv) ≈ 分钟级 pv_avg。

    策略：从秒级超表取一个 subtable 名 + 最新 ts，按该分钟窗口在秒级算 AVG(pv)，
    再到分钟级超表按 tbname LIKE '{raw_tb}%' 取同分钟的 pv_avg 比对。
    （聚合 subtable 名由 stream SUBTABLE() 生成，可能附哈希后缀，故用 LIKE 匹配。）
    """
    # 取秒级超表最新一个 subtable + 最新 ts
    raw_rows = client.query_safe("SELECT LAST_ROW(ts), tbname FROM st_loop_data", db=raw_db)
    if not raw_rows or not raw_rows[0][0]:
        report.add(
            CheckResult(
                name="consistency_sampling",
                status="SKIP",
                message="秒级无数据，跳过一致性抽样",
            )
        )
        return

    raw_ts_str, raw_tbname = raw_rows[0][0], raw_rows[0][1] if len(raw_rows[0]) > 1 else None
    ts = parse_tdengine_ts(raw_ts_str)
    if ts is None or not raw_tbname:
        report.add(
            CheckResult(
                name="consistency_sampling",
                status="SKIP",
                message="秒级最新数据解析失败",
            )
        )
        return

    # 该分钟窗口（UTC，与 TDengine 存储口径一致）
    minute_start = ts.strftime("%Y-%m-%d %H:%M:00")
    minute_end = (ts + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:00")

    # 秒级该 subtable 该分钟 AVG(pv)
    avg_rows = client.query_safe(
        f"SELECT AVG(pv) FROM st_loop_data "
        f"WHERE tbname = '{raw_tbname}' AND ts >= '{minute_start}' AND ts < '{minute_end}'",
        db=raw_db,
    )
    raw_avg = avg_rows[0][0] if avg_rows and avg_rows[0] else None

    # 分钟级该 subtable 该分钟 pv_avg（聚合 subtable 名含哈希后缀，用 LIKE）
    min_rows = client.query_safe(
        f"SELECT pv_avg FROM {table_1min} "
        f"WHERE tbname LIKE '{raw_tbname}%' AND ts = '{minute_start}'",
        db=agg_db,
    )
    pv_avg = min_rows[0][0] if min_rows and min_rows[0] else None

    details = {
        "sampleRawSubtable": raw_tbname,
        "sampleMinute": ts.isoformat(),
        "rawAvgPv": raw_avg,
        "aggAvgPv": pv_avg,
    }

    if raw_avg is None or pv_avg is None:
        report.add(
            CheckResult(
                name="consistency_sampling",
                status="WARN",
                message="抽样数据缺失，无法比对",
                details=details,
            )
        )
        return

    try:
        raw_f = float(raw_avg)
        agg_f = float(pv_avg)
        diff = abs(raw_f - agg_f)
        rel_err = diff / max(abs(raw_f), 1e-9)
        details["absDiff"] = round(diff, 6)
        details["relError"] = round(rel_err, 6)
        if rel_err > CONSISTENCY_TOLERANCE:
            report.add(
                CheckResult(
                    name="consistency_sampling",
                    status="WARN",
                    message=f"降采样不一致：秒级AVG={raw_f:.4f}，分钟级AVG={agg_f:.4f}，"
                    f"相对误差 {rel_err * 100:.2f}% > {CONSISTENCY_TOLERANCE * 100}%",
                    details=details,
                )
            )
        else:
            report.add(
                CheckResult(
                    name="consistency_sampling",
                    status="OK",
                    message=f"抽样一致（相对误差 {rel_err * 100:.2f}%）",
                    details=details,
                )
            )
    except (ValueError, TypeError) as e:
        report.add(
            CheckResult(
                name="consistency_sampling",
                status="WARN",
                message=f"数值转换失败: {e}",
                details=details,
            )
        )


def check_retention(
    client: TDengineClient,
    raw_db: str,
    agg_db: str,
    report: MonitorReport,
) -> None:
    """检查数据库保留策略。"""
    rows = client.query_safe(
        "SELECT name, `keep` FROM information_schema.ins_databases "
        f"WHERE name IN ('{raw_db}', '{agg_db}')"
    )
    found = {row[0]: row[1] for row in rows if row and len(row) >= 2}
    details = {"found": found, "expected": {raw_db: f"{RAW_KEEP_DAYS}", agg_db: f"{AGG_KEEP_DAYS}"}}

    issues: list[str] = []
    if raw_db not in found:
        issues.append(f"原始库 {raw_db} 不存在")
    if agg_db not in found:
        issues.append(f"聚合库 {agg_db} 不存在")

    # KEEP 值校验（容忍 ±1 天）。TDengine 返回形如 "35d,35d,35d"（3 级保留）
    def _parse_keep(keep_str: str) -> int | None:
        try:
            first = keep_str.split(",")[0]
            return int(first.rstrip("d").rstrip())
        except (ValueError, IndexError):
            return None

    if raw_db in found:
        keep_val = _parse_keep(found[raw_db])
        if keep_val is None:
            issues.append(f"{raw_db} KEEP 格式异常: {found[raw_db]}")
        elif abs(keep_val - RAW_KEEP_DAYS) > 1:
            issues.append(f"{raw_db} KEEP={found[raw_db]}，预期 {RAW_KEEP_DAYS}d")
    if agg_db in found:
        keep_val = _parse_keep(found[agg_db])
        if keep_val is None:
            issues.append(f"{agg_db} KEEP 格式异常: {found[agg_db]}")
        elif abs(keep_val - AGG_KEEP_DAYS) > 1:
            issues.append(f"{agg_db} KEEP={found[agg_db]}，预期 {AGG_KEEP_DAYS}d")

    if issues:
        report.add(
            CheckResult(
                name="retention_policy",
                status="WARN",
                message="；".join(issues),
                details=details,
            )
        )
    else:
        report.add(
            CheckResult(
                name="retention_policy",
                status="OK",
                message=(
                    f"保留策略正常（{raw_db}={found.get(raw_db)}, {agg_db}={found.get(agg_db)}）"
                ),
                details=details,
            )
        )


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------


def render_text(report: MonitorReport) -> str:
    """渲染为可读文本（适合日志/cron 输出）。"""
    lines = [
        "=" * 60,
        f"TDengine 三级降采样监控报告  {report.timestamp}",
        f"整体状态: {report.overall_status}",
        "=" * 60,
    ]
    for c in report.checks:
        icon = {"OK": "✅", "WARN": "⚠️ ", "CRITICAL": "❌", "SKIP": "⏭️ "}.get(c.status, "?")
        lines.append(f"{icon} [{c.status}] {c.name}: {c.message}")
        if c.details:
            for k, v in c.details.items():
                lines.append(f"      {k}: {v}")
    lines.append("-" * 60)
    ok = sum(1 for c in report.checks if c.status == "OK")
    warn = sum(1 for c in report.checks if c.status == "WARN")
    crit = sum(1 for c in report.checks if c.status == "CRITICAL")
    skip = sum(1 for c in report.checks if c.status == "SKIP")
    lines.append(f"汇总: OK={ok}  WARN={warn}  CRITICAL={crit}  SKIP={skip}")
    return "\n".join(lines)


def render_json(report: MonitorReport) -> str:
    """渲染为 JSON（适合接入告警系统）。"""
    data = asdict(report)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def maybe_alert(report: MonitorReport, no_alert: bool) -> None:
    """CRITICAL 时输出告警提示（可接入 alerting）。"""
    if no_alert:
        return
    if report.overall_status == "CRITICAL":
        print(
            "\n🚨 告警：检测到 CRITICAL 级别问题，请立即检查 TDengine stream 与数据链路！",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="TDengine 三级降采样运维监控")
    parser.add_argument("--host", default=TDENGINE_HOST, help="TDengine 主机")
    parser.add_argument("--port", type=int, default=TDENGINE_PORT, help="TDengine REST 端口")
    parser.add_argument("--user", default=TDENGINE_USER, help="用户名")
    parser.add_argument("--password", default=TDENGINE_PASSWORD, help="密码")
    parser.add_argument("--raw-db", default=TDENGINE_DB, help="原始库名")
    parser.add_argument("--agg-db", default=AGG_DB, help="聚合库名")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    parser.add_argument("--no-alert", action="store_true", help="不输出告警提示")
    args = parser.parse_args()

    table_1min = "st_loop_data_1min"
    table_1h = "st_loop_data_1h"

    report = MonitorReport(
        timestamp=datetime.now(UTC).isoformat(),
        overall_status="OK",
    )

    client = TDengineClient(args.host, args.port, args.user, args.password)

    # 1. Stream 状态
    check_streams(client, args.agg_db, report)

    # 2. 三级数据延迟
    check_latency(client, args.raw_db, args.agg_db, report, table_1min, table_1h)

    # 3. subtable 一致性
    check_subtable_consistency(client, args.raw_db, args.agg_db, report, table_1min, table_1h)

    # 4. 最近窗口行数
    check_fresh_rows(client, args.raw_db, args.agg_db, report, table_1min, table_1h)

    # 5. 降采样一致性抽样
    check_downsampling_consistency(client, args.raw_db, args.agg_db, report, table_1min)

    # 6. 保留策略
    check_retention(client, args.raw_db, args.agg_db, report)

    # 汇总
    report.summary = {
        "totalChecks": len(report.checks),
        "ok": sum(1 for c in report.checks if c.status == "OK"),
        "warn": sum(1 for c in report.checks if c.status == "WARN"),
        "critical": sum(1 for c in report.checks if c.status == "CRITICAL"),
        "skip": sum(1 for c in report.checks if c.status == "SKIP"),
    }

    if args.format == "json":
        print(render_json(report))
    else:
        print(render_text(report))

    maybe_alert(report, args.no_alert)

    # 退出码：0=OK，1=WARN，2=CRITICAL
    return {"OK": 0, "WARN": 1, "CRITICAL": 2}.get(report.overall_status, 2)


if __name__ == "__main__":
    sys.exit(main())
