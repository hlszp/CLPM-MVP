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
RUN = '39028560853'
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


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    futures = {label: pool.submit(capture, label, endpoint) for label, endpoint in {
        'run': f'{REPO}/actions/runs/{RUN}',
        'jobs': f'{REPO}/actions/runs/{RUN}/jobs?per_page=100',
        'pr': f'{REPO}/pulls/1',
        'artifacts': f'{REPO}/actions/runs/{RUN}/artifacts?per_page=100',
    }.items()}
    data = {label: future.result() for label, future in futures.items()}

summary = {'run': {key: data['run'].get(key) for key in ['id', 'event', 'head_sha', 'status', 'conclusion']},
           'jobs': [], 'downloaded': []}
pr = data['pr']
if 'head' in pr:
    summary['pullRequest'] = {'head': pr['head']['sha'], 'base': pr['base']['sha'], 'mergeCommitSha': pr['merge_commit_sha']}
for job in data['jobs'].get('jobs', []):
    summary['jobs'].append({'id': job['id'], 'name': job['name'], 'status': job['status'], 'conclusion': job['conclusion'],
                            'currentOrFailedSteps': [{'name': step['name'], 'status': step['status'], 'conclusion': step['conclusion']}
                                                     for step in job['steps'] if step['status'] == 'in_progress' or step['conclusion'] == 'failure']})
    destination = ROOT / 'job-logs' / (str(job['id']) + '.log')
    if job['status'] == 'completed' and job['conclusion'] != 'skipped' and not destination.exists():
        body = capture('job-' + str(job['id']), f'{REPO}/actions/jobs/{job["id"]}/logs', True, destination)
        if isinstance(body, bytes):
            text = body.decode(errors='replace')
            sha = re.findall(r'git log -1 --format=%H[^\n]*\n[^\n]*?\b([0-9a-f]{40})\b', text)
            summary['downloaded'].append({'job': job['name'], 'log': str(destination), 'checkoutShaFromGitLog': sha})
        else:
            destination.unlink(missing_ok=True)

for artifact in data['artifacts'].get('artifacts', []):
    directory = ROOT / 'artifacts' / str(artifact['id'])
    destination = directory.with_suffix('.zip')
    if destination.exists():
        continue
    body = capture('artifact-' + str(artifact['id']), f'{REPO}/actions/artifacts/{artifact["id"]}/zip', True, destination)
    if not isinstance(body, bytes):
        destination.unlink(missing_ok=True)
        continue
    entries = []
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for item in archive.infolist():
            relative = Path(item.filename)
            assert not relative.is_absolute() and '..' not in relative.parts
            assert not stat.S_ISLNK(item.external_attr >> 16)
            target = directory / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                entries.append({'file': str(relative), 'bytes': target.stat().st_size,
                                'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    metadata = {'github': artifact, 'archiveSha256': hashlib.sha256(body).hexdigest(), 'files': entries}
    directory.with_suffix('.metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    summary['downloaded'].append({'artifactId': artifact['id'], 'name': artifact['name'], 'files': len(entries), 'root': str(directory)})

(ROOT / (STAMP + '-summary.json')).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False))
