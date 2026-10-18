"""Disk-only comparison; no algorithm, product imports, or network calls."""

from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

D = Path(__file__).resolve().parent
NEW = D / "artifacts/12105065268/observation"
OLD = D.parents[1] / "f02/g3-budget-readonly/native-return-observed"
OUT = D / "offline-comparison"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ref(path):
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": sha(data)}


def load(path):
    return json.loads(path.read_text())


def scalar(value):
    value = float(value)
    return value if math.isfinite(value) else "NAN" if math.isnan(value) else "POS_INF" if value > 0 else "NEG_INF"


def array_facts(path):
    array = np.load(path, allow_pickle=False)
    return array, {**ref(path), "shape": list(array.shape), "dtype": str(array.dtype), "rawSha256": sha(array.tobytes())}


def compare_array(a_path, b_path):
    a, ar = array_facts(a_path)
    b, br = array_facts(b_path)
    result = {"baseline": ar, "linux": br, "sameShapeAndDtype": a.shape == b.shape and a.dtype == b.dtype}
    if not result["sameShapeAndDtype"]:
        return result
    equal = (a == b) | (np.isnan(a) & np.isnan(b))
    indices = np.flatnonzero(~equal)
    finite = np.isfinite(a) & np.isfinite(b)
    result.update({
        "sameRawPayload": ar["rawSha256"] == br["rawSha256"],
        "equalValuesWithNaNEqual": bool(np.all(equal)),
        "differentValues": len(indices),
        "maxFiniteAbsDifference": scalar(np.max(np.abs(a[finite] - b[finite]))) if finite.any() else None,
        "firstDifference": {"flatIndex": int(indices[0]), "baseline": scalar(a.flat[indices[0]]), "linux": scalar(b.flat[indices[0]])} if len(indices) else None,
    })
    return result


def inputs(directory, row):
    records = row.get("inputs", row.get("uYInputs"))
    result = {}
    for item in records:
        p = directory / item["file"]
        array, facts = array_facts(p)
        assert facts["sha256"] == item["sha256"] and facts["bytes"] == item["bytes"]
        assert facts["rawSha256"] == item.get("rawSha256", item.get("arrayBytesSha256"))
        result[item["role"]] = {"path": p, "facts": facts}
    assert set(result) == {"u", "y"}
    return result


def config_key(row):
    return tuple(row[key] for key in ("na", "nb", "nc", "d", "maxIter", "tol"))


def candidate(raw):
    if raw is None:
        return None
    if "modelType" in raw:
        return raw
    ev = raw.get("evidence") or {}
    rank = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1, "INCONCLUSIVE": 0}
    return {
        "modelType": raw["params"]["model_type"], "method": raw["identify_method"],
        "fittingScore": raw["fitting_score"], "confidence": raw["confidence"],
        "sortKey": [rank.get(raw["confidence"], 0), raw["fitting_score"], raw["residual_test_passed"]],
        "sortKeyProvenance": "Derived from actual captured pipeline._candidate_sort_key; raw full candidate preserved",
        "residualPassed": raw["residual_test_passed"], "joint": ev.get("joint_estimate"),
        "lowSnr": ev.get("low_snr"), "params": raw["params"], "reason": raw["reason"],
    }


