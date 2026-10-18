"""Root-only, pinned local F03 phase launcher; saves actual command exit status."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DRIVER = ROOT / "local-startup-v6/keeper-local-f03-v6.py"
PIN = "87c985f311f804c80a3beb84d78e342143e277fe18d8f5041c5fcb54c81e405f"
PY = "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python"
OUT = ROOT / "local-business-20261017223757-c6fa2b489af6"


def now():
    return datetime.now(timezone.utc).isoformat()


if len(sys.argv) != 2 or sys.argv[1] not in ("plan", "bootstrap", "build", "start", "seed", "gate", "status", "stop"):
    raise SystemExit("one exact operational phase is required")
if hashlib.sha256(DRIVER.read_bytes()).hexdigest() != PIN:
    raise SystemExit("fixed driver bytes changed")
phase = sys.argv[1]
argv = [PY, "-B", str(DRIVER), phase, "--checkout", "/Users/zhangping/DEV/clpm-remediation-20261017",
        "--expected-sha", "c6fa2b489af66da392ea72fa387629f1607ba613", "--run-id", "20261017223757",
        "--out", str(OUT), "--native-env-file", "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env", "--python", PY]
folder = ROOT / "local-decimal-fix-root-commands"
folder.mkdir(exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
prefix = folder / f"{stamp}-{phase}"
started = now()
with prefix.with_suffix(".stdout.log").open("xb") as stdout, prefix.with_suffix(".stderr.log").open("xb") as stderr:
    result = subprocess.run(argv, stdout=stdout, stderr=stderr)
metadata = {"argv": argv, "startedAt": started, "finishedAt": now(), "rc": result.returncode,
            "driverSha256": PIN, "stdoutSha256": hashlib.sha256(prefix.with_suffix(".stdout.log").read_bytes()).hexdigest(),
            "stderrSha256": hashlib.sha256(prefix.with_suffix(".stderr.log").read_bytes()).hexdigest()}
prefix.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
print(json.dumps({"phase": phase, "rc": result.returncode, "record": str(prefix.with_suffix(".json"))}), flush=True)
raise SystemExit(result.returncode)
