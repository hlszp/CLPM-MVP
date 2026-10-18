"""Keeper orchestration; never starts a second Worker/Beat or deletes results.

Requires a reviewed immutable protocol, all 20 real inputs, and final runtime SHA.
Stops on the first formal failure; no successful-batch stitching is allowed.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from celery import Celery
from dotenv import dotenv_values
import redis

E = Path(__file__).resolve().parent
R = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')
PY = R / 'backend/.venv/bin/python'


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def run(argv, label, env, cwd=R / 'backend'):
    started = now()
    with (label.with_suffix('.stdout.log')).open('wb') as stdout, (label.with_suffix('.stderr.log')).open('wb') as stderr:
        p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=stdout, stderr=stderr)
    record = {'argv': list(map(str, argv)), 'cwd': str(cwd), 'startedAt': started, 'finishedAt': now(), 'rc': p.returncode}
    for stream in ('stdout', 'stderr'):
        path = label.with_suffix('.' + stream + '.log')
        record[stream] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    write(label.with_suffix('.command.json'), record)
    assert p.returncode == 0, f'{label}: rc={p.returncode}'
    return record


def clear_only_input_caches(cfg, frozen_ids, path):
    client = redis.Redis(host='127.0.0.1', port=17123, db=0, decode_responses=True)
    removed = []
    assert cfg['REDIS_PORT'] == '17123'
    for prefix in ('pdb', 'pdb_l2', 'pdb_l3'):
        for key in client.scan_iter(match=prefix + ':*', count=200):
            parts = key.split(':')
            if len(parts) >= 3 and parts[1] in frozen_ids:
                value = client.get(key)
                removed.append({'key': key, 'ttlMs': client.pttl(key), 'valueSha256': hashlib.sha256(value.encode()).hexdigest() if value is not None else None})
                client.unlink(key)
    leftovers = [key for key in client.scan_iter(match='pdb*:*', count=200) if len(key.split(':')) > 1 and key.split(':')[1] in frozen_ids]
    client.close()
    write(path, {'at': now(), 'privatePort': 17123, 'db': 0, 'scope': 'only all input-cache keys for frozen 1000 IDs; all task/result history remains', 'removed': removed, 'leftovers': leftovers})
    assert not leftovers


def main(args):
    protocol_path = Path(args.protocol)
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    assert protocol['formalReady'] is True and protocol['requiredBatches'] == 20
    assert protocol['sloSeconds'] == 600 and protocol['memoryBudgetBytes'] == 6 * 1024**3
    plan = json.loads((E / 'f02/series-s01/planned-batches.json').read_text())['batches']
    assert len(plan) == 20 and (E / 'f02/series-s01/input-generation-complete.json').exists()
    progress = json.loads((E / 'f02/input-series-audit-v2/progress.json').read_text())
    assert progress['passed'] == 20
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=R, text=True).strip() == protocol['candidateSha']
    assert not subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'backend'], cwd=R).strip()
    cfg = dotenv_values(R / 'backend/.env')
    assert cfg['POSTGRES_DB'] == cfg['TDENGINE_DB'] == 'clpm_capacity_20261017_s01' and cfg['REDIS_PORT'] == '17123'
    env = os.environ.copy()
    env.update({k: v for k, v in cfg.items() if v is not None})
    env.update(CLPM_TEST_MODE='0', CLPM_SKIP_EXIT_HOOKS='1', CLPM_BUILD_CODE_SHA=protocol['candidateSha'], CLPM_G5_TRACE='0', NO_PROXY='127.0.0.1,localhost,::1', PYTHONUNBUFFERED='1')
    out = E / 'f02/series-s01/formal'
    out.mkdir(exist_ok=False)
    frozen_ids = set(protocol['eligibleIds'])
    assert len(frozen_ids) == 1000
    app = Celery('f02_keeper', broker='redis://127.0.0.1:17123/1', backend='redis://127.0.0.1:17123/2')
    outcomes = []
    write(out / 'series-start.json', {'startedAt': now(), 'candidateSha': protocol['candidateSha'], 'protocolSha256': hashlib.sha256(protocol_bytes).hexdigest(), 'requiredBatches': 20, 'acceptance': 'all 20 required; one failure stops entire series'})
    for index, item in enumerate(plan):
        label = f"{item['batch']:02d}-{item['scenario']}-{item['cache']}"
        batch = out / label
        batch.mkdir()
        collector = receiver = None
        try:
            if item['cache'] == 'cold':
                run([PY, E / 'capacity_lifecycle.py', 'stop', label + '-cold-stop'], batch / 'stop', env)
                clear_only_input_caches(cfg, frozen_ids, batch / 'cache-clear.json')
                run([PY, E / 'capacity_lifecycle.py', 'restart-td', label + '-td-cold'], batch / 'restart-td', env)
                run([PY, E / 'start_capacity_api_formal.py', label + '-formal-start'], batch / 'start', env)
            runtime_state = json.loads((E / 'runtime/current-capacity-api.json').read_text())
            assert runtime_state['sha'] == protocol['candidateSha']
            write(batch / 'runtime-start-ref.json', {'state': runtime_state, 'recordSha256': hashlib.sha256(Path(runtime_state['start_record']).read_bytes()).hexdigest()})
            ping = app.control.inspect(timeout=3).ping()
            assert ping and len(ping) == 1
            write(batch / 'enable-events.json', {'at': now(), 'destination': list(ping), 'response': app.control.enable_events(destination=list(ping), reply=True, timeout=3)})
            stopfile = batch / 'observer-stop'
            report = batch / 'product-report.json'
            receiver_cmd = [str(PY), str(E / 'f02/receive_capacity_events.py'), '--env-file', str(R / 'backend/.env'), '--out', str(batch / 'events'), '--stop-file', str(stopfile), '--max-seconds', '3300']
            collector_cmd = [str(PY), str(E / 'f02/collect_capacity_evidence.py'), '--env-file', str(R / 'backend/.env'), '--ts-start', item['ts_start'], '--report', str(report), '--out', str(batch / 'raw'), '--worker-log', str(R / 'backend/logs/celery-worker.log'), '--interval', '2', '--post-seconds', '30', '--max-seconds', '3100', '--containers', 'clpm-mvp-postgres', 'clpm-mvp-tdengine', 'clpm-f02-redis-20261017-s01', '--td-container', 'clpm-mvp-tdengine']
            observer_handles = []
            for name, argv in [('receiver', receiver_cmd), ('collector', collector_cmd)]:
                handle = (batch / (name + '.log')).open('wb'); observer_handles.append(handle)
                child = subprocess.Popen(argv, cwd=E / 'f02', env=env, stdout=handle, stderr=subprocess.STDOUT)
                write(batch / (name + '-command.json'), {'argv': argv, 'pid': child.pid, 'startedAt': now()})
                if name == 'receiver': receiver = child
                else: collector = child
            # The sampler runs before warm-up, therefore warm-up also has full
            # resource evidence and does not consume a fresh extra minute of TTL.
            started = time.monotonic()
            while time.monotonic() - started < 65:
                assert receiver.poll() is None and collector.poll() is None
                time.sleep(1)
            assert (batch / 'raw/bootstrap.json').exists()
            if item['cache'] == 'hot':
                t0 = now()
                warm = app.send_task('app.tasks.kpi_calc.prewarm_cache', kwargs={'ts_start': item['ts_start']}, queue='default', routing_key='default')
                result = warm.get(timeout=1200, propagate=False)
                write(batch / 'prewarm-task.json', {'submittedAt': t0, 'finishedAt': now(), 'taskId': warm.id, 'state': warm.state, 'result': result})
                assert warm.state == 'SUCCESS' and result['total'] == result['succeeded'] == 1000 and result['failed'] == 0
            run([PY, E / 'f02/capture_capacity_cache.py', '--env-file', R / 'backend/.env', '--ts-start', item['ts_start'], '--out', batch / 'cache-before'], batch / 'cache-before-capture', env)
            run([PY, E / 'capture_capacity_runtime_profile.py', batch / 'runtime-profile', item['ts_start']], batch / 'runtime-profile-capture', env)
            manifest = E / 'f02/series-s01/input-manifests' / item['manifest_name']
            command = [str(PY), '-u', 'scripts/kpi_capacity_benchmark.py', '--mode', 'standard-hourly', '--ts-start', item['ts_start'], '--manifest', str(manifest), '--expect-eligible', '1000', '--barrier-loop-prefix', 'PERF-T', '--slo-seconds', '600', '--timeout', '2400', '--poll-interval', '2', '--freeze-dir', str(batch / 'freeze'), '--report-out', str(report), '--plan-windows', ','.join(x['ts_start'] for x in plan[index:])]
            command_record = {'argv': command, 'cwd': str(R / 'backend'), 'startedAt': now(), 'candidateSha': protocol['candidateSha']}
            write(batch / 'benchmark-command.json', command_record)
            with (batch / 'benchmark.stdout.log').open('wb') as raw, (batch / 'benchmark.stderr.log').open('wb') as err, (batch / 'benchmark-timestamped.jsonl').open('w') as timed:
                product = subprocess.Popen(command, cwd=R / 'backend', env=env, stdout=subprocess.PIPE, stderr=err)
                for line in iter(product.stdout.readline, b''):
                    raw.write(line); raw.flush()
                    timed.write(json.dumps({'observedAt': now(), 'line': line.decode('utf-8', 'replace')}) + '\n'); timed.flush()
                rc = product.wait()
            command_record.update(finishedAt=now(), rc=rc)
            write(batch / 'benchmark-command.json', command_record)
            collector_rc = collector.wait(timeout=120)
            stopfile.write_text(now() + '\n')
            receiver_rc = receiver.wait(timeout=15)
            for handle in observer_handles: handle.close()
            product_report = json.loads(report.read_text())
            outcome = {'batch': item, 'finishedAt': now(), 'rc': rc, 'collectorRc': collector_rc, 'receiverRc': receiver_rc, 'completionReason': product_report['completionReason'], 'seconds': product_report['totalSeconds'], 'sloPassed': product_report['sloPassed'], 'reportSha256': hashlib.sha256(report.read_bytes()).hexdigest()}
            outcomes.append(outcome); write(out / 'outcomes.json', outcomes)
            print(json.dumps(outcome), flush=True)
            assert rc == collector_rc == receiver_rc == 0
            assert outcome['completionReason'] == 'BARRIER_PASSED' and outcome['sloPassed'] and outcome['seconds'] <= 600
        except BaseException as exc:
            write(batch / 'orchestration-failure.json', {'at': now(), 'type': type(exc).__name__, 'message': str(exc), 'completedBatches': len(outcomes), 'status': 'SERIES_FAILED_NOT_REUSED'})
            raise
        finally:
            for name, child in [('collector', collector), ('receiver', receiver)]:
                if child is not None and child.poll() is None:
                    # Observers only: never terminate the actual business task.
                    child.terminate()
                    try: child.wait(timeout=20)
                    except subprocess.TimeoutExpired: pass
    write(out / 'complete.json', {'finishedAt': now(), 'required': 20, 'executed': len(outcomes), 'businessReportsPassed': 20, 'independentAcceptance': 'PENDING', 'resourceAcceptance': 'PENDING'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--protocol', required=True)
    main(parser.parse_args())
