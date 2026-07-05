"""Benchmark 12 KPI metric calculators on real loop data.

Measures actual wall-clock time and infers time complexity by running
each calculator on 4 different input sizes (1k / 5k / 10k / 30k points)
per control type (FC / PC / TC / LC).

Usage:
    cd backend && uv run python scripts/benchmark_metrics.py
    cd backend && uv run python scripts/benchmark_metrics.py --loop-tag 41FIC20021_PIDA
    cd backend && uv run python scripts/benchmark_metrics.py --points 1000 5000 10000 30000 60000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure backend dir on path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Reduce noise from data_planner logs
logging.basicConfig(level=logging.WARNING)

from app.contracts.data_types import (  # noqa: E402
    ControlType,
    DataBlock,
    DataLineage,
    LoopPreprocessConfig,
    MetricDataBundle,
    QualitySummary,
    RawTimeSeries,
    TagGroup,
)
from app.core.config import settings  # noqa: E402
from app.core.tdengine import make_subtable_name  # noqa: E402
from app.services.metric_calculator import (  # noqa: E402
    AUXILIARY_METRIC_CODES,
    CORE_METRIC_CODES,
    DISCOUNT_METRIC_CODE,
    get_calculator,
)
from app.services.preprocessing.pipeline import PreprocessingPipeline  # noqa: E402
from app.services.preprocessing.thresholds import get_threshold  # noqa: E402

import httpx  # noqa: E402


# Default representative loops: one per control type
DEFAULT_LOOPS: list[tuple[str, ControlType, float, float]] = [
    ("41FIC20021_PIDA", ControlType.FLOW, 0.0, 200.0),
    ("41PIC20124_PIDA", ControlType.PRESSURE, 0.0, 1.5),
    ("41TIC20006_PIDA", ControlType.TEMPERATURE, 0.0, 400.0),
    ("41LIC20117_PIDA", ControlType.LEVEL, 0.0, 100.0),
]

# Test data sizes (points)
DEFAULT_SIZES = [1000, 5000, 10000, 30000]

# Number of repeated runs per measurement (for stable timing)
REPEAT = 3

# REST API endpoint
REST_URL = f"http://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 11}/rest/sql/{settings.TDENGINE_DB}"
AUTH = (settings.TDENGINE_USER, settings.TDENGINE_PASSWORD)


def query_raw_data(
    sub: str,
    n_points: int,
    range_min: float,
    range_max: float,
) -> RawTimeSeries:
    """Query last n_points from TDengine for a subtable."""
    sql = (
        f"SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
        f"FROM {sub} ORDER BY ts DESC LIMIT {n_points}"
    )
    resp = httpx.post(REST_URL, data=sql, auth=AUTH, timeout=120.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"TDengine query failed: {data}")
    rows = data.get("data", [])
    # Reverse to ascending order
    rows.reverse()
    timestamps = []
    pv, sp, op, mode = [], [], [], []
    pid_p, pid_i, pid_d, q = [], [], [], []
    for r in rows:
        # ts comes back as ISO string with Z
        ts_str = r[0]
        if isinstance(ts_str, str):
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            ts = datetime.fromtimestamp(r[0] / 1000.0, tz=timezone.utc)
        timestamps.append(ts)
        pv.append(float(r[1]) if r[1] is not None else 0.0)
        sp.append(float(r[2]) if r[2] is not None else 0.0)
        op.append(float(r[3]) if r[3] is not None else 0.0)
        mode.append(r[4] if r[4] is not None else 1)
        pid_p.append(float(r[5]) if r[5] is not None else 1.0)
        pid_i.append(float(r[6]) if r[6] is not None else 0.1)
        pid_d.append(float(r[7]) if r[7] is not None else 0.0)
        q.append(int(r[8]) if r[8] is not None else 1)
    return RawTimeSeries(
        timestamps=timestamps,
        signals={"pv": pv, "sp": sp, "op": op, "mode": mode,
                 "pid_p": pid_p, "pid_i": pid_i, "pid_d": pid_d},
        quality_codes={"pv_quality": q},
    )


def build_bundle(
    raw: RawTimeSeries,
    loop_id: str,
    control_type: ControlType,
    metric_code: str,
    range_min: float,
    range_max: float,
    tag_group: TagGroup = TagGroup.BASE,
) -> MetricDataBundle:
    """Run preprocessing pipeline and build a MetricDataBundle."""
    cfg = LoopPreprocessConfig(
        loop_id=loop_id,
        control_type=control_type,
        range_min=range_min,
        range_max=range_max,
    )
    pipeline = PreprocessingPipeline(cfg)
    block = pipeline.process(raw=raw, tag_group=tag_group)
    # Build mask: all True (calculator applies its own mask as needed)
    n = block.point_count
    indices = list(range(n))
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression="pv_valid",
        masked_indices=indices,
        lineage=DataLineage(
            sampling_freq=block.sampling_freq,
            tag_group=tag_group.value,
            valid_rate=block.quality_summary.valid_rate if block.quality_summary else 0.0,
            data_policy_version=block.preprocess_version,
            algorithm_version="KPI_CALC_v2.0",
        ),
    )


def build_config_bundle(
    loop_id: str,
    control_type: ControlType,
    range_min: float,
    range_max: float,
) -> MetricDataBundle:
    """Build a virtual CONFIG bundle (for ideal_settling_time)."""
    block = DataBlock(
        data_block_id=f"cfg_{loop_id}",
        loop_id=loop_id,
        tag_group="CONFIG",
        sampling_freq="config",
        timestamps=[datetime.now(timezone.utc)],
        signals={"control_type": [control_type.value], "range_min": [range_min], "range_max": [range_max]},
        validity={},
        quality_summary=QualitySummary(total_count=1, valid_count=1, valid_rate=1.0),
        point_count=1,
    )
    return MetricDataBundle(
        metric_code="ideal_settling_time",
        data_block=block,
        mask_expression="",
        masked_indices=[0],
        lineage=DataLineage(tag_group="CONFIG", algorithm_version="KPI_CALC_v2.0"),
    )


def measure_one(calc_code: str, bundle: MetricDataBundle) -> tuple[float, Any]:
    """Run calculator REPEAT times, return (median_ms, result)."""
    # Layer-2 metrics need deps injected
    calc = get_calculator(calc_code)
    if calc is None:
        return (0.0, None)
    # For Layer-2 metrics we'd need deps; for benchmark we just measure
    # the calculator itself (some may fail without deps, that's OK)
    results = []
    for _ in range(REPEAT):
        # Re-create calculator each run to avoid state leak
        calc = get_calculator(calc_code)
        if calc is None:
            return (0.0, None)
        t0 = time.perf_counter()
        try:
            res = calc.calculate(bundle)
        except Exception as exc:
            return (-1.0, str(exc)[:120])
        t1 = time.perf_counter()
        results.append((t1 - t0) * 1000.0)
        last_res = res
    return (statistics.median(results), last_res)


def infer_complexity(sizes: list[int], times_ms: list[float]) -> str:
    """Infer time complexity class from scaling behavior."""
    valid = [(s, t) for s, t in zip(sizes, times_ms) if t > 0]
    if len(valid) < 2:
        return "n/a"
    # Compute ratio of times for largest/smallest
    s1, t1 = valid[0]
    s2, t2 = valid[-1]
    if t1 <= 0 or t2 <= 0:
        return "n/a"
    n_ratio = s2 / s1
    t_ratio = t2 / t1
    # Expected ratios:
    # O(1):   t_ratio ≈ 1
    # O(log n): t_ratio < 2 for n_ratio=30
    # O(n):   t_ratio ≈ n_ratio
    # O(n log n): t_ratio ≈ n_ratio * log(n_ratio)/log(s1)
    # O(n²):  t_ratio ≈ n_ratio²
    if t_ratio < 2:
        return "O(1)"
    log_n_ratio = n_ratio ** 0.5  # sqrt
    if t_ratio < log_n_ratio * 1.5:
        return "O(√n) or O(log n)"
    if t_ratio < n_ratio * 1.3:
        return "O(n)"
    nlogn_ratio = n_ratio * (math.log(s2) / math.log(s1) if s1 > 1 else 1)
    if t_ratio < nlogn_ratio * 1.3:
        return "O(n log n)"
    if t_ratio < n_ratio * n_ratio * 1.3:
        return "O(n²)"
    return f"O(n^{2 + (t_ratio / (n_ratio ** 2) - 1):.1f})"


def run_benchmark(
    loop_tag: str,
    control_type: ControlType,
    range_min: float,
    range_max: float,
    sizes: list[int],
) -> dict:
    """Run benchmark for one loop."""
    sub = make_subtable_name(loop_tag)
    loop_id = f"bench-{loop_tag}"
    print(f"\n{'=' * 70}")
    print(f"回路: {loop_tag}  控制类型: {control_type.value}  量程: [{range_min}, {range_max}]")
    print(f"子表: {sub}")
    print(f"数据规模: {sizes}")
    print(f"{'=' * 70}")

    # Layer-1 codes
    layer1 = [
        "accuracy_rate",
        "effective_auto_rate",
        "good_value_rate",
        "oscillation_rate",
        "saturation_rate",
        "stiction_index",
        "output_trip_index",
        "auto_mode_rate",
        "settling_time",
        "ideal_settling_time",
    ]
    # Layer-2 codes (need deps; for benchmark we run with stub deps)
    layer2 = ["stability_rate", "fast_rate"]
    all_codes = layer1 + layer2

    # Build config bundle once
    config_bundle = build_config_bundle(loop_id, control_type, range_min, range_max)

    # For each size, query data + build bundles + measure each calculator
    results: dict[str, dict] = {}
    for n in sizes:
        print(f"\n--- 数据规模 n={n} ---")
        # Query raw data
        t0 = time.perf_counter()
        raw = query_raw_data(sub, n, range_min, range_max)
        t_query = (time.perf_counter() - t0) * 1000.0
        print(f"  查询 + 反序列化: {t_query:.1f} ms  (实际 {len(raw.timestamps)} 点)")

        # Preprocess once (shared across metrics)
        t0 = time.perf_counter()
        cfg = LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=control_type,
            range_min=range_min,
            range_max=range_max,
        )
        pipeline = PreprocessingPipeline(cfg)
        block = pipeline.process(raw=raw, tag_group=TagGroup.BASE)
        t_pre = (time.perf_counter() - t0) * 1000.0
        print(f"  预处理 Pipeline: {t_pre:.1f} ms  (valid_rate={block.quality_summary.valid_rate:.4f})")

        # Build per-metric bundles (different mask/tag_group per metric)
        bundles: dict[str, MetricDataBundle] = {}
        for code in all_codes:
            tg = "BASE"
            if code in ("auto_mode_rate", "effective_auto_rate"):
                tg = "MODE_HF"
            elif code == "good_value_rate":
                tg = "QUALITY_HF"
            elif code == "saturation_rate":
                tg = "OP_HF"
            elif code in ("stiction_index",):
                tg = "PVOP_HF"
            elif code == "ideal_settling_time":
                bundles[code] = config_bundle
                continue
            n_pts = block.point_count
            bundles[code] = MetricDataBundle(
                metric_code=code,
                data_block=block,
                mask_expression="pv_valid",
                masked_indices=list(range(n_pts)),
                lineage=DataLineage(
                    sampling_freq=block.sampling_freq,
                    tag_group=tg,
                    valid_rate=block.quality_summary.valid_rate,
                    algorithm_version="KPI_CALC_v2.0",
                ),
            )

        # Measure each calculator
        for code in all_codes:
            bundle = bundles[code]
            t_med, res = measure_one(code, bundle)
            if code not in results:
                results[code] = {"times": [], "results": [], "sizes": []}
            results[code]["times"].append(t_med)
            results[code]["sizes"].append(n)
            res_val = None
            if res is not None and hasattr(res, "value"):
                res_val = res.value
            elif isinstance(res, str):
                res_val = f"ERROR: {res}"
            results[code]["results"].append(res_val)
            status = "✓" if t_med >= 0 else "✗"
            print(f"  {status} {code:25s} {t_med:8.2f} ms  result={res_val}")

    # Infer complexity
    print(f"\n--- 复杂度推断 ---")
    for code in all_codes:
        times = results[code]["times"]
        sizes_used = results[code]["sizes"]
        complexity = infer_complexity(sizes_used, times)
        results[code]["complexity"] = complexity
        print(f"  {code:25s} {complexity:15s}  "
              f"sizes={sizes_used}  times={[f'{t:.2f}' for t in times]} ms")

    return {
        "loop_tag": loop_tag,
        "control_type": control_type.value,
        "range_min": range_min,
        "range_max": range_max,
        "sizes": sizes,
        "metrics": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark 12 KPI metric calculators")
    parser.add_argument("--loop-tag", type=str, default=None,
                        help="Specific loop tag to test (default: 4 loops, one per control type)")
    parser.add_argument("--points", type=int, nargs="+", default=None,
                        help="Data sizes to test (default: 1000 5000 10000 30000)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON file path (default: backend/logs/benchmark_<ts>.json)")
    args = parser.parse_args()

    sizes = args.points or DEFAULT_SIZES
    if args.loop_tag:
        # Find matching control type
        loops_to_test = None
        for tag, ct, rmin, rmax in DEFAULT_LOOPS:
            if tag == args.loop_tag:
                loops_to_test = [(tag, ct, rmin, rmax)]
                break
        if loops_to_test is None:
            print(f"Unknown loop tag: {args.loop_tag}")
            sys.exit(1)
    else:
        loops_to_test = DEFAULT_LOOPS

    all_results = []
    for tag, ct, rmin, rmax in loops_to_test:
        try:
            r = run_benchmark(tag, ct, rmin, rmax, sizes)
            all_results.append(r)
        except Exception as exc:
            print(f"\n!! 回路 {tag} 基准测试失败: {exc}")
            import traceback
            traceback.print_exc()

    # Write report
    out_dir = BACKEND_DIR / "logs" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else out_dir / f"benchmark_{ts}.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str))
    print(f"\n报告已写入: {out_path}")

    # Summary table
    print(f"\n{'=' * 90}")
    print(f"{'指标':25s} {'回路':22s} {'类型':8s} ", end="")
    for n in sizes:
        print(f"{'n='+str(n):>12s} ", end="")
    print(f"{'复杂度':>15s}")
    print("-" * 90)
    for r in all_results:
        for code, info in r["metrics"].items():
            print(f"{code:25s} {r['loop_tag']:22s} {r['control_type']:8s} ", end="")
            for t in info["times"]:
                if t >= 0:
                    print(f"{t:>10.2f}ms ", end="")
                else:
                    print(f"{'ERROR':>11s} ", end="")
            print(f"{info.get('complexity', 'n/a'):>15s}")
    print("=" * 90)


if __name__ == "__main__":
    main()
