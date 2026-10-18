"""Only guard functions/read-only plan; no valid writer ever executed."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(
    "/Users/zhangping/DEV/clpm-remediation-20261017/e2e/scripts/prepare-critical-fixture.py"
)
spec = importlib.util.spec_from_file_location("independent_local_guard_source", SCRIPT)
MODULE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MODULE)
HEAD = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=SCRIPT.parents[2], text=True
).strip()
API = "http://127.0.0.1:17261/api/v1"
LOCAL = {
    "E2E_EXECUTION_ENV": "disposable-local",
    "LOCAL_RUN_ID": "20261017203000",
    "LOCAL_RUN_ATTEMPT": "1",
    "CLPM_BUILD_CODE_SHA": HEAD,
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "17102",
    "POSTGRES_DB": "clpm_critical_20261017_local",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": "17124",
    "REDIS_DB": "0",
    "TDENGINE_HOST": "127.0.0.1",
    "TDENGINE_PORT": "17104",
    "TDENGINE_DB": "clpm_critical_20261017_local_ts",
    "E2E_BASE_URL": "http://127.0.0.1:17266",
}


def test_local_identity_is_honest_and_never_needs_github_impersonation():
    assert MODULE.isolation_errors(LOCAL, API) == []
    identity = MODULE.execution_identity(LOCAL)
    assert (
        identity["environment"] == "disposable-local"
        and identity["location"] == "local-keeper"
    )
    assert identity["prefix"] == "CRITICAL-LOCAL" and identity["sourceSha"] == HEAD


@pytest.mark.parametrize("key", list(LOCAL))
def test_each_local_field_cannot_silently_fall_back_to_shared_services(key):
    invalid = dict(LOCAL)
    invalid.pop(key)
    if key == "LOCAL_RUN_ATTEMPT":
        assert MODULE.isolation_errors(invalid, API) == []
        assert MODULE.execution_identity(invalid)["attempt"] == "1"
    else:
        assert MODULE.isolation_errors(invalid, API), key
    invalid[key] = "shared"
    assert MODULE.isolation_errors(invalid, API), key


@pytest.mark.parametrize(
    "patch",
    [
        {"GITHUB_ACTIONS": "true"},
        {"REDIS_PORT": "17123"},
        {"POSTGRES_DB": "clpm_capacity_20261017_s01"},
        {"TDENGINE_DB": "clpm_capacity_20261017_s01"},
        {"REDIS_DB": "1"},
        {"CLPM_BUILD_CODE_SHA": "0" * 40},
        {"LOCAL_RUN_ID": "1"},
        {"LOCAL_RUN_ATTEMPT": "0"},
    ],
)
def test_known_capacity_namespace_wrong_sha_or_claimed_github_is_rejected(patch):
    assert MODULE.isolation_errors(dict(LOCAL, **patch), API)


@pytest.mark.parametrize(
    "bad_api",
    [
        "http://127.0.0.1:17101/api/v1",
        API + "?x=1",
        "https://127.0.0.1:17261/api/v1",
        "http://foreign.invalid:17261/api/v1",
    ],
)
def test_local_api_is_exact_and_never_targets_the_capacity_api(bad_api):
    assert MODULE.isolation_errors(LOCAL, bad_api)


def test_original_ci_profile_stays_disposable_and_independent_of_local_keys():
    ci = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": "a" * 40,
        "POSTGRES_HOST": "localhost",
        "REDIS_HOST": "127.0.0.1",
        "TDENGINE_HOST": "localhost",
        "POSTGRES_DB": "clpm_critical_e2e",
        "TDENGINE_DB": "clpm_critical_e2e_ts",
    }
    assert MODULE.isolation_errors(ci, "http://localhost:17101/api/v1") == []
    for key in ci:
        invalid = dict(ci)
        invalid.pop(key)
        assert MODULE.isolation_errors(invalid, "http://localhost:17101/api/v1"), key


def test_actual_cli_plan_is_readonly_and_wrong_api_writers_refuse_before_imports():
    env = dict(os.environ, **LOCAL)
    env.pop("GITHUB_ACTIONS", None)
    plan = subprocess.run(
        [sys.executable, str(SCRIPT), "--plan", "--api-url", API],
        env=env,
        capture_output=True,
        text=True,
    )
    assert plan.returncode == 0 and '"writes": false' in plan.stdout
    for mode in ["--execute", "--configure-services"]:
        denied = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                mode,
                "--api-url",
                "http://127.0.0.1:17101/api/v1",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert (
            denied.returncode == 2 and "refusedBeforeImportsOrWrites" in denied.stderr
        )
