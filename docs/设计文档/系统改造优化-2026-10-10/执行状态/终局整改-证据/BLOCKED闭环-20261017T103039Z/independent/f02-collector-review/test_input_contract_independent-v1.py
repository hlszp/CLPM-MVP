"""Pure independent input-audit counterexamples; no real DB/TD calls."""

import copy
import importlib.util
import os
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("CLPM_AUDIT_INPUT_SOURCE", str(HERE / "sources-v1/audit_capacity_inputs.py")))
spec = importlib.util.spec_from_file_location("independent_target_input_audit", SOURCE)
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)


def own_fixture():
    manifest, loops, mappings, summary = {"rows": []}, [], [], []
    for i in range(1000):
        lid, tag = f"independent-loop-{i}", f"PERF-T{i+1:04}"
        manifest["rows"].append({"loopTag": tag, "composition": "normal", "pointsPerTag": {"PV": 3600, "OP": 3600, "SP": "COV_sparse", "MODE": 1}})
        loops.append({"id": lid, "tag_name": tag, "is_active": True, "status": "READY", "include_in_evaluation": True})
        for role in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"):
            point = lid + "-" + role
            count = 3600 if role in {"PV", "OP"} else 3 if role == "SP" else 1
            mappings.append({"loop_id": lid, "tag_role": role, "tag_id": point})
            summary.append({"point_id": point, "quality_class": 1, "row_count": count, "min_value": 1, "max_value": 1})
    return manifest, loops, mappings, summary


class InputAuditContract(unittest.TestCase):
    def test_duplicate_manifest_row_cannot_pass(self):
        manifest, loops, mappings, summary = own_fixture()
        self.assertEqual(target.evaluate(manifest, loops, mappings, summary)[0], [])
        manifest["rows"].append(copy.deepcopy(manifest["rows"][0]))
        self.assertTrue(target.evaluate(manifest, loops, mappings, summary)[0], "1001 manifest rows (1000 unique) were accepted")

    def test_all_good_pv_is_not_the_frozen_insufficient_input(self):
        manifest, loops, mappings, summary = own_fixture()
        self.assertEqual(target.evaluate(manifest, loops, mappings, summary)[0], [])
        manifest["rows"][0]["composition"] = "insufficient"
        self.assertTrue(target.evaluate(manifest, loops, mappings, summary)[0], "insufficient PV with 3600 GOOD / 0 BAD was accepted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
