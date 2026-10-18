"""Keeper-only controls for the explicitly isolated F02 test runtime.

No Worker or Beat is started by this script. API startup retains real lifespan.
Every command and signal is recorded; existing data/queues are never purged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone

from celery import Celery
from dotenv import dotenv_values
import httpx
from capacity_keeper_guards import private_broker_snapshot

ROOT = Path(__file__).resolve().parent
RUNTIME = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')


def clock():
    return datetime.now(timezone.utc).isoformat()


def proc(pid):
    p = subprocess.run(['ps', '-p', str(pid), '-o', 'pid=,ppid=,stat=,command='], capture_output=True, text=True)
    if p.returncode:
        return None
    row = p.stdout.strip().split(maxsplit=3)
    return {'pid': int(row[0]), 'ppid': int(row[1]), 'stat': row[2], 'command': row[3]}


def config():
    cfg = dotenv_values(RUNTIME / 'backend/.env')
    assert cfg['POSTGRES_DB'] == cfg['TDENGINE_DB'] == 'clpm_capacity_20261017_s01'
    assert cfg['REDIS_PORT'] == '17123'
    return cfg


def main(args):
    out = ROOT / 'runtime/lifecycles' / args.label
    out.mkdir(parents=True, exist_ok=False)
    record = {'action': args.action, 'startedAt': clock(), 'events': [], 'status': 'RUNNING'}

    def save():
        (out / 'control.json').write_text(json.dumps(record, indent=2) + '\n')

    def event(kind, **values):
        record['events'].append({'at': clock(), 'kind': kind, **values})
        save()

    cfg = config()
    try:
        if args.action == 'stop':
            state = json.loads((ROOT / 'runtime/current-capacity-api.json').read_text())
            api = proc(state['api_pid'])
            beat = proc(state['beat_pid'])
            assert api and str(RUNTIME / 'backend/.venv/bin/python') in api['command'] and '-m uvicorn app.main:app' in api['command']
            assert beat and beat['ppid'] == api['pid'] and '-m celery' in beat['command'] and ' beat ' in beat['command']
            event('owned-process-guard', api=api, beat=beat, startRecord=state['start_record'], loadedSha=state['sha'])
            app = Celery('f02_keeper', broker='redis://127.0.0.1:17123/1', backend='redis://127.0.0.1:17123/2')
            ping = app.control.inspect(timeout=3).ping()
            assert ping and len(ping) == 1, ping
            destination = list(ping)
            event('cancel-default-consumer', destination=destination, response=app.control.cancel_consumer('default', destination=destination, reply=True, timeout=3))
            drained = False
            for _ in range(60):
                inspector = app.control.inspect(destination=destination, timeout=3)
                active, reserved, scheduled = inspector.active(), inspector.reserved(), inspector.scheduled()
                broker_state = private_broker_snapshot()
                event('drain-observation', active=active, reserved=reserved, scheduled=scheduled, broker=broker_state)
                if broker_state['quiescent'] and all(isinstance(x, dict) and set(x) == set(destination) and all(not y for y in x.values()) for x in (active, reserved, scheduled)):
                    drained = True
                    break
                time.sleep(1)
            assert drained, 'Pending tasks retained; refusing lifecycle mutation'
            # Beat was stopped before any capacity dispatch. Deliver pending TERM
            # before CONT so it cannot run overdue schedules during API teardown.
            os.kill(beat['pid'], signal.SIGTERM)
            event('signal', pid=beat['pid'], signal='SIGTERM')
            if 'T' in beat['stat']:
                os.kill(beat['pid'], signal.SIGCONT)
                event('signal', pid=beat['pid'], signal='SIGCONT_AFTER_TERM')
            os.kill(api['pid'], signal.SIGTERM)
            event('signal', pid=api['pid'], signal='SIGTERM')
            for _ in range(45):
                if proc(api['pid']) is None:
                    break
                time.sleep(1)
            if proc(api['pid']) is not None:
                assert '-m uvicorn app.main:app' in proc(api['pid'])['command']
                os.kill(api['pid'], signal.SIGINT)
                event('signal', pid=api['pid'], signal='SIGINT_GRACEFUL_FALLBACK')
                for _ in range(20):
                    if proc(api['pid']) is None:
                        break
                    time.sleep(1)
            remaining = subprocess.check_output(['ps', '-ax', '-o', 'pid=,ppid=,command='], text=True).splitlines()
            remaining = [line for line in remaining if str(RUNTIME / 'backend/.venv/bin/python') in line and ('-m celery' in line or '-m uvicorn app.main:app' in line)]
            event('post-stop-processes', remaining=remaining)
            assert not remaining, 'No forced kills: service shutdown incomplete'
            post_stop_broker = private_broker_snapshot()
            event('post-stop-broker', snapshot=post_stop_broker)
            assert post_stop_broker['quiescent'], 'Post-stop messages retained; next startup refused'
            backups = out / 'beat-state'
            backups.mkdir()
            for path in sorted((RUNTIME / 'backend/logs').glob('celerybeat*')):
                if path.is_file():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    shutil.move(str(path), str(backups / path.name))
                    event('preserve-beat-state', name=path.name, sha256=digest)
        elif args.action == 'restart-td':
            allps = subprocess.check_output(['ps', '-ax', '-o', 'command='], text=True).splitlines()
            assert not any(str(RUNTIME / 'backend/.venv/bin/python') in line and ('-m celery' in line or '-m uvicorn app.main:app' in line) for line in allps)
            for phase in ('before', 'after'):
                if phase == 'after':
                    cmd = ['docker', 'restart', 'clpm-mvp-tdengine']
                    p = subprocess.run(cmd, capture_output=True, text=True)
                    event('restart-only-dev-td', argv=cmd, rc=p.returncode, stdout=p.stdout, stderr=p.stderr)
                    assert p.returncode == 0
                inspected = json.loads(subprocess.check_output(['docker', 'inspect', 'clpm-mvp-tdengine'], text=True))[0]
                selected = {'id': inspected['Id'], 'image': inspected['Image'], 'state': inspected['State'], 'mounts': inspected['Mounts'], 'memoryBytes': inspected['HostConfig']['Memory'], 'nanoCpus': inspected['HostConfig']['NanoCpus']}
                event('container-' + phase, **selected)
            with httpx.Client(auth=(cfg.get('TDENGINE_USER', 'root'), cfg['TDENGINE_PASSWORD']), timeout=2, trust_env=False) as client:
                for _ in range(90):
                    try:
                        r = client.post('http://127.0.0.1:17115/rest/sql', content=b'SELECT 1')
                        if r.status_code == 200 and r.json()['code'] == 0:
                            event('td-ready', httpStatus=r.status_code, response=r.json())
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError('TD readiness timeout')
            record['coldBoundary'] = 'TD process cache reset; Docker VM and OS page cache are not controlled or claimed cold.'
        record['status'] = 'COMPLETE'
    except BaseException as exc:
        record.update(status='FAILED', error={'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        record['finishedAt'] = clock()
        save()
    print(json.dumps({'status': record['status'], 'evidence': str(out / 'control.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('stop', 'restart-td'))
    parser.add_argument('label')
    main(parser.parse_args())
