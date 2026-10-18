import json
from pathlib import Path
import yaml
source=Path("/Users/zhangping/DEV/clpm-remediation-20261017/.github/workflows/ci.yml")
class UniqueLoader(yaml.SafeLoader):
    pass
def mapping(loader, node, deep=False):
    pairs=loader.construct_pairs(node, deep=deep)
    result={}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key]=value
    return result
UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
workflow=yaml.load(source.read_text(), Loader=UniqueLoader)
job=workflow["jobs"]["critical-e2e"]
steps=job["steps"]
names=[step.get("name", "") for step in steps]
assert job.get("continue-on-error", False) is False
assert all(step.get("continue-on-error", False) is False for step in steps)
assert names.index("Install E2E dependencies from its own lockfile") < names.index("Install Playwright browsers")
assert job["services"]["postgres"]["env"]["POSTGRES_DB"] == job["env"]["POSTGRES_DB"] == "clpm_critical_e2e"
assert job["env"]["TDENGINE_DB"] == "clpm_critical_e2e_ts"
assert "tdengine" in job["services"]
assert all(step.get("continue-on-error", False) is False for step in workflow["jobs"]["frontend-ci"]["steps"] if "e2e" in step.get("name", "").lower() or "playwright" in step.get("name", "").lower())
print(json.dumps({"yamlUniqueKeys":True,"criticalE2eBlocking":True,"ownLockInstallBeforePlaywright":True,"isolatedPostgres":job["env"]["POSTGRES_DB"],"isolatedTDengine":job["env"]["TDENGINE_DB"],"stepNames":names,"remoteCIExecuted":False}, indent=2))
