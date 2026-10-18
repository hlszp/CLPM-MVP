"""Complete pure-file identity controls; never actual configuration evidence."""

import importlib.util
import os
import uuid

import pytest

import adapt_capacity_artifacts as subject

if os.environ.get("CLPM_ADAPTER_IDENTITY_SOURCE"):
    spec = importlib.util.spec_from_file_location(
        "identity_admission_subject", os.environ["CLPM_ADAPTER_IDENTITY_SOURCE"]
    )
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)


def complete_config_fixture(tmp_path, mutation=None):
    originals = tmp_path / "originals"
    originals.mkdir()

    def file(name, value):
        path = originals / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subject.canonical(value))
        return path

    tree = [
        {"id": str(uuid.UUID(int=50000 + i)), "parent_id": None, "is_kpi_enabled": True}
        for i in range(20)
    ]
    loops = [
        {
            "id": str(uuid.UUID(int=i + 1)),
            "tag_name": f"PERF-T{i + 1:04d}",
            "unit_id": tree[i % 20]["id"],
            "complex_loop_group_id": None,
            "is_active": True,
            "status": "READY",
            "include_in_evaluation": True,
        }
        for i in range(1000)
    ]
    roles = ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]
    mappings = [
        {"loop_id": loop["id"], "tag_role": role, "tag_id": f"{loop['id']}-{role}"}
        for loop in loops
        for role in roles
    ]
    file("pg-raw/plant_node.json", tree)
    file("pg-raw/loop_ledger.json", loops)
    file("pg-raw/loop_tag_mapping.json", mappings)
    file("pg-raw/tag_registry.json", [{"id": m["tag_id"]} for m in mappings])
    version_sources = {
        "app/tasks/kpi_calc.py": (
            "ALGORITHM_VERSION = 'fixture-v'\n"
            "async def _do_calculate_node_kpi():\n"
            "    return select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True))\n"
        ),
        "app/services/preprocessing/pipeline.py": "PREPROCESS_VERSION = 'fixture-pre'\n",
        "app/services/metric_calculator/time_axis.py": (
            "TIME_QUALITY_POLICY_VERSION = 'fixture-time'\n"
        ),
    }
    for relative, text in version_sources.items():
        path = originals / "sources" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    config = {
        "status": "FROZEN",
        "candidateSha": "a" * 40,
        "configDigest": "d" * 64,
        "versions": {
            "algorithmVersion": "fixture-v",
            "preprocessVersion": "fixture-pre",
            "timeQualityPolicyVersion": "fixture-time",
        },
        "finishedAt": "2026-10-17T07:59:00+00:00",
        "syntheticFixtureOnly": True,
    }
    plan = {"candidateSha": "a" * 40, "configDigest": "d" * 64}
    if mutation:
        mutation(plan, config)
    config_path = file("config-freeze.json", config)
    after_path = file(
        "scope-after.json",
        {
            "readonly": "on",
            "isolation": "repeatable read",
            "snapshot": "synthetic",
            "observedAt": "2026-10-17T08:01:00+00:00",
            "loops": loops,
            "tree": tree,
        },
    )
    isolation = file("isolation.json", {"syntheticFixtureOnly": True})
    entries = [
        {
            "file": str(p.relative_to(originals)),
            "bytes": p.stat().st_size,
            "sha256": subject.digest(p.read_bytes()),
        }
        for p in sorted(originals.rglob("*"))
        if p.is_file()
    ]
    manifest = file("sha256-manifest.json", {"files": entries})
    adapter = subject.Adapter(tmp_path, tmp_path / "sidecars")
    plan.update(
        configFreezeManifest=adapter.ref(manifest),
        configFreeze=adapter.ref(config_path),
        nodeScopeAfter=adapter.ref(after_path),
        configurationIsolation=adapter.ref(isolation),
    )
    return adapter, plan, config


def test_complete_valid_triplet_and_digest_admit_the_same1000_twenty_scope(tmp_path):
    adapter, plan, config = complete_config_fixture(tmp_path)
    result = adapter.config(plan)
    assert result["config"] == config
    assert len(result["loops"]) == 1000 and len(result["scopes"]) == 20
    assert sorted(
        {loop for scope in result["scopes"].values() for loop in scope}
    ) == sorted(result["loops"])


@pytest.mark.parametrize("field", ["configDigest", "algorithmVersion"])
def test_null_single_identity_refused_with_other_identity_sources_complete(
    tmp_path, field
):
    def mutate(plan, config):
        if field == "configDigest":
            plan["configDigest"] = config["configDigest"] = None
        else:
            config["versions"][field] = None

    adapter, plan, _ = complete_config_fixture(tmp_path, mutate)
    with pytest.raises(subject.MissingEvidence, match="configDigest|algorithmVersion"):
        adapter.config(plan)


@pytest.mark.parametrize("field", ["preprocessVersion", "timeQualityPolicyVersion"])
@pytest.mark.parametrize("value", [None, "", "different-source-version"])
def test_every_version_must_be_nonempty_and_equal_actual_source(tmp_path, field, value):
    def mutate(plan, config):
        config["versions"][field] = value

    adapter, plan, _ = complete_config_fixture(tmp_path, mutate)
    with pytest.raises(subject.MissingEvidence, match=field):
        adapter.config(plan)


@pytest.mark.parametrize("value", [None, "", "d" * 63, "g" * 64, 64])
def test_digest_cannot_be_missing_or_malformed_even_when_both_headers_agree(
    tmp_path, value
):
    def mutate(plan, config):
        plan["configDigest"] = config["configDigest"] = value

    adapter, plan, _ = complete_config_fixture(tmp_path, mutate)
    with pytest.raises(subject.MissingEvidence, match="configDigest"):
        adapter.config(plan)


def test_series_id_requires_real_nonempty_value_before_any_original_read(tmp_path):
    adapter = subject.Adapter(tmp_path, tmp_path / "sidecars")
    with pytest.raises(subject.MissingEvidence, match="seriesId"):
        adapter.run(
            {"candidateSha": "a" * 40, "configDigest": "d" * 64, "seriesId": None}
        )
