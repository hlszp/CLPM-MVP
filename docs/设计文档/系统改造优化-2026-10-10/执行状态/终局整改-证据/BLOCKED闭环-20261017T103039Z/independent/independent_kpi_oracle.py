"""Read saved synthetic KPI inputs and compute three expected values independently.

No app modules, calculator/replay entrypoints, API/DB clients or actual result
values are read by `compute`. `compare` is a separate subsequent operation.
The current input adapter deliberately supports the frozen FLOW/1s, 0..100,
single-binding, no-gap synthetic fixture only. Other layouts are BLOCKED; the
tool never guesses missing hold policies, parameters, masks or window units.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import hashlib
import json
import math
import re
import statistics
from datetime import timedelta
from pathlib import Path

from independent_support import (
    InvalidEvidence,
    MissingEvidence,
    ReadSet,
    canonical_digest,
    finite,
    instant,
    need,
    result_rc,
    write_new,
)

AUTO_MODES = {1, 2, 3, 4}  # AUTO/CAS/REMOTE/APC; MANUAL=0.
FIELDS = {
    "good_value_rate": "goodValueRate",
    "auto_mode_rate": "autoModeRate",
    "stability_rate": "steadyRate",
}


def assert_fact(ok, message):
    if not ok:
        raise InvalidEvidence(message)


def sample_stdev(values):
    if len(values) < 2:
        raise MissingEvidence("sample variance requires n>=2")
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((x - mean) ** 2 for x in values) / (len(values) - 1))


def masked_flags(expression, validity, count):
    """Evaluate only a small, declarative boolean mask grammar (never eval)."""
    # The frozen mask contract explicitly defines empty/None as no filter.
    if expression is None or (isinstance(expression, str) and not expression.strip()):
        return [True] * count
    if not isinstance(expression, str):
        raise MissingEvidence("maskExpression is not a declarative string")
    normalized = re.sub(
        r"!(?!=)", " not ", expression.replace("&&", " and ").replace("||", " or ")
    ).strip()
    tree = ast.parse(normalized, mode="eval").body

    def at(node, index):
        if isinstance(node, ast.Name):
            values = validity.get(node.id)
            if values is None or len(values) != count:
                raise MissingEvidence(f"mask flag array {node.id} absent/wrong length")
            return bool(values[index])
        if isinstance(node, ast.Constant) and type(node.value) is bool:
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.BitOr)):
            a, b = at(node.left, index), at(node.right, index)
            return a and b if isinstance(node.op, ast.BitAnd) else a or b
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [at(child, index) for child in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.Invert)):
            return not at(node.operand, index)
        raise MissingEvidence(f"unsupported mask grammar: {ast.dump(node)}")

    return [at(tree, i) for i in range(count)]


def step_exclusions(sp, params, new_semantics, period):
    if not need(
        params.get("sp_step_exclusion_enabled"), "SP exclusion boolean not pinned"
    ):
        return [False] * len(sp)
    sigma_multiple = need(params.get("sp_step_sigma"), "SP step sigma not pinned")
    differences = [b - a for a, b in zip(sp, sp[1:])]
    deviation = statistics.pstdev(differences) if len(differences) > 1 else 0.0
    excluded = [False] * len(sp)
    if deviation == 0:
        return excluded
    if new_semantics:
        seconds = need(
            params.get("sp_tracking_window_seconds"), "physical SP window not pinned"
        )
        width = max(1, math.ceil(seconds / period))
    else:
        width = max(
            1,
            int(
                need(
                    params.get("sp_tracking_window"),
                    "legacy SP point window not pinned",
                )
            ),
        )
    for step, jump in enumerate(differences, start=1):
        if abs(jump) > sigma_multiple * deviation:
            for index in range(step, min(len(sp), step + width)):
                excluded[index] = True
    return excluded


def similarity(values, low, high):
    """Definition via direct squared distances; independent of vectorized code."""
    if len(values) < 2:
        return 0.0
    pivot = min(
        enumerate(values),
        key=lambda pair: (math.fsum((v - pair[1]) ** 2 for v in values), pair[0]),
    )[1]
    if abs(pivot) < 1e-12:
        return 0.0
    retained = [value for value in values if low <= abs(value / pivot) <= high]
    if not retained:
        return 0.0
    return max(
        0.0,
        min(1.0, 1.0 - abs(math.fsum(retained) / len(retained) - pivot) / abs(pivot)),
    )


def oscillation(errors, sp, timestamps, params, new_semantics, period, pv_range):
    excluded = step_exclusions(sp, params, new_semantics, period)
    # Retained independent runs; new semantics cannot join across exclusions.
    original_runs = []
    current = []
    for index in range(len(errors)):
        if excluded[index]:
            if current:
                original_runs.append(current)
                current = []
        else:
            current.append(index)
    if current:
        original_runs.append(current)
    if not new_semantics:
        original_runs = [[index for run in original_runs for index in run]]
    durations = [
        (timestamps[i + 1] - timestamps[i])
        if i + 1 < len(timestamps)
        else (timestamps[-1] - timestamps[-2])
        for i in range(len(timestamps))
    ]
    segments = []
    all_crossings = 0
    for run in original_runs:
        previous_sign = 0
        crossing_positions = []
        for position, index in enumerate(run):
            sign = (errors[index] > 0) - (errors[index] < 0)
            if not sign:
                continue  # A zero plateau retains the prior nonzero sign.
            if previous_sign and sign != previous_sign:
                crossing_positions.append(position)
            previous_sign = sign
        all_crossings += len(crossing_positions)
        for left, right in zip(crossing_positions, crossing_positions[1:]):
            indices = run[left:right]
            seconds = math.fsum(durations[index] for index in indices)
            area = math.fsum(abs(errors[index]) * durations[index] for index in indices)
            polarity = 1 if math.fsum(errors[index] for index in indices) > 0 else -1
            segments.append(
                {
                    "iae": area,
                    "points": right - left,
                    "seconds": seconds,
                    "sign": polarity,
                }
            )
    minimum_crossings = need(
        params.get("min_zero_crossings"), "osc min_zero_crossings not pinned"
    )
    if all_crossings < minimum_crossings:
        return {
            "value": 0.0,
            "isOscillating": False,
            "reason": "insufficient-zero-crossings",
            "crossings": all_crossings,
            "segments": segments,
        }
    positive = [segment["iae"] for segment in segments if segment["sign"] > 0]
    negative = [segment["iae"] for segment in segments if segment["sign"] < 0]
    if not positive or not negative:
        return {
            "value": 0.0,
            "isOscillating": False,
            "reason": "empty-polarity",
            "crossings": all_crossings,
            "segments": segments,
        }
    low = need(params.get("min_ratio"), "osc min_ratio not pinned")
    high = need(params.get("max_ratio"), "osc max_ratio not pinned")
    a, b = similarity(positive, low, high), similarity(negative, low, high)
    half_period = math.fsum(segment["points"] for segment in segments) / len(segments)
    amplitude = math.fsum(
        segment["iae"] / segment["seconds"]
        for segment in segments
        if segment["seconds"] > 0
    ) / len(segments)
    detected = (
        a >= need(params.get("similarity_threshold"), "osc threshold not pinned")
        and b >= params["similarity_threshold"]
        and half_period
        >= need(
            params.get("min_half_period_samples"),
            "osc min_half_period_samples not pinned",
        )
        and amplitude / pv_range
        >= need(params.get("min_amplitude_ratio"), "osc amplitude floor not pinned")
    )
    return {
        "value": min(a, b) * 100.0 if detected else 0.0,
        "isOscillating": detected,
        "crossings": all_crossings,
        "positiveSimilarity": a,
        "negativeSimilarity": b,
        "meanHalfPeriodPoints": half_period,
        "amplitude": amplitude,
        "segments": segments,
    }


def stability(frame, params, osc_params, new_semantics, pv_range):
    indices = frame["maskedIndices"]
    if len(indices) < 2:
        raise MissingEvidence("stability has fewer than two legitimate samples")
    pv = [frame["signals"]["pv"][i] for i in indices]
    sp = [frame["signals"]["sp"][i] for i in indices]
    errors = [a - b for a, b in zip(pv, sp)]
    timestamps = [frame["offsets"][i] for i in indices]
    seconds = [frame["slotSeconds"][i] for i in indices]
    removed = step_exclusions(sp, params, new_semantics, frame["period"])
    retained = [i for i in range(len(errors)) if not removed[i]]
    remaining_errors = [errors[i] for i in retained]
    deviation = sample_stdev(remaining_errors)
    mean = math.fsum(remaining_errors) / len(remaining_errors)
    if deviation == 0 and mean != 0 and len(set(pv)) == 1:
        raise InvalidEvidence(
            "frozen constant PV at nonzero offset is not assessable stability"
        )
    excluded_seconds = math.fsum(seconds[i] for i in range(len(errors)) if removed[i])
    band = need(params.get("band_ratio"), "stability band_ratio not pinned") * pv_range
    numerator = math.fsum(seconds[i] for i in retained if abs(errors[i]) <= band)
    denominator = (
        frame["windowSeconds"] - excluded_seconds
        if new_semantics
        else math.fsum(seconds[i] for i in retained)
    )
    if denominator <= 0:
        raise InvalidEvidence("stability has an empty evaluation domain")
    detail = {
        "n": len(remaining_errors),
        "sigmaUnbiased": deviation,
        "meanError": mean,
        "excludedPoints": sum(removed),
        "excludedSeconds": excluded_seconds,
        "pvRange": pv_range,
        "bandInSeconds": numerator,
        "bandDenominatorSeconds": denominator,
    }
    if need(params.get("band_in_score_enabled"), "stability score mode not pinned"):
        return {"value": numerator / denominator * 100, "mode": "band_in", **detail}
    decay = need(params.get("decay_ratio"), "stability decay_ratio not pinned")
    assert_fact(
        finite(decay) and decay > 0 and pv_range > 0,
        "invalid exponential normalization",
    )
    dependency = oscillation(
        errors, sp, timestamps, osc_params, new_semantics, frame["period"], pv_range
    )
    factor = 1.0 - dependency["value"] / 100.0 if dependency["isOscillating"] else 1.0
    value = math.exp(-deviation / (decay * pv_range)) * factor * 100.0
    return {
        "value": max(0.0, min(100.0, value)),
        "mode": "exponential",
        "decayRatio": decay,
        "oscFactor": factor,
        "oscillation": dependency,
        **detail,
    }


def load_archive(reads, ref):
    # NumPy is a file-format dependency only; pickle and app imports are forbidden.
    try:
        import numpy as np
    except ImportError as exc:
        raise MissingEvidence(
            "NumPy is required to read NPZ; use the existing backend venv"
        ) from exc
    path = reads.path(ref)
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key].copy() for key in archive.files}


def raw_state(samples, offset):
    offsets = [row[0] for row in samples]
    assert_fact(
        offsets == sorted(set(offsets)), "raw input offsets are unordered/duplicated"
    )
    position = bisect.bisect_right(offsets, offset) - 1
    return samples[position] if position >= 0 else None


def snapshot_input_digest(manifest, arrays):
    """Independently implement the documented input-hash byte format."""
    import numpy as np

    descriptions = need(
        manifest.get("arrays"), "manifest dtype/shape descriptions missing"
    )
    assert_fact(set(descriptions) == set(arrays), "manifest/NPZ array keys differ")
    core = {
        key: value
        for key, value in manifest.items()
        if key not in {"createdAt", "createdByRecordId"}
    }
    h = hashlib.sha256(b"clpm-calc-snapshot-v1\x00")
    h.update(
        json.dumps(
            core,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )
    for key in sorted(arrays):
        array = np.ascontiguousarray(arrays[key])
        assert_fact(not array.dtype.hasobject, "object/pickle arrays forbidden")
        descriptor = descriptions[key]
        assert_fact(
            descriptor.get("dtype") == array.dtype.str
            and descriptor.get("shape") == list(array.shape),
            f"manifest dtype/shape differs for {key}",
        )
        h.update(b"\x00" + key.encode())
        h.update(f"|{array.dtype.str}|{array.shape}|".encode())
        h.update(array.tobytes())
    return h.hexdigest()


def load_frame(meta, block_index, arrays, start, end, raw_roles, hold_limits):
    prefix = f"blk{block_index}"
    timestamps = need(arrays.get(prefix + "__ts"), "NPZ timestamp array missing")
    offsets = [float(value) / 1e9 - start.timestamp() for value in timestamps]
    period = float(
        need(
            (meta.get("seriesContext") or {}).get("gridPeriodS"), "grid period missing"
        )
    )
    if period != 1.0:
        raise MissingEvidence("current independent adapter supports FLOW 1s only")
    assert_fact(offsets == sorted(set(offsets)), "NPZ timestamps reordered/duplicated")
    n = len(offsets)
    signals, validity = {}, {}
    for key, array in arrays.items():
        if key.startswith(prefix + "__sig_"):
            name = key.split("__sig_", 1)[1]
            null = arrays.get(prefix + "__null_" + name)
            signals[name] = [
                None if null is not None and bool(null[i]) else float(value)
                for i, value in enumerate(array)
            ]
        elif key.startswith(prefix + "__valid_"):
            validity[key.split("__valid_", 1)[1]] = [bool(value) for value in array]
    assert_fact(
        meta.get("pointCount") == n, "manifest pointCount differs from NPZ length"
    )
    window_seconds = (end - start).total_seconds()
    slots = [
        max(
            0.0,
            min(offsets[i + 1] if i + 1 < n else window_seconds, window_seconds)
            - max(offset, 0.0),
        )
        for i, offset in enumerate(offsets)
    ]
    known = {}
    raw_checks = []
    for role in ("pv", "sp", "mode"):
        if role not in signals:
            continue
        assert_fact(len(signals[role]) == n, f"signal {role} has a wrong length")
        source = raw_roles.get(role.upper(), {}).get("samples")
        if source is None:
            raise MissingEvidence(f"raw {role.upper()} role is missing")
        limit = need(hold_limits.get(role), f"held age policy missing for {role}")
        known[role] = []
        for i, offset in enumerate(offsets):
            raw = raw_state(source, offset)
            is_known = raw is not None and offset - raw[0] <= limit
            known[role].append(is_known)
            expected_value = raw[1] if raw is not None else None
            actual = signals[role][i]
            assert_fact(
                (actual is None and expected_value is None)
                or (
                    finite(actual)
                    and finite(expected_value)
                    and abs(actual - expected_value) <= 1e-8
                ),
                f"{role} NPZ != independently held raw value at offset {offset}",
            )
        raw_checks.append(
            {
                "role": role,
                "count": n,
                "knownSlots": sum(known[role]),
                "rawValueMatch": True,
            }
        )
    quality = []
    for offset in offsets:
        raw = raw_state(
            need(raw_roles.get("PV"), "raw PV role absent")["samples"], offset
        )
        quality.append(raw[2] if raw is not None else None)
    if "pv_quality" in signals:
        assert_fact(
            signals["pv_quality"] == quality,
            "quality array differs from independently held raw QC",
        )
    if "maskExpression" not in meta:
        raise MissingEvidence(
            "maskExpression field is absent (different from explicit empty)"
        )
    consecutive = [False] * n
    for left, right in need(
        meta.get("consecutiveSegments"), "consecutiveSegments field absent"
    ):
        assert_fact(0 <= left <= right < n, "consecutive segment outside the frame")
        for i in range(left, right + 1):
            consecutive[i] = True
    validity["consecutive_valid"] = consecutive
    expression = meta["maskExpression"]
    expected_flags = masked_flags(expression, validity, n)
    expected_indices = [i for i, flag in enumerate(expected_flags) if flag]
    indices = need(meta.get("maskedIndices"), "maskedIndices missing")
    assert_fact(
        indices == expected_indices,
        "maskedIndices differ from independently evaluated boolean mask",
    )
    assert_fact(all(0 <= i < n for i in indices), "mask index out of range")
    return {
        "signals": signals,
        "validity": validity,
        "quality": quality,
        "offsets": offsets,
        "known": known,
        "period": period,
        "slotSeconds": slots,
        "windowSeconds": window_seconds,
        "maskedIndices": indices,
        "rawChecks": raw_checks,
        "meta": meta,
    }


def compute(plan, reads):
    fixture = reads.json(plan["fixture"])
    raw = reads.json(plan["rawInput"])
    manifest = reads.json(plan["datasetManifest"])
    arrays = load_archive(reads, plan["datasetNpz"])
    input_hash = snapshot_input_digest(manifest, arrays)
    if plan.get("declaredDatasetInputHash") is not None:
        assert_fact(
            input_hash == plan["declaredDatasetInputHash"],
            "dataset inputHash differs from independent bytes",
        )
    candidate = need(plan.get("candidateSha"), "candidate SHA is not frozen")
    assert_fact(
        fixture.get("candidateSha") == candidate,
        "fixture SHA differs from frozen candidate",
    )
    assert_fact(
        fixture.get("rawInputSha256") == plan["rawInput"]["sha256"],
        "fixture does not bind raw input file",
    )
    assert_fact(
        fixture.get("synthetic") is True
        and fixture.get("inputOnly") is True
        and raw.get("inputOnly") is True,
        "fixture does not declare synthetic INPUT only",
    )
    source = need(manifest.get("source"), "dataset source missing")
    for key in (
        "assessmentProfile",
        "algorithmVersion",
        "configRevision",
        "executableBuildDigest",
    ):
        expected = need(
            plan.get("identity", {}).get(key), f"planned {key} identity is missing"
        )
        assert_fact(
            source.get(key) == expected, f"dataset {key} differs from pre-frozen plan"
        )
    effective = need(source.get("effectiveParams"), "effectiveParams missing")
    assert_fact(
        effective.get("status") == "PINNED", "task algorithm parameters were not pinned"
    )
    parameter_map = need(effective.get("params"), "effective parameter map missing")
    assert_fact(
        canonical_digest(parameter_map)
        == need(
            plan.get("effectiveParamsSha256"),
            "planned effective params digest is missing",
        ),
        "effective parameters changed from pre-frozen values",
    )
    profile = source["assessmentProfile"]
    if profile not in {"LEGACY_V2", "CANDIDATE_V3_RC1"}:
        raise MissingEvidence(f"unsupported assessment profile: {profile!r}")
    start = instant(fixture["windowStart"])
    inclusive_end = instant(fixture["windowEnd"])
    assert_fact(
        instant(manifest["window"]["tsStart"]) == start
        and instant(manifest["window"]["tsEnd"]) == inclusive_end,
        "dataset window differs from frozen request",
    )
    assert_fact(
        raw.get("windowStart") == fixture["windowStart"]
        and raw.get("windowEnd") == fixture["windowEnd"],
        "raw input window differs from fixture",
    )
    increment = need(plan.get("inclusiveEndSeconds"), "inclusive end policy not frozen")
    assert_fact(increment == 1, "unsupported half-open conversion policy")
    end = inclusive_end + timedelta(seconds=increment)
    assert_fact(
        manifest.get("engineering", {}).get("pvRange") == [0, 100],
        "adapter requires frozen PV range [0,100]",
    )
    assert_fact(
        manifest.get("replayContext", {}).get("pvRange") == [0, 100],
        "replay PV range differs",
    )
    assert_fact(
        fixture.get("loopId") == manifest.get("loopId"),
        "fixture loop differs from dataset",
    )
    roles = need(raw.get("roles"), "raw input role map absent")
    for role in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"):
        samples = need(roles.get(role), f"raw role {role} absent")["samples"]
        declaration = need(
            fixture.get("inputs", {}).get(role), f"fixture hash for {role} absent"
        )
        # Reproduce the declared series serialization, not an algorithm value.
        actual = hashlib.sha256(json.dumps(samples).encode()).hexdigest()
        assert_fact(
            actual == declaration.get("sha256")
            and len(samples) == declaration.get("count"),
            f"raw {role} hash/count differs from fixture",
        )
        assert_fact(
            roles[role].get("pointId") == declaration.get("pointId"),
            f"raw {role} binding differs",
        )
    metas = need(manifest.get("blocks"), "dataset blocks absent")
    frames = {}
    for code in (*FIELDS, "oscillation_rate"):
        matches = [
            (i, meta) for i, meta in enumerate(metas) if meta.get("metricCode") == code
        ]
        if len(matches) != 1:
            raise MissingEvidence(
                f"need one unambiguous {code} input block, got {len(matches)}"
            )
        i, meta = matches[0]
        frames[code] = load_frame(
            meta,
            i,
            arrays,
            start,
            end,
            roles,
            need(plan.get("heldLimitsSeconds"), "held policies not frozen"),
        )
    good = frames["good_value_rate"]
    good_numerator = math.fsum(
        seconds
        for i, seconds in enumerate(good["slotSeconds"])
        if good["quality"][i] == 1
    )
    good_rate = good_numerator / good["windowSeconds"] * 100
    assert_fact(
        good_rate >= 20,
        "fixture good rate makes this critical calculation inconclusive",
    )
    auto = frames["auto_mode_rate"]
    if len(auto["maskedIndices"]) < 2:
        raise MissingEvidence("auto-mode has insufficient masked input")
    mode_flags = need(auto["validity"].get("mode_valid"), "MODE validity absent")
    numerator = math.fsum(
        seconds
        for i, seconds in enumerate(auto["slotSeconds"])
        if auto["known"]["mode"][i]
        and mode_flags[i]
        and int(auto["signals"]["mode"][i]) in AUTO_MODES
    )
    known_seconds = math.fsum(
        seconds
        for i, seconds in enumerate(auto["slotSeconds"])
        if auto["known"]["mode"][i]
    )

    def params(code):
        control = frames[code]["meta"].get("controlType")
        return need(
            parameter_map.get(f"{code}|{control}"),
            f"pinned parameters for {code}|{control} missing",
        )

    steady = stability(
        frames["stability_rate"],
        params("stability_rate"),
        params("oscillation_rate"),
        profile == "CANDIDATE_V3_RC1",
        100.0,
    )
    # Independently verify dependency input is the same selected PV/SP domain.
    for key in ("signals", "offsets", "maskedIndices"):
        if key == "signals":
            for role in ("pv", "sp"):
                assert_fact(
                    frames["stability_rate"][key][role]
                    == frames["oscillation_rate"][key][role],
                    "oscillation dependency uses a different PV/SP input",
                )
        else:
            assert_fact(
                frames["stability_rate"][key] == frames["oscillation_rate"][key],
                "oscillation dependency uses a different time/mask domain",
            )
    reads.verify_unchanged()
    return {
        "kind": "independent-kpi-expectation",
        "verdict": "PASS",
        "candidateSha": candidate,
        "fixtureLoopId": fixture["loopId"],
        "identity": plan["identity"],
        "recomputedDatasetInputHash": input_hash,
        "datasetDbInputHashCompared": plan.get("declaredDatasetInputHash") is not None,
        "inclusiveRequestEnd": fixture["windowEnd"],
        "halfOpenWindow": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "seconds": (end - start).total_seconds(),
        },
        "expected": {
            "goodValueRate": good_rate,
            "autoModeRate": numerator / auto["windowSeconds"] * 100,
            "steadyRate": steady["value"],
        },
        "details": {
            "goodValueRate": {
                "goodSeconds": good_numerator,
                "denominatorSeconds": good["windowSeconds"],
            },
            "autoModeRate": {
                "numeratorSeconds": numerator,
                "denominatorSeconds": auto["windowSeconds"],
                "knownSeconds": known_seconds,
                "unknownSeconds": auto["windowSeconds"] - known_seconds,
                "autoModes": sorted(AUTO_MODES),
            },
            "steadyRate": steady,
        },
        "rawToFrameChecks": {
            code: frame["rawChecks"] for code, frame in frames.items()
        },
        "preprocessingScope": "Raw held values/QC and mask-expression evaluation independently checked; exported validity flags are preserved preprocessed input facts, not a second implementation of every preprocessing classifier",
        "tolerancesPercentagePoints": {"unrounded": 1e-8, "rounded2dp": 0.005},
        "readActualMetrics": False,
        "productCalculatorImported": False,
        "businessOperations": False,
        "readSet": reads.files,
    }


def compare(expected, actual, storage):
    assert_fact(
        expected.get("readActualMetrics") is False
        and expected.get("verdict") == "PASS",
        "expectation was not a completed independent input-only calculation",
    )
    assert_fact(
        actual.get("loopId") == expected.get("fixtureLoopId"), "actual loop ID differs"
    )
    assert_fact(
        actual.get("algorithmVersion") == expected["identity"]["algorithmVersion"],
        "actual algorithm version differs",
    )
    assert_fact(actual.get("status") == "SUCCESS", "actual snapshot is not successful")
    tolerance = expected["tolerancesPercentagePoints"][storage]
    rows = []
    for field, target in expected["expected"].items():
        observed = actual.get(field)
        assert_fact(finite(observed), f"actual {field} is absent/not finite")
        delta = abs(observed - target)
        rows.append(
            {
                "field": field,
                "expected": target,
                "actual": observed,
                "absoluteDifference": delta,
                "tolerance": tolerance,
                "pass": delta <= tolerance + 1e-12,
            }
        )
    return {
        "kind": "independent-kpi-numeric-comparison",
        "verdict": "PASS" if all(row["pass"] for row in rows) else "REWORK",
        "rows": rows,
        "expectationModified": False,
        "businessOperations": False,
        "boundary": "Numerics only; not task, permission, lineage or remote CI acceptance",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("compute")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--evidence-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--expected", type=Path, required=True)
    p.add_argument("--actual", type=Path, required=True)
    p.add_argument("--storage", choices=("unrounded", "rounded2dp"), required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "compute":
            reads = ReadSet(args.evidence_root)
            plan = json.loads(args.plan.read_text())
            result = compute(plan, reads)
            result["planSha256"] = (
                __import__("hashlib").sha256(args.plan.read_bytes()).hexdigest()
            )
        else:
            expected = json.loads(args.expected.read_text())
            actual = json.loads(args.actual.read_text())
            result = compare(expected, actual, args.storage)
            result["expectationSha256"] = (
                __import__("hashlib").sha256(args.expected.read_bytes()).hexdigest()
            )
            result["actualSha256"] = (
                __import__("hashlib").sha256(args.actual.read_bytes()).hexdigest()
            )
    except (MissingEvidence, KeyError, FileNotFoundError, ImportError) as exc:
        result = {"verdict": "BLOCKED", "reason": str(exc), "businessOperations": False}
    except (InvalidEvidence, ValueError, TypeError) as exc:
        result = {"verdict": "REWORK", "reason": str(exc), "businessOperations": False}
    write_new(args.out, result)
    print(
        json.dumps(
            {"verdict": result["verdict"], "out": str(args.out)}, ensure_ascii=False
        )
    )
    return result_rc(result["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())
