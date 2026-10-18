"""Independently reconcile saved 20-batch evidence, without importing product code.

The input is an explicit export contract documented in tool-contract.md. This
program does not connect to PG/Redis/TD, send requests, enqueue jobs or mutate
inputs. Raw source refs and all object IDs remain in the generated read-set.
PASS is the artifact contract result, not a replacement for the human receipt.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from independent_support import (
    InvalidEvidence,
    MissingEvidence,
    ReadSet,
    finite,
    instant,
    need,
    result_rc,
    verdict,
    write_new,
)

GATES = (
    "ledger",
    "source",
    "run_binding",
    "algorithm_version",
    "expected_status",
    "latest",
    "nodes",
    "task_terminal",
    "parent_terminal",
)
RESOURCE_PATTERN = re.compile(
    r"too many clients|query memory exhausted|out of memory|oom.killed", re.I
)


class Auditor:
    def __init__(self, root):
        self.reads = ReadSet(root)
        self.issues = []

    def issue(self, level, code, detail, batch=None):
        self.issues.append(
            {"level": level, "code": code, "batch": batch, "detail": detail}
        )

    def check(self, ok, code, detail, batch=None, missing=False):
        if not ok:
            self.issue("BLOCKED" if missing else "REWORK", code, detail, batch)
        return bool(ok)

    def refs(self, refs, code, batch):
        if not isinstance(refs, list) or not refs:
            self.issue(
                "BLOCKED", code, "nonempty original evidence references required", batch
            )
            return
        for ref in refs:
            self.reads.path(ref)

    def indexed(self, rows, key, code, batch):
        if not isinstance(rows, list):
            self.issue("BLOCKED", code, f"missing raw rows ({key})", batch)
            return {}
        ids = [row.get(key) for row in rows]
        counts = Counter(ids)
        duplicates = [str(k) for k, count in counts.items() if count > 1]
        self.check(not duplicates, code + ".duplicates", duplicates, batch)
        self.check(None not in counts, code + ".missing-id", ids, batch)
        # Duplicates have already failed; no silent last-row-wins acceptance.
        return {row.get(key): row for row in rows if row.get(key) is not None}

    def same_window(self, row, window, batch, kind):
        matched = True
        for key in ("tsStart", "tsEnd"):
            value = row.get(key)
            if value is None:
                self.issue(
                    "BLOCKED", f"{kind}.window-missing", {"key": key, "row": row}, batch
                )
                matched = False
            else:
                matched &= self.check(
                    instant(value) == instant(window[key]), f"{kind}.window", row, batch
                )
        return matched

    def runtime(self, runtime, sha, config_digest, batch):
        members = need(runtime.get("members"), "runtime.members missing")
        for role in ("api", "worker_master", "beat"):
            matches = [p for p in members if p.get("role") == role]
            self.check(len(matches) == 1, "runtime.unique-" + role, matches, batch)
        ids = [p.get("identity") for p in members]
        self.check(
            len(ids) == len(set(ids)) and None not in ids,
            "runtime.process-identities",
            ids,
            batch,
        )
        for process in members:
            self.check(
                process.get("loadedSha") == sha, "runtime.loaded-sha", process, batch
            )
            self.check(
                bool(process.get("startedAt")) and bool(process.get("argv")),
                "runtime.identity-incomplete",
                process,
                batch,
                missing=True,
            )
        self.check(
            runtime.get("configDigest") == config_digest,
            "runtime.config-digest",
            runtime.get("configDigest"),
            batch,
        )
        self.check(
            bool(runtime.get("schemaRevision")),
            "runtime.schema",
            runtime,
            batch,
            missing=True,
        )
        # A dedicated DB and broker are identities, never credentials.
        self.check(
            bool(runtime.get("postgresDatabase"))
            and bool(runtime.get("brokerNamespace")),
            "runtime.namespace",
            runtime,
            batch,
            missing=True,
        )

    def td_profile(self, runtime, profile_ref, batch):
        planned = self.reads.json(profile_ref)
        response = self.reads.json(
            need(
                runtime.get("tdDnodeVariables"),
                "runtime.tdDnodeVariables raw reference missing",
            )
        )
        response = response.get("response", response)
        self.check(
            response.get("code") == 0,
            "td.profile-query-error",
            response.get("code"),
            batch,
        )
        variables = {
            str(row[1]): str(row[2])
            for row in need(response.get("data"), "SHOW dnode rows missing")
            if len(row) >= 3
        }
        for key, value in need(
            planned.get("tdVariables"), "frozen TD variables absent"
        ).items():
            self.check(
                variables.get(key) == str(value),
                "td.profile-drift",
                {"key": key, "planned": value, "actual": variables.get(key)},
                batch,
            )
        self.refs(planned.get("rawRefs"), "td.profile-originals-missing", batch)

    def resources(self, raw, submitted, observed, batch):
        samples = need(raw.get("samples"), "resources.samples missing")
        if not self.check(
            len(samples) >= 2, "resource.samples", len(samples), batch, missing=True
        ):
            return
        times = [instant(row.get("at")) for row in samples]
        self.check(
            all(a < b for a, b in zip(times, times[1:])),
            "resource.order",
            [str(t) for t in times],
            batch,
        )
        self.check(
            times[0].timestamp() <= submitted.timestamp() - 60,
            "resource.before60s",
            samples[0].get("at"),
            batch,
        )
        self.check(
            times[-1].timestamp() >= observed.timestamp() + 30,
            "resource.after30s",
            samples[-1].get("at"),
            batch,
        )
        self.check(
            max((b - a).total_seconds() for a, b in zip(times, times[1:])) <= 5,
            "resource.gap-over5s",
            "sampling has a gap >5s",
            batch,
        )
        budget = need(
            raw.get("memoryBudgetBytes"), "resources.memoryBudgetBytes missing"
        )
        self.check(finite(budget) and budget > 0, "resource.budget", budget, batch)
        scope = need(raw.get("scopeIdentities"), "resources.scopeIdentities missing")
        for row in samples:
            processes = need(row.get("processes"), "resource sample processes missing")
            actual_ids = [p.get("identity") for p in processes]
            self.check(
                set(actual_ids) == set(scope) and len(actual_ids) == len(scope),
                "resource.scope",
                row.get("at"),
                batch,
            )
            if not all(
                finite(p.get("rssBytes")) and p["rssBytes"] >= 0 for p in processes
            ):
                self.issue("BLOCKED", "resource.rss-missing", row.get("at"), batch)
            elif finite(budget) and budget > 0:
                total = sum(p["rssBytes"] for p in processes)
                self.check(
                    total <= budget * 0.8,
                    "resource.rss-budget",
                    {"at": row["at"], "bytes": total, "budget": budget},
                    batch,
                )
            self.check(
                row.get("workerMasters") == 1 and row.get("beats") == 1,
                "resource.duplicate-service",
                row.get("at"),
                batch,
            )
        for event in need(
            raw.get("events"),
            "resources.events missing (use [] if actually sampled none)",
        ):
            self.check(
                not RESOURCE_PATTERN.search(str(event.get("message", "")))
                and event.get("kind")
                not in {"OOM", "CONNECTION_EXHAUSTED", "TD_RESOURCE_EXHAUSTED"},
                "resource.failure-event",
                event,
                batch,
            )
            self.refs(event.get("rawRefs"), "resource.event-raw-missing", batch)
        self.refs(raw.get("rawRefs"), "resource.raw-missing", batch)

    def batch(self, spec, series):
        batch_id = need(spec.get("id"), "batch.id missing")
        before = len(self.issues)
        freeze = self.reads.json(spec["freeze"])
        data_manifest = self.reads.json(spec["manifest"])
        witness = self.reads.json(spec["witness"])
        resource = self.reads.json(spec["resources"])
        command = self.reads.json(spec["command"])
        self.refs(spec.get("rawRefs"), "batch.raw-missing", batch_id)
        log_refs = need(spec.get("rawLogRefs"), "batch.rawLogRefs missing")
        self.refs(log_refs, "batch.raw-log-missing", batch_id)
        for ref in log_refs:
            path = self.reads.path(ref)
            with path.open(encoding="utf-8", errors="replace") as log:
                for line_number, line in enumerate(log, start=1):
                    if RESOURCE_PATTERN.search(line):
                        self.issue(
                            "REWORK",
                            "resource.raw-log-exhaustion",
                            {
                                "path": str(path),
                                "line": line_number,
                                "text": line.rstrip(),
                            },
                            batch_id,
                        )
        # Verify freeze binds the exact original manifest bytes, not a later rewrite.
        self.check(
            freeze.get("manifest", {}).get("sha256") == spec["manifest"]["sha256"],
            "freeze.manifest-hash",
            freeze.get("manifest"),
            batch_id,
        )
        window = need(freeze.get("window"), "freeze.window missing")
        self.check(
            (instant(window["tsEnd"]) - instant(window["tsStart"])).total_seconds()
            == 3600,
            "window.hour",
            window,
            batch_id,
        )
        eligible = need(freeze.get("eligible"), "freeze.eligible missing")
        excluded = need(freeze.get("excluded"), "freeze.excluded missing")
        all_rows = eligible + excluded
        all_ids = [r.get("loopId") for r in all_rows]
        self.check(
            len(all_ids) == 1000 and len(set(all_ids)) == 1000 and None not in all_ids,
            "denominator.unique1000",
            {"total": len(all_ids), "unique": len(set(all_ids))},
            batch_id,
        )
        self.check(
            all(row.get("reason") for row in excluded),
            "denominator.excluded-reasons",
            excluded,
            batch_id,
        )
        expected_ids = {row["loopId"] for row in eligible}
        expected_tags = {row["loopTag"] for row in all_rows}
        manifest_rows = need(data_manifest.get("rows"), "manifest.rows missing")
        manifest_tags = [row.get("loopTag") for row in manifest_rows]
        self.check(
            len(manifest_tags) == 1000
            and len(set(manifest_tags)) == 1000
            and set(manifest_tags) == expected_tags,
            "manifest.identities",
            "manifest != frozen 1000 identities",
            batch_id,
        )
        manifest_map = {row["loopTag"]: row for row in manifest_rows}
        for row in all_rows:
            original = manifest_map.get(row.get("loopTag"), {})
            self.check(
                row.get("expectedStatus") == original.get("expectedStatus"),
                "freeze.expected-not-changed",
                row.get("loopTag"),
                batch_id,
            )
        composition = Counter(row.get("composition") for row in manifest_rows)
        if spec.get("scenario") == "A":
            self.check(
                composition == {"normal": 1000}
                and len(eligible) == 1000
                and not excluded,
                "scenario.A",
                dict(composition),
                batch_id,
            )
        elif spec.get("scenario") == "B":
            self.check(
                composition
                == {"normal": 800, "insufficient": 100, "manual": 50, "gap": 50},
                "scenario.B",
                dict(composition),
                batch_id,
            )
        else:
            self.issue("BLOCKED", "scenario.unknown", spec.get("scenario"), batch_id)

        submitted = instant(
            need(witness.get("submittedAt"), "witness.submittedAt missing")
        )
        observed = instant(
            need(witness.get("observedAt"), "witness.observedAt missing")
        )
        self.check(
            instant(freeze.get("frozenAt")) <= submitted <= observed,
            "time.freeze-submit-observe",
            [
                freeze.get("frozenAt"),
                witness.get("submittedAt"),
                witness.get("observedAt"),
            ],
            batch_id,
        )
        seconds = (observed - submitted).total_seconds()
        self.check(seconds <= 600, "slo.full-barrier-over600s", seconds, batch_id)
        self.check(
            witness.get("mode") == "standard-hourly",
            "entry.standard-hourly",
            witness.get("mode"),
            batch_id,
        )
        self.check(
            witness.get("transaction") == "REPEATABLE READ READ ONLY"
            and witness.get("readOnlyConfirmed") == "on",
            "db.read-only-snapshot",
            witness.get("transaction"),
            batch_id,
        )
        self.check(command.get("rc") == 0, "command.exit-code", command, batch_id)
        self.check(
            bool(command.get("argv"))
            and bool(command.get("startedAt"))
            and bool(command.get("endedAt")),
            "command.identity",
            command,
            batch_id,
            missing=True,
        )
        arguments = command.get("argv") or []
        self.check(
            "standard-hourly" in arguments
            and any(
                str(arg).endswith("kpi_capacity_benchmark.py") for arg in arguments
            ),
            "command.real-entry",
            arguments,
            batch_id,
        )
        self.reads.path(
            need(command.get("stdout"), "command.stdout raw reference missing")
        )
        self.reads.path(
            need(command.get("stderr"), "command.stderr raw reference missing")
        )
        vector = need(spec.get("cacheVector"), "batch.cacheVector missing")
        for dimension in ("input", "precompute", "redis", "process"):
            axis = need(vector.get(dimension), f"cache dimension {dimension} absent")
            self.check(
                bool(axis.get("state")) and bool(axis.get("definition")),
                "cache.dimension-definition",
                dimension,
                batch_id,
                missing=True,
            )
            self.refs(axis.get("rawRefs"), "cache.raw-missing-" + dimension, batch_id)
        self.runtime(
            need(witness.get("runtime"), "witness.runtime missing"),
            series["candidateSha"],
            series["configDigest"],
            batch_id,
        )
        self.td_profile(
            witness["runtime"],
            need(series.get("resourceProfile"), "series.resourceProfile missing"),
            batch_id,
        )

        parent = need(witness.get("parentTaskId"), "witness.parentTaskId missing")
        version = need(
            freeze.get("algorithmVersion"), "freeze.algorithmVersion missing"
        )
        barrier = need(witness.get("barrier"), "witness.barrier missing")
        ledger = self.indexed(barrier.get("ledger_rows"), "loopId", "ledger", batch_id)
        latest = self.indexed(barrier.get("latest_rows"), "loopId", "latest", batch_id)
        nodes = self.indexed(barrier.get("node_rows"), "plantNodeId", "nodes", batch_id)
        records = self.indexed(
            barrier.get("record_rows"), "recordId", "records", batch_id
        )
        gates = {key: True for key in GATES}
        gates["ledger"] = set(ledger) == expected_ids
        gates["latest"] = set(latest) == expected_ids
        self.check(
            gates["ledger"],
            "ledger.full-object-set",
            sorted(expected_ids ^ set(ledger)),
            batch_id,
        )
        self.check(
            gates["latest"],
            "latest.full-object-set",
            sorted(expected_ids ^ set(latest)),
            batch_id,
        )
        actual_statuses = Counter()
        frozen_by_id = {row["loopId"]: row for row in eligible}
        task_record_ids = set()
        for loop_id, row in ledger.items():
            gates["ledger"] &= self.same_window(row, window, batch_id, "ledger")
            original = frozen_by_id.get(loop_id)
            if original is None:
                continue
            actual_statuses[row.get("status")] += 1
            source_ok = row.get("source") in {"SCHEDULED", "MANUAL_STANDARD"}
            gates["source"] &= source_ok
            self.check(source_ok, "source.foreign", row, batch_id)
            record = records.get(row.get("resultRecordId"))
            if record is None:
                self.issue(
                    "BLOCKED",
                    "record.missing-original",
                    row.get("resultRecordId"),
                    batch_id,
                )
                gates["run_binding"] = False
                continue
            binding = (
                row.get("recordLogicalRunId") == parent == record.get("logicalRunId")
                and record.get("objectId") == loop_id
                and record.get("status") == "COMPLETED"
            )
            gates["run_binding"] &= binding
            self.check(
                binding,
                "record.run-binding",
                {"snapshot": row, "record": record},
                batch_id,
            )
            gates["run_binding"] &= self.same_window(record, window, batch_id, "record")
            version_ok = (
                row.get("algorithmVersion") == record.get("algorithmVersion") == version
            )
            gates["algorithm_version"] &= version_ok
            self.check(version_ok, "version.mismatch", loop_id, batch_id)
            expected = original.get("expectedSnapshotStatus")
            self.check(
                expected is not None,
                "expected.snapshot-state-missing",
                original,
                batch_id,
                missing=True,
            )
            status_ok = row.get("status") == expected
            if expected == "SUCCESS":
                status_ok &= finite(row.get("score"))
            if "DATA_INSUFFICIENT" in str(original.get("expectedStatus")):
                tags = row.get("fitnessTags")
                status_ok &= "DATA_INSUFFICIENT" in (
                    tags.get("tags", []) if isinstance(tags, dict) else tags or []
                )
                self.check(
                    not RESOURCE_PATTERN.search(json.dumps(record, ensure_ascii=False)),
                    "status.resource-as-insufficient",
                    record,
                    batch_id,
                )
            gates["expected_status"] &= status_ok
            self.check(
                status_ok,
                "status.mismatch",
                {"expected": original, "actual": row},
                batch_id,
            )
            task_record_ids.add(row.get("sourceTaskId"))
            projection = latest.get(loop_id)
            if projection is not None:
                gates["latest"] &= self.same_window(
                    projection, window, batch_id, "latest"
                )
                ok = (
                    projection.get("algorithmVersion") == version
                    and projection.get("recordLogicalRunId") == parent
                    and projection.get("resultRecordId") == row.get("resultRecordId")
                )
                gates["latest"] &= ok
                self.check(ok, "latest.binding", projection, batch_id)

        tasks = self.indexed(witness.get("celeryTasks"), "id", "celeryTasks", batch_id)
        parent_row = tasks.get(parent)
        gates["parent_terminal"] = bool(
            parent_row and parent_row.get("state") == "SUCCESS"
        )
        self.check(gates["parent_terminal"], "parent.not-success", parent_row, batch_id)
        if parent_row:
            self.check(
                parent_row.get("name") == "app.tasks.kpi_calc.calculate_hourly_kpi",
                "parent.entry",
                parent_row,
                batch_id,
            )
        for task_id, task in tasks.items():
            self.refs(task.get("rawRefs"), "task.raw-missing", batch_id)
            self.check(
                task.get("runtimeSha") == series["candidateSha"],
                "task.runtime-sha",
                task,
                batch_id,
            )
            self.check(
                task.get("state") == "SUCCESS", "task.not-success", task, batch_id
            )
            if task.get("finishedAt"):
                self.check(
                    instant(task["finishedAt"]) <= observed,
                    "task.not-terminal-at-witness",
                    task,
                    batch_id,
                )
            else:
                self.issue("BLOCKED", "task.finished-time-missing", task_id, batch_id)
            if task_id != parent:
                current = task_id
                visited = set()
                while current != parent and current in tasks and current not in visited:
                    visited.add(current)
                    current = tasks[current].get("parentId")
                self.check(
                    current == parent and task.get("rootId") == parent,
                    "task.parent-root-chain",
                    task,
                    batch_id,
                )
        task_record = need(witness.get("taskRecord"), "witness.taskRecord missing")
        gates["task_terminal"] = (
            task_record.get("status") == "SUCCESS"
            and task_record_ids == {task_record.get("id")}
            and None not in task_record_ids
        )
        self.check(
            gates["task_terminal"], "taskRecord.binding-status", task_record, batch_id
        )
        self.check(
            task_record.get("celeryTaskId") == parent,
            "taskRecord.celery-binding",
            task_record,
            batch_id,
        )
        node_check_start = len(self.issues)
        predicted = need(freeze.get("predictedNodes"), "freeze.predictedNodes missing")
        scope_file = self.reads.json(spec["nodeScopes"])
        self.check(
            instant(scope_file.get("frozenAt")) <= submitted,
            "nodes.scope-frozen-before-submit",
            scope_file.get("frozenAt"),
            batch_id,
        )
        self.check(
            scope_file.get("complexGroupPolicy") == "NONE",
            "nodes.complex-group-adapter",
            "capacity fixture requires an independently verified no-complex-group identity set",
            batch_id,
            missing=True,
        )
        self.refs(scope_file.get("rawRefs"), "nodes.scope-raw-missing", batch_id)
        scopes = need(scope_file.get("scopes"), "frozen descendant scopes missing")
        self.check(
            set(scopes) == set(predicted),
            "nodes.scope-key-set",
            sorted(set(scopes) ^ set(predicted)),
            batch_id,
        )
        gates["nodes"] = (
            set(nodes) == set(predicted)
            and len(set(predicted)) == len(predicted)
            and bool(predicted)
        )
        self.check(
            gates["nodes"],
            "nodes.full-predicted-set",
            sorted(set(nodes) ^ set(predicted)),
            batch_id,
        )
        for node_id, row in nodes.items():
            self.same_window(row, window, batch_id, "node")
            ok = row.get("algorithmVersion") == version
            gates["nodes"] &= ok
            self.check(ok, "node.version", row, batch_id)
            dispatches = [
                task
                for task in tasks.values()
                if task.get("name") == "app.tasks.kpi_calc.calculate_node_kpi_hourly"
                and node_id in task.get("nodeIds", [])
            ]
            self.check(
                bool(dispatches),
                "node.dispatch-proof-missing",
                node_id,
                batch_id,
                missing=True,
            )
            node_record = records.get(row.get("resultRecordId"))
            if node_record is None:
                self.issue(
                    "BLOCKED",
                    "node.original-record-missing",
                    row.get("resultRecordId"),
                    batch_id,
                )
                gates["nodes"] = False
                continue
            self.same_window(node_record, window, batch_id, "node-record")
            self.check(
                node_record.get("objectKind") == "NODE"
                and node_record.get("objectId") == node_id
                and node_record.get("status") == "COMPLETED"
                and node_record.get("algorithmVersion") == version,
                "node.record-binding",
                node_record,
                batch_id,
            )
            # NODE logicalRunId is content-addressed and must not be forced to
            # the Celery parent UUID. Exact loop record refs are stronger proof.
            payload = need(node_record.get("payload"), "node record payload absent")
            refs = need(
                payload.get("loopRecordIds"), "node payload.loopRecordIds absent"
            )
            input_ids = [entry.get("loopId") for entry in refs]
            node_scope = need(scopes.get(node_id), "node descendant scope absent")
            self.check(
                len(node_scope) == len(set(node_scope))
                and set(node_scope) <= expected_ids,
                "nodes.scope-identities",
                node_id,
                batch_id,
            )
            expected_inputs = {
                loop_id: ledger[loop_id].get("resultRecordId")
                for loop_id in node_scope
                if loop_id in ledger and ledger[loop_id].get("status") == "SUCCESS"
            }
            self.check(
                len(input_ids) == len(set(input_ids))
                and set(input_ids) == set(expected_inputs),
                "node.input-object-set",
                {
                    "nodeId": node_id,
                    "expected": sorted(expected_inputs),
                    "actual": input_ids,
                },
                batch_id,
            )
            for item in refs:
                self.check(
                    item.get("recordId") == expected_inputs.get(item.get("loopId")),
                    "node.input-record-version",
                    {"nodeId": node_id, "input": item},
                    batch_id,
                )
            stats = need(
                payload.get("assessment_stats"), "node payload.assessment_stats absent"
            )
            roster = need(stats.get("roster"), "node roster absent")
            members = need(roster.get("members"), "node roster.members absent")
            member_ids = [item.get("loopId") for item in members]
            self.check(
                set(member_ids) == set(node_scope)
                and len(member_ids) == len(set(member_ids)),
                "node.roster-identities",
                node_id,
                batch_id,
            )
            states = need(roster.get("states"), "node roster six-state counters absent")
            actual_states = Counter(item.get("state") for item in members)
            self.check(
                set(states)
                == {
                    "scorable",
                    "incomplete",
                    "failed",
                    "cancelled",
                    "pending",
                    "missing",
                }
                and states == {key: actual_states.get(key, 0) for key in states}
                and sum(states.values()) == len(node_scope),
                "node.roster-state-equation",
                {"nodeId": node_id, "declared": states, "actual": dict(actual_states)},
                batch_id,
            )
            self.check(
                states.get("pending") == 0
                and states.get("missing") == 0
                and states.get("failed") == 0
                and states.get("cancelled") == 0,
                "node.roster-unfinished",
                {"nodeId": node_id, "states": states},
                batch_id,
            )
        gates["nodes"] &= len(self.issues) == node_check_start
        self.check(
            not set(actual_statuses) - {"SUCCESS", "INCONCLUSIVE"},
            "denominator.unexpected-terminal",
            dict(actual_statuses),
            batch_id,
        )
        self.resources(resource, submitted, observed, batch_id)
        # Poll log is evidence, not a trusted PASS flag. The chosen witness is
        # recomputed above and cannot be assembled from firstMet values.
        poll_log = self.reads.json(spec["pollLog"])
        self.check(
            any(
                row.get("witnessSha256") == spec["witness"]["sha256"]
                and instant(row.get("observedAt")) == observed
                for row in poll_log
            ),
            "poll.same-witness-unobserved",
            spec["witness"],
            batch_id,
        )
        return {
            "id": batch_id,
            "scenario": spec.get("scenario"),
            "cacheClass": spec.get("cacheClass"),
            "window": window,
            "submittedAt": witness["submittedAt"],
            "observedAt": witness["observedAt"],
            "seconds": seconds,
            "parentTaskId": parent,
            "objectIds": sorted(all_ids),
            "eligible": len(eligible),
            "excluded": len(excluded),
            "statuses": dict(actual_statuses),
            "recomputedConditions": gates,
            "verdict": verdict(self.issues[before:]),
            "manualChecksPending": [
                "four-dimensional cold/hot semantics and namespace operations",
                "frozen node descendant membership versus query/dispatch evidence",
                "complete raw error/retry/FAILED persistence and cancellation chain",
                "independent authorized key-condition rerun",
            ],
        }

    def run(self, series):
        self.check(
            bool(re.fullmatch(r"[0-9a-f]{40}", str(series.get("candidateSha", "")))),
            "series.sha",
            series.get("candidateSha"),
            missing=True,
        )
        need(series.get("configDigest"), "series.configDigest missing")
        self.reads.path(
            need(
                series.get("coldHotDefinitions"),
                "series.coldHotDefinitions frozen artifact missing",
            )
        )
        specs = need(series.get("batches"), "series.batches missing")
        self.check(len(specs) >= 20, "series.minimum20", len(specs), missing=True)
        ids = [spec.get("id") for spec in specs]
        self.check(len(set(ids)) == len(ids), "series.duplicate-batches", ids)
        combinations = Counter(
            (row.get("scenario"), row.get("cacheClass")) for row in specs
        )
        for scenario in ("A", "B"):
            for cold_hot in ("COLD", "HOT"):
                self.check(
                    combinations[(scenario, cold_hot)] >= 5,
                    "series.cold-hot-matrix",
                    {
                        "scenario": scenario,
                        "cache": cold_hot,
                        "actual": combinations[(scenario, cold_hot)],
                    },
                    missing=True,
                )
        results = []
        for spec in specs:
            try:
                results.append(self.batch(spec, series))
            except (MissingEvidence, KeyError, FileNotFoundError) as exc:
                self.issue(
                    "BLOCKED", "batch.evidence-incomplete", str(exc), spec.get("id")
                )
            except InvalidEvidence as exc:
                self.issue("REWORK", "batch.evidence-invalid", str(exc), spec.get("id"))
        parent_ids = [row["parentTaskId"] for row in results]
        self.check(
            len(set(parent_ids)) == len(parent_ids),
            "series.reused-parent-task",
            parent_ids,
        )
        by_object = {}
        for row in results:
            for loop_id in row["objectIds"]:
                previous = by_object.get(loop_id)
                if previous is not None:
                    self.check(
                        instant(previous) < instant(row["window"]["tsStart"]),
                        "series.latest-nonmonotonic",
                        {
                            "loopId": loop_id,
                            "previous": previous,
                            "next": row["window"]["tsStart"],
                        },
                        row["id"],
                    )
                by_object[loop_id] = row["window"]["tsStart"]
        for first, second in zip(results, results[1:]):
            self.check(
                instant(first["observedAt"]) < instant(second["submittedAt"]),
                "series.batch-overlap",
                [first["id"], second["id"]],
            )
        date_windows = sorted(
            {instant(row["window"]["tsStart"]).date() for row in results}
        )
        self.check(
            any((b - a).days == 1 for a, b in zip(date_windows, date_windows[1:])),
            "series.two-real-dates",
            [str(day) for day in date_windows],
            missing=True,
        )
        for row in results:
            self.check(
                instant(row["window"]["tsEnd"]) < instant(row["submittedAt"]),
                "series.future-window",
                row["window"],
                row["id"],
            )
        history = self.reads.json(
            need(
                series.get("history"),
                "series.history original-nine report refs missing",
            )
        )
        historical_reports = [
            self.reads.json(ref)
            for ref in need(
                history.get("selectedReports"), "history.selectedReports missing"
            )
        ]
        historical_counts = Counter(
            row.get("completionReason") for row in historical_reports
        )
        self.check(
            len(historical_reports) == 9
            and historical_counts == {"BARRIER_PASSED": 6, "TERMINAL_MISMATCH": 3},
            "history.must-preserve6plus3",
            dict(historical_counts),
        )
        for raw in historical_reports:
            self.check(
                raw.get("gate", {}).get("denominators", {}).get("frozenEligible")
                == 1000,
                "history.denominator1000",
                raw.get("parentTaskId"),
            )
        self.reads.verify_unchanged()
        return {
            "kind": "independent-saved-artifact-reconciliation",
            "seriesId": series.get("seriesId"),
            "candidateSha": series.get("candidateSha"),
            "artifactContractVerdict": verdict(self.issues),
            "finalAcceptance": "REQUIRES_NONAUTHOR_RECEIPT_AND_MANUAL_RAW_EVIDENCE_CHECKS",
            "count": len(specs),
            "successfullyParsedBatches": len(results),
            "matrix": {f"{a}/{b}": n for (a, b), n in combinations.items()},
            "batches": results,
            "historicalCounts": dict(historical_counts),
            "issues": self.issues,
            "readSet": self.reads.files,
            "networkOrBusinessOperations": False,
            "productCalculatorOrGateImported": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    auditor = Auditor(args.evidence_root)
    try:
        series = json.loads(args.series.read_text())
        result = auditor.run(series)
    except (MissingEvidence, KeyError, FileNotFoundError) as exc:
        auditor.issue("BLOCKED", "series.evidence-incomplete", str(exc))
        result = {
            "artifactContractVerdict": verdict(auditor.issues),
            "issues": auditor.issues,
            "readSet": auditor.reads.files,
            "networkOrBusinessOperations": False,
        }
    except (InvalidEvidence, ValueError) as exc:
        auditor.issue("REWORK", "series.evidence-invalid", str(exc))
        result = {
            "artifactContractVerdict": verdict(auditor.issues),
            "issues": auditor.issues,
            "readSet": auditor.reads.files,
            "networkOrBusinessOperations": False,
        }
    write_new(args.out, result)
    print(
        json.dumps(
            {
                "verdict": result["artifactContractVerdict"],
                "issues": len(result["issues"]),
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return result_rc(result["artifactContractVerdict"])


if __name__ == "__main__":
    raise SystemExit(main())
