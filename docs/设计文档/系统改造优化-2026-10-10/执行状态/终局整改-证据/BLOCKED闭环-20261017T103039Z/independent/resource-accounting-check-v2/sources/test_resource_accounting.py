"""Own arithmetic fixtures; never reuse Keeper's implementation or tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from independent_resource_accounting import (
    CONTAINERS,
    MAXIMUM_ACCOUNTING,
    MEMORY_BUDGET,
    MINIMUM_AVAILABLE,
    reconcile,
)
from verify_capacity_series_frozen_profile import FrozenProfileAuditor


class RawMemoryAccountingTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "memoryBudgetBytes": MEMORY_BUDGET,
            "maximumAccountingBytes": MAXIMUM_ACCOUNTING,
            "minimumVmMemAvailableBytes": MINIMUM_AVAILABLE,
            "containers": sorted(CONTAINERS),
        }
        self.members = [
            {"pid": 10, "role": "api"},
            {"pid": 11, "role": "worker_master"},
            {"pid": 12, "role": "beat"},
            *[{"pid": pid, "role": "worker_child"} for pid in range(13, 17)],
        ]
        pids = []
        for member in self.members:
            pid = member["pid"]
            parent = 1 if pid == 10 else 10 if pid in (11, 12) else 11
            command = (
                "python -m uvicorn app.main:app --port 17101"
                if pid == 10
                else "python -m celery -A app.tasks.celery_app beat -l info"
                if pid == 12
                else "python -m celery -A app.tasks.celery_app worker -l info"
            )
            pids.append(f"{pid} {parent} 0.0 1024 S {command}")
        container_bytes = 268435456
        row = {
            "ownedProcesses": pids,
            "containerCgroups": {},
            "tdProcMeminfo": {
                "rc": 0,
                "argv": ["docker", "exec", "clpm-mvp-tdengine", "cat", "/proc/meminfo"],
                "stdout": "MemAvailable:   2097152 kB\n",
            },
        }
        for container in CONTAINERS:
            row["containerCgroups"][container] = {
                "memoryCurrentBytes": container_bytes,
                "fields": {
                    "anon": 134217728,
                    "shmem": 0,
                    "oom": 0,
                    "oom_kill": 0,
                    "oom_group_kill": 0,
                },
                "raw": {
                    "rc": 0,
                    "argv": [
                        "docker",
                        "exec",
                        container,
                        "cat",
                        "/sys/fs/cgroup/memory.current",
                        "/sys/fs/cgroup/memory.stat",
                        "/sys/fs/cgroup/memory.events",
                    ],
                    "stdout": f"{container_bytes}\nanon 134217728\nshmem 0\noom 0\noom_kill 0\noom_group_kill 0\n",
                },
            }
        self.t0 = datetime(2026, 10, 16, tzinfo=timezone.utc)
        self.rows = []
        for i in range(51):
            copied = deepcopy(row)
            copied["observedAt"] = (self.t0 + timedelta(seconds=2 * i)).isoformat()
            self.rows.append(copied)

    def run_case(self):
        return reconcile(
            self.rows,
            self.members,
            self.profile,
            (self.t0 + timedelta(seconds=60)).isoformat(),
            (self.t0 + timedelta(seconds=70)).isoformat(),
        )

    def test_closed_form_and_no_anon_shmem_double_count(self):
        result = self.run_case()
        self.assertEqual(result["resourceArtifactVerdict"], "PASS")
        self.assertEqual(
            result["samples"][0]["conservativeUpperBoundBytes"],
            3 * 268435456 + 7 * 1024 * 1024,
        )
        for sample in self.rows:
            for info in sample["containerCgroups"].values():
                info["fields"]["anon"] = 10**15
                info["fields"]["shmem"] = 10**15
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "PASS")

    def test_raw_current_cannot_be_replaced_by_smaller_metadata(self):
        info = self.rows[0]["containerCgroups"]["clpm-mvp-postgres"]
        info["raw"]["stdout"] = info["raw"]["stdout"].replace(
            "268435456", "6000000000", 1
        )
        result = self.run_case()
        self.assertEqual(result["resourceArtifactVerdict"], "REWORK")
        self.assertGreater(
            result["samples"][0]["conservativeUpperBoundBytes"], MAXIMUM_ACCOUNTING
        )

    def test_missing_oom_raw_is_blocked_even_with_zero_metadata(self):
        for info in self.rows[0]["containerCgroups"].values():
            info["raw"]["stdout"] = "268435456\nanon 100\nshmem 0\n"
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "BLOCKED")

    def test_raw_oom_counter_rejected(self):
        info = self.rows[0]["containerCgroups"]["clpm-mvp-tdengine"]
        info["raw"]["stdout"] = info["raw"]["stdout"].replace(
            "oom_kill 0", "oom_kill 1"
        )
        info["fields"]["oom_kill"] = 1
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")

    def test_missing_pid_or_container_not_silently_zero(self):
        self.rows[0]["ownedProcesses"].pop()
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "BLOCKED")
        self.rows[0]["containerCgroups"].pop("clpm-mvp-postgres")
        self.assertNotEqual(self.run_case()["resourceArtifactVerdict"], "PASS")

    def test_new_worker_cannot_hide_outside_known_pid_list(self):
        self.rows[0]["ownedProcesses"].append(
            "99 10 0.0 1000 S python -m celery -A app.tasks.celery_app worker -l info"
        )
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")

    def test_gap_duplicate_and_backwards_rejected(self):
        self.rows[10]["observedAt"] = self.rows[9]["observedAt"]
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")
        self.rows[10]["observedAt"] = (self.t0 + timedelta(seconds=17)).isoformat()
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")
        self.rows[10]["observedAt"] = (self.t0 + timedelta(seconds=27)).isoformat()
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")

    def test_floor_and_fixed_budget_not_retuned(self):
        self.rows[0]["tdProcMeminfo"]["stdout"] = "MemAvailable:  1048575 kB\n"
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")
        self.profile["maximumAccountingBytes"] = 10**15
        self.assertEqual(self.run_case()["resourceArtifactVerdict"], "REWORK")

    def test_series_adapter_reads_original_refs_without_rss_relabel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def artifact(name, body, jsonl=False):
                path = root / name
                path.write_text(
                    "".join(json.dumps(item) + "\n" for item in body)
                    if jsonl
                    else json.dumps(body)
                )
                return {
                    "path": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }

            profile = artifact("profile.json", self.profile)
            runtime = artifact("runtime.json", {"runtime": {"members": self.members}})
            raw = artifact("resources.jsonl", self.rows, jsonl=True)
            exported = {
                "accountingMethod": "CgroupCurrentPlusHostPIDRSS-v1",
                "profile": profile,
                "runtimeProfile": runtime,
                "resourcesJsonl": raw,
                "rawRefs": [profile, runtime, raw],
            }
            auditor = FrozenProfileAuditor(root)
            auditor.resources(
                exported,
                self.t0 + timedelta(seconds=60),
                self.t0 + timedelta(seconds=70),
                "synthetic-batch",
            )
            self.assertFalse(auditor.issues)
            self.assertEqual(
                auditor.resource_results["synthetic-batch"]["resourceArtifactVerdict"],
                "PASS",
            )
            self.assertIn(
                "containerCurrentBytes",
                auditor.resource_results["synthetic-batch"]["samples"][0],
            )
            self.assertNotIn("rssBytes", exported)


if __name__ == "__main__":
    unittest.main()
