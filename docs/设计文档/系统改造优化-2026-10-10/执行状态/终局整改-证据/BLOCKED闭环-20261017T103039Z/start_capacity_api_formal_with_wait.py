"""Wrapper: run start_capacity_api_formal.py then wait for Celery workers to register."""
import subprocess
import sys
import time
from pathlib import Path

E = Path(__file__).resolve().parent
R = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')
PY = R / 'backend/.venv/bin/python'

# Run original start script
label = sys.argv[1]
result = subprocess.run([str(PY), str(E / 'start_capacity_api_formal.py'), label], cwd=E)
if result.returncode != 0:
    sys.exit(result.returncode)

# Wait for Celery workers to register
sys.path.insert(0, str(E))
from celery import Celery

app = Celery('f02_keeper', broker='redis://127.0.0.1:17123/1', backend='redis://127.0.0.1:17123/2')
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    inspector = app.control.inspect(timeout=3)
    ping = inspector.ping()
    if ping and len(ping) == 1:
        print(f"WORKERS_READY {list(ping.keys())[0]}", flush=True)
        sys.exit(0)
    time.sleep(1)

print("WORKERS_TIMEOUT", flush=True)
sys.exit(1)
