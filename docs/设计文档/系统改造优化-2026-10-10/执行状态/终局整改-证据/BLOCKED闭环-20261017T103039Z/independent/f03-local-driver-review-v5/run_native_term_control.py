"""Independent Uvicorn TERM observation, no CLPM imports or business access."""

import datetime as dt
import hashlib
import inspect
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import uvicorn


def main():
    out = Path(__file__).resolve().parent
    source = out / "native_probe.py"
    source.write_text(
        'async def app(scope, receive, send):\n'
        '    if scope["type"] != "lifespan":\n'
        '        raise RuntimeError("No HTTP requests in independent control")\n'
        '    while True:\n'
        '        event = await receive()\n'
        '        if event["type"] == "lifespan.startup":\n'
        '            await send({"type": "lifespan.startup.complete"})\n'
        '        elif event["type"] == "lifespan.shutdown":\n'
        '            await send({"type": "lifespan.shutdown.complete"})\n'
        '            return\n'
    )
    capture_source = inspect.getsource(uvicorn.Server.capture_signals)
    (out / "actual-capture-signals.py").write_text(capture_source)
    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        "native_probe:app",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--timeout-graceful-shutdown",
        "15",
        "--no-access-log",
    ]
    log_path = out / "independent-native-server.log"
    errors = []
    started = dt.datetime.now(dt.UTC).isoformat()
    signal_at = None
    used_cleanup = False
    with log_path.open("xb") as log:
        process = subprocess.Popen(argv, cwd=out, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 8
            while b"Application startup complete.\n" not in log_path.read_bytes():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Independent native ASGI did not start")
                time.sleep(0.02)
            offset = log_path.stat().st_size
            signal_at = dt.datetime.now(dt.UTC).isoformat()
            process.send_signal(signal.SIGTERM)
            rc = process.wait(timeout=8)
        except BaseException as error:
            errors.append(repr(error))
            used_cleanup = True
            if process.poll() is None:
                process.kill()
            rc = process.wait(timeout=5)
    raw = log_path.read_bytes()
    suffix = raw[offset:] if signal_at is not None else b""
    tokens = [
        b"INFO:     Shutting down\n",
        b"INFO:     Waiting for application shutdown.\n",
        b"INFO:     Application shutdown complete.\n",
        f"INFO:     Finished server process [{process.pid}]\n".encode(),
    ]
    positions = [suffix.find(token) for token in tokens]
    accepted = (
        rc == -signal.SIGTERM
        and not used_cleanup
        and not errors
        and min(positions) >= 0
        and positions == sorted(set(positions))
    )
    result = {
        "argv": argv,
        "cwd": str(out),
        "uvicornVersion": uvicorn.__version__,
        "startedAt": started,
        "signalAt": signal_at,
        "signal": "SIGTERM",
        "pid": process.pid,
        "actualExitRc": rc,
        "logOffsetBeforeSignal": offset if signal_at else None,
        "orderedSuffixOffsets": positions,
        "cleanupUsed": used_cleanup,
        "errors": errors,
        "logSha256": hashlib.sha256(raw).hexdigest(),
        "applicationSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "captureSignalsSha256": hashlib.sha256(capture_source.encode()).hexdigest(),
        "nativeTermReplayedAfterGracefulShutdown": accepted,
        "scope": "Own minimal ASGI; no HTTP, CLPM, Worker, Beat, database, or queue",
    }
    (out / "independent-native-control.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
