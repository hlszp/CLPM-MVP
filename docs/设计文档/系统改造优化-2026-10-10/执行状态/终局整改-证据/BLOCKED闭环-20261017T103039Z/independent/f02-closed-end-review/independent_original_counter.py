"""Replay the reviewer's unchanged actual row counterexample; no network."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--counter", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.parent))
    spec = importlib.util.spec_from_file_location("endpoint_under_review", args.source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = json.loads(args.counter.read_bytes())
    row = original["actualRow"]
    failures = module.evaluate_chunk([row["point_id"]], row["ts"], 1, [row])
    result = {
        "source": str(args.source),
        "unchangedCounter": str(args.counter),
        "actualRow": row,
        "failures": failures,
        "incompleteOriginalRejected": bool(failures),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert failures, (
        "Incomplete original row was accepted without value/quality evidence"
    )


if __name__ == "__main__":
    main()
