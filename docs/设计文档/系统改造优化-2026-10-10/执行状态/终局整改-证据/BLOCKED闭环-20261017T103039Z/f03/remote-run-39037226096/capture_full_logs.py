"""GET-only collector for one immutable Actions run. Never reruns or edits GitHub."""
import concurrent.futures
import datetime
import hashlib
import io
import json
import re
import stat
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = 'repos/hlszp/CLPM-MVP'
RUN = '39037226096'
STAMP = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def capture(label, endpoint, binary=False, output=None):
    argv = ['gh', 'api', endpoint]
    process = subprocess.run(argv, capture_output=True)
    prefix = ROOT / (STAMP + '-' + label)
    destination = output or prefix.with_suffix('.zip' if binary else '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(process.stdout)
    prefix.with_suffix('.stderr.log').write_bytes(process.stderr)
    prefix.with_suffix('.command.json').write_text(json.dumps({
        'argv': argv, 'rc': process.returncode, 'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'output': str(destination), 'bytes': len(process.stdout),
        'stdout_sha256': hashlib.sha256(process.stdout).hexdigest(),
        'stderr_sha256': hashlib.sha256(process.stderr).hexdigest(),
    }, indent=2) + '\n')
    if process.returncode:
        return {'error': process.stderr.decode(errors='replace'), 'rc': process.returncode}
    return process.stdout if binary else json.loads(process.stdout)



body = capture('full-run-logs', f'{REPO}/actions/runs/{RUN}/logs', True, ROOT/'full-run-logs.zip')
print(json.dumps({'run': RUN, 'fullLogsBytes': len(body) if isinstance(body,bytes) else None, 'result': 'downloaded' if isinstance(body,bytes) else body}), flush=True)
