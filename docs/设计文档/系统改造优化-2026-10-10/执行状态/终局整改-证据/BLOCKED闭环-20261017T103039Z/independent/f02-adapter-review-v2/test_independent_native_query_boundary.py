"""Independent native temporal-boundary probes on complete original-file fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parents[1]/"f02"
sys.path.insert(0,str(BASE));sys.path.insert(0,str(ROOT.parent))
import adapt_capacity_artifacts as fixture_subject
from test_adapt_capacity_native import native_fixture

source=Path(os.environ.get("CLPM_ADAPTER_SOURCE",str(BASE/"adapt_capacity_artifacts.py")))
spec=importlib.util.spec_from_file_location("boundary_subject",source)
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)


def test_complete_original_native_span_has_both_query_boundaries(tmp_path):
    _,runtime,_,bundle,span=native_fixture(tmp_path)
    proof=subject.native_span(runtime,bundle,span)
    assert proof["checkedNativeBirthSamples"]>=2


def test_last_ps_started_before_business_end_is_not_a_confirmed_after_observation(tmp_path):
    original,runtime,_,bundle,span=native_fixture(tmp_path)
    observations=bundle["observations"][:12]
    observations[-1]["psCommand"]["startedAt"]="2026-10-17T08:00:20.500000+00:00"
    # Last query is 20.5..22.0s; the business ended at21.0s. No query starts after it.
    assert all(subject.instant(r["psCommand"]["startedAt"])<span[1] for r in observations)
    raw=b"".join(subject.canonical(row)+b"\n" for row in observations)
    path=tmp_path/"native/observations.jsonl";path.write_bytes(raw)
    summary_path=tmp_path/"native/summary.json";summary=json.loads(summary_path.read_text())
    summary.update(pollCount=len(observations),observations={"path":str(path),"bytes":len(raw),"sha256":subject.digest(raw)})
    summary_path.write_bytes(subject.canonical(summary))
    fresh=subject.Adapter(tmp_path,tmp_path/"new-sidecars")
    refs=bundle["refs"]
    checked=fresh.native_bundle(fresh.ref(summary_path),refs[3],refs[4],{"envFileSha256":"synthetic-env","taosConfigSha256":"synthetic-taos"},runtime)
    # Index/file/hash admission is complete: only the actual query-time bracket is uncertain.
    assert len(checked["observations"])==12
    with pytest.raises(subject.MissingEvidence):
        subject.native_span(runtime,checked,span)
