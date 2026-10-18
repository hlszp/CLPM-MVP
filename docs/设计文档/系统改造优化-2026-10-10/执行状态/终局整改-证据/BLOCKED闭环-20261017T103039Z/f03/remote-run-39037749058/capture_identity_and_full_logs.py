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
RUN = '39037749058'
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
SHAS={'f29bMerge':'adc74b7a365b480f234dd6e5a2df7ac5592335ef','8aMerge':'820aba1aecc204578b34d7892b543a21369d650c'}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    futures = {label: pool.submit(capture,label,endpoint,binary,output) for label,endpoint,binary,output in [
        ('actual-source',f'{REPO}/contents/e2e/scripts/prepare-critical-fixture.py?ref='+SHAS['8aMerge'],False,None),
        ('merge-f29b',f'{REPO}/git/commits/'+SHAS['f29bMerge'],False,None),
        ('merge-8a',f'{REPO}/git/commits/'+SHAS['8aMerge'],False,None),
        ('full-run-logs',f'{REPO}/actions/runs/{RUN}/logs',True,ROOT/'full-run-logs.zip'),
    ]}
    data={label:future.result() for label,future in futures.items()}
source=base64.b64decode(data['actual-source']['content'])
(ROOT/'actual-checkout-prepare-critical-fixture.py').write_bytes(source)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    futures = {name:pool.submit(capture,'root-tree-'+name,f'{REPO}/git/trees/'+data[label]['tree']['sha']) for name,label in [('f29b','merge-f29b'),('8a','merge-8a')]}
    trees={name:future.result() for name,future in futures.items()}
backend={name:next(entry['sha'] for entry in tree['tree'] if entry['path']=='backend') for name,tree in trees.items()}
result={'actualMergeShas':SHAS,'backendTrackedTreeGitSha':backend,'allBackendTrackedBytesAndModesIdentical':backend['f29b']==backend['8a'],'actualFixtureSourceSha256':hashlib.sha256(source).hexdigest(),'fullRunLogsZipSha256':hashlib.sha256(data['full-run-logs']).hexdigest(),'scope':'GitHub actual merge Git trees; includes all tracked backend blobs/modes, does not assert ignored environment or CPU identities'}
assert result['allBackendTrackedBytesAndModesIdentical']
(ROOT/'actual-merge-backend-identity.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2),flush=True)
