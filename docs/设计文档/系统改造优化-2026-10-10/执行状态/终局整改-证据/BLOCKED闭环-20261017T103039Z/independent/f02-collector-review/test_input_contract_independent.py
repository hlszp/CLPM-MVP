"""Pure independent input-audit counterexamples; no real DB/TD calls."""

import copy
import importlib.util
import os
from pathlib import Path
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("CLPM_AUDIT_INPUT_SOURCE", str(HERE / "sources-v1/audit_capacity_inputs.py")))
spec = importlib.util.spec_from_file_location("independent_target_input_audit", SOURCE)
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)


def own_fixture(scenario="a"):
    manifest, loops, mappings, summary = {"scenario": scenario, "rows": []}, [], [], []
    for i in range(1000):
        lid, tag = f"independent-loop-{i}", f"PERF-T{i+1:04}"
        composition = "normal" if scenario == "a" or i < 800 else "insufficient" if i < 900 else "manual" if i < 950 else "gap"
        dense = 900 if composition == "gap" else 3600
        manifest["rows"].append({"loopTag": tag, "composition": composition, "pointsPerTag": {"PV": dense, "OP": dense, "SP": "COV_sparse", "MODE": 1}})
        loops.append({"id": lid, "tag_name": tag, "is_active": True, "status": "READY", "include_in_evaluation": True})
        for role in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"):
            point = lid + "-" + role
            count = dense if role in {"PV", "OP"} else (1 if composition == "gap" else 3) if role == "SP" else 1
            mappings.append({"loop_id": lid, "tag_role": role, "tag_id": point})
            histogram = {1: count}
            if role == "PV" and composition == "insufficient":
                rng = np.random.default_rng(i + 1)
                rng.standard_normal(3600)
                rng.standard_normal(3600)
                bad = sum(value < 0.7 for value in rng.random(3600))
                histogram = {0: int(bad), 1: int(3600 - bad)}
            mode = 0 if role == "MODE" and composition == "manual" else 1
            summary.extend({"point_id": point, "quality_class": quality, "row_count": number, "min_value": mode, "max_value": mode} for quality, number in histogram.items())
    return manifest, loops, mappings, summary


class InputAuditContract(unittest.TestCase):
    def test_duplicate_manifest_row_cannot_pass(self):
        manifest, loops, mappings, summary = own_fixture()
        self.assertEqual(target.evaluate(manifest, loops, mappings, summary)[0], [])
        manifest["rows"].append(copy.deepcopy(manifest["rows"][0]))
        self.assertTrue(target.evaluate(manifest, loops, mappings, summary)[0], "1001 manifest rows (1000 unique) were accepted")

    def test_all_good_pv_is_not_the_frozen_insufficient_input(self):
        manifest, loops, mappings, summary = own_fixture("b")
        self.assertEqual(target.evaluate(manifest, loops, mappings, summary)[0], [])
        point = "independent-loop-800-PV"
        summary = [row for row in summary if row["point_id"] != point]
        summary.append({"point_id": point, "quality_class": 1, "row_count": 3600, "min_value": 1, "max_value": 1})
        self.assertTrue(target.evaluate(manifest, loops, mappings, summary)[0], "insufficient PV with 3600 GOOD / 0 BAD was accepted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
