"""Root-only, pinned local F03 phase launcher; saves actual command exit status."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DRIVER = ROOT / "local-term-v5/keeper-local-f03-v5b.py"
PIN = "9fdc2b0314e4c54a483c59131383945677e86920171b4e2b7cd5ef88f10af1bd"
PY = "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python"
OUT = ROOT / "local-business-20261017213911-8a438372d213"


def now():
    return datetime.now(timezone.utc).isoformat()


if len(sys.argv) != 2 or sys.argv[1] not in ("plan", "bootstrap", "build", "start", "seed", "gate", "status", "stop"):
    raise SystemExit("one exact operational phase is required")
if hashlib.sha256(DRIVER.read_bytes()).hexdigest() != PIN:
    raise SystemExit("fixed driver bytes changed")
phase = sys.argv[1]
argv = [PY, "-B", str(DRIVER), phase, "--checkout", "/Users/zhangping/DEV/clpm-remediation-20261017",
        "--expected-sha", "8a438372d21395fdb5246e4d8f73a0a5b7bf9c97", "--run-id", "20261017213911",
        "--out", str(OUT), "--native-env-file", "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env", "--python", PY]
folder = ROOT / "local-v5b-fixed-input-root-commands"
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
