"""完整窗口影子对照；复用正式算法，只捕获结果，禁止发布正式投影。"""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select, text

from app.models.loop import LoopLedger
from app.models.metric import MetricConfig
from app.models.metric_data_requirement import ClpmMetricDataRequirement
from app.models.sys_config import SysConfig
from app.services import calc_snapshot, result_ledger
from app.services.ten_minute_blocks import ReferenceEvents, SourceChanged, TenMinuteBlocks, digest


def columns(row) -> dict:
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


def runtime_fence() -> dict:
    from dataclasses import asdict

    from app.contracts.data_types import ControlType
    from app.services.confidence_evaluator import ConfidenceEvaluator
    from app.services.preprocessing.thresholds import get_detector_switches, get_threshold

    return {
        "quality": {ct.value: asdict(get_threshold(ct)) for ct in ControlType},
        "switches": get_detector_switches(),
        "confidence": ConfidenceEvaluator.get_thresholds(),
    }


async def load_context(db, loop_id: str) -> dict:
    """一次固定五层参数、权重、门禁与预处理配置；前后重新查询检测变更。"""
    from app.services.config_publish import pin_config_snapshot
    from app.services.loop_config import get_loop_type_weights_map
    from app.tasks import kpi_calc as kpi

    loop = (await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))).scalar_one()
    metrics = list((await db.execute(select(MetricConfig).order_by(MetricConfig.id))).scalars())
    requirements = list(
        (
            await db.execute(
                select(ClpmMetricDataRequirement).order_by(ClpmMetricDataRequirement.id)
            )
        ).scalars()
    )
    sys_rows = (await db.execute(select(SysConfig).order_by(SysConfig.key))).scalars().all()
    pinned = await pin_config_snapshot(db)  # 失败必须显式终止影子验证，禁止 legacy 回落。
    cfg = (await kpi._batch_load_loop_configs(db, [loop_id]))[loop_id]
    context = {
        "loop": loop,
        "metric_configs": {m.metric_code.lower(): m for m in metrics},
        "type_weights": await get_loop_type_weights_map(db),
        "loop_cfg": cfg,
        "pinned_config": pinned,
        "runtime_overrides": await kpi._load_batch_runtime_overrides(db),
        "template_keys": await kpi._batch_load_template_keys(db, pinned, [loop]),
        "release_context": await kpi._resolve_task_release_context(db),
        "requirements": {r.metric_code: SimpleNamespace(**columns(r)) for r in requirements},
    }
    # sys_config 含质量/可信度配置；不包含影子开关自身（停用由编排层单独检查）。
    context["configHash"] = digest(
        {
            "loop": columns(loop),
            "metrics": [columns(m) for m in metrics],
            "requirements": [columns(r) for r in requirements],
            "sys": {r.key: r.value for r in sys_rows if r.key != "kpi.shadow.config"},
            "pinned": {k: v for k, v in pinned.items() if k not in {"pinnedAt", "pid", "hostname"}},
            "weights": context["type_weights"],
            "loopCfg": cfg,
            "runtime": context["runtime_overrides"],
            "runtimeFence": runtime_fence(),
            "release": context["release_context"],
            "templates": context["template_keys"],
        }
    )
    context["loop"] = SimpleNamespace(**columns(loop))
    context["metric_configs"] = {
        m.metric_code.lower(): SimpleNamespace(**columns(m)) for m in metrics
    }
    return context


class CapturingPlanner:
    """在计算器消费前保存完整输入指纹（含全部 SeriesContext 游程）。"""

    def __init__(self, planner):
        self.planner = planner
        self.input_hash = None
        self.points = 0

    async def request_bundles(self, **kwargs):
        bundles = await self.planner.request_bundles(**kwargs)
        arrays, meta = calc_snapshot.serialize_data_blocks(bundles)
        contexts = [b.data_block.series_context for b in bundles]
        self.input_hash = calc_snapshot.compute_input_hash(
            {
                "blocks": meta,
                "contexts": [sc.to_dict() if sc is not None else None for sc in contexts],
            },
            arrays,
        )
        self.points = max((len(b.data_block.timestamps) for b in bundles), default=0)
        return bundles

    @property
    def _last_request_timing(self):
        return self.planner._last_request_timing


