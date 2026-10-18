"""Root Keeper only: launch isolated static servers, never a CLPM service."""

from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.request import urlopen


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, item):
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--native-env-file", type=Path, required=True)
    parser.add_argument("--original-css", type=Path, required=True)
    parser.add_argument("--first-run-id", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    scope = here.parent
    args.out.mkdir(parents=True, exist_ok=False)
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.checkout, text=True
    ).strip()
    if actual != args.expected_sha:
        raise RuntimeError("source identity changed")
    # This fixture contains no product JS, auth, result data, or API requests.
    css = args.original_css.read_bytes()
    if (
        digest(css)
        != "8c30731fcf2d78091b6770ef364d34839cae3418074cbfc7bd77a8d104152dab"
    ):
        raise RuntimeError("original failed CSS bytes differ")
    node_probe = here / "probe-static-cold.mjs"
    env = {
        **os.environ,
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    summary = {
        "purpose": "TRANSPORT_ONLY_NO_CLPM_API",
        "sourceSha": actual,
        "attempts": [],
    }
    for index, (name, driver) in enumerate(
        [
            ("old-v2", scope / "local-four-case-plan/keeper-local-f03-v2.py"),
            ("new-v3", here / "keeper-local-f03-v3.py"),
        ]
    ):
        for port in (17261, 17267):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(
                        f"transport-only probe requires free port {port}"
                    )
        run_id = str(args.first_run_id + index)
        transport = scope / f"local-business-{run_id}-{actual[:12]}"
        transport.mkdir(exist_ok=False)
        site = transport / "site"
        (site / "css").mkdir(parents=True)
        files = {}
        links = []
        for number in range(96):
            relative = f"css/node-radar-panel-{number:03}.css"
            (site / relative).write_bytes(css)
            links.append(f'<link rel="stylesheet" href="/{relative}">')
        html = (
            '<!doctype html><html><head><link rel="icon" href="data:,">'
            + "".join(links)
            + "</head><body>TRANSPORT ONLY; NO CLPM API"
            '<script>document.documentElement.dataset.transportReady="1"</script></body></html>'
        ).encode()
        (site / "index.html").write_bytes(html)
        for file in sorted(site.rglob("*")):
            if file.is_file():
                raw = file.read_bytes()
                files[str(file.relative_to(site))] = {
                    "bytes": len(raw),
                    "sha256": digest(raw),
                }
        fixture = {
            "purpose": summary["purpose"],
            "files": files,
            "sourceCssSha256": digest(css),
        }
        write(transport / "transport-only-fixture.json", fixture)
        original_source = driver.read_text()
        needle = '(("127.0.0.1", 17266), Handler)'
        if original_source.count(needle) != 1:
            raise RuntimeError("expected exact single static listener literal")
        execution_source = original_source.replace(
            needle, '(("127.0.0.1", 17267), Handler)', 1
        )
        execution_driver = transport / "driver-port17267.py"
        execution_driver.write_text(execution_source)
        (transport / "driver-original.py").write_text(original_source)
        write(
            transport / "port-only-change.json",
            {
                "oldPort": 17266,
                "newPort": 17267,
                "originalSha256": digest(original_source.encode()),
                "executionSha256": digest(execution_source.encode()),
                "singleLiteralOnly": True,
            },
        )
        argv = [
            args.python,
            "-B",
            str(execution_driver),
            "serve-site",
            "--checkout",
            str(args.checkout),
            "--expected-sha",
            actual,
            "--run-id",
            run_id,
            "--out",
            str(transport),
            "--native-env-file",
            str(args.native_env_file),
            "--python",
            args.python,
        ]
        entry = {
            "name": name,
            "driver": str(driver),
            "driverSha256": digest(driver.read_bytes()),
            "serverArgv": argv,
            "transportDirectory": str(transport),
            "businessCasesExecuted": 0,
        }
        process = None
        error = None
        with (
            (transport / "server.stdout.log").open("xb") as stdout,
            (transport / "server.stderr.log").open("xb") as stderr,
        ):
            try:
                process = subprocess.Popen(
                    argv, stdout=stdout, stderr=stderr, env=env, start_new_session=True
                )
                entry.update(
                    {
                        "serverPid": process.pid,
                        "serverPgid": os.getpgid(process.pid),
                        "startedAt": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                    }
                )
                write(transport / "server-launch.json", entry)
                for _ in range(50):
                    if process.poll() is not None:
                        raise RuntimeError("static server exited before readiness")
                    try:
                        with urlopen(
                            "http://127.0.0.1:17267/index.html", timeout=1
                        ) as response:
                            if response.read() != html:
                                raise RuntimeError("served HTML differs")
                        break
                    except (OSError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise RuntimeError("static readiness timeout")
                probe_argv = [
                    args.node,
                    str(node_probe),
                    str(args.checkout),
                    str(transport / "transport-only-fixture.json"),
                    str(transport),
                ]
                completed = subprocess.run(probe_argv, capture_output=True, env=env)
                (transport / "probe.stdout.log").write_bytes(completed.stdout)
                (transport / "probe.stderr.log").write_bytes(completed.stderr)
                entry.update(
                    {
                        "probeArgv": probe_argv,
                        "probeRc": completed.returncode,
                        "probeStdoutSha256": digest(completed.stdout),
                        "probeStderrSha256": digest(completed.stderr),
                    }
                )
                if (transport / "result.json").exists():
                    entry["result"] = json.loads(
                        (transport / "result.json").read_text()
                    )
            except Exception as problem:
                error = repr(problem)
            finally:
                if process is not None:
                    if process.poll() is None:
                        if os.getpgid(process.pid) != entry["serverPgid"]:
                            raise RuntimeError("own static server pgid changed")
                        os.killpg(entry["serverPgid"], signal.SIGTERM)
                    try:
                        entry["serverExitRc"] = process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        error = "own static server did not exit; no force kill"
                entry["controlError"] = error
                write(transport / "control-result.json", entry)
                summary["attempts"].append(entry)
                write(args.out / "summary.json", summary)
        if error:
            raise RuntimeError(error)
    # Old success is valid evidence too. Never manufacture a required old failure.
    summary["passed"] = summary["attempts"][1].get("probeRc") == 0
    summary["oldProbeRc"] = summary["attempts"][0].get("probeRc")
    write(args.out / "summary.json", summary)
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
