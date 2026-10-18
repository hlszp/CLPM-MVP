"""Independently reconstruct a saved config freeze; local artifacts/git only."""

import argparse
import ast
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from verify_closed_endpoints_raw import Reader, canonical, digest, instant


class MissingAfterSnapshotEvidence(ValueError):
    pass


def literal(raw, name):
    for node in ast.parse(raw).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Frozen source literal missing: " + name)


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def git_catalog(repo, sha, path):
    raw = path.read_bytes()
    catalog = json.loads(raw)
    assert catalog["candidateSha"] == sha
    tree_raw = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", "--full-tree", sha], cwd=repo
    )
    tree = {}
    for record in tree_raw.split(b"\0"):
        if not record:
            continue
        prefix, filename = record.split(b"\t", 1)
        mode, kind, oid = prefix.decode().split()
        assert kind == "blob"
        tree[filename.decode()] = (mode, oid)
    files = catalog["files"]
    assert (
        len(files)
        == len({item["path"] for item in files})
        == len(tree)
        == catalog["trackedFileCount"]
    )
    assert {item["path"] for item in files} == set(tree)
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    hashes = {}
    try:
        for item in files:
            name = item["path"]
            mode, oid = tree[name]
            assert item["mode"] == mode and item["gitBlob"] == oid
            if oid not in hashes:
                process.stdin.write((oid + "\n").encode())
                process.stdin.flush()
                header = process.stdout.readline().decode().strip().split()
                assert len(header) == 3 and header[0] == oid and header[1] == "blob"
                size = int(header[2])
                body = process.stdout.read(size)
                assert len(body) == size and process.stdout.read(1) == b"\n"
                hashes[oid] = (size, digest(body))
            size, sha256 = hashes[oid]
            assert size == item["committedBlobBytes"] == item["runtimeBytes"]
            assert sha256 == item["committedBlobSha256"] == item["runtimeSha256"]
            assert item["runtimeMatchesCommittedBytes"] is True
    finally:
        process.stdin.close()
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
        assert process.wait() == 0 and not stderr
    assert sum(item["runtimeBytes"] for item in files) == catalog["trackedRuntimeBytes"]
    root_tree = subprocess.check_output(
        ["git", "rev-parse", sha + "^{tree}"], cwd=repo, text=True
    ).strip()
    assert root_tree == catalog["rootTree"]
    return catalog, {
        "path": str(path),
        "sha256": digest(raw),
        "gitTree": root_tree,
        "files": len(files),
        "totalBytes": catalog["trackedRuntimeBytes"],
        "allImmutableGitBlobsRehashed": True,
    }


