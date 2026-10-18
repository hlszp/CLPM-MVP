"""Supervise the bound series CLI with the independent method-05 native producer.

This closes orchestration/coverage only; seven birth identities require a separate
non-author per-business-span decision. No Worker/Beat/product-task signal is sent.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def stamp():
    return datetime.now(UTC).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def identity(path):
    path = Path(path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "native timestamps must include their zone")
    return result.astimezone(UTC)


def load(protocol_path, output, argv):
    protocol_path, output = Path(protocol_path).resolve(), Path(output).resolve()
    protocol = read(protocol_path)
    for key in ("supervisor", "runner", "nativeObserver"):
        entry = protocol["tools"][key]
        path = Path(entry["path"])
        require(path.is_absolute() and digest(path) == entry["sha256"], f"{key}: byte mismatch")
    require(
        Path(protocol["tools"]["supervisor"]["path"]).resolve() == Path(__file__).resolve(),
        "supervisor self path mismatch",
    )
    spec = importlib.util.spec_from_file_location(
        "supervised_capacity_runner", protocol["tools"]["runner"]["path"]
    )
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    ctx = runner.load_contract(protocol_path)
    require(ctx.protocol == protocol, "protocol changed during preflight")
    execution, config = protocol["execution"], protocol["execution"]["supervision"]
    require(ctx.output_parent == output, "runner outputParent must be the supervision directory")
    require(output.parent == Path(config["outputParent"]).resolve(), "unbound supervision parent")
    expected = [
        str(ctx.python),
        "-B",
        str(Path(__file__).resolve()),
        "--protocol",
        str(protocol_path),
        "--output-dir",
        str(output),
    ]
    require(config["approvedInvocation"] == expected, "unapproved supervisor invocation")
    require(argv == expected or argv == [*expected, "--check-only"], "actual argv differs")
    require(
        execution["invocation"]
        == [
            str(ctx.python),
            "-B",
            ctx.tools["runner"]["path"],
            "--protocol",
            str(protocol_path),
            "--evidence-root",
            str(ctx.evidence),
        ],
        "unapproved runner invocation",
    )
    require(
        config["nativeIntervalSeconds"] == 2 and config["maximumGapSeconds"] == 5,
        "method-05 sampling contract changed",
    )
    require(config["requireSloPass"] is True, "formal supervision requires strict 600s SLO")
    for key in ("pollSeconds", "startupTimeoutSeconds", "stopTimeoutSeconds", "nativeMaxSeconds"):
        require(
            type(config[key]) in (int, float) and math.isfinite(config[key]) and config[key] > 0,
            f"invalid {key}",
        )
    require(config["pollSeconds"] <= 0.25, "supervisor polling is too slow")
    require(
        bool(re.fullmatch("[0-9a-f]{40}", config["backendTreeSha"])),
        "backend tree identity missing",
    )
    require(bool(re.fullmatch("[0-9a-f]{64}", config["taosConfigSha256"])), "taos identity missing")
    return runner, ctx, config


class NativeStream:
    """Read the producer's actual append stream; never trust summary maximumGap."""

    def __init__(self, ctx, config, root):
        self.ctx, self.config, self.root = ctx, config, root
        self.file, self.pending, self.rows, self.refs = None, b"", [], {}
        self.plan_hash, self.last_tick = None, None
        self.command_times = {}

    def poll(self):
        path = self.root / "observations.jsonl"
        if not path.exists():
            return
        if self.file is None:
            plan = read(self.root / "plan.json")
            p, c = self.ctx.protocol, self.config
            require(
                plan["expectedSha"] == p["candidateSha"]
                and Path(plan["runtime"]).resolve() == self.ctx.runtime,
                "native plan runtime/candidate mismatch",
            )
            require(
                plan["toolSha256"] == self.ctx.tools["nativeObserver"]["sha256"]
                and plan["nativeEnvSha256"] == p["configFreeze"]["envSha256"]
                and plan["taosConfigSha256"] == c["taosConfigSha256"],
                "native plan hash mismatch",
            )
            require(
                plan["intervalSeconds"] == 2 and plan["maximumAcceptedIntervalSeconds"] == 5,
                "native plan sampling mismatch",
            )
            require(plan["maxSeconds"] == c["nativeMaxSeconds"], "native duration mismatch")
            require(Path(plan["stopFile"]) == self.root / "STOP", "native stop path mismatch")
            self.plan_hash = digest(self.root / "plan.json")
            self.file = path.open("rb")
        require(path.stat().st_size >= self.file.tell(), "native stream truncated")
        require(digest(self.root / "plan.json") == self.plan_hash, "native plan changed")
        self.pending += self.file.read()
        while b"\n" in self.pending:
            raw, self.pending = self.pending.split(b"\n", 1)
            row = json.loads(raw)
            tick = row["monotonicSeconds"]
            require(type(tick) in (int, float) and math.isfinite(tick), "invalid native clock")
            require(row["sequence"] == len(self.rows), "native sequence missing/reused")
            require(
                self.last_tick is None or 0 < tick - self.last_tick <= 5,
                "native actual sampling gap exceeds 5s",
            )
            require(row["observationValid"] is True, "native observation rejected")
            p, c = self.ctx.protocol, self.config
            versions = row["gitCommand"]["stdout"].splitlines()
            require(
                versions == [p["candidateSha"], c["backendTreeSha"]]
                and row["sourceShaObserved"] == p["candidateSha"]
                and row["backendTreeObserved"] == c["backendTreeSha"],
                "native Git identity mismatch",
            )
            require(
                row["nativeEnvSha256Observed"] == p["configFreeze"]["envSha256"]
                and row["taosConfigSha256Observed"] == c["taosConfigSha256"],
                "native environment identity mismatch",
            )
            for key in ("psCommand", "gitCommand"):
                command = row[key]
                began = utc(command["startedAt"])
                require(
                    command["rc"] == 0 and began <= utc(command["finishedAt"]) <= datetime.now(UTC),
                    "native command failed",
                )
                if key in self.command_times:
                    require(
                        0 < (began - self.command_times[key]).total_seconds() <= 5,
                        "native actual command gap exceeds 5s",
                    )
                self.command_times[key] = began
            for ref in row["firstSeenLsofRefs"]:
                target = Path(ref["path"]).resolve()
                require(target.is_relative_to(self.root.resolve()), "foreign native lsof reference")
                require(digest(target) == ref["sha256"], "native lsof hash mismatch")
                require(read(target)["actualLsof"]["rc"] == 0, "native first-seen lsof failed")
                self.refs[str(target)] = ref["sha256"]
            self.rows.append(hashlib.sha256(raw).hexdigest())
            self.last_tick = tick
        if self.rows:
            require(0 <= time.monotonic() - self.last_tick <= 5, "native producer stalled")

    def finish(self):
        self.poll()
        require(self.rows and not self.pending, "native stream empty/incomplete")
        path = self.root / "observations.jsonl"
        require(
            [hashlib.sha256(r).hexdigest() for r in path.read_bytes().splitlines()] == self.rows,
            "native stream prefix changed",
        )
        summary = read(self.root / "summary.json")
        require(
            summary["stopReason"] == "requested-stop"
            and not summary["errors"]
            and not summary.get("signals", [])
            and summary["expectedSha"] == self.ctx.protocol["candidateSha"]
            and summary["pollCount"] == len(self.rows),
            "native summary incomplete",
        )
        require(summary["planSha256"] == self.plan_hash, "native summary plan mismatch")
        require(summary["observations"] == identity(path), "native whole-stream hash mismatch")
        refs = []
        for path, expected in self.refs.items():
            require(digest(path) == expected, "native lsof bytes changed")
            refs.append(identity(path))
        return {
            "plan": identity(self.root / "plan.json"),
            "summary": identity(self.root / "summary.json"),
            "observations": identity(self.root / "observations.jsonl"),
            "lsof": refs,
            "pollCount": len(self.rows),
            "sevenIdentityAcceptance": "PENDING",
        }

    def close(self):
        if self.file:
            self.file.close()


