"""Evidence-only guard repair and checks; no candidate mutation or algorithm calls."""

import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone


D = Path(__file__).resolve().parent
CANDIDATE = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
E = D.parents[2]
PROBE = E / "independent/f02-g3-diagnostic-review/probe_network_guard.py"
CONFIG = CANDIDATE / "backend/pyproject.toml"
RUFF = Path("/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/ruff")
SOURCE = CANDIDATE / "backend/scripts/profile_identification_numerics.py"
OUT = D / "profile_identification_numerics.py"
PYTHON = sys.executable


def identity(path):
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run(name, argv, expected):
    env = os.environ.copy()
    env.update(CLPM_TEST_MODE="1", CLPM_SKIP_EXIT_HOOKS="1", PYTHONDONTWRITEBYTECODE="1")
    start = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run(argv, cwd=CANDIDATE / "backend", env=env, capture_output=True)
    end = datetime.now(timezone.utc).isoformat()
    (D / f"{name}.stdout.log").write_bytes(proc.stdout)
    (D / f"{name}.stderr.log").write_bytes(proc.stderr)
    result = {
        "argv": list(map(str, argv)), "cwd": str(CANDIDATE / "backend"),
        "startedAt": start, "finishedAt": end, "rc": proc.returncode,
        "explicitEnv": {key: env[key] for key in ("CLPM_TEST_MODE", "CLPM_SKIP_EXIT_HOOKS", "PYTHONDONTWRITEBYTECODE")},
        "sourceAtExecution": identity(OUT), "probeAtExecution": identity(PROBE),
    }
    write_json(D / f"{name}.command.json", result)
    assert proc.returncode == expected, (name, proc.returncode)
    return {"name": name, "rc": proc.returncode}


def main():
    for source, backup in (
        (OUT, D / "format-default88-intermediate.py"),
        (D / "source-identity.json", D / "source-identity-default88-intermediate.json"),
        (D / "minimal-change.diff", D / "minimal-change-default88-intermediate.diff"),
    ):
        if not backup.exists():
            shutil.copyfile(source, backup)
    original = (D / "original-6d9ffd.py").read_text()
    assert hashlib.sha256(original.encode()).hexdigest() == "6d9ffd634fdeac37d75a7e1b4f3176ac91a53553df2840e99071aacbeca119cf"
    assert SOURCE.read_text() == original
    assert identity(PROBE)["sha256"] == "6216987ff881779ce800fbd37595cb9d09c665fe6f4064cc4b4e9f47f26bceb1"
    old = '        if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):\n'
    new = '''        if event in (
            "socket.connect",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
            "socket.sendto",
            "socket.sendmsg",
        ):
'''
    assert original.count(old) == 1
    OUT.write_text(original.replace(old, new))
    results = [run("config100-format", [str(RUFF), "format", "--config", str(CONFIG), str(OUT)], 0)]
    results.append(run("config100-ruff", [str(RUFF), "check", "--config", str(CONFIG), str(OUT)], 0))
    results.append(run("config100-format-check", [str(RUFF), "format", "--check", "--config", str(CONFIG), str(OUT)], 0))
    tree = ast.parse(OUT.read_text())
    main_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    guard = next(node for node in main_node.body if isinstance(node, ast.FunctionDef) and node.name == "no_network")
    patched = guard.body[0].test.comparators[0]
    patched.elts[:] = [ast.Constant(value=x) for x in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname")]
    assert ast.dump(tree) == ast.dump(ast.parse(original))
    results.append(run("config100-same-original-red", [PYTHON, str(PROBE), "--source", str(D / "original-6d9ffd.py"), "--out", str(D / "config100-same-original-red.json")], 1))
    results.append(run("config100-same-original-green", [PYTHON, str(PROBE), "--source", str(OUT), "--out", str(D / "config100-same-original-green.json")], 0))
    namespace = {"network_attempts": []}
    guard = next(node for node in ast.parse(OUT.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "main")
    guard = next(node for node in guard.body if isinstance(node, ast.FunctionDef) and node.name == "no_network")
    exec(compile(ast.Module(body=[guard], type_ignores=[]), str(OUT), "exec"), namespace)
    refused = False
    try:
        namespace["no_network"]("socket.getnameinfo", (("192.0.2.1", 80), 0))
    except RuntimeError:
        refused = True
    assert refused and namespace["network_attempts"] == ["socket.getnameinfo"]
    write_json(D / "config100-additional-reverse-dns-control.json", {"event": "socket.getnameinfo", "refused": refused, "networkAttemptsRecorded": namespace["network_attempts"], "actualSocketCalls": 0, "productImports": 0, "algorithmsInvoked": 0, "source": identity(OUT)})
    diff = "".join(difflib.unified_diff(original.splitlines(True), OUT.read_text().splitlines(True), fromfile="original-6d9ffd.py", tofile="profile_identification_numerics.py"))
    (D / "config100-minimal-change.diff").write_text(diff)
    assert SOURCE.read_text() == original
    final = {
        "originalUnchanged": identity(SOURCE), "newEvidenceCopy": identity(OUT),
        "newLines": len(OUT.read_text().splitlines()),
        "addedAuditEvents": ["socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg"],
        "otherAllModuleAstUnchanged": True, "independentOriginalProbe": identity(PROBE),
        "results": results,
        "extraGetnameinfoRefused": True,
        "candidateTreeMutation": "NOT_PERFORMED_WAITING_ROOT_KEEPER_PERMISSION",
        "actualAlgorithmRun": "NOT_PERFORMED", "actualSocketCalls": 0,
        "formatterConfig": identity(CONFIG),
        "intermediatePreserved": identity(D / "format-default88-intermediate.py"),
    }
    write_json(D / "source-identity-config100-final.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