def main():
    OUT.mkdir(exist_ok=False)
    inner = load(NEW / "sha256-manifest.json")
    for item in inner["files"]:
        p = NEW / item["file"]
        assert ref(p)["sha256"] == item["sha256"] and p.stat().st_size == item["bytes"]
    outer_count = 0
    for line in (NEW.parent / "ci-sha256.txt").read_text().splitlines():
        digest, original_path = line.split("  ", 1)
        relative = Path(original_path).relative_to("/home/runner/work/_temp/g3-numerical-originals")
        assert sha((NEW.parent / relative).read_bytes()) == digest
        outer_count += 1
    command = load(NEW / "command.json")
    assert command["sourceBefore"] == command["sourceAfter"]
    for item in command["sourceBefore"]:
        p = NEW / "source" / item["file"]
        assert p.stat().st_size == item["bytes"] and ref(p)["sha256"] == item["sha256"]
    source_old = D.parents[1] / "f02/g3-budget-readonly/source-snapshot"
    source_comparison = []
    for item in command["sourceBefore"]:
        if item["file"].startswith("backend/app/services/tuning_identification/"):
            original = source_old / item["file"]
            source_comparison.append({"file": item["file"], "baseline": ref(original), "linuxSha256": item["sha256"], "same": ref(original)["sha256"] == item["sha256"]})
    old_returns, new_returns = (load(directory / "armax-return-observations.json") for directory in (OLD, NEW))
    old_inputs = [inputs(OLD, row) for row in old_returns]
    new_inputs = [inputs(NEW, row) for row in new_returns]
    comparisons = []
    for index, (row, inp) in enumerate(zip(new_returns, new_inputs, strict=True)):
        exact = [i for i, old in enumerate(old_returns) if config_key(old) == config_key(row) and all(old_inputs[i][role]["facts"]["rawSha256"] == inp[role]["facts"]["rawSha256"] for role in ("u", "y"))]
        record = {"linuxCallIndex": index + 1, "config": dict(zip(("na", "nb", "nc", "d", "maxIter", "tol"), config_key(row), strict=True)), "linuxInputs": {role: x["facts"] for role, x in inp.items()}, "exactMatchingBaselineCallIndices": [i + 1 for i in exact]}
        if len(exact) == 1:
            i = exact[0]
            old = old_returns[i]
            record.update({"alignment": "EXACT_RAW_U_Y_AND_CONFIG_UNIQUE", "coefficientComparison": {key: {"baseline": old[key], "linux": row[key], "same": old[key] == row[key]} for key in ("a", "b", "c", "iterationsDone", "lastMaxDiff", "metActualConvergenceCriterion")}})
        else:
            record["alignment"] = "NO_UNIQUE_EXACT_INPUT_IDENTITY_DO_NOT_INFER_SAME_CALL"
            alternatives = []
            for i, old in enumerate(old_returns):
                if config_key(old) == config_key(row) and all(old_inputs[i][role]["facts"]["shape"] == inp[role]["facts"]["shape"] for role in ("u", "y")):
                    alternatives.append({"baselineCallIndex": i + 1, "provenance": "SAME_CONFIG_AND_SHAPE_ONLY_NOT_IDENTITY", "inputs": {role: compare_array(old_inputs[i][role]["path"], inp[role]["path"]) for role in ("u", "y")}})
            record["possibleInputComparisons"] = alternatives
        comparisons.append(record)
    old_select, new_select = (load(directory / "selection-events.json") for directory in (OLD, NEW))
    summary = {
        "createdAt": datetime.now(UTC).isoformat(), "scope": "OFFLINE_ORIGINALS_ONLY_NO_ALGORITHM_REEXECUTION",
        "baseline": str(OLD), "linux": str(NEW), "manifestVerification": {"innerFiles": len(inner["files"]), "outerOriginalAbsoluteRefsMapped": outer_count, "sourceBeforeAfterSame": True},
        "sourceComparison": source_comparison, "inputArrays": {role: compare_array(OLD / f"{role}.npy", NEW / f"{role}.npy") for role in ("op", "pv")},
        "armaxCallCounts": {"baseline": len(old_returns), "linux": len(new_returns)}, "armaxCalls": comparisons,
        "linuxArmaxTerminalFacts": [{key: row[key] for key in ("na", "nb", "nc", "d", "iterationsDone", "lastMaxDiff", "metActualConvergenceCriterion", "a", "b", "c")} for row in new_returns],
        "selection": {"baseline": [{key: candidate(row[key]) for key in ("singleBest", "jointBest")} for row in old_select], "linux": [{key: candidate(row[key]) for key in ("singleBest", "jointBest")} for row in new_select]},
        "linuxSummary": load(NEW / "summary.json"), "linuxEnvironmentRef": ref(NEW / "environment.json"),
        "limitations": ["Old failing Linux run 39037749058 did not capture raw arrays, each ARMAX return, or LS iteration matrices; this comparison cannot reconstruct that failed execution.", "Same config/shape or call order is not a substitute for exact input identity.", "Terminal maxDiff and coefficient differences are not the first LS/iteration divergence; no causal hardware/solver conclusion is established.", "Fresh post-pytest diagnostic is distinct from the original pytest process."],
        "actualAlgorithmInvocationsThisComparison": 0, "productImports": [name for name in sys.modules if name == "app.main" or name.startswith("app.services.tuning_identification")],
    }
    assert not summary["productImports"]
    (OUT / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"manifestVerification": summary["manifestVerification"], "allAlgorithmSourceFilesIdentical": all(x["same"] for x in source_comparison), "inputFacts": {key: {k: v for k, v in val.items() if k not in ("baseline", "linux")} for key, val in summary["inputArrays"].items()}, "armaxCallCounts": summary["armaxCallCounts"], "exactInputMatches": sum(x["alignment"] == "EXACT_RAW_U_Y_AND_CONFIG_UNIQUE" for x in comparisons), "linuxConvergedByActualABCriterion": sum(x["metActualConvergenceCriterion"] for x in new_returns), "linuxSummary": summary["linuxSummary"], "singleBest": summary["selection"]["linux"][0]["singleBest"], "jointBest": summary["selection"]["linux"][0]["jointBest"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
