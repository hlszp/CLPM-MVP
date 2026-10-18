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



import base64
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    futures = {label: pool.submit(capture, label, endpoint) for label, endpoint in {
        'checkout-fixture-source': f'{REPO}/contents/e2e/scripts/prepare-critical-fixture.py?ref=adc74b7a365b480f234dd6e5a2df7ac5592335ef',
        'run-later': f'{REPO}/actions/runs/{RUN}',
        'jobs-later': f'{REPO}/actions/runs/{RUN}/jobs?per_page=100',
    }.items()}
    data = {label: future.result() for label,future in futures.items()}
source=base64.b64decode(data['checkout-fixture-source']['content'])
(ROOT/'actual-checkout-prepare-critical-fixture.py').write_bytes(source)
print(json.dumps({'actualCheckoutSourceSha256':hashlib.sha256(source).hexdigest(), 'run':{k:data['run-later'].get(k) for k in ('id','status','conclusion')},'jobs':[{k:j[k] for k in ('id','name','status','conclusion')} for j in data['jobs-later']['jobs']]}),flush=True)
