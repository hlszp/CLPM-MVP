"""隔离的 24h 合成输入 A/B：比较指定 Git 基线，断言完整质量结果等价。

uv run python scripts/benchmark_history_hotpaths.py --baseline-ref 9c0dd8db6 --output result.json
不访问数据库或启动 Worker/Beat；结果不能替代现场基准。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries
from app.services.data_source import logical_wide_builder as builder
from app.services.data_source import tdengine_provider
from app.services.preprocessing.data_quality_assessor import DataQualityAssessor


def load_baseline(ref, relative, directory, name):
    source = subprocess.check_output(["git", "show", f"{ref}:{relative}"])
    path = directory / (name + ".py")
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(result):
    payload = json.dumps(asdict(result), sort_keys=True, default=str, allow_nan=True).encode()
    return hashlib.sha256(payload).hexdigest()


async def benchmark_provider(provider, raw):
    original_builder = builder.build_logical_wide

    async def supplied_grid(*args, **kwargs):
        return raw

    builder.build_logical_wide = supplied_grid
    try:
        timings = []
        start, end = raw.timestamps[0], raw.timestamps[-1]
        layouts = [SimpleNamespace(start=start, end=end)]
        for _ in range(3):
            clock = time.perf_counter()
            result = await provider._point_or_mixed_query(
                None, "benchmark", list(raw.signals), start, end, 1, layouts
            )
            timings.append(time.perf_counter() - clock)
        return timings, digest(result)
    finally:
        builder.build_logical_wide = original_builder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--points", type=int, default=86400)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.points < 1000:
        parser.error("--points must be >= 1000")
    n = args.points
    origin = datetime(2026, 10, 18)
    ts = [origin + timedelta(seconds=i) for i in range(n)]
    x = np.arange(n)
    raw = RawTimeSeries(
        timestamps=ts,
        signals={
            "pv": (50 + np.sin(x / 150)).tolist(),
            "sp": [50.0] * n,
            "op": (45 + np.sin(x / 130)).tolist(),
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},
    )
    raw.signals["pv"][123] = None
    raw.quality_codes["pv_quality"][456] = 0
    config = LoopPreprocessConfig(
        loop_id="benchmark", control_type=ControlType.LEVEL, range_min=0, range_max=100
    )
    results = {}
    with tempfile.TemporaryDirectory(prefix="clpm-history-ab-") as scratch:
        directory = Path(scratch)
        prefix = "backend/app/services/"
        old_detector = load_baseline(
            args.baseline_ref,
            prefix + "preprocessing/outlier_detection.py",
            directory,
            "ab_detector",
        )
        old_assessor = load_baseline(
            args.baseline_ref,
            prefix + "preprocessing/data_quality_assessor.py",
            directory,
            "ab_assessor",
        )
        old_assessor.OutlierDetector = old_detector.OutlierDetector
        old_provider = load_baseline(
            args.baseline_ref, prefix + "data_source/tdengine_provider.py", directory, "ab_provider"
        )
        for label, assessor_type, provider in [
            ("baseline", old_assessor.DataQualityAssessor, old_provider),
            ("optimized", DataQualityAssessor, tdengine_provider),
        ]:
            assessor = assessor_type(config)
            timings = []
            for _ in range(3):
                started = time.perf_counter()
                result = assessor.assess(raw)
                timings.append(time.perf_counter() - started)
            read_times, read_digest = asyncio.run(benchmark_provider(provider, raw))
            results[label] = {
                "qualitySeconds": timings,
                "qualityMedianSeconds": statistics.median(timings),
                "qualityResultSha256": digest(result),
                "gridAssemblySeconds": read_times,
                "gridAssemblyMedianSeconds": statistics.median(read_times),
                "gridResultSha256": read_digest,
            }
    assert results["baseline"]["qualityResultSha256"] == results["optimized"]["qualityResultSha256"]
    assert results["baseline"]["gridResultSha256"] == results["optimized"]["gridResultSha256"]
    results["points"] = n
    results["baselineRef"] = args.baseline_ref
    results["equivalent"] = True
    results["syntheticInput"] = True
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