def verify(args):
    reader = Reader(args.freeze)
    manifest_raw = reader.raw("sha256-manifest.json")
    assert digest(manifest_raw) == args.manifest_sha256
    listed = json.loads(manifest_raw)["files"]
    actual = {
        str(path.relative_to(args.freeze))
        for path in args.freeze.rglob("*")
        if path.is_file() and path.name != "sha256-manifest.json"
    }
    assert (
        len(listed) == len({item["file"] for item in listed})
        and {item["file"] for item in listed} == actual
    )
    for item in listed:
        raw = reader.raw(item["file"])
        assert digest(raw) == item["sha256"] and len(raw) == item["bytes"]
    report = reader.json("config-freeze.json")
    assert report["status"] == "FROZEN" and report["candidateSha"] == args.candidate_sha
    catalog, catalog_check = git_catalog(args.repo, args.candidate_sha, args.catalog)
    catalog_by_path = {row["path"]: row for row in catalog["files"]}
    sources = {}
    for source in report["sourceFiles"]:
        body = reader.raw(source["file"])
        assert digest(body) == source["sha256"] and len(body) == source["bytes"]
        relative = source["file"].removeprefix("sources/")
        assert source["file"].startswith("sources/")
        assert catalog_by_path["backend/" + relative]["committedBlobSha256"] == digest(
            body
        )
        sources[relative] = body
    tables = {}
    for path in sorted((args.freeze / "pg-raw").glob("*.json")):
        raw = reader.json("pg-raw/" + path.name)
        restored = reader.json("pg-canonical/" + path.name + ".gz")
        assert raw == restored
        tables[path.stem] = raw
    postgres_digest = digest(canonical(tables))
    assert postgres_digest == report["postgresDigest"]
    before, after = report["pgBefore"], report["pgAfter"]
    for observation in [before, after]:
        assert (
            observation["database"] == "clpm_capacity_20261017_s01"
            and observation["readonly"] == "on"
            and observation["isolation"] == "repeatable read"
        )
    after_archived = (args.freeze / "pg-after-raw").is_dir()
    if after_archived:
        after_tables = {}
        for path in sorted((args.freeze / "pg-after-raw").glob("*.json")):
            raw = reader.json("pg-after-raw/" + path.name)
            restored = reader.json("pg-after-canonical/" + path.name + ".gz")
            assert raw == restored
            after_tables[path.stem] = raw
        assert after_tables == tables, "Original before/after table contents differ"
    elif before["snapshot"] != after["snapshot"]:
        raise MissingAfterSnapshotEvidence(
            "Different PG snapshots need the original after table export; a summary stable flag is insufficient"
        )
    defaults = literal(sources["app/services/algorithm_config.py"], "_DEFAULTS")
    assert defaults == reader.json("source-defaults.json")
    versions = {
        "algorithmVersion": literal(
            sources["app/tasks/kpi_calc.py"], "ALGORITHM_VERSION"
        ),
        "preprocessVersion": literal(
            sources["app/services/preprocessing/pipeline.py"], "PREPROCESS_VERSION"
        ),
        "timeQualityPolicyVersion": literal(
            sources["app/services/metric_calculator/time_axis.py"],
            "TIME_QUALITY_POLICY_VERSION",
        ),
    }
    assert versions == report["versions"]
    global_params = {}
    for metric, by_type in defaults.items():
        for control in ["STABLE", "SLOW", "FAST", "LOGIC"]:
            params = deepcopy(by_type.get(control, {}))
            for row in tables["algorithm_parameter"]:
                if (
                    row["is_enabled"]
                    and row["metric_code"] == metric
                    and row["control_type"] == control
                ):
                    params.update(decoded(row["params"]) or {})
            for row in tables["metric_config"]:
                threshold = decoded(row["threshold"])
                if row["metric_code"] == metric and isinstance(threshold, dict):
                    params.update(threshold)
            global_params[metric + "|" + control] = params
    assert global_params == reader.json("effective-global-reconstructed.json")
    api = {}
    for name, observation in report["api"].items():
        raw = reader.raw("api/" + name + ".raw.json")
        body = json.loads(raw)
        assert (
            digest(raw) == observation["rawSha256"]
            and body == observation["body"]
            and observation["httpStatus"] == 200
        )
        assert instant(observation["requestedAt"]) <= instant(observation["observedAt"])
        if name != "health":
            assert body["code"] == "0"
            assert (
                instant(before["captured_at"])
                <= instant(observation["requestedAt"])
                <= instant(observation["observedAt"])
                <= instant(after["captured_at"])
            )
        api[name] = body
    api_global = {
        metric["metricCode"] + "|" + row["controlType"]: row["params"]
        for metric in api["configs-algorithm-params"]["data"]["metrics"]
        for row in metric["items"]
    }
    assert api_global == global_params
    revision = max((row["revision"] for row in tables["config_publication"]), default=0)
    assert (
        revision
        == report["configRevision"]
        == api["configs-publish-revision"]["data"]["revision"]
    )
    expected_digest = digest(
        canonical(
            {
                "versions": versions,
                "postgresConfigDigest": postgres_digest,
                "sourceFiles": report["sourceFiles"],
                "effectiveGlobal": global_params,
                "candidateSha": args.candidate_sha,
            }
        )
    )
    assert expected_digest == report["configDigest"]
    loops, mappings = tables["loop_ledger"], tables["loop_tag_mapping"]
    ids = {row["id"] for row in loops}
    assert len(loops) == len(ids) == 1000 and {row["tag_name"] for row in loops} == {
        f"PERF-T{i:04d}" for i in range(1, 1001)
    }
    assert all(
        row["is_active"]
        and row["include_in_evaluation"]
        and row["status"] == "READY"
        and row["complex_loop_group_id"] is None
        for row in loops
    )
    roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
    assert len(mappings) == len({row["tag_id"] for row in mappings}) == 7000
    assert {(row["loop_id"], row["tag_role"]) for row in mappings} == {
        (loop, role) for loop in ids for role in roles
    }
    assert len(tables["tag_registry"]) == 7000 and {
        row["id"] for row in tables["tag_registry"]
    } == {row["tag_id"] for row in mappings}
    nodes = {row["id"]: row for row in tables["plant_node"]}
    contexts, effective, scopes = [], [], defaultdict(list)
    template_keys = {
        row["id"]: row["template_key"] for row in tables["engineering_template"]
    }
    for loop in loops:
        ancestor, visited, node = [], set(), loop["unit_id"]
        while node:
            assert node in nodes and node not in visited
            visited.add(node)
            ancestor.append(node)
            if nodes[node]["is_kpi_enabled"]:
                scopes[node].append(loop["id"])
            node = nodes[node]["parent_id"]
        template = template_keys.get(loop["engineering_template_id"])
        contexts.append(
            {
                "loopId": loop["id"],
                "loopTag": loop["tag_name"],
                "unitId": loop["unit_id"],
                "ancestorNodeIds": ancestor,
                "controlType": loop["control_type"],
                "loopType": loop["loop_type"],
                "templateKey": template,
                "templateId": loop["engineering_template_id"],
                "pinnedTemplateVersion": loop["engineering_template_version"],
            }
        )
        params_by_key = deepcopy(global_params)
        for layer, scope in [
            ("TEMPLATE", template),
            ("NODE", loop["unit_id"]),
            ("LOOP", loop["id"]),
        ]:
            for override in tables["config_override"]:
                if (
                    override["is_enabled"]
                    and override["layer"] == layer
                    and override["scope_id"] == scope
                ):
                    for key, params in params_by_key.items():
                        if key.rsplit("|", 1)[0] == override["metric_code"]:
                            params.update(decoded(override["params"]) or {})
        effective.append({"loopId": loop["id"], "params": params_by_key})
    saved = reader.json("object-node-contexts.json.gz")
    expected_scopes = {key: sorted(value) for key, value in sorted(scopes.items())}
    assert (
        saved["contexts"] == contexts
        and saved["effectiveParamsReconstructed"] == effective
        and saved["nodeScopes"] == expected_scopes
    )
    assert (
        set(expected_scopes)
        == {key for key, node in nodes.items() if node["is_kpi_enabled"]}
        and len(expected_scopes) == report["predictedNodeCount"] == 20
    )
    assert len(loops) == report["loopCount"] and len(mappings) == report["mappingCount"]
    assert (
        reader.unchanged()
        and digest(args.catalog.read_bytes()) == catalog_check["sha256"]
    )
    return {
        "verdict": "PASS",
        "scope": "Saved configuration contents/source identity only; not loaded Worker memory, formal20 or F03 business acceptance",
        "candidateSha": args.candidate_sha,
        "configDigest": expected_digest,
        "configRevision": revision,
        "manifestSha256": args.manifest_sha256,
        "manifestFileCount": len(listed),
        "allRawCanonicalTables": len(tables),
        "pgSnapshotBefore": before,
        "pgSnapshotAfter": after,
        "sameMvccVisibleVersion": before["snapshot"] == after["snapshot"],
        "afterFullRawArchived": after_archived,
        "loops": len(loops),
        "mappings": len(mappings),
        "nodeScopes": len(expected_scopes),
        "globalMetricControlPairs": len(global_params),
        "perLoopEffectivePairsRecomputed": len(effective) * len(global_params),
        "controlTypes": dict(Counter(row["control_type"] for row in loops)),
        "versions": versions,
        "catalogCheck": catalog_check,
        "sourceFileCount": len(sources),
        "readSet": reader.reads,
        "originalFilesUnchanged": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result, rc = verify(args), 0
    except (FileNotFoundError, MissingAfterSnapshotEvidence) as exc:
        result, rc = {"verdict": "BLOCKED", "error": str(exc)}, 2
    except Exception as exc:
        import traceback

        result, rc = (
            {
                "verdict": "REWORK",
                "errorType": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            1,
        )
    with args.out.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "readSet"},
            ensure_ascii=False,
        )
    )
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
