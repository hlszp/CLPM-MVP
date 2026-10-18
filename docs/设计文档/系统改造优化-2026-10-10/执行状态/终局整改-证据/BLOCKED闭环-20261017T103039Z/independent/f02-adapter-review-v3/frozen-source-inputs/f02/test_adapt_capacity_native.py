"""Pure original-file native birth controls; no ps/lsof/service is executed."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import shlex

import pytest

from adapt_capacity_artifacts import (
    Adapter,
    MissingEvidence,
    NATIVE_PS_ARGV,
    NATIVE_SCHEMA,
    canonical,
    closed_worker_pool,
    digest,
    native_span,
)
from test_adapt_capacity_artifacts import pool_fixture


def native_capture(root, members, file):
    """All contents are synthetic fixtures, not host/runtime evidence."""
    api = next(row for row in members if row["role"] == "api")
    launch = {
        "candidate_sha": "a" * 40,
        "backend_tree": "b" * 40,
        "api_pid": api["pid"],
        "cwd": "/synthetic/backend",
        "command": shlex.split(api["argv"]),
        "env_sha256": "synthetic-env",
        "started_at": datetime(
            2026, 10, 17, 6, 59, 59, 900000, tzinfo=timezone.utc
        ).timestamp(),
        "ready_at": datetime(2026, 10, 17, 7, 0, 2, tzinfo=timezone.utc).timestamp(),
        "syntheticFixtureOnly": True,
    }
    launch_ref = file("native-launch.json", launch)
    sampler_ref = file("synthetic-native-tool.json", {"syntheticToolOnly": True})
    interpreter_ref = file(
        "native-interpreter.json",
        {
            "at": "2026-10-17T07:59:00+00:00",
            "argv0": launch["command"][0],
            "resolved": "/synthetic/python3.12",
            "resolvedSha256": "c" * 64,
            "resolvedBytes": 49968,
            "runtimeHead": "a" * 40,
        },
    )
    plan = {
        "schema": NATIVE_SCHEMA,
        "expectedSha": "a" * 40,
        "runtime": "/synthetic",
        "psArgv": NATIVE_PS_ARGV,
        "intervalSeconds": 2,
        "maximumAcceptedIntervalSeconds": 5,
        "toolSha256": sampler_ref["sha256"],
        "nativeEnvSha256": "synthetic-env",
        "taosConfigSha256": "synthetic-taos",
        "timezoneSymlink": "/synthetic/zoneinfo/Asia/Shanghai",
        "timezoneRaw": {"rc": 0, "stdout": "CST +0800\n"},
    }
    native_plan = file("native/plan.json", plan)
    observations = []
    start = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
    for index in range(16):
        at = start + timedelta(seconds=2 * index)
        selected = []
        for row in members:
            raw = f"{row['pid']} {row['ppid']} {row['startedAtLocal']} S {row['argv']}"
            selected.append(
                {
                    **deepcopy(row),
                    "state": "S",
                    "rawLine": raw,
                    "identity": f"{row['pid']}:{row['startedAtLocal']}",
                }
            )
        observation = {
            "schema": NATIVE_SCHEMA,
            "sequence": index,
            "observedAt": at.isoformat(),
            "monotonicSeconds": 10000 + 2 * index,
            "previousPollGapSeconds": 2 if index else None,
            "psCommand": {
                "argv": NATIVE_PS_ARGV,
                "rc": 0,
                "startedAt": (at - timedelta(milliseconds=10)).isoformat(),
                "finishedAt": at.isoformat(),
                "stderr": "",
            },
            "members": selected,
            "selectedRawPsLines": [r["rawLine"] for r in selected],
            "sourceShaObserved": "a" * 40,
            "backendTreeObserved": "b" * 40,
            "nativeEnvSha256Observed": "synthetic-env",
            "taosConfigSha256Observed": "synthetic-taos",
            "gitCommand": {
                "argv": [
                    "git",
                    "-C",
                    "/synthetic",
                    "rev-parse",
                    "HEAD",
                    "HEAD:backend",
                ],
                "rc": 0,
                "stdout": "a" * 40 + "\n" + "b" * 40 + "\n",
                "startedAt": (at + timedelta(milliseconds=1)).isoformat(),
                "finishedAt": (at + timedelta(milliseconds=2)).isoformat(),
            },
            "firstSeenLsofRefs": [],
            "syntheticFixtureOnly": True,
        }
        if index == 0:
            lsof = file(
                "native/lsof-first.json",
                {
                    "members": selected,
                    "actualLsof": {
                        "argv": [
                            "lsof",
                            "-a",
                            "-p",
                            ",".join(str(r["pid"]) for r in members),
                            "-d",
                            "cwd,txt",
                            "-Fn",
                        ],
                        "rc": 0,
                        "startedAt": at.isoformat(),
                        "finishedAt": (at + timedelta(milliseconds=40)).isoformat(),
                        "stdout": "".join(
                            f"p{r['pid']}\nfcwd\nn/synthetic/backend\nftxt\nn/synthetic/python3.12\n"
                            for r in members
                        ),
                        "stderr": "",
                    },
                },
            )
            observation["firstSeenLsofRefs"] = [
                {"path": str(root / "native/lsof-first.json"), "sha256": lsof["sha256"]}
            ]
        observations.append(observation)
    observations_path = root / "native/observations.jsonl"
    observations_path.parent.mkdir(parents=True, exist_ok=True)
    observations_raw = b"".join(canonical(row) + b"\n" for row in observations)
    observations_path.write_bytes(observations_raw)
    summary_ref = file(
        "native/summary.json",
        {
            "schema": NATIVE_SCHEMA,
            "expectedSha": "a" * 40,
            "stopReason": "requested-stop",
            "errors": [],
            "pollCount": len(observations),
            "planSha256": native_plan["sha256"],
            "observations": {
                "path": str(observations_path),
                "bytes": len(observations_raw),
                "sha256": digest(observations_raw),
            },
        },
    )
    return {
        "launch": launch_ref,
        "summary": summary_ref,
        "sampler": sampler_ref,
        "interpreter": interpreter_ref,
    }


def native_fixture(tmp_path):
    adapter = Adapter(tmp_path, tmp_path / "sidecars")

    def file(name, body):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(body))
        return adapter.ref(path)

    runtime, resources = pool_fixture()
    capture = native_capture(tmp_path, runtime["members"], file)
    for row in runtime["members"]:
        row["identity"] = f"{row['pid']}:{row['startedAtLocal']}"
        row["startedAt"] = "2026-10-17T07:00:00+00:00"
    runtime.update(
        nativeLaunchOriginal=adapter.json(capture["launch"]),
        nativeLaunchRef=capture["launch"],
    )
    bundle = adapter.native_bundle(
        capture["summary"],
        capture["sampler"],
        capture["interpreter"],
        {"envFileSha256": "synthetic-env", "taosConfigSha256": "synthetic-taos"},
        runtime,
    )
    span = (
        datetime(2026, 10, 17, 8, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 17, 8, 0, 21, tzinfo=timezone.utc),
    )
    return adapter, runtime, resources, bundle, span


def test_native_originals_close_actual_birth_and_typed_cwd_interpreter_span(tmp_path):
    adapter, runtime, resources, bundle, span = native_fixture(tmp_path)
    proof = native_span(runtime, bundle, span)
    assert proof["selectedFirstSequence"] == 0 and proof["selectedLastSequence"] == 11
    assert len(proof["firstSeenLsof"]) == 7
    assert (
        closed_worker_pool(runtime, resources)["kind"]
        == "rawResourceProcessAncestryOnly"
    )
    adapter.reads.verify_unchanged()


@pytest.mark.parametrize(
    "defect",
    [
        "new-birth-same-pid",
        "member-raw-mismatch",
        "missing-pid",
        "extra-pid",
        "sample-gap",
        "source-drift",
        "env-drift",
        "tree-drift",
        "wrong-cwd",
        "wrong-interpreter",
        "ps-error",
        "git-error",
        "native-utc-naive",
        "monotonic-reset",
        "untyped-lsof",
        "lsof-error",
        "lsof-missing-pid",
        "first-lsof-future",
    ],
)
def test_native_identity_defects_cannot_prove_a_closed_execution_pool(tmp_path, defect):
    _, runtime, _, bundle, span = native_fixture(tmp_path)
    sample = bundle["observations"][3]
    if defect == "new-birth-same-pid":
        row = sample["members"][0]
        row["startedAtLocal"] = "Sat Oct 17 15:59:59 2026"
        row["identity"] = f"{row['pid']}:{row['startedAtLocal']}"
        row["rawLine"] = (
            f"{row['pid']} {row['ppid']} {row['startedAtLocal']} S {row['argv']}"
        )
        sample["selectedRawPsLines"][0] = row["rawLine"]
    elif defect == "member-raw-mismatch":
        sample["members"][0]["startedAtLocal"] = "Sat Oct 17 15:59:59 2026"
    elif defect == "missing-pid":
        sample["members"].pop()
        sample["selectedRawPsLines"].pop()
    elif defect == "extra-pid":
        row = deepcopy(sample["members"][-1])
        row["pid"] = 999
        row["identity"] = f"999:{row['startedAtLocal']}"
        row["rawLine"] = f"999 {row['ppid']} {row['startedAtLocal']} S {row['argv']}"
        sample["members"].append(row)
        sample["selectedRawPsLines"].append(row["rawLine"])
    elif defect == "sample-gap":
        sample["observedAt"] = "2026-10-17T08:00:12+00:00"
        sample["psCommand"]["finishedAt"] = sample["observedAt"]
    elif defect in {"source-drift", "env-drift", "tree-drift"}:
        key = {
            "source-drift": "sourceShaObserved",
            "env-drift": "nativeEnvSha256Observed",
            "tree-drift": "backendTreeObserved",
        }[defect]
        sample[key] = "forged"
    elif defect == "ps-error":
        sample["psCommand"]["rc"] = 2
    elif defect == "git-error":
        sample["gitCommand"]["rc"] = 1
    elif defect == "native-utc-naive":
        sample["observedAt"] = sample["observedAt"].replace("+00:00", "")
        sample["psCommand"]["finishedAt"] = sample["observedAt"]
    elif defect == "monotonic-reset":
        sample["monotonicSeconds"] = 0
    elif defect in {
        "untyped-lsof",
        "lsof-error",
        "lsof-missing-pid",
        "first-lsof-future",
    }:
        raw = bundle["lsof"][0]["body"]["actualLsof"]
        if defect == "untyped-lsof":
            raw["stdout"] = raw["stdout"].replace("fcwd\n", "")
        elif defect == "lsof-error":
            raw["rc"] = 1
        elif defect == "lsof-missing-pid":
            raw["stdout"] = raw["stdout"].split("p106\n")[0]
        else:
            raw["finishedAt"] = "2026-10-17T08:00:01.001+00:00"
    else:
        raw = bundle["lsof"][0]["body"]["actualLsof"]
        raw["stdout"] = raw["stdout"].replace(
            "/synthetic/backend" if defect == "wrong-cwd" else "/synthetic/python3.12",
            "/different",
        )
    with pytest.raises(MissingEvidence):
        native_span(runtime, bundle, span)


def test_missing_external_native_ref_is_blocked_not_inferred_from_state_columns(
    tmp_path,
):
    adapter = Adapter(tmp_path, tmp_path / "sidecars")
    with pytest.raises(MissingEvidence, match="summary reference missing"):
        adapter.native_bundle(None, None, None, {}, {})


def test_real_stat_change_alone_is_not_a_new_birth_identity(tmp_path):
    _, runtime, _, bundle, span = native_fixture(tmp_path)
    sample = bundle["observations"][3]
    row = sample["members"][-1]
    row["state"] = "R"
    row["rawLine"] = row["rawLine"].replace(" S ", " R ")
    sample["selectedRawPsLines"][-1] = row["rawLine"]
    proof = native_span(runtime, bundle, span)
    assert proof["checkedNativeBirthSamples"] == 12


def test_transition_outside_a_real_business_span_is_retained_without_splicing_it_in(
    tmp_path,
):
    _, runtime, _, bundle, span = native_fixture(tmp_path)
    sample = bundle["observations"][-1]
    row = sample["members"][0]
    row["startedAtLocal"] = "Sat Oct 17 16:00:29 2026"
    row["identity"] = f"{row['pid']}:{row['startedAtLocal']}"
    row["rawLine"] = (
        f"{row['pid']} {row['ppid']} {row['startedAtLocal']} S {row['argv']}"
    )
    sample["selectedRawPsLines"][0] = row["rawLine"]
    proof = native_span(runtime, bundle, span)
    assert proof["selectedLastSequence"] == 11
    assert bundle["observations"][-1]["members"][0]["identity"] == row["identity"]
