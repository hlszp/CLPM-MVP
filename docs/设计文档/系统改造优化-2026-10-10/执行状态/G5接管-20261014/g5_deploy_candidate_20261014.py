import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, '/private/tmp/clpm-g5-followup-20261014/backend')
from app.tasks.celery_app import celery_app

REPO = Path('/private/tmp/clpm-g5-followup-20261014')
OLD = Path('/Users/zhangping/DEV/CLPM-MVP-p1int/backend')
BASE = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014')
REPORT = BASE / 'runtime-deployment-20261014.json'
state = {'startedAtUTC': datetime.now(UTC).isoformat(), 'owner': 'Codex root, user authorized Keeper transfer', 'candidateSHA': '6c92cf6944d40bc2c92c9296110711933be28ec7', 'traceEnabled': True, 'stages': []}

def note(kind, **fields):
    event = {'kind': kind, 'observedAtUTC': datetime.now(UTC).isoformat(), **fields}
    state['stages'].append(event)
    REPORT.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)

def processes():
    rows = {}
    for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,lstart=,command='], text=True).splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3:
            rows[int(fields[0])] = {'pid': int(fields[0]), 'ppid': int(fields[1]), 'description': fields[2]}
    return rows

def descendants(rows, root):
    found = {root}
    while True:
        new = {pid for pid, row in rows.items() if row['ppid'] in found}
        if new <= found:
            return found
        found |= new

def send_exact(row, sig):
    current = processes().get(row['pid'])
    if current is None:
        return
    assert current['description'] == row['description'], f"PID identity changed: {row['pid']}"
    os.kill(row['pid'], sig)
    note('signal', pid=row['pid'], signal=sig.name)

def broker_pending():
    with celery_app.connection_for_read() as connection:
        channel = connection.channel()
        queues = {repr(channel._q_for_pri(q, p)): channel.client.llen(channel._q_for_pri(q, p)) for q in ('default', 'dead_letter') for p in channel.priority_steps}
        pending = {'queues': queues, 'unackedHash': channel.client.hlen(channel.unacked_key), 'unackedIndex': channel.client.zcard(channel.unacked_index_key)}
        channel.close()
        return pending

def main():
    fixed = json.loads((BASE / '固定候选与门禁.json').read_text())
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip() == state['candidateSHA']
    before = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in fixed['frozenFiles']}
    assert before == fixed['frozenFiles']
    assert not subprocess.check_output(['git', 'status', '--porcelain', '-uno'], cwd=REPO, text=True).strip()
    rollback_sha = subprocess.check_output(['git', 'rev-parse', 'bd223e39^{commit}'], cwd=OLD, text=True).strip()
    rollback = Path('/private/tmp/clpm-g5-rollback-bd223e39-20261014')
    if not rollback.exists():
        archive = subprocess.check_output(['git', 'archive', rollback_sha, 'backend'], cwd=OLD.parent)
        rollback.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
            tf.extractall(rollback, filter='data')
        (rollback / 'backend/.venv').symlink_to(OLD / '.venv', target_is_directory=True)
        shutil.copy2(OLD / '.env', rollback / 'backend/.env')
        os.chmod(rollback / 'backend/.env', 0o600)
    old_env = hashlib.sha256((OLD / '.env').read_bytes()).hexdigest()
    new_env = hashlib.sha256((REPO / 'backend/.env').read_bytes()).hexdigest()
    assert old_env == new_env, 'Environment changed: reconcile before deployment'
    note('ready', rollbackSHA=rollback_sha, rollbackPath=str(rollback), envFileHash=old_env, sourceFiles=before)
    rows = processes()
    apis = [r for r in rows.values() if 'uvicorn app.main:app --host 0.0.0.0 --port 17101' in r['description'] and '/.venv/' in r['description']]
    assert len(apis) == 1, f'Expected one existing API, found {len(apis)}'
    api = apis[0]
    family = {pid: rows[pid] for pid in descendants(rows, api['pid'])}
    beat = [r for r in family.values() if 'celery -A app.tasks.celery_app beat ' in r['description']]
    assert len(beat) == 1
    stopped = False
    try:
        send_exact(beat[0], signal.SIGSTOP)
        stopped = True
        note('scheduler_paused_for_drain', oldFamily=family)
        deadline = time.monotonic() + 180
        while True:
            inspect = celery_app.control.inspect(timeout=2)
            active, reserved = inspect.active(), inspect.reserved()
            pending = broker_pending()
            note('drain', active=active, reserved=reserved, broker=pending)
            assert active is not None and reserved is not None, 'Worker inspect reply missing'
            empty = all(not x for x in active.values()) and all(not x for x in reserved.values()) and not any(pending['queues'].values()) and pending['unackedHash'] == 0 and pending['unackedIndex'] == 0
            if empty:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Drain deadline reached; no tasks killed')
            time.sleep(2)
        send_exact(api, signal.SIGTERM)
        send_exact(beat[0], signal.SIGCONT)
        stopped = False
        deadline = time.monotonic() + 35
        while any(pid in processes() for pid in family) and time.monotonic() < deadline:
            time.sleep(1)
        for pid, row in reversed(list(family.items())):
            if pid in processes():
                send_exact(row, signal.SIGTERM)
        time.sleep(3)
        for pid, row in reversed(list(family.items())):
            if pid in processes():
                send_exact(row, signal.SIGKILL)
        assert not any(pid in processes() for pid in family), 'Old API family remains'
        port = subprocess.run(['lsof', '-nP', '-iTCP:17101', '-sTCP:LISTEN'], capture_output=True, text=True)
        assert not port.stdout.strip(), 'Port still has listener'
        note('old_runtime_stopped')
        logdir = REPO / 'backend/logs'
        logdir.mkdir(exist_ok=True)
        logpath = logdir / 'uvicorn-g5-20261014.log'
        env = {**os.environ, 'CLPM_G5_TRACE': '1'}
        with logpath.open('ab') as log:
            process = subprocess.Popen([str(REPO / 'backend/.venv/bin/python'), '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '17101'], cwd=REPO / 'backend', env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        note('new_api_started', pid=process.pid, cwd=str(REPO / 'backend'), log=str(logpath), trace='1')
        deadline = time.monotonic() + 90
        health = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'API exited: {process.returncode}; see {logpath}')
            try:
                with urllib.request.urlopen('http://127.0.0.1:17101/health', timeout=3) as response:
                    health = {'status': response.status, 'body': response.read().decode()}
                if health['status'] == 200:
                    break
            except Exception:
                pass
            time.sleep(2)
        assert health and health['status'] == 200, 'API health deadline reached'
        time.sleep(4)
        rows = processes()
        newfamily = {pid: rows[pid] for pid in descendants(rows, process.pid)}
        after = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in fixed['frozenFiles']}
        assert after == before
        note('deployment_certified', health=health, newFamily=newfamily, afterSourceFiles=after)
        state['status'] = 'DEPLOYED_SOURCE_AND_NEW_PROCESS_CHAIN_CERTIFIED'
        state['realSmokePassed'] = False
        state['capacityPassed'] = False
        REPORT.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + '\n')
    finally:
        if stopped:
            send_exact(beat[0], signal.SIGCONT)

try:
    main()
except BaseException as exc:
    note('error', errorType=type(exc).__name__, message=str(exc))
    raise
