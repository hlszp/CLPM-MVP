"""Independent saved-artifact counterexamples; no runtime or author fixtures."""

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

SOURCE = Path(os.environ["CAPACITY_VALIDATOR_SOURCE"])
SPEC = importlib.util.spec_from_file_location("reviewed_validator", SOURCE)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
CONTAINERS = ["clpm-mvp-postgres", "clpm-mvp-tdengine", "clpm-f02-redis-20261017-s01"]
PY = "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python"


class ResourceGateCounterexamples(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.batch = Path(self.temporary.name) / "01-a-cold"
        self.batch.mkdir()
        self.raw = self.batch / "raw"
        self.raw.mkdir()
        (self.batch / "runtime-profile").mkdir()
        (self.batch / "cache-before").mkdir()
        (self.batch / "freeze").mkdir()
        self.t0 = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
        self.profile = {
            "containers": CONTAINERS,
            "maximumAccountingBytes": 5153960755,
            "minimumVmMemAvailableBytes": 1024**3,
            "sampling": {"maximumGapSeconds": 5},
        }
        self.members = [
            {"pid": 1000, "ppid": 1, "role": "api"},
            {"pid": 1001, "ppid": 1000, "role": "worker_master"},
            {"pid": 1002, "ppid": 1000, "role": "beat"},
            *[{"pid": pid, "ppid": 1001, "role": "worker_child"} for pid in range(1003, 1007)],
        ]
        self.dump(
            self.batch / "runtime-profile/runtime-profile.json",
            {"runtime": {"state": {"api_pid": 1000}, "members": self.members}},
        )
        self.rows = []
        for index in range(51):
            processes = []
            for member in self.members:
                if member["role"] == "api":
                    command = PY + " -m uvicorn app.main:app --port 17101"
                else:
                    kind = "beat" if member["role"] == "beat" else "worker"
                    command = PY + f" -m celery -A app.tasks.celery_app {kind} -l info"
                processes.append(f"{member['pid']} {member['ppid']} 0.0 1024 S {command}")
            cgroups = {}
            for container in CONTAINERS:
                text = "268435456\nanon 134217728\nshmem 0\noom 0\noom_kill 0\noom_group_kill 0\n"
                cgroups[container] = {
                    "memoryCurrentBytes": 268435456,
                    "fields": {"anon": 134217728, "shmem": 0, "oom": 0, "oom_kill": 0, "oom_group_kill": 0},
                    "raw": {"rc": 0, "stdout": text, "stderr": "", "argv": ["docker", "exec", container, "cat", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat", "/sys/fs/cgroup/memory.events"]},
                }
            self.rows.append({
                "poll": index + 1,
                "observedAt": (self.t0 + timedelta(seconds=2 * index)).isoformat(),
                "completedAt": (self.t0 + timedelta(seconds=2 * index + 0.2)).isoformat(),
                "dockerStats": {"rc": 0, "stdout": "synthetic complete container stats"},
                "containerCgroups": cgroups,
                "ownedProcesses": processes,
                "tdProcMeminfo": {"rc": 0, "stdout": "MemAvailable:    2097152 kB\n", "argv": ["docker", "exec", CONTAINERS[1], "cat", "/proc/meminfo"]},
                "tdActiveQueries": {"httpStatus": 200, "response": {"code": 0, "data": []}},
            })
        self.dump(self.batch / "product-report.json", {"observedAt": (self.t0 + timedelta(seconds=70)).isoformat(), "parentTaskId": "synthetic-parent"})
        (self.batch / "benchmark-timestamped.jsonl").write_text(json.dumps({"observedAt": (self.t0 + timedelta(seconds=60)).isoformat(), "line": "[submit] calculate_hourly_kpi.delay(ts_start='synthetic')"}) + "\n")
        self.dump(self.batch / "cache-before/cache-state.json", {"keys": [], "l2PresentLoopCount": 0})
        self.dump(self.batch / "freeze/freeze_synthetic.json", {"eligible": []})
        log = b"synthetic complete worker log; no resource event\n"
        (self.raw / "worker.log").write_bytes(log)
        self.observations = [{"workerLog": {"source": "synthetic", "available": True, "pendingBytes": 0, "rotated": False, "file": "worker.log", "inode": 1, "offsetStart": 0, "offsetEnd": len(log), "bytes": len(log), "sha256": hashlib.sha256(log).hexdigest()}}]

    def tearDown(self):
        output = Path(os.environ["CAPACITY_VALIDATOR_ARTIFACTS"]) / self._testMethodName
        output.mkdir(parents=True, exist_ok=False)
        shutil.copytree(self.batch, output / "input")
        self.temporary.cleanup()

    @staticmethod
    def dump(path, payload):
        path.write_text(json.dumps(payload))

    def validate(self):
        (self.raw / "resources.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.rows))
        (self.raw / "observations.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.observations))
        result = VALIDATOR.validate(self.batch, self.profile)
        self.dump(self.batch / "independent-observed-result.json", result)
        return result

    def test_valid_complete_control(self):
        self.assertEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_missing_oom_evidence_cannot_be_zero(self):
        for row in self.rows:
            for info in row["containerCgroups"].values():
                for key in ("oom", "oom_kill", "oom_group_kill"):
                    info["fields"].pop(key)
                info["raw"]["stdout"] = "268435456\nanon 134217728\nshmem 0\n"
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_missing_entire_worker_log_cannot_be_no_error(self):
        self.observations = [{"workerLog": {"path": "missing-synthetic-worker.log", "available": False}}]
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_duplicate_timestamp_cannot_satisfy_sample_interval(self):
        self.rows[10]["observedAt"] = self.rows[9]["observedAt"]
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_backwards_timestamp_cannot_satisfy_sample_interval(self):
        self.rows[10]["observedAt"] = (self.t0 + timedelta(seconds=17)).isoformat()
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_memory_summary_cannot_override_overbudget_raw_current(self):
        self.rows[10]["containerCgroups"][CONTAINERS[0]]["raw"]["stdout"] = self.rows[10]["containerCgroups"][CONTAINERS[0]]["raw"]["stdout"].replace("268435456", "6000000000", 1)
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_redis_outofmemoryerror_is_resource_failure(self):
        content = b"redis.exceptions.OutOfMemoryError: command not allowed when used memory exceeds maxmemory\n"
        (self.raw / "worker.log").write_bytes(content)
        self.observations[0]["workerLog"].update(bytes=len(content), offsetEnd=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")

    def test_redis_oom_command_is_resource_failure(self):
        content = b"Redis ResponseError: OOM command not allowed when used memory > maxmemory\n"
        (self.raw / "worker.log").write_bytes(content)
        self.observations[0]["workerLog"].update(bytes=len(content), offsetEnd=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.assertNotEqual(self.validate()["status"], "LOCAL_CHECKS_PASS")


if __name__ == "__main__":
    unittest.main()
