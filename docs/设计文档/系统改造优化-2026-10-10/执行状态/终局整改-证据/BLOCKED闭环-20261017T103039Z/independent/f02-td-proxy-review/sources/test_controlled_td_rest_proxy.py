"""Real temporary loopback-stub checks. No application/real TD/processes touched."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import gzip
import hashlib
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import threading
import select
import subprocess
import sys
from types import SimpleNamespace

import pytest
from controlled_td_rest_proxy import (
    EXPECTED_DATABASE,
    FAULT_BODY,
    ProxyServer,
    ProxyState,
    main,
    match_events,
    validate_policy,
)

POINT = "00000000-0000-4000-8000-000000000004"
OTHER = "00000000-0000-4000-8000-000000000005"
START = "2026-10-14T00:00:00.000Z"
END = "2026-10-14T01:00:00.000Z"
SQL = f"SELECT point_id, ts, `value`, quality_class FROM {EXPECTED_DATABASE}.st_point_data_v1 WHERE point_id IN ('{POINT}') AND ts >= '{START}' AND ts <= '{END}' ORDER BY ts ASC LIMIT 50000"
STUB_BODY = b'{"code":0,"column_meta":[["stub","INT",4]],"data":[[42]],"rows":1}'


def make_policy(tmp_path):
    plan = [
        {
            "batch": i + 1,
            "ts_start": (datetime(2026, 10, 15, 12) + timedelta(hours=i)).isoformat(),
        }
        for i in range(20)
    ]
    path = tmp_path / "formal-plan.json"
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    return {
        "schemaVersion": 1,
        "epoch": "start-disarmed",
        "armed": False,
        "database": EXPECTED_DATABASE,
        "loopTag": "PERF-T0004",
        "pointId": POINT,
        "candidateSha": "a" * 40,
        "normalPlan": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
        "activeWindow": "recovery",
        "windows": {
            "recovery": {
                "start": START,
                "end": END,
                "mode": "fail_first_n",
                "failFirstN": 2,
            },
            "exhaustion": {
                "start": "2026-10-14T01:00:00Z",
                "end": "2026-10-14T02:00:00Z",
                "mode": "persistent",
                "failFirstN": None,
            },
        },
    }


def write_policy(path, policy):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(policy))
    pending.replace(path)


def post(port, body=SQL, headers=None, path="/rest/sql"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=body.encode(), headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append(
            {"body": body, "hasAuthorization": bool(self.headers.get("Authorization"))}
        )
        if getattr(self.server, "drop_response", False):
            self.close_connection = True
            return
        response = (
            body
            if b"CREATE USER" in body
            else getattr(self.server, "response_bytes", STUB_BODY)
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class StubServer(ThreadingHTTPServer):
    request_queue_size = 128


@pytest.fixture
def harness(tmp_path, request):
    stub = StubServer(("127.0.0.1", 0), Stub)
    stub.requests = []
    stub_thread = threading.Thread(
        target=stub.serve_forever, kwargs={"poll_interval": 0.01}
    )
    stub_thread.start()
    policy = make_policy(tmp_path)
    path = tmp_path / "policy.json"
    write_policy(path, policy)
    state = ProxyState(
        path,
        tmp_path / "proxy",
        f"http://127.0.0.1:{stub.server_address[1]}/rest/sql",
        timeout=1,
    )
    proxy = ProxyServer(("127.0.0.1", 0), state)
    proxy_thread = threading.Thread(
        target=proxy.serve_forever, kwargs={"poll_interval": 0.01}
    )
    proxy_thread.start()
    item = SimpleNamespace(
        stub=stub,
        proxy=proxy,
        policy=policy,
        path=path,
        state=state,
        port=proxy.server_address[1],
    )
    try:
        yield item
    finally:
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join(timeout=5)
        stub.shutdown()
        stub.server_close()
        stub_thread.join(timeout=5)
        assert not proxy_thread.is_alive() and not stub_thread.is_alive()
        root = os.environ.get("CLPM_PROXY_TEST_EVIDENCE")
        if root:
            target = Path(root) / request.node.name
            target.mkdir(parents=True, exist_ok=False)
            shutil.copytree(state.out, target / "proxy")
            (target / "cleanup.json").write_text(
                json.dumps(
                    {
                        "stubPort": stub.server_address[1],
                        "proxyPort": proxy.server_address[1],
                        "stubThreadAlive": False,
                        "proxyThreadAlive": False,
                        "realServiceTouched": False,
                        "requestsReceivedByStub": len(stub.requests),
                    }
                )
            )


def arm(h, mode="recovery", epoch="armed"):
    h.policy.update(epoch=epoch, armed=True, activeWindow=mode)
    write_policy(h.path, h.policy)


def logs(h):
    return [
        json.loads(line)
        for line in (h.state.out / "requests.jsonl").read_text().splitlines()
    ]


def test_disarmed_target_is_real_passthrough_and_response_restorable(harness):
    h = harness
    assert post(h.port) == (200, STUB_BODY)
    assert len(h.stub.requests) == 1
    row = logs(h)[0]
    assert (
        row["armed"] is False
        and row["injected"] is False
        and row["transportRc"] == 0
        and row["tdCode"] == 0
    )
    raw = gzip.decompress((h.state.out / row["responseFile"]).read_bytes())
    assert raw == STUB_BODY and hashlib.sha256(raw).hexdigest() == row["responseSha256"]


def test_recovery_only_two_matching_requests_fail_then_real_forward(harness):
    h = harness
    arm(h)
    assert post(h.port, SQL.replace(POINT, OTHER)) == (200, STUB_BODY)
    assert [post(h.port) for _ in range(3)] == [
        (200, FAULT_BODY),
        (200, FAULT_BODY),
        (200, STUB_BODY),
    ]
    assert len(h.stub.requests) == 2
    rows = logs(h)
    assert [r.get("matchingRequestOrdinal") for r in rows] == [None, 1, 2, 3]
    assert [r["tdCode"] for r in rows] == [0, 1850, 1850, 0]


def test_persistent_does_not_disarm_after_nine_or_twelve_and_keeper_disarms(harness):
    h = harness
    arm(h, "exhaustion", "persistent")
    sql = SQL.replace(START, "2026-10-14T01:00:00Z").replace(
        END, "2026-10-14T02:00:00Z"
    )
    assert all(post(h.port, sql) == (200, FAULT_BODY) for _ in range(12))
    assert len(h.stub.requests) == 0
    h.policy.update(epoch="keeper-disarm", armed=False)
    write_policy(h.path, h.policy)
    assert post(h.port, sql) == (200, STUB_BODY)
    assert logs(h)[-1]["injected"] is False


def test_concurrent_recovery_counter_is_atomic(harness):
    h = harness
    arm(h)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: post(h.port), range(20)))
    assert responses.count((200, FAULT_BODY)) == 2
    assert responses.count((200, STUB_BODY)) == 18
    assert sorted(r["matchingRequestOrdinal"] for r in logs(h)) == list(range(1, 21))


def test_authorization_and_credential_sql_are_not_archived(harness):
    h = harness
    arm(h)
    sentinel = "SECRET_SENTINEL_9a71"
    assert post(h.port, headers={"Authorization": "Basic " + sentinel}) == (
        200,
        FAULT_BODY,
    )
    credential = "CREATE USER audit_user PASS '" + sentinel + "'"
    assert post(
        h.port,
        credential,
        {"Authorization": "Basic " + sentinel},
        path="/rest/sql?password=" + sentinel,
    ) == (200, credential.encode())
    for path in h.state.out.rglob("*"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes()
    row = logs(h)[-1]
    assert row["credentialSqlRedacted"] and row["responseFile"] is None


def test_epoch_cannot_be_reused_to_reset_count_and_invalid_policy_is_visible(harness):
    h = harness
    arm(h)
    assert post(h.port) == (200, FAULT_BODY)
    h.policy["armed"] = False
    write_policy(h.path, h.policy)
    status, body = post(h.port)
    assert status == 503 and b"AUDIT_PROXY_POLICY_INVALID" in body
    assert logs(h)[-1]["transportRc"] == 78
    h.policy["epoch"] = "fresh-disarmed"
    write_policy(h.path, h.policy)
    assert post(h.port) == (200, STUB_BODY)


@pytest.mark.parametrize(
    "query",
    [
        "SHOW DNODE 1 VARIABLES",
        "CREATE DATABASE clpm_capacity_20261017_s01",
        f"SELECT COUNT(*) FROM {EXPECTED_DATABASE}.st_point_data_v1",
        f"SELECT point_id, LAST_ROW(ts) AS last_ts FROM {EXPECTED_DATABASE}.st_point_data_v1 WHERE point_id IN ('{POINT}') AND ts < '{START}' GROUP BY point_id",
        SQL.replace(POINT, OTHER),
        SQL.replace(EXPECTED_DATABASE, "clpm_ts"),
        SQL.replace("ts <=", "ts <"),
        SQL.replace(START, "2026-10-14T03:00:00Z"),
        SQL.replace("'" + POINT + "'", "'" + POINT + "', '" + OTHER + "'"),
        SQL + "; SELECT 1",
        "/* hidden */ " + SQL,
    ],
)
def test_non_target_queries_cannot_match(tmp_path, query):
    assert match_events(query, make_policy(tmp_path))[0] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(database="clpm_ts"),
        lambda p: p.update(loopTag="PERF-T0005"),
        lambda p: p.update(pointId="not-a-uuid"),
        lambda p: p.update(armed=1),
        lambda p: p.update(candidateSha="short"),
        lambda p: p["normalPlan"].update(sha256="0" * 64),
        lambda p: p["windows"]["recovery"].update(failFirstN=3),
        lambda p: p["windows"]["exhaustion"].update(mode="fail_first_n", failFirstN=9),
        lambda p: p["windows"]["recovery"].update(
            start="2026-10-15T12:00:00Z", end="2026-10-15T13:00:00Z"
        ),
        lambda p: p["windows"]["recovery"].update(end="2026-10-14T00:30:00Z"),
        lambda p: p["windows"]["recovery"].update(
            start="2999-10-14T00:00:00Z", end="2999-10-14T01:00:00Z"
        ),
    ],
)
def test_policy_guards_reject_wrong_scope_and_relaxed_failure_contract(
    tmp_path, mutation
):
    policy = make_policy(tmp_path)
    mutation(policy)
    with pytest.raises((ValueError, TypeError)):
        validate_policy(policy)


def test_startup_requires_disarmed_and_no_external_upstream_or_listener(tmp_path):
    policy = make_policy(tmp_path)
    policy["armed"] = True
    path = tmp_path / "policy.json"
    write_policy(path, policy)
    with pytest.raises(ValueError, match="DISARMED"):
        ProxyState(path, tmp_path / "armed", "http://127.0.0.1:1/rest/sql")
    policy["armed"] = False
    write_policy(path, policy)
    with pytest.raises(ValueError, match="Upstream"):
        ProxyState(path, tmp_path / "external", "http://example.com:80/rest/sql")
    state = ProxyState(path, tmp_path / "state", "http://127.0.0.1:1/rest/sql")
    with pytest.raises(ValueError, match="Listen host"):
        ProxyServer(("0.0.0.0", 0), state)


def test_no_serve_flag_does_not_open_any_listener():
    with pytest.raises(SystemExit, match="does not start"):
        main(SimpleNamespace(serve=False))


def test_real_upstream_business_error_is_preserved_not_labelled_injected(harness):
    h = harness
    h.stub.response_bytes = FAULT_BODY
    assert post(h.port) == (200, FAULT_BODY)
    row = logs(h)[-1]
    assert (
        row["injected"] is False and row["transportRc"] == 0 and row["tdCode"] == 1850
    )


def test_real_upstream_transport_failure_is_explicit_not_data_insufficient(harness):
    h = harness
    h.stub.drop_response = True
    status, body = post(h.port)
    assert status == 502 and b"AUDIT_PROXY_UPSTREAM_TRANSPORT_FAILED" in body
    row = logs(h)[-1]
    assert row["transportRc"] == 70 and row["injected"] is False


def test_actual_cli_ephemeral_listener_sigterm_true_rc_zero_and_cleanup(
    harness, tmp_path
):
    h = harness
    source = Path(__file__).with_name("controlled_td_rest_proxy.py")
    out = tmp_path / "cli-proxy"
    command = [
        sys.executable,
        str(source),
        "--serve",
        "--policy",
        str(h.path),
        "--out",
        str(out),
        "--listen-port",
        "0",
        "--upstream",
        f"http://127.0.0.1:{h.stub.server_address[1]}/rest/sql",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    line = ""
    try:
        assert select.select([process.stdout], [], [], 5)[0], (
            "Own temporary CLI did not announce readiness"
        )
        line = process.stdout.readline()
        ready = json.loads(line)
        assert ready["status"] == "READY_DISARMED"
        assert post(ready["listen"][1]) == (200, STUB_BODY)
    finally:
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0 and (out / "shutdown.json").exists()
    root = os.environ.get("CLPM_PROXY_TEST_EVIDENCE")
    if root:
        evidence = Path(root) / "cli-process-raw"
        evidence.mkdir(exist_ok=False)
        shutil.copytree(out, evidence / "proxy")
        (evidence / "stdout.log").write_text(line + stdout)
        (evidence / "stderr.log").write_text(stderr)
        (evidence / "command.json").write_text(
            json.dumps(
                {
                    "argv": command,
                    "rc": process.returncode,
                    "ephemeralLoopbackOnly": True,
                    "actualServiceTouched": False,
                    "terminatedOwnPid": process.pid,
                }
            )
        )