async def capture_pass(db, context, start, end, reader) -> dict:
    from app.services.data_planner import _REQUIREMENT_CODE_ALIASES
    from app.tasks import kpi_calc as kpi

    cfg = context["loop_cfg"]
    loop = context["loop"]
    planner = kpi._build_data_planner(db, bundle_cache=False, event_reader=reader)
    planner._config_loader = kpi._make_config_loader(cfg)
    planner._preloaded_op_limits = {str(loop.id): (cfg["op_lower"], cfg["op_upper"])}

    async def requirements(codes):
        rows = context["requirements"]
        return {
            c: deepcopy(rows[c if c in rows else _REQUIREMENT_CODE_ALIASES.get(c)])
            for c in codes
            if c in rows or _REQUIREMENT_CODE_ALIASES.get(c) in rows
        }

    planner._load_requirements = requirements
    captured = CapturingPlanner(planner)

    async def sink(**payload):
        # 此处是唯一结果出口；不调用 _persist_snapshot，不 commit、不存输入包。
        return result_ledger.canonical_value({k: v for k, v in payload.items() if k != "db"})

    t0 = time.perf_counter()
    result = await kpi._calculate_loop_kpi(
        db=db,
        loop=loop,
        metric_configs=context["metric_configs"],
        ts_start=start,
        ts_end=end,
        data_planner=captured,
        type_weights=context["type_weights"],
        loop_cfg=cfg,
        pinned_config=context["pinned_config"],
        runtime_overrides=context["runtime_overrides"],
        template_keys=context["template_keys"],
        release_context=context["release_context"],
        source="SHADOW",
        logical_run_id="shadow-only",
        result_writer=sink,
    )
    result.pop("_timing", None)
    return {
        "inputHash": captured.input_hash,
        "resultHash": digest(result),
        "result": result,
        "pointCount": captured.points,
        "elapsedSeconds": round(time.perf_counter() - t0, 4),
    }


def compare_passes(baseline: dict, candidate: dict, verification: dict) -> dict:
    """严格相等；只排除计时，不给浮点差异预设容差。"""
    report = {
        name: {k: v for k, v in value.items() if k != "result"}
        for name, value in (
            ("baseline", baseline),
            ("candidate", candidate),
            ("verification", verification),
        )
    }
    if baseline["inputHash"] != verification["inputHash"]:
        report["status"] = "INVALIDATED"
        report["reason"] = "fresh_input_changed"
    elif (
        baseline["resultHash"] != verification["resultHash"]
        or baseline["inputHash"] != candidate["inputHash"]
        or baseline["resultHash"] != candidate["resultHash"]
    ):
        report["status"] = "MISMATCH"
        # 完整结果均在报告中，不靠采样或只比总分掩盖差异。
        report["results"] = {
            "baseline": baseline["result"],
            "candidate": candidate["result"],
            "verification": verification["result"],
        }
    elif baseline["result"].get("status") != "SUCCESS" or not baseline["pointCount"]:
        report["status"] = "INSUFFICIENT_DATA"
    else:
        report["status"] = "EQUAL"
        report["result"] = baseline["result"]
    return report


async def validate_loop(redis, loop_id: str, start: datetime, end: datetime) -> dict:
    """基线、块候选、权威复核三遍；补数/改绑/缺口会反映在完整输入指纹。"""
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        context = await load_context(db, loop_id)
        await db.rollback()  # 已物化纯列快照，不跨 TD IO 持有 PG 事务。
    reference = ReferenceEvents()
    blocks = TenMinuteBlocks(redis, reference)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            baseline = await capture_pass(db, context, start, end, reference)
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            candidate = await capture_pass(db, context, start, end, blocks)
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            verification = await capture_pass(db, context, start, end, ReferenceEvents())
        async with AsyncSessionLocal() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            current = await load_context(db, loop_id)
        if current["configHash"] != context["configHash"]:
            return {"status": "INVALIDATED", "reason": "config_changed"}
        return {
            **compare_passes(baseline, candidate, verification),
            "configHash": context["configHash"],
            "cache": dict(blocks.stats),
        }
    except SourceChanged as exc:
        return {"status": "INVALIDATED", "reason": str(exc), "cache": dict(blocks.stats)}
    finally:
        await blocks.finish()
