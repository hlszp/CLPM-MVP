#!/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python
"""Root exact-scope bridge: host pg_dump CLI to the same test container's v16."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

CONTAINER = "4ec91098b6ce86a4a6edc34ffa7ef9dcad5e92ade6206db3df9d9ef7561aa3c0"
DATABASE = "clpm_capacity_20261017_s01"
TARGET = Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02/synthetic-capacity-binding-actual-root-v1/before.dump")
TEMPLATE = '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Config.Image}},"Ports":{{json .NetworkSettings.Ports}}}'


def validate(argv, target):
    if len(argv) != 7:
        raise ValueError("only the reviewed seven pg_dump arguments are allowed")
    expected = [None, "--port=17102", "--username=clpm", "--dbname=" + DATABASE,
                "--format=custom", "--no-owner", "--file=" + str(target)]
    if argv[0] not in ("--host=127.0.0.1", "--host=localhost") or argv[1:] != expected[1:]:
        raise ValueError("refuse nonreviewed database/endpoint/output/options")


def check_identity(info):
    if info.get("Id") != CONTAINER or info.get("Name") != "/clpm-mvp-postgres" or info.get("Image") != "postgres:16":
        raise ValueError("test container identity changed")
    ports = info.get("Ports", {}).get("5432/tcp")
    if not ports or any(p.get("HostPort") != "17102" for p in ports):
        raise ValueError("test host port no longer maps to this PostgreSQL container")


def main(argv):
    validate(argv, TARGET)
    if not os.environ.get("PGPASSWORD"):
        raise ValueError("PGPASSWORD must come from the caller's private environment")
    if not TARGET.parent.is_dir() or TARGET.parent.is_symlink() or TARGET.exists() or TARGET.is_symlink():
        raise ValueError("refuse absent output directory or existing/symlink backup")
    began = datetime.now(timezone.utc).isoformat()
    checks = {}
    for name, command in (
        ("identity", ["docker", "inspect", "--format", TEMPLATE, CONTAINER]),
        ("version", ["docker", "exec", CONTAINER, "pg_dump", "--version"]),
    ):
        p = subprocess.run(command, capture_output=True, timeout=15)
        for stream in ("stdout", "stderr"):
            (TARGET.parent / ("dump-adapter-" + name + "." + stream + ".log")).write_bytes(getattr(p, stream))
        checks[name] = {"argv": command, "rc": p.returncode,
                        "stdoutSha256": hashlib.sha256(p.stdout).hexdigest(),
                        "stderrSha256": hashlib.sha256(p.stderr).hexdigest()}
        if p.returncode:
            raise ValueError("test container read-only preflight failed: " + name)
        if name == "identity":
            check_identity(json.loads(p.stdout))
        elif not p.stdout.startswith(b"pg_dump (PostgreSQL) 16."):
            raise ValueError("same-container pg_dump is not version16")
    command = ["docker", "exec", "-e", "PGPASSWORD", CONTAINER, "pg_dump",
               "--host=127.0.0.1", "--port=5432", "--username=clpm", "--dbname=" + DATABASE,
               "--format=custom", "--no-owner", "--no-password"]
    before = {"startedAt": began, "callerArgv": argv, "actualDumpArgv": command,
              "checks": checks, "output": str(TARGET), "passwordOnlyInEnvironment": True}
    (TARGET.parent / "dump-adapter-before.json").write_text(json.dumps(before, indent=2) + "\n")
    with TARGET.open("xb") as output:
        os.chmod(TARGET, 0o600)
        p = subprocess.run(command, stdout=output, timeout=60)
    result = {"finishedAt": datetime.now(timezone.utc).isoformat(), "rc": p.returncode,
              "bytes": TARGET.stat().st_size, "sha256": hashlib.sha256(TARGET.read_bytes()).hexdigest()}
    (TARGET.parent / "dump-adapter-result.json").write_text(json.dumps(result, indent=2) + "\n")
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
