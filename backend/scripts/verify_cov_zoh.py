"""只读 COV/ZOH 取证：python -m scripts.verify_cov_zoh --help.

可在现有 worker 容器中运行，也可通过 ``docker exec -i ... python -`` 送入 stdin。
不调用 request_bundles/计算任务/快照写入，不读写 Redis 缓存；计算只查本地 TDengine。
省略时间窗时明确选择最近写入的 hourly/custom 快照窗口；数据库时间均为 UTC。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import event, select, text

from app.core.db import AsyncSessionLocal, engine
from app.models.loop import LoopLedger
from app.services.data_planner import DataPlanner
from app.services.data_source.factory import close_provider, get_provider
from app.services.metric_calculator.time_axis import (
    TIME_QUALITY_POLICY_VERSION,
    classify_coverage,
    utc,
)
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.services.preprocessing.data_quality_assessor import (
    GATE_CORE_TAGS,
    DataQualityAssessor,
)
from app.services.preprocessing.pipeline import PREPROCESS_VERSION, PreprocessingPipeline
from app.services.preprocessing.quality_summary import compute_time_coverage
from app.tasks.kpi_calc import _ALL_METRIC_CODES_DB, _loop_type_to_control_type


class ProbeInputError(ValueError):
    """只包含本脚本可安全展示的输入/证据缺失说明。"""


def parse_time(value: str) -> datetime:
    """接受显式时区，统一为数据库使用的 naive UTC，避免现场误差 8 小时。"""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("时间须为带时区 ISO 格式") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("必须带时区，如 2026-10-23T14:00:00+08:00")
    return parsed.astimezone(UTC).replace(tzinfo=None)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="只读检查快照与 COV/ZOH 预处理，不发布评分")
    result.add_argument("--tag", default="41LIC20130_PIDA")
    result.add_argument("--start", type=parse_time, help="含时区 ISO 起点，与 --end 一起传入")
    result.add_argument("--end", type=parse_time, help="含时区 ISO 终点，与 --start 一起传入")
    result.add_argument("--limit", type=int, choices=range(1, 101), default=5, metavar="1..100")
    result.add_argument("--snapshots-only", action="store_true", help="仅查 PG，不取 TDengine")
    return result


async def _set_read_only(connection: Any) -> None:
    await connection.execute("SET default_transaction_read_only = on")


def _read_only_connection(dbapi_connection: Any, _record: Any) -> None:
    # asyncpg 原始连接在事务开始前设只读，覆盖 builder 自开的全部 PG 短会话。
    dbapi_connection.run_async(_set_read_only)


async def read_snapshots(db: Any, loop_id: str, args: argparse.Namespace) -> dict[str, Any]:
    result = {}
    for table in ("kpi_snapshot_hourly", "kpi_snapshot_custom"):
        custom = table == "kpi_snapshot_custom"
        source = "'CUSTOM'" if custom else "k.source"
        task = "k.task_id::text" if custom else "k.source_task_id"
        created = "k.created_at" if custom else "NULL::timestamp"
        written = "COALESCE(r.created_at,k.created_at)" if custom else "r.created_at"
        window_filter = " AND k.ts_start=:start AND k.ts_end=:end" if args.start else ""
        statement = text(
            f"SELECT k.id AS snapshot_id,k.ts_start,k.ts_end,k.status,k.valid_rate,"
            f"k.confidence_level,k.score,k.algorithm_version,k.data_lineage,"
            "k.effective_auto_rate,k.auto_mode_rate,k.accuracy_rate,k.fast_rate,k.steady_rate,"
            "r.payload->'composite' AS composite_semantic,"
            "r.payload->'metricsSemantic'->'effective_auto_rate' AS effective_auto_semantic,"
            "r.payload->'metricsSemantic'->'auto_mode_rate' AS auto_mode_semantic,"
            "jsonb_build_object("
            "'accuracy_rate',r.payload->'metricsDetail'->'accuracy_rate',"
            "'fast_rate',r.payload->'metricsDetail'->'fast_rate',"
            "'steady_rate',r.payload->'metricsDetail'->'steady_rate',"
            "'effective_auto_rate',r.payload->'metricsDetail'->'effective_auto_rate',"
            "'auto_mode_rate',r.payload->'metricsDetail'->'auto_mode_rate'"
            ") AS score_metrics_detail,"
            f"{source} AS source,{task} AS task_id,{created} AS row_created_at,"
            f"k.result_record_id,r.created_at AS record_created_at,r.logical_run_id,"
            f"{written} AS evidence_written_at,COUNT(*) OVER() AS total_matching "
            f"FROM {table} k LEFT JOIN calculation_result_record r "
            "ON r.id=k.result_record_id WHERE k.loop_id=:loop_id"
            f"{window_filter} ORDER BY {written} DESC NULLS LAST,k.ts_start DESC LIMIT :limit"
        )
        rows = (
            (
                await db.execute(
                    statement,
                    {"loop_id": loop_id, "start": args.start, "end": args.end, "limit": args.limit},
                )
            )
            .mappings()
            .all()
        )
        result[table] = {
            "total_matching": rows[0]["total_matching"] if rows else 0,
            "shown": len(rows),
            "rows": [dict(row) for row in rows],
        }
    return result


def select_window(snapshots: dict[str, Any], args: argparse.Namespace) -> tuple[Any, Any, dict]:
    if args.start is not None:
        return args.start, args.end, {"selection": "explicit_cli"}
    candidates = [
        (table, row)
        for table, payload in snapshots.items()
        for row in payload["rows"]
        if row["evidence_written_at"] is not None
    ]
    if not candidates:
        raise ProbeInputError("没有带写入时间的快照，请显式传入 --start 和 --end")
    table, row = max(candidates, key=lambda item: item[1]["evidence_written_at"])
    return (
        row["ts_start"],
        row["ts_end"],
        {
            "selection": "latest_persisted_snapshot",
            "table": table,
            "snapshot_id": row["snapshot_id"],
            "task_id": row["task_id"],
            "evidence_written_at": row["evidence_written_at"],
        },
    )


def summarize_raw(raw: Any, pipeline: PreprocessingPipeline, task: Any, window: tuple) -> dict:
    block = pipeline.process(raw, task.tag_group, window)
    ctx = raw.series_context
    coverage = getattr(ctx, "role_coverage", None) or {}
    n = len(raw.timestamps)
    zoh_masks = dict(block.validity)
    helper = getattr(pipeline, "_zoh_validity", None)
    roles = {}
    for role in sorted(set(raw.signals) | set(coverage)):
        mask = block.validity.get(f"{role}_valid", [])
        cov = coverage.get(role)
        states: Counter = Counter()
        reasons: Counter = Counter()
        if cov is not None:
            for ts in raw.timestamps:
                known, observed, reason = classify_coverage(cov, utc(ts))
                states["observed" if observed else "held" if known else "unknown"] += 1
                if not known:
                    reasons[reason or "unspecified"] += 1
        expanded = helper(mask, raw.timestamps, cov) if cov is not None and helper else None
        if role in GATE_CORE_TAGS and expanded is not None and mask:
            zoh_masks[f"{role}_valid"] = expanded
        roles[role] = {
            "coverage_present": cov is not None,
            "coverage_slots": dict(states),
            "unknown_reason_slots": dict(reasons),
            "non_null_values": sum(value is not None for value in raw.signals.get(role, [])),
            "raw_valid_count": sum(mask),
            "zoh_valid_count": sum(expanded) if expanded is not None else None,
            "outlier_reason_counts": dict(
                Counter(reason for group in block.outlier_reasons.get(role, []) for reason in group)
            ),
        }
    time_coverage = compute_time_coverage(
        raw.timestamps,
        expected_interval_s=float(pipeline.threshold.base_sampling_freq),
        window_start=window[0],
        window_end=window[1],
    )
    raw_rate = DataQualityAssessor.compute_loop_valid_rate(block.validity, n)
    zoh_rate = DataQualityAssessor.compute_loop_valid_rate(zoh_masks, n) if helper else None
    return {
        "tag_group": task.tag_group.value,
        "requested_roles": task.tag_roles,
        "requested_interval_s": task.interval_s,
        "points": n,
        "first_ts": raw.timestamps[0] if n else None,
        "last_ts": raw.timestamps[-1] if n else None,
        "context_layout": getattr(ctx, "layout", None),
        "coverage_present": bool(coverage),
        "grid_period_s": getattr(ctx, "grid_period_s", None),
        "expected_slots": getattr(ctx, "expected_slots", None),
        "context_unknown_reasons": getattr(ctx, "unknown_reasons", None),
        "gate_core_tags": GATE_CORE_TAGS,
        "raw_valid_rate": raw_rate,
        "zoh_helper_present": helper is not None,
        "zoh_valid_rate": zoh_rate,
        "time_coverage": time_coverage,
        "zoh_times_coverage": zoh_rate * time_coverage if zoh_rate is not None else None,
        "actual_pipeline_effective_valid_rate": block.loop_valid_rate,
        "actual_pipeline_confidence": block.loop_confidence_level,
        "roles": roles,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    event.listen(engine.sync_engine, "connect", _read_only_connection)
    report: dict[str, Any] = {
        "status": "PROBE_RUNNING",
        "phase": "postgres_read_only",
        "read_only": True,
        "generated_at_utc": datetime.now(UTC),
        "preprocess_version": PREPROCESS_VERSION,
        "time_quality_policy_version": TIME_QUALITY_POLICY_VERSION,
        "notice": "快照是已落库结果；fresh_preprocessing 是绕过缓存的只读复算，不写评分。",
    }
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            report["postgres_transaction_read_only"] = (
                await db.execute(text("SHOW transaction_read_only"))
            ).scalar_one()
            loop = (
                await db.execute(select(LoopLedger).where(LoopLedger.tag_name == args.tag))
            ).scalar_one_or_none()
            if loop is None:
                raise ProbeInputError(f"回路不存在: {args.tag}")
            loop_id = str(loop.id)
            control_type = _loop_type_to_control_type(loop.loop_type)
            report["loop"] = {
                "id": loop_id,
                "tag": loop.tag_name,
                "loop_type": loop.loop_type,
                "sampling_control_type": control_type.value,
                "response_category": loop.control_type,
            }
            mappings = (
                (
                    await db.execute(
                        text(
                            "SELECT m.tag_role,t.id AS point_id,t.tag_name,t.range_min,t.range_max "
                            "FROM loop_tag_mapping m JOIN tag_registry t ON t.id=m.tag_id "
                            "WHERE m.loop_id=:loop_id ORDER BY m.tag_role"
                        ),
                        {"loop_id": loop_id},
                    )
                )
                .mappings()
                .all()
            )
            report["point_mapping"] = [dict(row) for row in mappings]
            report["phase"] = "snapshot_read"
            report["snapshots"] = await read_snapshots(db, loop_id, args)
            if args.snapshots_only:
                report["status"] = "SNAPSHOTS_ONLY"
                return report
            start, end, selected = select_window(report["snapshots"], args)
            if end <= start or end - start > timedelta(days=30):
                raise ProbeInputError("窗口必须大于 0 且不超过 30 天")
            report["window"] = {"start_utc": start, "end_utc": end, **selected}
            provider = get_provider()
            report["provider"] = type(provider).__name__
            query = provider.make_query_fn(db)
            report["phase"] = "query_plan"
            # 仅复用契约规划/配置读取；不进入任何 L1/L2 读写或任务持久化路径。
            planner = DataPlanner(None, query, MetricDataBundleAssembler(), db=db)
            requirements = await planner._load_requirements(_ALL_METRIC_CODES_DB)
            plan = planner._build_query_plan(requirements, control_type)
            if not plan:
                raise ProbeInputError("指标数据契约为空，无法生成真实查询计划")
            config = await planner._default_config_loader(loop_id, control_type)
            report["query_plan"] = [
                {
                    "tag_group": task.tag_group.value,
                    "roles": task.tag_roles,
                    "interval_s": task.interval_s,
                    "reused_from": task.reused_from.value if task.reused_from else None,
                }
                for task in plan
            ]
            report["fresh_preprocessing"] = []
            for task in plan:
                if task.reused_from is not None:
                    continue  # 该组实际继承 BASE；不伪装为独立查询/预处理。
                report["phase"] = f"tdengine_read:{task.tag_group.value}"
                raw = await query(loop_id, task.tag_roles, start, end, task.interval_s)
                report["phase"] = f"preprocess:{task.tag_group.value}"
                report["fresh_preprocessing"].append(
                    summarize_raw(raw, PreprocessingPipeline(config), task, (start, end))
                )
        report["status"] = "COMPLETE"
        report["phase"] = "complete"
        return report
    except Exception as exc:
        # 保留已查到的快照/窗口；后续取数失败不能吞掉已有证据或冒充空数据。
        report.update(status="PROBE_FAILED", error_type=type(exc).__name__)
        if isinstance(exc, ProbeInputError):
            report["error"] = str(exc)
        return report
    finally:
        await close_provider()
        await engine.dispose()
        event.remove(engine.sync_engine, "connect", _read_only_connection)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


if __name__ == "__main__":
    argument_parser = parser()
    options = argument_parser.parse_args()
    if (options.start is None) != (options.end is None):
        argument_parser.error("--start 和 --end 必须一起传入")
    if options.start is not None and (
        options.end <= options.start or options.end - options.start > timedelta(days=30)
    ):
        argument_parser.error("窗口必须大于 0 且不超过 30 天")
    try:
        output = asyncio.run(run(options))
    except Exception as exc:
        # 不打印异常文本/traceback，数据库驱动异常可能携带 DSN 或连接参数。
        print(json.dumps({"error_type": type(exc).__name__, "status": "PROBE_FAILED"}))
        raise SystemExit(1) from None
    print(json.dumps(output, default=_json_default, ensure_ascii=False, indent=2))
    raise SystemExit(1 if output["status"] == "PROBE_FAILED" else 0)