class Client:
    """Only direct, owned CLI handles; never a process-group or PID scan."""

    def __init__(self, argv, label, cwd):
        self.label, self.process = label, None
        self.handles = [
            label.with_suffix(".stdout.log").open("xb"),
            label.with_suffix(".stderr.log").open("xb"),
        ]
        self.record = {"argv": argv, "cwd": str(cwd), "startedAt": stamp(), "rc": None}
        try:
            self.process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=self.handles[0],
                stderr=self.handles[1],
                start_new_session=True,
            )
            self.record["pid"] = self.process.pid
        except Exception as exc:
            self.record["exception"] = repr(exc)
            self.finish()
            raise

    def finish(self):
        for handle in self.handles:
            handle.close()
        self.record.update(finishedAt=stamp(), rc=self.process.poll() if self.process else None)
        for name in ("stdout", "stderr"):
            self.record[name] = identity(self.label.with_suffix(f".{name}.log"))
        write(self.label.with_suffix(".command.json"), self.record)


def supervise(runner, ctx, config, output, requested):
    output.mkdir(parents=True, exist_ok=False)
    native_root, series = output / "native", output / "series"
    clients, stream, failure = {}, NativeStream(ctx, config, native_root), None
    protocol_sha = digest(ctx.protocol_path)
    parse_pending = None
    completion = None

    def inspect_outcomes():
        nonlocal parse_pending
        path = series / "outcomes.json"
        if not path.exists():
            return []
        try:
            outcomes = read(path)
        except json.JSONDecodeError:
            # The immutable v4 runner replaces this file in place. A short
            # partial write is not a completed batch; a persistent defect fails.
            parse_pending = parse_pending or time.monotonic()
            require(time.monotonic() - parse_pending <= 1, "series outcomes remain malformed")
            return []
        parse_pending = None
        for result in outcomes:
            seconds = result["seconds"]
            require(
                result["sloPassed"] is True
                and type(seconds) in (int, float)
                and math.isfinite(seconds)
                and 0 <= seconds <= 600,
                "formal 600s SLO failed; verified batch evidence preserved",
            )
        return outcomes

    def verify_bytes():
        require(digest(ctx.protocol_path) == protocol_sha, "protocol byte drift")
        for group in (ctx.tools, ctx.protocol["methodBindings"]):
            for name, entry in group.items():
                require(digest(entry["path"]) == entry["sha256"], f"{name}: tool byte drift")

    def check():
        require(not requested, f"supervisor signal requested: {requested}")
        verify_bytes()
        require(clients["native"].process.poll() is None, "native observer exited early")
        stream.poll()
        inspect_outcomes()

    def until(predicate, timeout):
        started = time.monotonic()
        while True:
            check()
            if predicate():
                return
            require(time.monotonic() - started < timeout, "native coverage/startup timed out")
            time.sleep(config["pollSeconds"])

    try:
        native_argv = [
            str(ctx.python),
            "-B",
            ctx.tools["nativeObserver"]["path"],
            "--expected-sha",
            ctx.protocol["candidateSha"],
            "--out",
            str(native_root),
            "--interval",
            "2",
            "--max-seconds",
            str(config["nativeMaxSeconds"]),
        ]
        clients["native"] = Client(native_argv, output / "native-producer", ctx.cwd)
        until(lambda: bool(stream.rows), config["startupTimeoutSeconds"])
        clients["runner"] = Client(
            [*ctx.protocol["execution"]["invocation"], "--output-dir", str(series)],
            output / "runner",
            ctx.cwd,
        )
        while clients["runner"].process.poll() is None:
            check()
            time.sleep(config["pollSeconds"])
        require(clients["runner"].process.returncode == 0, "runner nonzero exit")
        ended = time.monotonic()
        until(lambda: stream.last_tick >= ended, 5)
        write(native_root / "STOP", {"requestedAt": stamp(), "reason": "runner finished"})
        clients["native"].process.wait(timeout=config["stopTimeoutSeconds"])
        require(clients["native"].process.returncode == 0, "native observer nonzero exit")
        native_evidence = stream.finish()
        verify_bytes()
        complete = read(series / "complete.json")
        require(
            complete["status"] == "COMPLETE_AWAITING_INDEPENDENT_ACCEPTANCE"
            and complete["required"]
            == complete["executed"]
            == complete["businessReportsPassed"]
            == complete["resourceReportsVerified"]
            == 20
            and complete["independentAcceptance"] == "PENDING"
            and complete["evidenceLevel"] == ctx.protocol["execution"]["evidenceLevel"],
            "runner series is not complete",
        )
        outcomes = inspect_outcomes()
        require(
            len(outcomes) == 20 and complete["sloPassed"] == 20,
            "formal series lacks 20 SLO-qualified outcomes",
        )
        completion = {
            "finishedAt": stamp(),
            "status": "SUPERVISION_COMPLETE_PENDING_INDEPENDENT_ACCEPTANCE",
            "evidenceLevel": ctx.protocol["execution"]["evidenceLevel"],
            "series": identity(series / "complete.json"),
            "native": native_evidence,
            "requireSloPass": True,
            "sevenIdentityAcceptance": "PENDING",
            "independentAcceptance": "PENDING",
        }
    except BaseException as exc:
        failure = exc
        write(
            output / "supervision-failure.json",
            {
                "at": stamp(),
                "type": type(exc).__name__,
                "message": str(exc),
                "signals": list(requested),
                "independentAcceptance": "PENDING",
            },
        )
        raise
    finally:
        # SIGINT reaches the runner's BaseException/finally boundary. Only this
        # owned client may be escalated; no descendant/Worker/Beat lookup exists.
        cleanup_errors = []
        for name in ("runner", "native"):
            client = clients.get(name)
            if client is None:
                continue
            process = client.process
            try:
                if process.poll() is None:
                    if (
                        name == "native"
                        and native_root.exists()
                        and not (native_root / "STOP").exists()
                    ):
                        write(native_root / "STOP", {"requestedAt": stamp(), "reason": "cleanup"})
                        client.record["cleanupRequest"] = "STOP_FILE"
                    elif name == "runner":
                        try:
                            process.send_signal(signal.SIGINT)
                            client.record["cleanupRequest"] = "OWNED_CLIENT_SIGINT"
                        except ProcessLookupError:
                            pass  # The owned client exited between poll and signal.
                    try:
                        process.wait(timeout=config["stopTimeoutSeconds"])
                    except subprocess.TimeoutExpired:
                        process.kill()
                        client.record["cleanupEscalation"] = "OWNED_CLIENT_SIGKILL"
                        process.wait(timeout=10)
            except Exception as exc:
                cleanup_errors.append({"client": name, "message": str(exc)})
            finally:
                client.record["supervisionFailed"] = failure is not None
                try:
                    client.finish()
                except Exception as exc:
                    cleanup_errors.append({"client": name, "evidenceError": str(exc)})
        stream.close()
        if cleanup_errors:
            write(output / "owned-cleanup-failure.json", {"errors": cleanup_errors})
            raise RuntimeError("owned client cleanup/evidence failed")
    if requested:
        write(
            output / "supervision-failure.json",
            {"at": stamp(), "signals": list(requested), "message": "signal during finalization"},
        )
        raise InterruptedError("signal during supervisor finalization")
    write(output / "supervision-complete.json", completion)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    actual = (
        sys.orig_argv
        if argv is None
        else [sys.executable, "-B", str(Path(__file__).resolve()), *argv]
    )
    try:
        runner, ctx, config = load(args.protocol, args.output_dir, actual)
        require(not Path(args.output_dir).exists(), "supervision directory already exists")
        if args.check_only:
            print(
                json.dumps(
                    {
                        "status": "SUPERVISOR_BINDINGS_VALIDATED",
                        "formalReady": ctx.protocol["formalReady"],
                    }
                )
            )
            return 0
        runner.require_ready(ctx)
    except Exception as exc:
        print(json.dumps({"status": "PREFLIGHT_REJECTED", "message": str(exc)}), file=sys.stderr)
        return 2
    requested, previous = [], {}
    for number in (signal.SIGINT, signal.SIGTERM):
        previous[number] = signal.signal(number, lambda value, _frame: requested.append(value))
    try:
        supervise(runner, ctx, config, Path(args.output_dir).resolve(), requested)
        return 0
    except BaseException as exc:
        print(json.dumps({"status": "SUPERVISION_FAILED", "message": str(exc)}), file=sys.stderr)
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
