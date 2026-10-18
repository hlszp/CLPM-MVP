"""Disk-only lossless sidecars for the frozen independent capacity checker.

No network, DB/Redis/client, subprocess, product calculator, or product gate import.
Original rows and references remain recoverable. This tool never signs acceptance.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "independent"))
from independent_support import (  # noqa: E402
    InvalidEvidence,
    MissingEvidence,
    ReadSet,
    instant,
    write_new,
)

PARENT_NAME = "app.tasks.kpi_calc.calculate_hourly_kpi"
NODE_NAME = "app.tasks.kpi_calc.calculate_node_kpi_hourly"
TERMINAL = {"SUCCESS", "FAILURE", "REVOKED"}
ALIASES = {
    "ledger": {
        "loop_id": "loopId",
        "ts_start": "tsStart",
        "ts_end": "tsEnd",
        "source_task_id": "sourceTaskId",
        "result_record_id": "resultRecordId",
        "algorithm_version": "algorithmVersion",
        "fitness_tags": "fitnessTags",
    },
    "latest": {
        "loop_id": "loopId",
        "data_ts_start": "tsStart",
        "data_ts_end": "tsEnd",
        "algorithm_version": "algorithmVersion",
        "result_record_id": "resultRecordId",
    },
    "nodes": {
        "plant_node_id": "plantNodeId",
        "ts_start": "tsStart",
        "ts_end": "tsEnd",
        "algorithm_version": "algorithmVersion",
        "result_record_id": "resultRecordId",
    },
    "records": {
        "id": "recordId",
        "object_kind": "objectKind",
        "object_id": "objectId",
        "logical_run_id": "logicalRunId",
        "ts_start": "tsStart",
        "ts_end": "tsEnd",
        "algorithm_version": "algorithmVersion",
    },
}
PAYLOAD_PROJECTIONS = {
    "payload_fitness_tags": "fitness_tags",
    "payload_snapshot_status": "snapshotStatus",
    "payload_loop_record_ids": "loopRecordIds",
}


def require(value, message):
    if not value:
        raise MissingEvidence(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def unique_index(rows, key):
    require(isinstance(rows, list), f"Original {key} rows missing")
    values = [row.get(key) for row in rows]
    if None in values or len(values) != len(set(values)):
        raise InvalidEvidence(f"Missing/duplicate original {key}; no last-row-wins")
    return {row[key]: row for row in rows}


def score_number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidEvidence("Boolean is not an original numeric score")
    try:
        decimal = Decimal(str(value))
        number = float(decimal)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise InvalidEvidence("Invalid original score") from exc
    if (
        not decimal.is_finite()
        or not math.isfinite(number)
        or Decimal(str(number)) != decimal
    ):
        raise InvalidEvidence(
            "Score cannot be represented without decimal value change"
        )
    return number


def compare_projection(critical, full):
    for key, value in critical.items():
        actual = (
            (full.get("payload") or {}).get(PAYLOAD_PROJECTIONS[key])
            if key in PAYLOAD_PROJECTIONS
            else full.get(key)
        )
        if key not in PAYLOAD_PROJECTIONS and key not in full:
            raise MissingEvidence(
                f"Full immutable row lacks original critical column {key}"
            )
        if actual != value:
            raise InvalidEvidence(
                f"Full immutable record differs from witnessed critical column {key}"
            )


def map_rows(kind, rows, records=None):
    mapped = []
    for row in rows:
        target = deepcopy(row)
        target["originalRow"] = deepcopy(row)
        for source, alias in ALIASES[kind].items():
            require(source in row, f"Original {kind} column missing: {source}")
            if alias in row and row[alias] != row[source]:
                raise InvalidEvidence(f"Conflicting original/aliased field {alias}")
            target[alias] = deepcopy(row[source])
        if kind == "ledger":
            require(
                "score" in row and "status" in row and "source" in row,
                "Original ledger score/status/source missing",
            )
            target["score"] = score_number(row["score"])
        if kind in {"ledger", "latest"}:
            record = (records or {}).get(row.get("result_record_id"))
            require(
                record is not None, "Missing actual immutable record for projection"
            )
            target["recordLogicalRunId"] = record.get("logical_run_id")
            target["recordLogicalRunIdProvenance"] = {
                "foreignKey": row["result_record_id"],
                "source": "Same record ID witnessed in the original PG projection",
            }
        mapped.append(target)
    return mapped


def submit_from_timestamped(rows, window, parent):
    calls = [
        row
        for row in rows
        if isinstance(row.get("line"), str)
        and row["line"].startswith("[submit] calculate_hourly_kpi.delay(")
    ]
    ids = [
        row
        for row in rows
        if isinstance(row.get("line"), str)
        and row["line"].startswith("[submit] parent_task_id=")
    ]
    require(
        len(calls) == len(ids) == 1,
        "Exactly one original pre-submit line and one actual parent ID line required",
    )
    match = re.search(r"ts_start='([^']+)'", calls[0]["line"])
    require(
        match is not None and instant(match[1]) == instant(window["tsStart"]),
        "Actual submit window differs from freeze",
    )
    require(
        ids[0]["line"].strip() == "[submit] parent_task_id=" + parent,
        "Original submit parent differs from PG/task witness",
    )
    submitted = calls[0].get("observedAt")
    require(
        isinstance(submitted, str)
        and datetime.fromisoformat(submitted.replace("Z", "+00:00")).tzinfo is not None,
        "Original pre-submit UTC/offset timestamp absent",
    )
    require(
        instant(submitted) <= instant(ids[0]["observedAt"]),
        "Submission log timestamps reversed",
    )
    return submitted, {
        "preSubmitLine": deepcopy(calls[0]),
        "parentIdLine": deepcopy(ids[0]),
        "meaning": "Original Keeper UTC receipt timestamp of pre-submit stdout line; not an invented exact backend call clock",
    }


def parsed_kwargs(raw):
    if isinstance(raw, dict):
        return deepcopy(raw)
    require(isinstance(raw, str), "Original request kwargs missing")
    try:
        result = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise MissingEvidence(
            "Original kwargs are not a supported literal mapping"
        ) from exc
    require(isinstance(result, dict), "Original kwargs mapping absent")
    return result


def closed_worker_pool(runtime, samples):
    """Derive closed execution-code identity from raw PID lines, not event pid."""
    members = unique_index(runtime.get("members"), "pid")
    roles = Counter(row.get("role") for row in members.values())
    require(
        roles == {"api": 1, "worker_master": 1, "beat": 1, "worker_child": 4},
        "Actual closed seven-process scope is missing",
    )
    master = next(row for row in members.values() if row["role"] == "worker_master")
    api = next(row for row in members.values() if row["role"] == "api")
    require(
        all(row.get("loadedSha") == master.get("loadedSha") for row in members.values())
        and master.get("loadedSha"),
        "Frozen execution pool contains mixed/unknown code identities",
    )
    require(
        isinstance(samples, list) and len(samples) >= 2,
        "Continuous raw PID samples missing",
    )
    previous = None
    for sample in samples:
        at = instant(sample["observedAt"])
        if previous is not None:
            require(
                0 < (at - previous).total_seconds() <= 5,
                "Closed-pool raw sample gap/nonincreasing time",
            )
        previous = at
        observed = {}
        for line in sample.get("ownedProcesses", []):
            fields = line.strip().split(maxsplit=5)
            if len(fields) != 6:
                continue
            pid, ppid, _, _, _, command = fields
            if "-m uvicorn app.main:app" in command:
                role = "api"
            elif "-m celery " in command and " beat " in command:
                role = "beat"
            elif "-m celery " in command and " worker " in command:
                role = "worker_master" if int(ppid) == api["pid"] else "worker_child"
            else:
                continue
            require(
                int(pid) not in observed,
                "Duplicate application PID in raw resource sample",
            )
            observed[int(pid)] = {"role": role, "ppid": int(ppid), "argv": command}
        require(
            set(observed) == set(members),
            "Actual application PID set is missing/extra/recycled",
        )
        for pid, actual in observed.items():
            frozen = members[pid]
            require(
                actual["role"] == frozen["role"]
                and actual["ppid"] == frozen.get("ppid")
                and actual["argv"] == frozen.get("argv"),
                "Actual application process ancestry/argv/role changed",
            )
            if actual["role"] == "worker_child":
                require(
                    actual["ppid"] == master["pid"],
                    "Actual prefork is not a child of the unique master",
                )
            elif actual["role"] in {"worker_master", "beat"}:
                require(
                    actual["ppid"] == api["pid"],
                    "Master/Beat is not owned by this lifespan API",
                )
    return {
        "kind": "derivedFromClosedWorkerPoolIdentity",
        "producerMasterPid": master["pid"],
        "masterArgv": master["argv"],
        "loadedSha": master["loadedSha"],
        "expectedNativePids": sorted(members),
        "poolPids": sorted(
            row["pid"] for row in members.values() if row["role"] == "worker_child"
        ),
        "intervalStart": samples[0]["observedAt"],
        "intervalEnd": samples[-1]["observedAt"],
        "checkedSamples": len(samples),
        "executorPid": None,
        "boundary": "Celery event.pid is the master dispatcher producer, not the prefork executor. Every possible pool member has the same frozen code identity and continuous PID/ancestry/argv samples; actual executor PID remains unknown.",
    }


class Adapter:
    def __init__(self, root, out):
        self.reads = ReadSet(root)
        self.root = Path(root).resolve()
        self.out = Path(out).resolve()
        require(
            self.out.is_relative_to(self.root),
            "Sidecar output must stay within evidence root",
        )
        self.out.mkdir(parents=True, exist_ok=False)
        self.issues = []

    def ref(self, path):
        path = Path(path).resolve()
        require(
            path.is_relative_to(self.root) and path.is_file(),
            "Reference must be an existing evidence-root file",
        )
        return {
            "path": str(path.relative_to(self.root)),
            "sha256": digest(path.read_bytes()),
            "size": path.stat().st_size,
        }

    def json(self, ref):
        return self.reads.json(ref)

    def jsonl(self, ref):
        path = self.reads.path(ref)
        return [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]

    def put(self, relative, value):
        path = self.out / relative
        write_new(path, value)
        return self.ref(path)

    def manifest(self, ref):
        path = self.reads.path(ref)
        body = self.json(ref)
        rows = unique_index(body.get("files"), "file")
        indexed = {}
        for name, row in rows.items():
            target = (path.parent / name).resolve()
            if not target.is_relative_to(path.parent.resolve()):
                raise InvalidEvidence(
                    "Collector/config manifest path escapes its original directory"
                )
            require(isinstance(row.get("bytes"), int), "Original manifest size missing")
            original = {
                "path": str(target.relative_to(self.root)),
                "sha256": row["sha256"],
                "size": row["bytes"],
            }
            self.reads.path(original)
            indexed[name] = original
        return path.parent, indexed

    def object(self, base, indexed, ref):
        require(
            isinstance(ref, dict) and ref.get("file") in indexed,
            "Original object ref absent from exact manifest",
        )
        source = indexed[ref["file"]]
        require(
            source["sha256"] == ref.get("gzipSha256"),
            "Collector object gzip SHA differs from original manifest",
        )
        raw = gzip.decompress(self.reads.path(source).read_bytes())
        if digest(raw) != ref.get("rawSha256"):
            raise InvalidEvidence("Collector decompressed original SHA mismatch")
        result = json.loads(raw)
        if ref.get("rows") is not None and (
            not isinstance(result, list) or len(result) != ref["rows"]
        ):
            raise InvalidEvidence(
                "Collector original array denominator differs from its ref"
            )
        return result

    def config(self, plan):
        base, indexed = self.manifest(plan["configFreezeManifest"])
        config = self.json(plan["configFreeze"])
        require(
            self.reads.path(indexed["config-freeze.json"])
            == self.reads.path(plan["configFreeze"]),
            "Config report identity differs from original manifest",
        )
        require(
            config.get("status") == "FROZEN", "Actual configuration freeze not closed"
        )
        require(
            config.get("candidateSha") == plan.get("candidateSha")
            and config.get("configDigest") == plan.get("configDigest"),
            "Candidate/config identities differ from actual freeze",
        )
        loops = self.json(indexed["pg-raw/loop_ledger.json"])
        tree = self.json(indexed["pg-raw/plant_node.json"])
        mappings = self.json(indexed["pg-raw/loop_tag_mapping.json"])
        loop_map = unique_index(loops, "id")
        node_map = unique_index(tree, "id")
        require(
            len(loops) == 1000
            and len(mappings) == 7000
            and len({r["tag_id"] for r in mappings}) == 7000,
            "Actual frozen1000/7000 identities missing",
        )
        require(
            len({(r["loop_id"], r["tag_role"]) for r in mappings}) == 7000,
            "Duplicate actual frozen point role mapping",
        )
        require(
            {r["loop_id"] for r in mappings} == set(loop_map),
            "Actual point mappings do not cover the frozen1000 IDs",
        )
        roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
        for loop_id in loop_map:
            require(
                {r["tag_role"] for r in mappings if r["loop_id"] == loop_id} == roles,
                "Frozen object lacks exact seven role mappings",
            )
        registry = self.json(indexed["pg-raw/tag_registry.json"])
        require(
            len(registry) == 7000
            and {r["id"] for r in registry} == {r["tag_id"] for r in mappings},
            "Original registered7000 point IDs differ from actual bindings",
        )
        require(
            all(not row.get("complex_loop_group_id") for row in loops),
            "Complex group scope unsupported; no representative guessing",
        )
        scopes = {}
        for row in loops:
            require(
                row.get("is_active")
                and row.get("status") == "READY"
                and row.get("include_in_evaluation"),
                "Frozen object not eligible",
            )
            node_id, visited = row.get("unit_id"), set()
            while node_id:
                require(
                    node_id not in visited and node_id in node_map,
                    "Frozen tree cycle/missing ancestor",
                )
                visited.add(node_id)
                node = node_map[node_id]
                if node.get("is_kpi_enabled"):
                    scopes.setdefault(node_id, []).append(row["id"])
                node_id = node.get("parent_id")
        require(
            len(scopes) == 20
            and set(scopes) == {row["id"] for row in tree if row.get("is_kpi_enabled")},
            "Actual frozen enabled capacity node denominator must remain20",
        )
        source_ref = indexed["sources/app/tasks/kpi_calc.py"]
        source = self.reads.path(source_ref).read_text()
        parsed = ast.parse(source)
        function = next(
            (
                n
                for n in parsed.body
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "_do_calculate_node_kpi"
            ),
            None,
        )
        require(function is not None, "Frozen actual node dispatcher function missing")
        expected = ast.dump(
            ast.parse(
                "select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True))",
                mode="eval",
            ).body,
            include_attributes=False,
        )
        require(
            any(
                isinstance(n, ast.Call)
                and ast.dump(n, include_attributes=False) == expected
                for n in ast.walk(function)
            ),
            "Frozen dispatch is not the explicit all-enabled query; unsupported scope",
        )
        after = self.json(plan["nodeScopeAfter"])
        require(
            after.get("readonly") == "on"
            and after.get("isolation") == "repeatable read"
            and after.get("snapshot"),
            "Actual post-scope RR read-only transaction proof missing",
        )
        after_loops = unique_index(after.get("loops"), "id")
        after_tree = unique_index(after.get("tree"), "id")
        require(
            set(after_loops) == set(loop_map) and set(after_tree) == set(node_map),
            "Post-scope actual IDs drifted",
        )
        for identity, original in loop_map.items():
            require(
                all(
                    k in after_loops[identity]
                    for k in ("unit_id", "complex_loop_group_id")
                ),
                "Scope-after loop fields missing; absent is not actual NULL",
            )
            require(
                all(
                    after_loops[identity].get(k) == original.get(k)
                    for k in ("unit_id", "complex_loop_group_id")
                ),
                "Actual loop descendant/group scope changed",
            )
        for identity, original in node_map.items():
            require(
                all(k in after_tree[identity] for k in ("parent_id", "is_kpi_enabled")),
                "Scope-after node fields missing; absent is not actual NULL",
            )
            require(
                all(
                    after_tree[identity].get(k) == original.get(k)
                    for k in ("parent_id", "is_kpi_enabled")
                ),
                "Actual enabled-node scope changed",
            )
        self.reads.path(plan["configurationIsolation"])
        scope_ref = self.put(
            "node-scopes.json",
            {
                "frozenAt": config["finishedAt"],
                "complexGroupPolicy": "NONE",
                "scopes": {k: sorted(v) for k, v in scopes.items()},
                "rawRefs": [
                    indexed["pg-raw/loop_ledger.json"],
                    indexed["pg-raw/plant_node.json"],
                    indexed["pg-raw/loop_tag_mapping.json"],
                    plan["nodeScopeAfter"],
                    plan["configurationIsolation"],
                ],
            },
        )
        return {
            "config": config,
            "indexed": indexed,
            "loops": loop_map,
            "nodes": node_map,
            "scopes": scopes,
            "scopeRef": scope_ref,
            "querySourceRef": source_ref,
            "scopeAfter": after,
        }

    def runtime(self, raw, plan, config, td_ref):
        runtime = deepcopy(raw["runtime"])
        roles = Counter(row.get("role") for row in runtime.get("members", []))
        require(
            roles == {"api": 1, "worker_master": 1, "beat": 1, "worker_child": 4},
            "Actual runtime complete seven-PID identity missing",
        )
        require(
            raw.get("envFileSha256") == config["envFileSha256"],
            "Runtime environment identity differs from config freeze",
        )
        for member in runtime["members"]:
            require(
                member.get("loadedSha") == plan["candidateSha"],
                "Actual runtime candidate SHA differs",
            )
            require(
                member.get("identity")
                and member.get("argv")
                and type(member.get("pid")) is int,
                "Actual runtime process identity incomplete",
            )
            if not member.get("startedAt"):
                require(
                    plan.get("runtimeStartTimezone") == "Asia/Shanghai",
                    "Runtime local start conversion requires explicit frozen Asia/Shanghai convention",
                )
                try:
                    local = datetime.strptime(
                        member["startedAtLocal"], "%a %b %d %H:%M:%S %Y"
                    ).replace(tzinfo=ZoneInfo(plan["runtimeStartTimezone"]))
                except (KeyError, ValueError) as exc:
                    raise MissingEvidence(
                        "Actual runtime local start timestamp unsupported"
                    ) from exc
                member["startedAt"] = local.astimezone(timezone.utc).isoformat()
                member["startedAtProvenance"] = {
                    "originalLocal": member["startedAtLocal"],
                    "timezone": plan["runtimeStartTimezone"],
                }
        runtime.update(
            schemaRevision=raw.get("schemaRevision"),
            configDigest=config["configDigest"],
            postgresDatabase=raw.get("postgres", {}).get("database"),
            brokerNamespace=f"redis://{plan['redisHost']}:{plan['redisPort']}/1",
            tdDnodeVariables=td_ref,
        )
        require(
            runtime.get("schemaRevision") and runtime.get("postgresDatabase"),
            "Actual runtime schema/database absent",
        )
        return runtime

    def td_responses(self, raw, name):
        pieces = raw.get("tdResponses")
        require(
            isinstance(pieces, list) and pieces,
            "Original TD dnode HTTP responses missing",
        )
        first = pieces[0]["response"]
        columns = first.get("column_meta")
        combined = []
        for piece in pieces:
            body = piece.get("response", {})
            require(
                piece.get("httpStatus") == 200
                and body.get("code") == 0
                and body.get("column_meta") == columns,
                "Actual TD variable query failed or schema differs",
            )
            require(
                type(body.get("rows")) is int
                and body["rows"] == len(body.get("data", [])),
                "Original TD variable rows mismatch",
            )
            combined.extend(deepcopy(body["data"]))
        require(
            len({row[1] for row in combined}) == len(combined),
            "Duplicate actual dnode variable rows",
        )
        response = deepcopy(first)
        response.update(data=combined, rows=len(combined))
        return self.put(
            name,
            {
                "response": response,
                "originalResponses": deepcopy(pieces),
                "boundary": "Lossless concatenation of original separately timed HTTP responses; not a single atomic TD query",
            },
        )

    def snapshots(self, raw_base, indexed, observations):
        immutable = {}
        for name in (
            "first-terminal-full-payloads.json",
            "first-complete-full-payloads.json",
            "final-full-payloads.json",
        ):
            if name not in indexed:
                continue
            snapshot = self.json(indexed[name])
            rows = self.object(raw_base, indexed, snapshot["artifacts"]["records"])
            for identity, row in unique_index(rows, "id").items():
                require(
                    isinstance(row.get("payload"), dict),
                    "Full actual immutable payload absent",
                )
                if identity in immutable and canonical(
                    immutable[identity]
                ) != canonical(row):
                    raise InvalidEvidence(
                        "An immutable record changed between original full captures"
                    )
                immutable[identity] = row
        require(immutable, "No complete immutable raw payload originals")
        return immutable

    def observation_arrays(self, raw_base, indexed, observation, immutable):
        arrays = {
            name: self.object(raw_base, indexed, observation["artifacts"][name])
            for name in ("ledger", "latest", "nodes", "records")
        }
        records = []
        for critical in arrays["records"]:
            full = immutable.get(critical.get("id"))
            require(
                full is not None, "Witnessed record has no recoverable full original"
            )
            compare_projection(critical, full)
            require(
                full.get("created_at") is not None
                and instant(full["created_at"])
                <= instant(observation["pgTransaction"]["observed_at"]),
                "Full immutable record was not yet created at its original PG witness",
            )
            records.append(deepcopy(full))
        arrays["records"] = records
        used = []
        for key in ("firstTerminalFullPayloads", "firstCompleteFullPayloads"):
            if key not in observation:
                continue
            snapshot = observation[key]
            require(
                snapshot.get("poll") == observation["poll"]
                and snapshot.get("transaction") == observation["pgTransaction"],
                "Full arrays are not from the same original observation transaction",
            )
            require(
                all(
                    snapshot.get(k) == observation.get(k)
                    for k in (
                        "parentTaskId",
                        "pgStartedAt",
                        "pgReadCompletedAt",
                        "pgEndedAt",
                        "taskObservedAt",
                    )
                ),
                "Full snapshot timing/parent differs from original observation",
            )
            for kind, identity in (
                ("ledger", "loop_id"),
                ("latest", "loop_id"),
                ("nodes", "plant_node_id"),
                ("records", "id"),
            ):
                fullrows = self.object(raw_base, indexed, snapshot["artifacts"][kind])
                criticalrows = self.object(
                    raw_base, indexed, observation["artifacts"][kind]
                )
                fullmap = unique_index(fullrows, identity)
                criticalmap = unique_index(criticalrows, identity)
                require(
                    set(fullmap) == set(criticalmap),
                    "Same-transaction full/critical original IDs differ",
                )
                for rid, row in criticalmap.items():
                    compare_projection(row, fullmap[rid])
                arrays[kind] = fullrows
            used.append(key)
        return arrays, {
            "sameObservationFullCaptures": used,
            "immutableFullOnlySupplement": not bool(used),
            "boundary": "Later immutable payloads supplement only the identical witnessed record ID after every original critical field matches and created_at precedes the PG witness; mutable later latest/node/ledger projections are never copied",
        }

    def log_refs(self, indexed, observations):
        refs = []
        previous = None
        for observation in observations:
            info = observation.get("workerLog") or observation.get("finalLog")
            require(
                isinstance(info, dict) and info.get("available") is not False,
                "Original worker log coverage unavailable",
            )
            require(
                all(
                    k in info
                    for k in (
                        "source",
                        "inode",
                        "offsetStart",
                        "offsetEnd",
                        "bytes",
                        "sha256",
                        "pendingBytes",
                        "rotated",
                    )
                ),
                "Original worker log coverage metadata incomplete",
            )
            require(
                info["pendingBytes"] == 0 and info["rotated"] is False,
                "Pending/rotated original log requires explicit recovery",
            )
            name = info.get("file")
            if name:
                require(
                    name in indexed,
                    "Referenced original worker log absent from manifest",
                )
                raw = self.reads.path(indexed[name]).read_bytes()
                refs.append(indexed[name])
            else:
                raw = b""
            require(
                digest(raw) == info["sha256"]
                and len(raw)
                == info["bytes"]
                == info["offsetEnd"] - info["offsetStart"],
                "Original worker log hash/size/range mismatch",
            )
            if previous:
                require(
                    info["source"] == previous["source"]
                    and info["inode"] == previous["inode"]
                    and info["offsetStart"] == previous["offsetEnd"],
                    "Gap/changed source in actual worker log timeline",
                )
            previous = info
        require(refs, "Formal worker log has no recoverable nonempty segment")
        return list({ref["path"]: ref for ref in refs}.values())

    def tasks(
        self,
        events,
        redis_state,
        parent,
        runtime,
        window,
        observed,
        scope,
        source_refs,
        pool_proof,
    ):
        requests = {}
        for envelope in events:
            event = envelope.get("event", {})
            if event.get("type") != "task-received":
                continue
            identity = event.get("uuid")
            if (
                identity == parent
                or event.get("root_id") == parent
                or event.get("parent_id") == parent
            ):
                if identity in requests:
                    raise MissingEvidence(
                        "Repeated actual request/attempt needs explicit review; not silently deduplicated"
                    )
                requests[identity] = deepcopy(envelope)
        require(parent in requests, "Actual parent request event missing")
        node_requests = [
            r for r in requests.values() if r["event"].get("name") == NODE_NAME
        ]
        require(
            len(node_requests) == 1,
            "Exactly one actual same-parent standard-node child request required",
        )
        terminal_meta = redis_state.get("taskMeta", {})
        tasks = []
        require(
            pool_proof.get("kind") == "derivedFromClosedWorkerPoolIdentity",
            "Original closed execution pool proof missing",
        )
        for identity, envelope in requests.items():
            request = envelope["event"]
            meta = terminal_meta.get(identity)
            require(
                isinstance(meta, dict) and meta.get("status") in TERMINAL,
                "Parent/required child backend not terminal at original task observation",
            )
            require(
                meta.get("date_done") is not None
                and instant(meta["date_done"]) <= instant(observed),
                "Original task finish time absent/later than PG witness; use a later actual poll",
            )
            starts = [
                e
                for e in events
                if e.get("event", {}).get("type") == "task-started"
                and e["event"].get("uuid") == identity
            ]
            require(
                len(starts) == 1
                and starts[0]["event"].get("pid") == pool_proof["producerMasterPid"]
                and request.get("pid") == pool_proof["producerMasterPid"],
                "Actual request/start event producer is not the unique frozen worker master",
            )
            host = request.get("hostname")
            require(
                isinstance(host, str)
                and "@" in host
                and starts[0]["event"].get("hostname") == host,
                "Actual event hostname is missing/inconsistent",
            )
            master_argv = shlex.split(pool_proof["masterArgv"])
            require(
                "--hostname" in master_argv
                and master_argv.index("--hostname") + 1 < len(master_argv),
                "Frozen master hostname argv missing",
            )
            declared_host = master_argv[master_argv.index("--hostname") + 1]
            require(
                declared_host.replace("%h", host.split("@", 1)[1]) == host,
                "Actual event hostname differs from frozen master",
            )
            event_times = []
            for event in (request, starts[0]["event"]):
                stamp = event.get("timestamp")
                require(
                    isinstance(stamp, (int, float))
                    and not isinstance(stamp, bool)
                    and math.isfinite(stamp),
                    "Original Celery producer timestamp missing",
                )
                event_times.append(datetime.fromtimestamp(stamp, tz=timezone.utc))
            require(
                instant(pool_proof["intervalStart"])
                <= event_times[0]
                <= event_times[1]
                <= instant(meta["date_done"])
                <= instant(pool_proof["intervalEnd"]),
                "Actual task interval is not enclosed by continuous pool identity evidence",
            )
            kwargs = parsed_kwargs(request.get("kwargs"))
            require(
                kwargs.get("ts_start") is not None
                and instant(kwargs["ts_start"]) == instant(window["tsStart"]),
                "Actual task request is for another window",
            )
            if identity != parent:
                require(
                    request.get("parent_id") == parent
                    and request.get("root_id") == parent,
                    "Actual node parent/root chain differs",
                )
                require(
                    kwargs.get("ts_end") is not None
                    and instant(kwargs["ts_end"]) == instant(window["tsEnd"]),
                    "Actual node request end differs",
                )
            task = {
                "id": identity,
                "name": request.get("name"),
                "state": meta["status"],
                "finishedAt": meta["date_done"],
                "runtimeSha": pool_proof["loadedSha"],
                "runtimeShaProvenance": {
                    **deepcopy(pool_proof),
                    "eventProducerPid": starts[0]["event"]["pid"],
                    "executorPid": None,
                    "rawRefs": source_refs,
                },
                "parentId": request.get("parent_id"),
                "rootId": request.get("root_id"),
                "actualRequest": deepcopy(request),
                "actualStartedEvent": deepcopy(starts[0]),
                "actualTerminalBackend": deepcopy(meta),
                "rawRefs": source_refs,
            }
            if request.get("name") == NODE_NAME:
                result = meta.get("result")
                require(
                    isinstance(result, dict)
                    and result.get("total") == result.get("success") == 20
                    and result.get("skipped") == result.get("failed") == 0,
                    "Actual node task did not report full20 success with0 skipped/failed",
                )
                task["nodeIds"] = sorted(scope["scopes"])
                task["nodeScopeProvenance"] = {
                    "kind": "derivedFromFrozenEnabledScope",
                    "querySourceRef": scope["querySourceRef"],
                    "enabledNodesSnapshotRef": scope["scopeRef"],
                    "requestEventRefs": source_refs,
                    "window": deepcopy(window),
                    "parentId": request.get("parent_id"),
                    "rootId": request.get("root_id"),
                    "uniqueNodeChildEvidence": {
                        "actualNodeRequestCount": 1,
                        "actualResult": deepcopy(result),
                    },
                    "boundary": "These IDs are explicitly derived from frozen enabled-node scope and stable after-scope evidence; the original request above has no invented node_ids field. All NODE records/rosters/input versions still require independent per-node reconciliation.",
                }
            tasks.append(task)
        require(
            next(t for t in tasks if t["id"] == parent)["name"] == PARENT_NAME,
            "Original parent request is not standard-hourly",
        )
        return tasks

    def batch(self, item, planned, plan, scope, resource_ref):
        label = f"batch-{planned['batch']:02d}"
        raw_base, indexed = self.manifest(item["collectorManifest"])
        bootstrap = self.json(indexed["bootstrap.json"])
        summary = self.json(indexed["collector-summary.json"])
        require(
            summary.get("status") == "COLLECTED",
            "Actual collector did not complete; no capacity substitution",
        )
        require(
            bootstrap.get("redisHost") == plan["redisHost"]
            and bootstrap.get("redisPort") == plan["redisPort"],
            "Actual collector broker namespace differs",
        )
        require(
            {r["id"] for r in bootstrap.get("loops", [])} == set(scope["loops"])
            and len(bootstrap["loops"]) == 1000,
            "Actual collector object IDs differ from frozen1000",
        )
        require(
            set(bootstrap.get("predictedNodes", [])) == set(scope["scopes"])
            and len(bootstrap["predictedNodes"]) == 20,
            "Actual predicted nodes differ from frozen20",
        )
        freeze = self.json(item["freeze"])
        manifest = self.json(item["manifest"])
        require(
            freeze.get("manifest", {}).get("sha256") == item["manifest"]["sha256"],
            "Original benchmark freeze does not bind exact manifest",
        )
        require(
            instant(freeze["window"]["tsStart"]) == instant(planned["ts_start"])
            and instant(freeze["window"]["tsEnd"]) == instant(planned["ts_end"]),
            "Benchmark frozen window differs from original20 plan",
        )
        require(
            freeze.get("algorithmVersion")
            == scope["config"]["versions"]["algorithmVersion"],
            "Benchmark algorithm version differs from fixed configuration",
        )
        rows = freeze.get("eligible", [])
        require(
            len(rows) == len({r.get("loopId") for r in rows}) == 1000
            and not freeze.get("excluded"),
            "Actual original standard1000 eligible denominator missing",
        )
        require(
            {r.get("loopId") for r in rows} == set(scope["loops"]),
            "Original benchmark1000 IDs differ from frozen configuration",
        )
        require(
            set(freeze.get("predictedNodes", [])) == set(scope["scopes"]),
            "Benchmark predicted node IDs differ",
        )
        composition = Counter(r.get("composition") for r in manifest.get("rows", []))
        expected_composition = (
            {"normal": 1000}
            if planned["scenario"] == "a"
            else {"normal": 800, "insufficient": 100, "manual": 50, "gap": 50}
        )
        require(
            len(manifest.get("rows", [])) == 1000
            and composition == expected_composition,
            "Original A/B composition is not full1000",
        )
        require(
            len({r.get("loopTag") for r in manifest["rows"]}) == 1000
            and {r.get("loopTag") for r in manifest["rows"]}
            == {r["tag_name"] for r in scope["loops"].values()},
            "Original manifest1000 unique tag identities differ",
        )
        expectations = Counter(r.get("expectedSnapshotStatus") for r in rows)
        require(
            expectations
            == (
                {"SUCCESS": 1000}
                if planned["scenario"] == "a"
                else {"SUCCESS": 850, "INCONCLUSIVE": 150}
            ),
            "Original expected statuses changed; no denominator adaptation",
        )
        original_observations = self.jsonl(indexed["observations.jsonl"])
        logs = self.log_refs(indexed, original_observations)
        observations = [r for r in original_observations if "poll" in r]
        require(observations, "Original timestamped PG observations missing")
        immutable = self.snapshots(raw_base, indexed, observations)
        runtime_raw = self.json(item["runtimeProfile"])
        td_ref = self.td_responses(runtime_raw, label + "/td-dnode-responses.json")
        runtime = self.runtime(runtime_raw, plan, scope["config"], td_ref)
        pool_proof = closed_worker_pool(runtime, self.jsonl(indexed["resources.jsonl"]))
        events = self.jsonl(item["events"])
        selected = None
        candidate_failures = []
        for observation in observations:
            if (
                item.get("witnessPoll") is not None
                and observation["poll"] != item["witnessPoll"]
            ):
                continue
            try:
                arrays, provenance = self.observation_arrays(
                    raw_base, indexed, observation, immutable
                )
                require(
                    len(unique_index(arrays["ledger"], "loop_id"))
                    == len(unique_index(arrays["latest"], "loop_id"))
                    == 1000
                    and len(unique_index(arrays["nodes"], "plant_node_id")) == 20,
                    "Actual poll does not yet have complete denominator",
                )
                redis_state = self.object(
                    raw_base, indexed, observation["artifacts"]["redis-tasks"]
                )
                parent = observation.get("parentTaskId")
                require(parent, "Actual parent ID missing")
                tasks = self.tasks(
                    events,
                    redis_state,
                    parent,
                    runtime,
                    freeze["window"],
                    observation["pgTransaction"]["observed_at"],
                    scope,
                    [
                        item["events"],
                        indexed[observation["artifacts"]["redis-tasks"]["file"]],
                        item["runtimeProfile"],
                        indexed["resources.jsonl"],
                        *plan["celeryEventSources"],
                    ],
                    pool_proof,
                )
                require(
                    instant(scope["scopeAfter"]["observedAt"])
                    >= instant(observation["pgEndedAt"]),
                    "Scope-after original precedes chosen actual witness",
                )
                selected = (observation, arrays, redis_state, tasks, provenance)
                break
            except MissingEvidence as exc:
                candidate_failures.append(
                    {"poll": observation["poll"], "reason": str(exc)}
                )
        self.put(
            label + "/witness-candidate-availability.json",
            {
                "rejectedEarlierPolls": candidate_failures,
                "selection": "First actual complete evidence-presence observation, or explicitly chosen original poll; not product report gates or firstMet",
            },
        )
        require(
            selected is not None,
            "No complete actual PG/task witness; see per-poll missing evidence",
        )
        observation, arrays, redis_state, tasks, provenance = selected
        tx = observation["pgTransaction"]
        require(
            tx.get("readonly") == "on"
            and tx.get("isolation") == "repeatable read"
            and tx.get("snapshot"),
            "Original observation transaction not RR READ ONLY",
        )
        require(
            observation.get("pgStartedAt")
            and observation.get("pgReadCompletedAt")
            and observation.get("pgEndedAt"),
            "Original transaction timing missing",
        )
        require(
            instant(observation["pgStartedAt"])
            <= instant(observation["pgReadCompletedAt"])
            <= instant(observation["pgEndedAt"])
            <= instant(redis_state["observedAt"]),
            "Original PG/task time ordering contradictory",
        )
        timestamped = self.jsonl(item["benchmarkTimestamped"])
        stdout = (
            self.reads.path(item["benchmarkStdout"])
            .read_bytes()
            .decode("utf-8", "replace")
        )
        require(
            all(isinstance(r.get("line"), str) for r in timestamped)
            and "".join(r["line"] for r in timestamped) == stdout,
            "Timestamped benchmark lines do not restore the exact original stdout",
        )
        submitted, submit_proof = submit_from_timestamped(
            timestamped, freeze["window"], observation["parentTaskId"]
        )
        records = unique_index(arrays["records"], "id")
        sources = {r.get("source_task_id") for r in arrays["ledger"]}
        require(
            len(sources) == 1 and None not in sources,
            "Actual ledger source TaskRecord IDs are not one complete set",
        )
        tracker_id = next(iter(sources))
        tracker = redis_state.get("sourceTaskRecords", {}).get(tracker_id)
        require(
            isinstance(tracker, dict) and tracker.get("task_id") == tracker_id,
            "Original TaskRecord hash identity absent/mismatched",
        )
        require(
            tracker.get("celery_task_id") is not None
            and tracker.get("status") is not None,
            "Actual TaskRecord status/Celery binding absent",
        )
        witness = {
            "submittedAt": submitted,
            "submittedAtProvenance": submit_proof,
            "observedAt": redis_state["observedAt"],
            "mode": "standard-hourly",
            "transaction": "REPEATABLE READ READ ONLY",
            "readOnlyConfirmed": tx["readonly"],
            "parentTaskId": observation["parentTaskId"],
            "runtime": runtime,
            "barrier": {
                "ledger_rows": map_rows("ledger", arrays["ledger"], records),
                "latest_rows": map_rows("latest", arrays["latest"], records),
                "node_rows": map_rows("nodes", arrays["nodes"]),
                "record_rows": map_rows("records", arrays["records"]),
            },
            "celeryTasks": tasks,
            "taskRecord": {
                **deepcopy(tracker),
                "id": tracker["task_id"],
                "celeryTaskId": tracker["celery_task_id"],
                "originalHash": deepcopy(tracker),
            },
            "originalObservation": deepcopy(observation),
            "fullPayloadProvenance": provenance,
            "boundary": "PG RR snapshot and separately timed Redis observation are not a distributed atomic transaction. Original record created/updated/event times are retained; post30 finalFull is never backdated.",
        }
        witness_ref = self.put(label + "/witness.json", witness)
        poll_sidecar = [
            {
                "observedAt": witness["observedAt"],
                "witnessSha256": witness_ref["sha256"],
                "originalPoll": observation["poll"],
                "originalObservation": deepcopy(observation),
                "sourceRef": indexed["observations.jsonl"],
            }
        ]
        poll_ref = self.put(label + "/poll-log.json", poll_sidecar)
        command = deepcopy(self.json(item["benchmarkCommand"]))
        require(
            command.get("rc") is not None
            and command.get("argv")
            and command.get("startedAt")
            and command.get("finishedAt"),
            "Original benchmark command/real rc incomplete",
        )
        argv = command["argv"]
        require(
            any(str(a).endswith("kpi_capacity_benchmark.py") for a in argv),
            "Actual benchmark entry missing",
        )
        for flag, value in (
            ("--mode", "standard-hourly"),
            ("--expect-eligible", "1000"),
            ("--slo-seconds", "600"),
            ("--timeout", "2400"),
            ("--barrier-loop-prefix", "PERF-T"),
        ):
            require(
                flag in argv
                and argv.index(flag) + 1 < len(argv)
                and argv[argv.index(flag) + 1] == value,
                "Original benchmark argument differs: " + flag,
            )
        self.reads.path(item["benchmarkStdout"])
        self.reads.path(item["benchmarkStderr"])
        command.update(
            endedAt=command["finishedAt"],
            stdout=item["benchmarkStdout"],
            stderr=item["benchmarkStderr"],
            originalCommandRef=item["benchmarkCommand"],
        )
        command_ref = self.put(label + "/command.json", command)
        resources_ref = self.put(
            label + "/resources.json",
            {
                "accountingMethod": "CgroupCurrentPlusHostPIDRSS-v1",
                "profile": resource_ref,
                "runtimeProfile": item["runtimeProfile"],
                "resourcesJsonl": indexed["resources.jsonl"],
                "rawRefs": [
                    resource_ref,
                    item["runtimeProfile"],
                    indexed["resources.jsonl"],
                ],
            },
        )
        for axis in ("input", "precompute", "redis", "process"):
            vector = item.get("cacheVector", {}).get(axis)
            require(
                isinstance(vector, dict)
                and vector.get("state")
                and vector.get("definition")
                and vector.get("rawRefs"),
                "Actual four-dimensional cache evidence incomplete: " + axis,
            )
            for ref in vector["rawRefs"]:
                self.reads.path(ref)
        all_refs = (
            list(indexed.values())
            + [
                item[k]
                for k in (
                    "collectorManifest",
                    "manifest",
                    "freeze",
                    "runtimeProfile",
                    "benchmarkCommand",
                    "benchmarkTimestamped",
                    "benchmarkStdout",
                    "benchmarkStderr",
                    "events",
                )
            ]
            + [
                scope["scopeRef"],
                plan["configurationIsolation"],
                plan["nodeScopeAfter"],
            ]
        )
        self.put(
            label + "/raw-reference-index.json",
            {
                "refs": all_refs,
                "originalCollectorManifest": item["collectorManifest"],
                "fieldMappings": ALIASES,
                "scoreRepresentation": "Original DB Decimal string retained in originalRow; numeric alias accepted only when Decimal(str(float(original))) equals original exactly",
            },
        )
        return {
            "id": label,
            "scenario": planned["scenario"].upper(),
            "cacheClass": planned["cache"].upper(),
            "manifest": item["manifest"],
            "freeze": item["freeze"],
            "nodeScopes": scope["scopeRef"],
            "witness": witness_ref,
            "resources": resources_ref,
            "command": command_ref,
            "pollLog": poll_ref,
            "cacheVector": deepcopy(item["cacheVector"]),
            "rawRefs": all_refs,
            "rawLogRefs": logs + item.get("additionalRawLogRefs", []),
        }

    def run(self, plan):
        require(
            re.fullmatch(r"[0-9a-f]{40}", str(plan.get("candidateSha", ""))),
            "Final candidate full40 SHA missing",
        )
        require(
            plan.get("dbNaiveTimezone") == "UTC",
            "Explicit frozen DB naive UTC convention required",
        )
        require(
            plan.get("redisHost") in {"localhost", "127.0.0.1"}
            and plan.get("redisPort") == 17123,
            "Actual private series broker identity missing",
        )
        planned = self.json(plan["plannedBatches"])
        planned = planned.get("batches") if isinstance(planned, dict) else planned
        require(
            isinstance(planned, list)
            and len(planned) == 20
            and [r.get("batch") for r in planned] == list(range(1, 21)),
            "Original complete ascending20 planned batches required",
        )
        require(
            Counter((r.get("scenario"), r.get("cache")) for r in planned)
            == {("a", "cold"): 5, ("a", "hot"): 5, ("b", "cold"): 5, ("b", "hot"): 5},
            "Original20 A/B cold/hot matrix missing",
        )
        for first, second in zip(planned, planned[1:]):
            require(
                instant(first["ts_end"]) == instant(second["ts_start"]),
                "Original planned windows are not consecutive",
            )
        inputs = unique_index(plan.get("batches"), "batch")
        require(
            set(inputs) == set(range(1, 21)),
            "Exact original20 batch input objects required; partial plan stays BLOCKED",
        )
        self.reads.path(plan["coldHotDefinitions"])
        self.reads.path(plan["history"])
        require(
            len(plan.get("celeryEventSources", [])) == 2,
            "Exact installed Celery request/dispatcher source references required",
        )
        for ref in plan["celeryEventSources"]:
            self.reads.path(ref)
        resource = deepcopy(self.json(plan["resourceProfile"]))
        require(
            resource.get("memoryBudgetBytes") == 6442450944
            and resource.get("maximumAccountingBytes") == 5153960755
            and resource.get("minimumVmMemAvailableBytes") == 1073741824,
            "Frozen resource profile cannot be relaxed",
        )
        proof_refs = [plan["resourceProfile"]] + plan.get("resourceProfileProofs", [])
        require(
            len(proof_refs) > 1,
            "Original effective resource/TD profile proof refs absent",
        )
        for ref in proof_refs:
            self.reads.path(ref)
        resource["rawRefs"] = proof_refs
        resource_ref = self.put("resource-profile.json", resource)
        scope = self.config(plan)
        specs = []
        for item in planned:
            try:
                specs.append(
                    self.batch(inputs[item["batch"]], item, plan, scope, resource_ref)
                )
            except (MissingEvidence, KeyError, FileNotFoundError) as exc:
                self.issues.append(
                    {"level": "BLOCKED", "batch": item["batch"], "detail": str(exc)}
                )
            except (InvalidEvidence, ValueError, TypeError) as exc:
                self.issues.append(
                    {"level": "REWORK", "batch": item["batch"], "detail": str(exc)}
                )
        if len(specs) != 20:
            self.issues.append(
                {
                    "level": "BLOCKED",
                    "detail": f"Only{len(specs)}/20 complete raw batch sidecars; series.json not emitted",
                }
            )
        else:
            series = {
                "seriesId": plan["seriesId"],
                "candidateSha": plan["candidateSha"],
                "configDigest": plan["configDigest"],
                "coldHotDefinitions": plan["coldHotDefinitions"],
                "resourceProfile": resource_ref,
                "history": plan["history"],
                "batches": specs,
                "adaptationBoundary": "Lossless original-file sidecars only; not independent acceptance. No product report gate or firstMet field read. All raw references and original rows retained.",
            }
            self.put("series.json", series)
        self.reads.verify_unchanged()
        return {
            "status": "REWORK"
            if any(r["level"] == "REWORK" for r in self.issues)
            else "BLOCKED"
            if self.issues
            else "READY_FOR_INDEPENDENT_RECONCILIATION",
            "mappedBatches": len(specs),
            "requiredBatches": 20,
            "issues": self.issues,
            "readSet": self.reads.files,
            "networkOrBusinessOperations": False,
            "finalAcceptance": "PENDING_NONAUTHOR_RECONCILIATION_AND_RECEIPT",
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--evidence-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    adapter = Adapter(args.evidence_root, args.out)
    try:
        plan = adapter.json(adapter.ref(args.plan))
        result = adapter.run(plan)
    except (MissingEvidence, KeyError, FileNotFoundError) as exc:
        result = {
            "status": "BLOCKED",
            "issues": [{"level": "BLOCKED", "detail": str(exc)}],
            "readSet": adapter.reads.files,
        }
    except (InvalidEvidence, ValueError, TypeError) as exc:
        result = {
            "status": "REWORK",
            "issues": [{"level": "REWORK", "detail": str(exc)}],
            "readSet": adapter.reads.files,
        }
    result.update(
        networkOrBusinessOperations=False,
        finalAcceptance="PENDING_NONAUTHOR_RECONCILIATION_AND_RECEIPT",
    )
    adapter.reads.verify_unchanged()
    adapter.put("adaptation-result.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "mappedBatches": result.get("mappedBatches", 0),
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return (
        0
        if result["status"] == "READY_FOR_INDEPENDENT_RECONCILIATION"
        else 1
        if result["status"] == "REWORK"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
