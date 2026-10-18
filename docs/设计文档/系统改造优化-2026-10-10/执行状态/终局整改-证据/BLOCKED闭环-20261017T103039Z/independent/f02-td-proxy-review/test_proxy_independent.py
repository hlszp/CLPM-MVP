"""Own loopback-only oracle; no author test fixture or real service is used."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest


SOURCE = Path(os.environ["INDEPENDENT_PROXY_SOURCE"])
SPEC = importlib.util.spec_from_file_location("reviewed_proxy", SOURCE)
PROXY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROXY)
POINT = "00000000-0000-4000-8000-000000000004"
OTHER = "00000000-0000-4000-8000-000000000099"
DATABASE = "clpm_capacity_20261017_s01"
START = "2026-10-14T10:00:00Z"
END = "2026-10-14T11:00:00Z"
EX_START = "2026-10-14T11:00:00Z"
EX_END = "2026-10-14T12:00:00Z"
FAULT = b'{"code":1850,"desc":"Query memory exhausted"}'
REAL = b'{"code":0,"column_meta":[["value","DOUBLE",8]],"data":[[12.125]],"rows":1}'


def sql(start=START, end=END):
    return (
        f"SELECT point_id, ts, `value`, quality_class FROM {DATABASE}.st_point_data_v1 "
        f"WHERE point_id IN ('{POINT}') AND ts >= '{start}' AND ts <= '{end}' "
        "ORDER BY ts ASC LIMIT 50000"
    )


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with self.server.guard:
            self.server.seen.append(
                {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "path": self.path,
                    "authorizationPresent": bool(self.headers.get("Authorization")),
                }
            )
        if self.server.drop:
            self.close_connection = True
            return
        response = raw if raw.startswith(b"CREATE USER") else self.server.body
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Independent-Stub", "yes")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class OwnServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


class IndependentProxyTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(os.environ["INDEPENDENT_PROXY_ARTIFACTS"]) / self._testMethodName
        self.out.mkdir(parents=True, exist_ok=False)
        self.upstream = OwnServer(("127.0.0.1", 0), Upstream)
        self.upstream.body = REAL
        self.upstream.status = 200
        self.upstream.drop = False
        self.upstream.guard = threading.Lock()
        self.upstream.seen = []
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.upstream_thread.start()
        plan = [
            {"ts_start": (datetime(2026, 10, 15, 12) + timedelta(hours=i)).isoformat()}
            for i in range(20)
        ]
        plan_path = self.out / "formal-plan-synthetic.json"
        plan_path.write_text(json.dumps(plan))
        self.policy = {
            "schemaVersion": 1,
            "epoch": "independent-initial",
            "armed": False,
            "database": DATABASE,
            "loopTag": "PERF-T0004",
            "pointId": POINT,
            "candidateSha": "e" * 40,
            "normalPlan": {
                "path": str(plan_path),
                "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            },
            "activeWindow": "recovery",
            "windows": {
                "recovery": {
                    "start": START,
                    "end": END,
                    "mode": "fail_first_n",
                    "failFirstN": 2,
                },
                "exhaustion": {
                    "start": EX_START,
                    "end": EX_END,
                    "mode": "persistent",
                    "failFirstN": None,
                },
            },
        }
        self.policy_path = self.out / "policy.json"
        self.persist()
        self.state = PROXY.ProxyState(
            self.policy_path,
            self.out / "proxy",
            f"http://127.0.0.1:{self.upstream.server_address[1]}/rest/sql",
            timeout=0.5,
        )
        self.server = PROXY.ProxyServer(("127.0.0.1", 0), self.state)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive() or self.upstream_thread.is_alive())
        (self.out / "cleanup.json").write_text(
            json.dumps(
                {
                    "proxyPort": self.server.server_address[1],
                    "upstreamPort": self.upstream.server_address[1],
                    "bothEphemeralLoopback": True,
                    "threadsStopped": True,
                    "actualServiceTouched": False,
                    "upstreamRequests": self.upstream.seen,
                }
            )
        )

    def persist(self):
        staged = self.policy_path.with_suffix(".new")
        staged.write_text(json.dumps(self.policy))
        staged.replace(self.policy_path)

    def arm(self, kind="recovery", epoch="independent-armed"):
        self.policy.update(armed=True, activeWindow=kind, epoch=epoch)
        self.persist()

    def post(self, body=None, path="/rest/sql", authorization=False):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=5
        )
        try:
            connection.request(
                "POST",
                path,
                body=(body or sql()).encode(),
                headers={"Authorization": "Basic INDEPENDENT_SYNTHETIC_TOKEN"}
                if authorization
                else {},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def logs(self):
        return [
            json.loads(line)
            for line in (self.out / "proxy/requests.jsonl").read_text().splitlines()
        ]

    def test_disarmed_and_true_body_restore(self):
        self.assertEqual(self.post(authorization=True), (200, REAL))
        row = self.logs()[0]
        self.assertFalse(row["injected"])
        self.assertEqual(row["tdCode"], 0)
        self.assertTrue(self.upstream.seen[0]["authorizationPresent"])
        compressed = (self.state.out / row["responseFile"]).read_bytes()
        self.assertEqual(hashlib.sha256(compressed).hexdigest(), row["responseGzipSha256"])
        self.assertEqual(gzip.decompress(compressed), REAL)
        self.assertEqual(hashlib.sha256(REAL).hexdigest(), row["responseSha256"])

    def test_exact_guards_do_not_consume_failure_counter(self):
        self.arm()
        variants = [
            sql().replace(POINT, OTHER),
            sql().replace(DATABASE, "clpm_ts"),
            sql().replace("ts <=", "ts <"),
            sql("2026-10-14T09:00:00Z", START),
            sql().replace(f"'{POINT}'", f"'{POINT}', '{OTHER}'"),
            sql() + "; SELECT 1",
            "SHOW QUERIES",
            sql().replace("point_id, ts, `value`, quality_class", "COUNT(*)"),
            "/* prefix */ " + sql(),
        ]
        for query in variants:
            with self.subTest(query=query):
                self.assertEqual(self.post(query), (200, REAL))
        self.assertEqual(self.post(path="/wrong-endpoint"), (200, REAL))
        self.assertEqual([self.post() for _ in range(4)], [(200, FAULT), (200, FAULT), (200, REAL), (200, REAL)])
        matches = [row for row in self.logs() if row["match"]]
        self.assertEqual([row["matchingRequestOrdinal"] for row in matches], [1, 2, 3, 4])
        self.assertEqual(len(self.upstream.seen), len(variants) + 3)

    def test_concurrent_first_two_exactly_and_atomic_ordinals(self):
        self.arm()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: self.post(), range(18)))
        self.assertEqual(results.count((200, FAULT)), 2)
        self.assertEqual(results.count((200, REAL)), 16)
        self.assertEqual(sorted(row["matchingRequestOrdinal"] for row in self.logs()), list(range(1, 19)))

    def test_persistent_not_auto_green_after_nine(self):
        self.arm("exhaustion", "independent-persistent")
        query = sql(EX_START, EX_END)
        self.assertEqual([self.post(query) for _ in range(17)], [(200, FAULT)] * 17)
        self.assertEqual(len(self.upstream.seen), 0)
        self.policy.update(armed=False, epoch="independent-explicit-disarm")
        self.persist()
        self.assertEqual(self.post(query), (200, REAL))
        self.assertFalse(self.logs()[-1]["injected"])

    def test_changed_same_epoch_is_explicit_503(self):
        self.arm()
        self.assertEqual(self.post(), (200, FAULT))
        self.policy["armed"] = False
        self.persist()
        status, body = self.post()
        self.assertEqual(status, 503)
        self.assertIn(b"AUDIT_PROXY_POLICY_INVALID", body)
        self.assertEqual(self.logs()[-1]["transportRc"], 78)
        self.policy["epoch"] = "independent-new-disarm"
        self.persist()
        self.assertEqual(self.post(), (200, REAL))

    def test_upstream_own_td_error_preserved_and_not_injected(self):
        self.upstream.body = FAULT
        self.assertEqual(self.post(), (200, FAULT))
        row = self.logs()[0]
        self.assertFalse(row["injected"])
        self.assertEqual((row["transportRc"], row["tdCode"]), (0, 1850))

    def test_real_upstream_transport_drop_explicit(self):
        self.upstream.drop = True
        status, body = self.post()
        self.assertEqual(status, 502)
        self.assertIn(b"UPSTREAM_TRANSPORT_FAILED", body)
        self.assertEqual(self.logs()[0]["transportRc"], 70)

    def test_credentials_absent_from_plain_and_decompressed_evidence(self):
        self.arm()
        self.assertEqual(self.post(authorization=True), (200, FAULT))
        secret = "INDEPENDENT_SYNTHETIC_SECRET_7c91"
        query = "CREATE USER independent PASS '" + secret + "'"
        self.assertEqual(self.post(query, path="/rest/sql?password=" + secret), (200, query.encode()))
        for path in self.state.out.rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                if path.name.endswith(".gz"):
                    raw = gzip.decompress(raw)
                self.assertNotIn(secret.encode(), raw, str(path))
                self.assertNotIn(b"INDEPENDENT_SYNTHETIC_TOKEN", raw, str(path))
        self.assertIsNone(self.logs()[-1]["responseFile"])

    def test_policy_rejects_formal_overlap_wrong_scope_and_relaxed_counts(self):
        changes = [
            {"database": "clpm_ts"},
            {"loopTag": "PERF-T0005"},
            {"pointId": "not-canonical"},
        ]
        for change in changes:
            candidate = json.loads(json.dumps(self.policy))
            candidate.update(change)
            with self.assertRaises(ValueError):
                PROXY.validate_policy(candidate)
        for target, change in [
            ("recovery", {"failFirstN": 3}),
            ("exhaustion", {"mode": "fail_first_n", "failFirstN": 9}),
            ("recovery", {"start": "2026-10-15T12:00:00Z", "end": "2026-10-15T13:00:00Z"}),
        ]:
            candidate = json.loads(json.dumps(self.policy))
            candidate["windows"][target].update(change)
            with self.assertRaises(ValueError):
                PROXY.validate_policy(candidate)

    def test_startup_armed_external_and_global_listen_rejected(self):
        self.policy["armed"] = True
        self.persist()
        with self.assertRaisesRegex(ValueError, "DISARMED"):
            PROXY.ProxyState(self.policy_path, self.out / "bad-armed", f"http://127.0.0.1:{self.upstream.server_address[1]}/rest/sql")
        self.policy["armed"] = False
        self.persist()
        with self.assertRaisesRegex(ValueError, "Upstream"):
            PROXY.ProxyState(self.policy_path, self.out / "bad-external", "http://example.invalid:6041/rest/sql")
        with self.assertRaisesRegex(ValueError, "Listen host"):
            PROXY.ProxyServer(("0.0.0.0", 0), self.state)


if __name__ == "__main__":
    unittest.main()
