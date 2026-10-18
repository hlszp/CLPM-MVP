"""Synthetic fixtures test the verifier, not CLPM or a real capacity workload."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from independent_kpi_oracle import (
    compare,
    compute,
    masked_flags,
    oscillation,
    sample_stdev,
    similarity,
    stability,
    step_exclusions,
)
from independent_support import (
    InvalidEvidence,
    MissingEvidence,
    ReadSet,
    canonical_digest,
    write_new,
)
from verify_capacity_series import Auditor

SHA = "a" * 40
OSC = {
    "sp_step_exclusion_enabled": False,
    "sp_step_sigma": 3,
    "sp_tracking_window": 60,
    "sp_tracking_window_seconds": 60,
    "min_zero_crossings": 4,
    "min_ratio": 0.05,
    "max_ratio": 15,
    "similarity_threshold": 0.4,
    "min_half_period_samples": 8,
    "min_amplitude_ratio": 0.002,
}
STABLE = {
    "sp_step_exclusion_enabled": False,
    "sp_step_sigma": 3,
    "sp_tracking_window": 60,
    "sp_tracking_window_seconds": 60,
    "band_ratio": 0.01,
    "decay_ratio": 0.05,
    "band_in_score_enabled": False,
}


def frame(pv, sp=None):
    n = len(pv)
    return {
        "maskedIndices": list(range(n)),
        "signals": {"pv": pv, "sp": sp or [50.0] * n},
        "offsets": list(range(n)),
        "slotSeconds": [1.0] * n,
        "windowSeconds": n,
        "period": 1.0,
    }


class MathOracleTests(unittest.TestCase):
    def test_unbiased_stdev(self):
        self.assertAlmostEqual(sample_stdev([1, 2, 3]), 1)
        self.assertNotEqual(sample_stdev([1, 2, 3]), math.sqrt(2 / 3))

    def test_mask_boolean_precedence_and_empty(self):
        flags = {"pv_valid": [True, True, False], "sp_valid": [True, False, True]}
        self.assertEqual(
            masked_flags("pv_valid && !sp_valid", flags, 3), [False, True, False]
        )
        self.assertEqual(masked_flags("", flags, 3), [True, True, True])

    def test_mask_unknown_rejected(self):
        with self.assertRaises(MissingEvidence):
            masked_flags("unknown || pv_valid", {"pv_valid": [True]}, 1)

    def test_mask_code_not_executable(self):
        with self.assertRaises(MissingEvidence):
            masked_flags(
                "__import__('os').system('touch /tmp/should-never-run')", {}, 1
            )

    def test_physical_step_window_not_points(self):
        params = dict(
            STABLE, sp_step_exclusion_enabled=True, sp_tracking_window_seconds=60
        )
        excluded = step_exclusions([50] * 20 + [60] * 20, params, True, 5)
        self.assertEqual(sum(excluded), 12)
        self.assertEqual(
            sum(step_exclusions([50] * 20 + [60] * 20, params, False, 5)), 20
        )

    def test_sigma_zero_perfect_tracking100(self):
        out = stability(frame([50] * 20), STABLE, OSC, False, 100)
        self.assertEqual(out["value"], 100)

    def test_closed_form_exponential_and_no_noise_penalty(self):
        pv = [49] * 50 + [51] * 50
        out = stability(frame(pv), STABLE, OSC, False, 100)
        expected = 100 * math.exp(-math.sqrt(100 / 99) / 5)
        self.assertAlmostEqual(out["value"], expected, places=12)
        self.assertFalse(out["oscillation"]["isOscillating"])

    def test_dead_signal_not_rewarded(self):
        with self.assertRaises(InvalidEvidence):
            stability(frame([49] * 20), STABLE, OSC, False, 100)

    def test_band_in_real_denominator(self):
        out = stability(
            frame([50, 52, 50, 52]),
            dict(STABLE, band_in_score_enabled=True),
            OSC,
            True,
            100,
        )
        self.assertEqual(out["value"], 50)

    def test_similarity_symmetric(self):
        # Filtered mean above the pivot must not be rewarded with constant 1.
        self.assertLess(similarity([10, 10, 20], 0.05, 15), 1)

    def test_oscillation_plateau_not_fake_crossing(self):
        out = oscillation(
            [1, 0, 0, 1, 0, 0, 1, 1], [50] * 8, list(range(8)), OSC, False, 1, 100
        )
        self.assertEqual(out["crossings"], 0)
        self.assertFalse(out["isOscillating"])

    def test_real_repeating_half_period_detected(self):
        errors = [sign for sign in [1, -1] * 6 for _ in range(10)]
        out = oscillation(
            errors, [50] * len(errors), list(range(len(errors))), OSC, False, 1, 100
        )
        self.assertTrue(out["isOscillating"])
        self.assertEqual(out["value"], 100)

    def test_compare_rejects_wrong_value_and_version(self):
        expected = {
            "verdict": "PASS",
            "readActualMetrics": False,
            "fixtureLoopId": "fixture",
            "identity": {"algorithmVersion": "v"},
            "expected": {"goodValueRate": 100, "autoModeRate": 100, "steadyRate": 80.0},
            "tolerancesPercentagePoints": {"unrounded": 1e-8, "rounded2dp": 0.005},
        }
        actual = {
            "loopId": "fixture",
            "algorithmVersion": "v",
            "status": "SUCCESS",
            "goodValueRate": 100,
            "autoModeRate": 100,
            "steadyRate": 79,
        }
        self.assertEqual(compare(expected, actual, "rounded2dp")["verdict"], "REWORK")
        actual["steadyRate"] = 80.004
        self.assertEqual(compare(expected, actual, "rounded2dp")["verdict"], "PASS")
        actual["algorithmVersion"] = "foreign"
        with self.assertRaises(InvalidEvidence):
            compare(expected, actual, "rounded2dp")


class ArtifactContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="clpm-independent-tool-selftest-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_hash_mismatch_rejected(self):
        ref = self.put("raw.json", {"n": 1})
        (self.root / "raw.json").write_text('{"n":2}')
        with self.assertRaises(InvalidEvidence):
            ReadSet(self.root).json(ref)

    def test_path_escape_rejected(self):
        with self.assertRaises(InvalidEvidence):
            ReadSet(self.root).path({"path": "../not-in-root", "sha256": "a" * 64})

    def test_missing_never_green(self):
        auditor = Auditor(self.root)
        with self.assertRaises(MissingEvidence):
            auditor.run({"candidateSha": SHA})

    def test_no_silent_duplicate_collapse(self):
        auditor = Auditor(self.root)
        auditor.indexed(
            [{"loopId": "x"}, {"loopId": "x"}], "loopId", "ledger", "fixture"
        )
        self.assertEqual(auditor.issues[0]["level"], "REWORK")

    def test_expected_file_cannot_be_overwritten(self):
        path = self.root / "expected.json"
        write_new(path, {"value": 1})
        with self.assertRaises(FileExistsError):
            write_new(path, {"value": 2})

    def test_npz_input_adapter_then_separate_numeric_comparison(self):
        import numpy as np

        start = datetime(2026, 10, 14, tzinfo=UTC)
        end = start + timedelta(seconds=99)
        n = 100
        window = {"windowStart": start.isoformat(), "windowEnd": end.isoformat()}
        series = {"PV": [[i, 49.0 if i < 50 else 51.0, 1, 1] for i in range(n)]}
        for role, value in {
            "SP": 50.0,
            "MODE": 1.0,
            "OP": 50.0,
            "PID_P": 1.2,
            "PID_I": 60.0,
            "PID_D": 0.0,
        }.items():
            series[role] = [[0, value, 1, 1]]
        raw = {
            "synthetic": True,
            "inputOnly": True,
            **window,
            "roles": {
                role: {"pointId": role, "samples": samples}
                for role, samples in series.items()
            },
        }
        raw_ref = self.put("raw-input.json", raw)
        fixture = {
            "synthetic": True,
            "inputOnly": True,
            "candidateSha": SHA,
            "loopId": "fixture",
            **window,
            "rawInputSha256": raw_ref["sha256"],
            "inputs": {
                role: {
                    "pointId": role,
                    "count": len(samples),
                    "sha256": hashlib.sha256(json.dumps(samples).encode()).hexdigest(),
                }
                for role, samples in series.items()
            },
        }
        params = {"stability_rate|STABLE": STABLE, "oscillation_rate|STABLE": OSC}
        identity = {
            "assessmentProfile": "LEGACY_V2",
            "algorithmVersion": "test-v",
            "configRevision": "digest:synthetic",
            "executableBuildDigest": "f" * 16,
        }
        arrays = {}
        metas = []
        for i, code in enumerate(
            ("good_value_rate", "auto_mode_rate", "stability_rate", "oscillation_rate")
        ):
            prefix = f"blk{i}"
            arrays[prefix + "__ts"] = np.array(
                [int(start.timestamp() * 1e9) + j * 1_000_000_000 for j in range(n)],
                dtype=np.int64,
            )
            signals = (
                {"pv_quality": [1] * n}
                if code == "good_value_rate"
                else {"mode": [1] * n}
                if code == "auto_mode_rate"
                else {"pv": [row[1] for row in series["PV"]], "sp": [50] * n}
            )
            for role, values in signals.items():
                arrays[prefix + "__sig_" + role] = np.asarray(values, dtype=float)
                arrays[prefix + "__null_" + role] = np.zeros(n, dtype=bool)
                arrays[prefix + "__valid_" + role + "_valid"] = np.ones(n, dtype=bool)
            expression = (
                ""
                if code == "good_value_rate"
                else "mode_valid"
                if code == "auto_mode_rate"
                else "pv_valid && sp_valid"
            )
            metas.append(
                {
                    "metricCode": code,
                    "controlType": "STABLE",
                    "pointCount": n,
                    "maskExpression": expression,
                    "maskedIndices": list(range(n)),
                    "consecutiveSegments": [[0, n - 1]],
                    "seriesContext": {"gridPeriodS": 1.0},
                }
            )
        archive = self.root / "inputs.npz"
        np.savez_compressed(archive, **arrays)
        manifest = {
            "arrays": {
                key: {"dtype": array.dtype.str, "shape": list(array.shape)}
                for key, array in arrays.items()
            },
            "loopId": "fixture",
            "source": {
                **identity,
                "effectiveParams": {"status": "PINNED", "params": params},
            },
            "window": {"tsStart": window["windowStart"], "tsEnd": window["windowEnd"]},
            "engineering": {"pvRange": [0, 100]},
            "replayContext": {"pvRange": [0, 100]},
            "blocks": metas,
        }
        plan = {
            "candidateSha": SHA,
            "identity": identity,
            "inclusiveEndSeconds": 1,
            "heldLimitsSeconds": {"pv": 60, "sp": 7200, "mode": 7200},
            "effectiveParamsSha256": canonical_digest(params),
            "fixture": self.put("fixture.json", fixture),
            "rawInput": raw_ref,
            "datasetManifest": self.put("dataset.manifest.json", manifest),
            "datasetNpz": {
                "path": "inputs.npz",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            },
        }
        expected = compute(plan, ReadSet(self.root))
        self.assertFalse(expected["readActualMetrics"])
        self.assertEqual(expected["halfOpenWindow"]["seconds"], 100)
        self.assertEqual(expected["expected"]["goodValueRate"], 100)
        self.assertEqual(expected["expected"]["autoModeRate"], 100)
        self.assertAlmostEqual(
            expected["expected"]["steadyRate"],
            100 * math.exp(-math.sqrt(100 / 99) / 5),
            places=12,
        )
        actual = {
            "loopId": "fixture",
            "algorithmVersion": "test-v",
            "status": "SUCCESS",
            **expected["expected"],
        }
        self.assertEqual(compare(expected, actual, "unrounded")["verdict"], "PASS")

    def test_twenty_batch_full_synthetic_control_and_mutants(self):
        # Full 20x1000 DATA-shaped synthetic evidence. No app/services/network.
        start = datetime(2026, 10, 14, 12, tzinfo=UTC)
        submit = datetime(2026, 10, 17, 1, tzinfo=UTC)
        source = self.put(
            "synthetic-raw.log", {"selftest": True, "note": "not a real business run"}
        )
        loop_ids = [f"synthetic-loop-{i}" for i in range(1000)]
        specs = []
        for b in range(20):
            folder = f"batch-{b:02}"
            a = b < 10
            scenario = "A" if a else "B"
            window = {
                "tsStart": (start + timedelta(hours=b)).isoformat(),
                "tsEnd": (start + timedelta(hours=b + 1)).isoformat(),
            }
            sent = submit + timedelta(minutes=b * 2)
            observed = sent + timedelta(seconds=10)
            parent, tracker = f"parent-{b}", f"tracker-{b}"
            manifest_rows = []
            eligible = []
            ledger = []
            latest = []
            records = []
            for i, loop_id in enumerate(loop_ids):
                composition = (
                    "normal"
                    if a or i < 800
                    else "insufficient"
                    if i < 900
                    else "manual"
                    if i < 950
                    else "gap"
                )
                success = composition in {"normal", "manual"}
                expected = "EVALUATED" if success else "DATA_INSUFFICIENT"
                tag = f"SYNTHETIC-{i}"
                manifest_rows.append(
                    {
                        "loopTag": tag,
                        "composition": composition,
                        "expectedStatus": expected,
                    }
                )
                eligible.append(
                    {
                        "loopId": loop_id,
                        "loopTag": tag,
                        "composition": composition,
                        "expectedStatus": expected,
                        "expectedSnapshotStatus": "SUCCESS"
                        if success
                        else "INCONCLUSIVE",
                    }
                )
                record_id = f"record-{b}-{i}"
                common = {
                    "loopId": loop_id,
                    **window,
                    "algorithmVersion": "test-v",
                    "recordLogicalRunId": parent,
                    "resultRecordId": record_id,
                }
                ledger.append(
                    {
                        **common,
                        "status": "SUCCESS" if success else "INCONCLUSIVE",
                        "score": 80 if success else None,
                        "source": "MANUAL_STANDARD",
                        "sourceTaskId": tracker,
                        "fitnessTags": {
                            "tags": [] if success else ["DATA_INSUFFICIENT"]
                        },
                    }
                )
                latest.append(dict(common))
                records.append(
                    {
                        "recordId": record_id,
                        "objectId": loop_id,
                        "logicalRunId": parent,
                        "algorithmVersion": "test-v",
                        "status": "COMPLETED",
                        **window,
                    }
                )
            manifest_ref = self.put(folder + "/manifest.json", {"rows": manifest_rows})
            freeze = {
                "frozenAt": (sent - timedelta(seconds=1)).isoformat(),
                "window": window,
                "manifest": {"sha256": manifest_ref["sha256"]},
                "eligible": eligible,
                "excluded": [],
                "predictedNodes": ["synthetic-node"],
                "algorithmVersion": "test-v",
            }
            runtime = {
                "tdDnodeVariables": self.put(
                    folder + "/dnode.json",
                    {
                        "code": 0,
                        "data": [
                            [1, "queryUseMemoryPool", "0"],
                            [1, "minReservedMemorySize", "1024"],
                        ],
                    },
                ),
                "members": [
                    {
                        "role": role,
                        "identity": role,
                        "loadedSha": SHA,
                        "startedAt": (submit - timedelta(hours=1)).isoformat(),
                        "argv": ["synthetic-selftest"],
                    }
                    for role in ("api", "worker_master", "beat")
                ],
                "configDigest": "synthetic-config",
                "schemaRevision": "synthetic-schema",
                "postgresDatabase": "synthetic-selftest",
                "brokerNamespace": "synthetic-selftest",
            }
            tasks = [
                {
                    "id": parent,
                    "name": "app.tasks.kpi_calc.calculate_hourly_kpi",
                    "state": "SUCCESS",
                    "runtimeSha": SHA,
                    "finishedAt": (sent + timedelta(seconds=3)).isoformat(),
                    "rawRefs": [source],
                },
                {
                    "id": f"child-{b}",
                    "name": "app.tasks.kpi_calc.calculate_node_kpi_hourly",
                    "nodeIds": ["synthetic-node"],
                    "state": "SUCCESS",
                    "runtimeSha": SHA,
                    "parentId": parent,
                    "rootId": parent,
                    "finishedAt": (sent + timedelta(seconds=9)).isoformat(),
                    "rawRefs": [source],
                },
            ]
            node_record_id = f"node-record-{b}"
            members = [
                {
                    "loopId": row["loopId"],
                    "state": "scorable" if row["status"] == "SUCCESS" else "incomplete",
                }
                for row in ledger
            ]
            records.append(
                {
                    "recordId": node_record_id,
                    "objectKind": "NODE",
                    "objectId": "synthetic-node",
                    "logicalRunId": f"content-addressed-node-{b}",
                    "status": "COMPLETED",
                    "algorithmVersion": "test-v",
                    **window,
                    "payload": {
                        "loopRecordIds": [
                            {"loopId": row["loopId"], "recordId": row["resultRecordId"]}
                            for row in ledger
                            if row["status"] == "SUCCESS"
                        ],
                        "assessment_stats": {
                            "roster": {
                                "members": members,
                                "states": {
                                    "scorable": sum(
                                        row["state"] == "scorable" for row in members
                                    ),
                                    "incomplete": sum(
                                        row["state"] == "incomplete" for row in members
                                    ),
                                    "failed": 0,
                                    "cancelled": 0,
                                    "pending": 0,
                                    "missing": 0,
                                },
                            }
                        },
                    },
                }
            )
            witness = {
                "submittedAt": sent.isoformat(),
                "observedAt": observed.isoformat(),
                "mode": "standard-hourly",
                "transaction": "REPEATABLE READ READ ONLY",
                "readOnlyConfirmed": "on",
                "runtime": runtime,
                "parentTaskId": parent,
                "barrier": {
                    "ledger_rows": ledger,
                    "latest_rows": latest,
                    "record_rows": records,
                    "node_rows": [
                        {
                            "plantNodeId": "synthetic-node",
                            "algorithmVersion": "test-v",
                            "resultRecordId": node_record_id,
                            **window,
                        }
                    ],
                },
                "celeryTasks": tasks,
                "taskRecord": {
                    "id": tracker,
                    "celeryTaskId": parent,
                    "status": "SUCCESS",
                },
            }
            witness_ref = self.put(folder + "/witness.json", witness)
            resource = {
                "memoryBudgetBytes": 1000,
                "scopeIdentities": ["api", "worker_master", "beat"],
                "samples": [
                    {
                        "at": (sent + timedelta(seconds=s)).isoformat(),
                        "processes": [
                            {"identity": role, "rssBytes": 100}
                            for role in ("api", "worker_master", "beat")
                        ],
                        "workerMasters": 1,
                        "beats": 1,
                    }
                    for s in range(-60, 41, 5)
                ],
                "events": [],
                "rawRefs": [source],
            }
            cache = {
                axis: {
                    "state": "synthetic",
                    "definition": "tool selftest only",
                    "rawRefs": [source],
                }
                for axis in ("input", "precompute", "redis", "process")
            }
            specs.append(
                {
                    "id": folder,
                    "scenario": scenario,
                    "cacheClass": "COLD" if b % 10 < 5 else "HOT",
                    "freeze": self.put(folder + "/freeze.json", freeze),
                    "manifest": manifest_ref,
                    "nodeScopes": self.put(
                        folder + "/node-scopes.json",
                        {
                            "frozenAt": (sent - timedelta(seconds=1)).isoformat(),
                            "complexGroupPolicy": "NONE",
                            "scopes": {"synthetic-node": loop_ids},
                            "rawRefs": [source],
                        },
                    ),
                    "witness": witness_ref,
                    "resources": self.put(folder + "/resource.json", resource),
                    "command": self.put(
                        folder + "/command.json",
                        {
                            "argv": [
                                "python",
                                "kpi_capacity_benchmark.py",
                                "--mode",
                                "standard-hourly",
                            ],
                            "rc": 0,
                            "startedAt": sent.isoformat(),
                            "endedAt": observed.isoformat(),
                            "stdout": source,
                            "stderr": source,
                        },
                    ),
                    "pollLog": self.put(
                        folder + "/polls.json",
                        [
                            {
                                "observedAt": observed.isoformat(),
                                "witnessSha256": witness_ref["sha256"],
                            }
                        ],
                    ),
                    "cacheVector": cache,
                    "rawRefs": [source],
                    "rawLogRefs": [source],
                }
            )
        history = []
        for i in range(9):
            history.append(
                self.put(
                    f"history/{i}.json",
                    {
                        "completionReason": "BARRIER_PASSED"
                        if i < 6
                        else "TERMINAL_MISMATCH",
                        "gate": {"denominators": {"frozenEligible": 1000}},
                    },
                )
            )
        series = {
            "seriesId": "SYNTHETIC-TOOL-SELFTEST-NOT-CAPACITY",
            "candidateSha": SHA,
            "configDigest": "synthetic-config",
            "resourceProfile": self.put(
                "resource-profile.json",
                {
                    "tdVariables": {
                        "queryUseMemoryPool": 0,
                        "minReservedMemorySize": 1024,
                    },
                    "rawRefs": [source],
                },
            ),
            "batches": specs,
            "history": self.put("history.json", {"selectedReports": history}),
            "coldHotDefinitions": source,
        }
        out = Auditor(self.root).run(series)
        self.assertEqual(out["artifactContractVerdict"], "PASS", out["issues"][:4])
        self.assertEqual(out["successfullyParsedBatches"], 20)
        self.assertTrue(all(row["manualChecksPending"] for row in out["batches"]))
        # Read the raw first witness; each mutant receives a fresh file/hash.
        first = json.loads((self.root / specs[0]["witness"]["path"]).read_text())
        mutants = {}
        missing = copy.deepcopy(first)
        missing["barrier"]["ledger_rows"].pop()
        mutants["missing-object"] = missing
        duplicate = copy.deepcopy(first)
        duplicate["barrier"]["ledger_rows"].append(
            duplicate["barrier"]["ledger_rows"][0]
        )
        mutants["duplicate-object"] = duplicate
        wrong_run = copy.deepcopy(first)
        wrong_run["barrier"]["record_rows"][0]["logicalRunId"] = "foreign"
        mutants["foreign-run"] = wrong_run
        wrong_window = copy.deepcopy(first)
        wrong_window["barrier"]["latest_rows"][0]["tsStart"] = start.replace(
            year=2025
        ).isoformat()
        mutants["wrong-latest-window"] = wrong_window
        child_pending = copy.deepcopy(first)
        child_pending["celeryTasks"][1]["state"] = "PENDING"
        mutants["pending-child"] = child_pending
        worker_old = copy.deepcopy(first)
        worker_old["runtime"]["members"][1]["loadedSha"] = "b" * 40
        mutants["stale-worker"] = worker_old
        foreign_node_input = copy.deepcopy(first)
        foreign_node_input["barrier"]["record_rows"][-1]["payload"]["loopRecordIds"][0][
            "recordId"
        ] = "foreign-record"
        mutants["foreign-node-input-record"] = foreign_node_input
        for name, value in mutants.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(specs[0])
                candidate["witness"] = self.put(f"mutants/{name}.json", value)
                candidate["pollLog"] = self.put(
                    f"mutants/{name}-polls.json",
                    [
                        {
                            "observedAt": value["observedAt"],
                            "witnessSha256": candidate["witness"]["sha256"],
                        }
                    ],
                )
                auditor = Auditor(self.root)
                checked = auditor.batch(candidate, series)
                self.assertEqual(checked["verdict"], "REWORK", auditor.issues[:3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
