"""Finite saved prefix and configuration comparison; no live process/DB/API calls."""
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import traceback
from zoneinfo import ZoneInfo

D = Path(__file__).resolve().parent
E = D.parents[1]
F = E / "f02/series-s01/config-freeze-bc48701e"
OLD = E / "f02/synthetic-capacity-binding-actual-root-v1/persisted/raw"
M = "bc48701efe90e2a27555a4f8794f0965e82b0902"

def load(path):
    return json.loads(path.read_text())

def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

def canonical_rows(rows):
    output = []
    for original in rows:
        row = dict(original)
        for key, value in row.items():
            if value is not None and isinstance(value, str) and (key.endswith("_at") or key in ("valid_from", "valid_to")):
                timestamp = datetime.fromisoformat(value)
                assert timestamp.tzinfo is not None
                row[key] = timestamp.astimezone(UTC).isoformat()
        output.append(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return sorted(output)

def verify():
    frozen = load(F / "config-freeze.json")
    assert frozen["status"] == "FROZEN" and frozen["configRevision"] == 3 and frozen["candidateSha"] == M
    assert frozen["configDigest"] == "374f9964e62d6ae784403e664861c940a74df63b1c819c317a1a3af380eed2c9"
    independent = load(D / "configuration-independent.json")
    assert independent["verdict"] == "PASS" and independent["afterFullRawArchived"] and independent["perLoopEffectivePairsRecomputed"] == 36000
    table_facts = []
    for old in sorted(OLD.glob("*.json")):
        table = old.stem
        before, after = F / "pg-raw" / old.name, F / "pg-after-raw" / old.name
        assert canonical_rows(load(old)) == canonical_rows(load(before)) == canonical_rows(load(after)), table
        table_facts.append({"table": table, "rows": len(load(old)), "s01Persisted": ref(old), "freezeBefore": ref(before), "freezeAfter": ref(after), "allRowFactsUnchanged": True})
    assert len(table_facts) == 25
    assert next(row["rows"] for row in table_facts if row["table"] == "loop_tag_binding_history") == 7000
    plan = load(D / "native-prefix/plan.json")
    rows = [json.loads(raw) for raw in (D / "native-prefix/observations.jsonl").read_bytes().splitlines()]
    assert len(rows) >= 2 and plan["expectedSha"] == M and plan["toolSha256"] == "f9ef43b64c5b3deeed24d8429f6a5d2eaaf96ccd40c3e41a99ddceff26a245f4"
    stable = None
    gaps = []
    refs = {}
    for index, row in enumerate(rows):
        assert row["sequence"] == index and row["observationValid"] is True
        assert row["sourceShaObserved"] == M and row["backendTreeObserved"] == "e6e5fd28bcba319f674281a1704e0a6c86764332"
        assert row["nativeEnvSha256Observed"] == plan["nativeEnvSha256"] and row["taosConfigSha256Observed"] == plan["taosConfigSha256"]
        assert row["psCommand"]["rc"] == row["gitCommand"]["rc"] == 0 and row["psCommand"]["stderr"] == row["gitCommand"]["stderr"] == ""
        assert row["gitCommand"]["stdout"].splitlines() == [M, row["backendTreeObserved"]]
        members = row["members"]
        assert len(members) == 7 and len({x["pid"] for x in members}) == 7
        assert Counter(x["role"] for x in members) == {"api": 1, "beat": 1, "worker_master": 1, "worker_child": 4}
        roles = {role: [x for x in members if x["role"] == role] for role in ("api", "beat", "worker_master", "worker_child")}
        api, beat, master = (roles[role][0] for role in ("api", "beat", "worker_master"))
        assert api["pid"] == 90715 and beat["pid"] == 90836 and beat["state"].startswith("T")
        assert beat["ppid"] == master["ppid"] == api["pid"] and all(child["ppid"] == master["pid"] for child in roles["worker_child"])
        identities = [(x["pid"], x["ppid"], x["startedAtLocal"], x["identity"], x["argv"], x["role"]) for x in members]
        if stable is None:
            stable = identities
        assert identities == stable
        assert set(row["selectedRawPsLines"]) == {x["rawLine"] for x in members}
        for member in members:
            parts = member["rawLine"].split(None, 8)
            assert [int(parts[0]), int(parts[1])] == [member["pid"], member["ppid"]]
            assert " ".join(parts[2:7]) == member["startedAtLocal"] and parts[7] == member["state"] and parts[8] == member["argv"]
            assert member["identity"] == str(member["pid"]) + ":" + member["startedAtLocal"]
            birth = datetime.strptime(member["startedAtLocal"], "%a %b %d %H:%M:%S %Y").replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(UTC)
            assert birth <= datetime.fromisoformat(row["psCommand"]["startedAt"])
        if index:
            gap = row["monotonicSeconds"] - rows[index - 1]["monotonicSeconds"]
            assert 0 < gap <= 5 and abs(gap - row["previousPollGapSeconds"]) < 0.1
            gaps.append(gap)
        for source in row["firstSeenLsofRefs"]:
            path = Path(source["path"])
            assert ref(path)["sha256"] == source["sha256"]
            target = D / "native-prefix/first-seen-lsof" / path.name
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                target.write_bytes(path.read_bytes())
            assert ref(target)["sha256"] == source["sha256"]
            refs[str(path)] = target
    assert refs
    mapped = {}
    for path in refs.values():
        value = load(path)
        call = value["actualLsof"]
        assert call["rc"] == 0 and call.get("stderr", "") == ""
        assert {x["identity"] for x in value["members"]} == {x[3] for x in stable}
        current, field = None, None
        for line in call["stdout"].splitlines():
            if line.startswith("p"):
                current = int(line[1:]); mapped.setdefault(current, {"cwd": [], "txt": []})
            elif line.startswith("f"):
                field = line[1:]
            elif line.startswith("n") and field in ("cwd", "txt"):
                mapped[current][field].append(line[1:])
    assert set(mapped) == {x[0] for x in stable}
    exe = "/Users/zhangping/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12"
    for item in mapped.values():
        assert item["cwd"] == ["/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend"] and exe in item["txt"]
    return {"verdict": "PASS_FINITE_CONFIG_AND_NATIVE_PREFLIGHT", "tablesSameAsS01Persisted": table_facts, "all25TablesUnchanged": True, "history7000NoFurtherChanges": True, "polls": len(rows), "from": rows[0]["psCommand"]["startedAt"], "through": rows[-1]["psCommand"]["finishedAt"], "maxGapSeconds": max(gaps), "sevenBirthIdentities": stable, "allRawPsAndGitRc0": True, "allSevenCwdAndMappedExeConfirmed": True, "mappedExe": exe, "lsofCopies": [ref(x) for x in refs.values()], "loadedAlgorithmMemoryClaim": False, "newLiveDbDockerApiOrProcessCommands": 0, "futureSeriesAccepted": False, "formalReadyClaim": False}

if __name__ == "__main__":
    try:
        result = verify()
    except Exception as exc:
        result = {"verdict": "REWORK", "error": str(exc), "traceback": traceback.format_exc()}
    (D / "finite-prefix-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in ("tablesSameAsS01Persisted", "sevenBirthIdentities")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["verdict"].startswith("PASS_") else 1)
