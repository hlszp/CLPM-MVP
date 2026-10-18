import hashlib
import json
from datetime import datetime, UTC
from pathlib import Path
import subprocess

ROOT = Path(__file__).parent
PY = "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python"
RUFF = "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/ruff"
PROBE = str(ROOT.parents[1] / "independent/f03-fixture-binding-review/probe_post_commit_evidence.py")


def run(label, argv, expected=0, cwd=ROOT):
    start = datetime.now(UTC).isoformat()
    result = subprocess.run(argv, cwd=cwd, capture_output=True)
    (ROOT / (label + ".stdout.log")).write_bytes(result.stdout)
    (ROOT / (label + ".stderr.log")).write_bytes(result.stderr)
    payload = {"argv": [str(a) for a in argv], "cwd": str(cwd), "startedAt": start,
               "finishedAt": datetime.now(UTC).isoformat(), "rc": result.returncode,
               "expectedRc": expected,
               "stdoutSha256": hashlib.sha256(result.stdout).hexdigest(),
               "stderrSha256": hashlib.sha256(result.stderr).hexdigest()}
    (ROOT / (label + ".json")).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"label": label, "rc": result.returncode, "expected": expected}), flush=True)
    assert result.returncode == expected, payload


if __name__ == "__main__":
    run("00-format-external", [RUFF, "format", "--line-length", "88", str(ROOT / "after")])
    run("01-original-independent-probe", [PY, PROBE, "--source", str(ROOT / "before/prepare-critical-fixture.py"), "--out", str(ROOT / "old-probe")], expected=1)
    run("02-fixed-independent-probe", [PY, PROBE, "--source", str(ROOT / "after/prepare-critical-fixture.py"), "--out", str(ROOT / "new-probe")])
    run("03-existing-seventeen", [PY, str(ROOT / "before/test_critical_fixture.py"), "-v"])
    run("04-fixed-seventeen-plus-readback-states", [PY, str(ROOT / "after/test_critical_fixture.py"), "-v"])
    run("05-fixed-lint", [RUFF, "check", str(ROOT / "after")])
    run("06-fixed-format", [RUFF, "format", "--check", "--line-length", "88", str(ROOT / "after")])
