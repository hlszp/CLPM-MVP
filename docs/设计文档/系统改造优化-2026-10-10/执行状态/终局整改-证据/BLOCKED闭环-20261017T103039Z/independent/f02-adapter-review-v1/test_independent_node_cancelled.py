"""Independent rejection assertions on frozen synthetic raw inputs. No services."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1] / "f02"
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(ROOT.parent))
SOURCE = Path(os.environ.get("CLPM_ADAPTER_SOURCE", str(BASE / "adapt_capacity_artifacts.py")))
spec = importlib.util.spec_from_file_location("independent_adapter_subject", SOURCE)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def call(tmp_path, name):
    data = json.loads((ROOT / name).read_text())
    return subject.Adapter(tmp_path, tmp_path / "sidecars").tasks(
        data["events"], data["redis"], data["parent"], data["runtime"],
        data["window"], data["observedAt"], data["scope"], [], data["scope"]["_poolProof"],
    ), data["child"]


def test_positive_explicit_zero_cancelled_keeps_derived20(tmp_path):
    tasks, child = call(tmp_path, "positive-zero.json")
    assert len(next(t for t in tasks if t["id"] == child)["nodeIds"]) == 20


def test_node_cancelled_one_cannot_be_derived_as_full20_success(tmp_path):
    with pytest.raises(subject.MissingEvidence):
        call(tmp_path, "negative-one.json")
