"""Independent full original 22-case capture using one frozen ARMAX variant."""

import argparse
import dataclasses
import enum
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def normalize(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def array(value):
    value = np.asarray(value)
    return {"shape": list(value.shape), "dtype": str(value.dtype), "sha256": hashlib.sha256(value.tobytes(order="C")).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--armax", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.backend))
    frozen = module(args.armax, "independent_armax_full22")
    from app.services.tuning_identification import pipeline
    pipeline.identify_armax = frozen.identify_armax
    original_test = module(args.test, "independent_original_annotated_test")
    identify = original_test.identify_from_history
    evaluate = original_test._evaluate_loop
    real_lstsq = np.linalg.lstsq
    matrices, identifications, loops = [], [], []

    def capture_matrix(a, b, *extra, **options):
        matrices.append({"matrix": array(a), "target": array(b)})
        return real_lstsq(a, b, *extra, **options)

    def capture_identification(*values, **options):
        result = identify(*values, **options)
        identifications.append(json.loads(json.dumps(result, default=normalize)))
        return result

    def capture_loop(*values, **options):
        result = evaluate(*values, **options)
        loops.append({"id": values[0]["id"], "result": result})
        return result

    original_test.identify_from_history = capture_identification
    original_test._evaluate_loop = capture_loop
    np.linalg.lstsq = capture_matrix
    started = time.perf_counter()
    try:
        original_test.test_core_loops_identification_accuracy()
    finally:
        np.linalg.lstsq = real_lstsq
    elapsed = time.perf_counter() - started
    evidence = {"loops": loops, "identifications": identifications, "lstsqInputs": matrices}
    raw = (json.dumps(evidence, sort_keys=True, ensure_ascii=False, indent=2, default=normalize) + "\n").encode()
    args.out.write_bytes(raw)
    summary = {
        "armaxSha256": hashlib.sha256(args.armax.read_bytes()).hexdigest(),
        "originalTestSha256": hashlib.sha256(args.test.read_bytes()).hexdigest(),
        "loops": len(loops), "fullIdentificationResults": len(identifications),
        "leastSquaresInputs": len(matrices), "evidenceSha256": hashlib.sha256(raw).hexdigest(),
        "wallSecondsIncludingCapture": elapsed,
        "scope": "Original 22-case assertion and exact capture; actual pytest timeout and remote CI remain separate",
    }
    args.out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
