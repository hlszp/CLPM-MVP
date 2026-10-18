"""Archive cleanup audit plus native read-only ps/lsof; no signals, services or DB calls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE / "f03/local-business-20261017223757-c6fa2b489af6"
OUT = BASE / "independent/f03-local-numeric-review/run-20261017223757-c6fa2b489af6"


def run():
    reads = []

    def raw(path):
        data = path.read_bytes()
        reads.append({"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return data

    def value(path):
        return json.loads(raw(path))

    state = value(ROOT / "input-only-before-gate/started-state.json")
    outcome = value(ROOT / "stop-outcome.json")
    assert outcome["errors"] == [] and outcome["watchdogBarrierObserved"] is True and outcome["beatReleasedAfterBarrier"] is True
    acceptance = value(ROOT / "api-exit-acceptance.json")
    signal = value(ROOT / "api-stop-signal.json")
    assert signal == acceptance["requestedSignal"]
    assert signal["pid"] == state["apiPid"] == acceptance["actualExit"]["pid"]
    assert signal["signal"] == "SIGTERM" and signal["beatRemainsPaused"] is True
    assert acceptance["actualExit"]["rc"] == outcome["actualApiExit"]["rc"] == -15 and acceptance["noSigintFallback"] is True
    log = raw(ROOT / "backend-api.stdout.log")
    chain = value(ROOT / "uvicorn-clean-shutdown-chain.json")
    assert chain == acceptance["cleanShutdownChain"] and chain["pid"] == state["apiPid"]
    expected_lines = [b"INFO:     Waiting for application shutdown.\n", b"INFO:     Application shutdown complete.\n", f"INFO:     Finished server process [{state['apiPid']}]\n".encode()]
    cursor = signal["logOffsetBeforeSignal"]
    for entry, expected in zip(chain["eventsInOrder"], expected_lines, strict=True):
        segment = raw(ROOT / entry["file"])
        assert entry["offsetStart"] >= cursor and log[entry["offsetStart"]:entry["offsetEnd"]] == segment == expected
        assert hashlib.sha256(segment).hexdigest() == entry["rawSha256"]
        cursor = entry["offsetEnd"]
    barrier = value(ROOT / "watchdog-stop-barrier.json")
    barrier_raw = raw(ROOT / "watchdog-stop-barrier.raw.log")
    assert log[barrier["offsetStart"]:barrier["offsetEnd"]] == barrier_raw
    assert hashlib.sha256(barrier_raw).hexdigest() == barrier["rawSha256"]
    event = json.loads(barrier_raw)
    assert event == barrier["record"] and event["logger"] == "app.main" and event["message"] == "停止 Celery Worker 进程..."
    assert event["timestamp"] >= signal["at"] and barrier["offsetStart"] > chain["eventsInOrder"][0]["offsetEnd"]
    beat = value(ROOT / "beat-stop-signals.json")
    assert beat["pid"] == state["beatPausedPid"] and beat["parent"] == state["apiPid"] and beat["createTime"] == state["beatPausedCreated"]
    assert beat["signalsInOrder"] == ["SIGTERM", "SIGCONT"] and beat["watchdogBarrier"] == barrier and beat["at"] > barrier["observedAt"]
    stopped = value(ROOT / "stopped-process-check.json")
    by_pid = {row["pid"]: row for row in state["processesAtStart"]}
    assert len(by_pid) == 7 and stopped["remaining"] == []
    assert {row["pid"] for row in stopped["observed"]} == set(by_pid)
    history = [json.loads(line) for line in raw(ROOT / "process-tree-stop-history.ndjson").decode().splitlines()]
    assert history[-1]["rows"] == []
    for observation in history:
        for row in observation["rows"]:
            origin = by_pid[row["pid"]]
            for key in ("ppid", "created", "argv"):
                assert row[key] == origin[key]
    front = value(ROOT / "frontend-stop.json")
    assert front["pid"] == state["frontendPid"] and front["pgid"] == state["frontendPgid"]
    # psutil.wait for a non-child can return None; do not invent an exit code.
    queue = value(ROOT / "queue-after-stop.json")
    before_queue = value(ROOT / "queue-before-stop.json")
    assert queue["actualBroker"] == {"host": "127.0.0.1", "port": 17124, "db": "/1", "resultDb": "/2"}
    assert queue["redis"]["role"] == "master" and queue["redis"]["runId"] == before_queue["redis"]["runId"]
    assert queue["redis"]["unacked"] == queue["redis"]["unackedIndex"] == 0
    assert all(value == 0 for value in queue["redis"]["brokerLists"].values())
    qcommand_path = next((ROOT / "commands").glob("*queue-after-stop.command.json"))
    qcommand = value(qcommand_path)
    assert qcommand["rc"] == 0
    for stream in ("stdout", "stderr"):
        data = raw(qcommand_path.with_name(qcommand_path.name.replace("command.json", stream + ".log")))
        assert hashlib.sha256(data).hexdigest() == qcommand[stream + "Sha256"]
    phases = []
    raw_files = []
    for phase, suffix in (("before", "before"), ("after-gate", "after-gate"), ("after-stop", "after-stop")):
        summary = value(ROOT / f"capacity-readonly-{suffix}.json")
        tx = value(ROOT / f"capacity-transaction-{suffix}.json")
        identity = json.loads(tx["rawRow"])
        assert identity == tx["parsed"] and identity["database_name"] == "clpm_capacity_20261017_s01"
        assert identity["read_only"] == "on" and identity["isolation"] == "repeatable read"
        assert summary["database"] == identity["database_name"] and len(summary["tables"]) == 9
        for table, meta in summary["tables"].items():
            folder = ROOT / "capacity-raw" / phase
            data, canon = [raw(folder / (table + ext)) for ext in (".ndjson", ".canonical.ndjson")]
            assert len(data) == meta["bytes"] and hashlib.sha256(data).hexdigest() == meta["sha256"]
            assert len(canon) == meta["canonicalBytes"] and hashlib.sha256(canon).hexdigest() == meta["canonicalSha256"]
            rows = [json.loads(line) for line in data.decode().splitlines()]
            assert len(rows) == meta["count"]
            assert [json.loads(line) for line in canon.decode().splitlines()] == rows
            raw_files += [{"phase": phase, "table": table, "rawSha256": meta["sha256"], "canonicalSha256": meta["canonicalSha256"]}]
        assert summary["tables"]["loop_ledger"]["count"] == 1000 and summary["tables"]["loop_tag_mapping"]["count"] == summary["tables"]["tag_registry"]["count"] == 7000
        assert all(meta["count"] == 0 for table, meta in summary["tables"].items() if table not in ("loop_ledger", "loop_tag_mapping", "tag_registry"))
        phases.append(summary)
    assert phases[0]["tables"] == phases[1]["tables"] == phases[2]["tables"]
    native = OUT / "native-cleanup"
    native.mkdir(exist_ok=False)
    commands = {
        "ps": ["ps", "-p", ",".join(map(str, sorted(set(by_pid) | {front['pid']}))), "-o", "pid=,ppid=,lstart=,state=,command="],
        "listeners": ["lsof", "-nP", "-iTCP:17261", "-iTCP:17266", "-sTCP:LISTEN"],
    }

    def observe(item):
        name, argv = item
        start = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(argv, capture_output=True, timeout=10)
        for stream, data in (("stdout", result.stdout), ("stderr", result.stderr)):
            (native / f"{name}.{stream}.log").write_bytes(data)
        return {"name": name, "argv": argv, "startedAtUtc": start, "finishedAtUtc": datetime.now(timezone.utc).isoformat(), "rc": result.returncode, "stdoutSha256": hashlib.sha256(result.stdout).hexdigest(), "stderrSha256": hashlib.sha256(result.stderr).hexdigest(), "empty": not result.stdout.strip() and not result.stderr.strip()}

    with ThreadPoolExecutor(max_workers=2) as pool:
        observed = list(pool.map(observe, commands.items()))
    (native / "commands.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2) + "\n")
    assert all(row["rc"] == 1 and row["empty"] for row in observed)
    result = {
        "reviewedAtUtc": datetime.now(timezone.utc).isoformat(),
        "cleanup": "PASS",
        "actualApiExit": acceptance["actualExit"],
        "requestedTermAndOwnPidThreeRawShutdownLines": True,
        "beatResumeAfterActualWatchdogStopBarrier": True,
        "exactSevenProductIdentitiesNoReplacementAndGone": True,
        "frontendExitCode": front["waitReturnCode"],
        "frontendGoneByIndependentNativeObservation": True,
        "privateBrokerEmpty": True,
        "wholeRedisDatabaseNotClaimedEmpty": True,
        "capacityNineTablesAllRawAndCanonicalBeforeGateAndStopEqual": True,
        "capacityRawFilesVerified": 54,
        "capacityFormalResultsStillZeroInThisObservation": True,
        "independentNativeReadonlyCommands": observed,
        "signalsDatabaseServicesOrQueueOperationsByReviewer": False,
        "readSet": reads,
    }
    (OUT / "cleanup-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: val for key, val in result.items() if key != "readSet"}, ensure_ascii=False))


if __name__ == "__main__":
    run()
