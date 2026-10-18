"""Keeper-controlled, private test TD REST fault proxy; no application imports.

Default policy is DISARMED. Only an exact single-point events SELECT may receive
synthetic HTTP200/code1850. Other SQL is forwarded to the real loopback TD REST.
Never record Authorization/headers, credentials, or credential-bearing SQL bodies.
The proxy does not enqueue tasks, modify TD/PG data itself, or alter app settings.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import signal
import threading
from urllib.parse import urlsplit
import uuid

EXPECTED_DATABASE = "clpm_capacity_20261017_s01"
EXPECTED_LOOP_TAG = "PERF-T0004"
FAULT_BODY = b'{"code":1850,"desc":"Query memory exhausted"}'
CREDENTIAL_SQL = re.compile(
    r"\b(?:pass(?:word|wd)?|secret|token|authorization|identified)\b", re.I
)
EVENTS_SQL = re.compile(
    r"SELECT\s+point_id\s*,\s*ts\s*,\s*`?value`?\s*,\s*quality_class"
    r"\s+FROM\s+`?(?P<database>[a-zA-Z_][a-zA-Z0-9_]*)`?\."
    r"`?st_point_data_v1`?\s+WHERE\s+point_id\s+IN\s*\(\s*'(?P<point>[0-9a-fA-F-]+)'\s*\)"
    r"\s+AND\s+ts\s*>=\s*'(?P<start>[^']+)'\s+AND\s+ts\s*<=\s*'(?P<end>[^']+)'"
    r"\s+ORDER\s+BY\s+ts\s+ASC\s+LIMIT\s+(?P<limit>[1-9][0-9]*)\s*;?\s*",
    re.I,
)
HOP_HEADERS = {
    "connection",
    "content-length",
    "transfer-encoding",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "upgrade",
    "host",
    "server",
    "date",
}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def parse_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Explicit UTC/offset required")
    return parsed.astimezone(timezone.utc)


def validate_policy(policy):
    allowed = {
        "schemaVersion",
        "epoch",
        "armed",
        "database",
        "loopTag",
        "pointId",
        "windows",
        "activeWindow",
        "normalPlan",
        "candidateSha",
    }
    if set(policy) - allowed:
        raise ValueError("Unknown policy fields rejected")
    if policy.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(policy.get("epoch", ""))):
        raise ValueError("Explicit simple policy epoch required")
    if type(policy.get("armed")) is not bool:
        raise ValueError(
            "armed must be explicit boolean (default policy must be false)"
        )
    if (
        policy.get("database") != EXPECTED_DATABASE
        or policy.get("loopTag") != EXPECTED_LOOP_TAG
    ):
        raise ValueError("Only designated private capacity DB/PERF-T0004 permitted")
    point = policy.get("pointId", "")
    if str(uuid.UUID(point)) != point:
        raise ValueError("pointId must be a canonical actual PV UUID")
    if not re.fullmatch(r"[0-9a-f]{40}", policy.get("candidateSha", "")):
        raise ValueError("Frozen full candidateSha required")
    plan_ref = policy.get("normalPlan", {})
    plan_path = Path(plan_ref.get("path", ""))
    plan_raw = plan_path.read_bytes()
    if hashlib.sha256(plan_raw).hexdigest() != plan_ref.get("sha256"):
        raise ValueError("Formal normal plan hash mismatch")
    planned = json.loads(plan_raw)
    if isinstance(planned, dict):
        planned = planned["batches"]
    if len(planned) != 20:
        raise ValueError("Complete normal 20-batch plan required")
    formal_starts = {
        parse_utc(row["ts_start"] + ("Z" if len(row["ts_start"]) == 19 else ""))
        for row in planned
    }
    if len(formal_starts) != 20:
        raise ValueError("Formal plan window starts must be unique")
    windows = policy.get("windows", {})
    if set(windows) != {"recovery", "exhaustion"}:
        raise ValueError("Exactly two extra fault windows required")
    starts = set()
    for name, window in windows.items():
        if set(window) != {"start", "end", "mode", "failFirstN"}:
            raise ValueError("Unknown/missing fault window fields")
        start, end = parse_utc(window["start"]), parse_utc(window["end"])
        if (
            end - start != timedelta(hours=1)
            or end > datetime.now(timezone.utc)
            or (start.minute, start.second, start.microsecond) != (0, 0, 0)
        ):
            raise ValueError("Fault window must be a complete past hour")
        if start in formal_starts:
            raise ValueError("Fault window overlaps formal normal capacity series")
        starts.add(start)
        expected_mode = "fail_first_n" if name == "recovery" else "persistent"
        expected_count = 2 if name == "recovery" else None
        if window["mode"] != expected_mode or window["failFirstN"] != expected_count:
            raise ValueError(
                "Recovery must fail first 2; exhaustion persistent until Keeper disarm"
            )
    if len(starts) != 2 or policy.get("activeWindow") not in windows:
        raise ValueError("Distinct fault windows/explicit activeWindow required")
    return policy


def match_events(sql, policy):
    if CREDENTIAL_SQL.search(sql):
        return False, "credential_sql_not_eligible"
    match = EVENTS_SQL.fullmatch(sql.strip())
    if not match:
        return False, "not_exact_single_point_events_select"
    if match["database"] != policy["database"]:
        return False, "wrong_database"
    if match["point"].lower() != policy["pointId"]:
        return False, "wrong_point"
    window = policy["windows"][policy["activeWindow"]]
    try:
        same_window = parse_utc(match["start"]) == parse_utc(
            window["start"]
        ) and parse_utc(match["end"]) == parse_utc(window["end"])
    except (ValueError, TypeError):
        return False, "invalid_query_window"
    return (
        (True, "exact_target_events")
        if same_window
        else (False, "outside_active_window")
    )


class ProxyState:
    def __init__(self, policy_path, evidence_dir, upstream, timeout=65):
        self.policy_path = Path(policy_path)
        self.out = Path(evidence_dir)
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "responses").mkdir()
        target = urlsplit(upstream)
        if (
            target.scheme != "http"
            or target.hostname != "127.0.0.1"
            or not target.port
            or target.path != "/rest/sql"
            or target.username
            or target.password
            or target.query
            or target.fragment
        ):
            raise ValueError(
                "Upstream must be credential-free http://127.0.0.1:PORT/rest/sql"
            )
        self.target, self.timeout = target, timeout
        self.lock = threading.Lock()
        self.sequence = 0
        self.active_requests = set()
        self.completed_requests = 0
        self.seen_epochs = {}
        self.matches = {}
        policy, policy_hash = self.load_policy()
        if policy["armed"]:
            raise ValueError(
                "Startup policy must be DISARMED; Keeper may arm only after startup verification"
            )
        self.save_json(
            "startup.json",
            {
                "at": utcnow(),
                "upstream": upstream,
                "policySha256": policy_hash,
                "epoch": policy["epoch"],
                "armed": False,
                "sourceSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "boundary": "Private test proxy only; no task enqueue or app/env changes.",
            },
        )

    def save_json(self, name, value):
        with (self.out / name).open("xb") as stream:
            stream.write(encode(value))

    def load_policy(self):
        raw = self.policy_path.read_bytes()
        policy = validate_policy(json.loads(raw))
        digest = hashlib.sha256(raw).hexdigest()
        prior = self.seen_epochs.get(policy["epoch"])
        if prior is not None and prior != digest:
            raise ValueError(
                "Policy epoch reused with different bytes; every arm/disarm/change requires a new epoch"
            )
        self.seen_epochs[policy["epoch"]] = digest
        policy_file = self.out / ("policy-" + digest + ".json")
        if not policy_file.exists():
            policy_file.write_bytes(raw)
        return policy, digest

    def decide(self, body, path):
        with self.lock:
            self.sequence += 1
            self.active_requests.add(self.sequence)
            meta = {
                "sequence": self.sequence,
                "startedAt": utcnow(),
                "method": "POST",
                "path": urlsplit(path).path,
                "sqlSha256": hashlib.sha256(body).hexdigest(),
                "sqlBytes": len(body),
                "headersRecorded": False,
                "authorizationRecorded": False,
            }
            try:
                policy, digest = self.load_policy()
                meta.update(
                    policyEpoch=policy["epoch"],
                    policySha256=digest,
                    armed=policy["armed"],
                    activeWindow=policy["activeWindow"],
                )
            except Exception as exc:
                meta.update(
                    policyValid=False,
                    decision="POLICY_INVALID",
                    errorType=type(exc).__name__,
                    error="Policy validation failed; see operator policy, no secret values logged",
                )
                return meta, False, False
            sql = body.decode("utf-8", "replace")
            sensitive = bool(CREDENTIAL_SQL.search(sql))
            meta["credentialSqlRedacted"] = sensitive
            eligible, reason = match_events(sql, policy)
            if path.split("?", 1)[0] not in (
                "/rest/sql",
                "/rest/sql/" + policy["database"],
            ):
                eligible, reason = False, "not_sql_endpoint"
            window = policy["windows"][policy["activeWindow"]]
            meta.update(
                policyValid=True,
                match=eligible,
                matchReason=reason,
                mode=window["mode"],
            )
            if not sensitive and eligible:
                meta["sql"] = sql
            inject = False
            if eligible and policy["armed"]:
                key = (policy["epoch"], policy["activeWindow"])
                count = self.matches.get(key, 0) + 1
                self.matches[key] = count
                meta["matchingRequestOrdinal"] = count
                inject = count <= 9
            meta.update(
                injected=inject,
                decision="INJECT_0x073a" if inject else "FORWARD_REAL_TD",
            )
            return meta, inject, True

    def record(self, meta, status, response, transport_rc):
        meta.update(
            finishedAt=utcnow(),
            httpStatus=status,
            transportRc=transport_rc,
            responseSha256=hashlib.sha256(response).hexdigest(),
            responseBytes=len(response),
        )
        try:
            payload = json.loads(response)
            meta["tdCode"] = payload.get("code") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            meta["tdCode"] = None
        with self.lock:
            if not meta.get("credentialSqlRedacted", False):
                name = "responses/" + meta["responseSha256"] + ".bin.gz"
                path = self.out / name
                if not path.exists():
                    path.write_bytes(gzip.compress(response, mtime=0))
                meta.update(
                    responseFile=name,
                    responseGzipSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            else:
                meta["responseFile"] = None
                meta["responseRedacted"] = (
                    "Credential-bearing SQL: response bytes not archived, only hashes"
                )
            with (self.out / "requests.jsonl").open("ab") as stream:
                stream.write(encode(meta) + b"\n")
            self.active_requests.discard(meta["sequence"])
            self.completed_requests += 1


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/__audit_proxy_health":
            self.send_error(404)
            return
        state = self.server.state
        with state.lock:
            try:
                policy, digest = state.load_policy()
                body = encode(
                    {
                        "status": "READY",
                        "armed": policy["armed"],
                        "epoch": policy["epoch"],
                        "activeWindow": policy["activeWindow"],
                        "policySha256": digest,
                        "requestCount": state.sequence,
                        "completedRequestCount": state.completed_requests,
                        "inFlight": len(state.active_requests),
                    }
                )
                code = 200
            except Exception:
                body, code = encode({"status": "POLICY_INVALID"}), 503
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        state = self.server.state
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > 32 * 1024 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        meta, inject, valid = state.decide(body, self.path)
        transport_rc, headers = 0, {"Content-Type": "application/json"}
        if not valid:
            status, response = (
                503,
                b'{"code":65535,"desc":"AUDIT_PROXY_POLICY_INVALID"}',
            )
            transport_rc = 78
        elif inject:
            status, response = 200, FAULT_BODY
        else:
            connection = http.client.HTTPConnection(
                state.target.hostname, state.target.port, timeout=state.timeout
            )
            try:
                forwarded = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in HOP_HEADERS
                }
                connection.request("POST", self.path, body=body, headers=forwarded)
                upstream = connection.getresponse()
                status, response = upstream.status, upstream.read()
                headers = {
                    key: value
                    for key, value in upstream.getheaders()
                    if key.lower() not in HOP_HEADERS
                }
            except Exception as exc:
                status, response, transport_rc = (
                    502,
                    b'{"code":65534,"desc":"AUDIT_PROXY_UPSTREAM_TRANSPORT_FAILED"}',
                    70,
                )
                meta.update(
                    upstreamErrorType=type(exc).__name__,
                    upstreamError="Transport failed; no request headers or credentials recorded",
                )
            finally:
                connection.close()
        state.record(meta, status, response, transport_rc)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        try:
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, state):
        if address[0] != "127.0.0.1":
            raise ValueError("Listen host must be 127.0.0.1")
        self.state = state
        super().__init__(address, ProxyHandler)


def main(args):
    if not args.serve:
        raise SystemExit(
            "Use --serve only as Keeper after independent tool review; default does not start a listener."
        )
    state = ProxyState(args.policy, args.out, args.upstream, args.timeout)
    server = ProxyServer(("127.0.0.1", args.listen_port), state)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        json.dumps(
            {
                "status": "READY_DISARMED",
                "listen": server.server_address,
                "at": utcnow(),
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        state.save_json(
            "shutdown.json",
            {
                "at": utcnow(),
                "requestCount": state.sequence,
                "completedRequestCount": state.completed_requests,
                "inFlightAtStop": len(state.active_requests),
                "status": "STOPPED",
            },
        )
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--serve", action="store_true")
    p.add_argument("--policy", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--listen-port", type=int, default=17125)
    p.add_argument("--upstream", default="http://127.0.0.1:17115/rest/sql")
    p.add_argument("--timeout", type=float, default=65)
    raise SystemExit(main(p.parse_args()))
