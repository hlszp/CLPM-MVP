"""Capture this agent's reviewed read-only command, preserving rc/stdout/stderr."""
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
name = sys.argv[1]
assert name.replace('-', '').replace('_', '').isalnum(), 'simple evidence name required'
command = sys.argv[2:]
assert command, 'explicit command argv required'
started = datetime.now(timezone.utc).isoformat()
clock = time.monotonic()
try:
    result = subprocess.run(command, capture_output=True, timeout=50)
    rc, stdout, stderr = result.returncode, result.stdout, result.stderr
except subprocess.TimeoutExpired as exc:
    rc, stdout, stderr = 124, exc.stdout or b'', (exc.stderr or b'') + b'\nCAPTURE_TIMEOUT_50_SECONDS\n'
(HERE / (name+'.stdout.log')).write_bytes(stdout)
(HERE / (name+'.stderr.log')).write_bytes(stderr)
metadata = {
    'command': command, 'cwd': str(Path.cwd()), 'startedAt': started,
    'elapsedSeconds': time.monotonic()-clock, 'rc': rc,
    'stdoutSha256': hashlib.sha256(stdout).hexdigest(),
    'stderrSha256': hashlib.sha256(stderr).hexdigest(),
}
(HERE / (name+'.command.json')).write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'name': name, 'rc': rc, 'stdoutBytes': len(stdout), 'stderrBytes': len(stderr)}))
