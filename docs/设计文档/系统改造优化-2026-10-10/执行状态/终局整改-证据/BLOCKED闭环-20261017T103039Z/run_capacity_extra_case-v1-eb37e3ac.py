"""Keeper executes one frozen extra request; never part of normal20 denominator.

The non-author reviewer freezes exact requests after final runtime/config/input
identities exist. This driver imports only the already-reviewed Keeper helpers;
it neither changes product gates nor generates data or clears task history.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from celery import Celery
from dotenv import dotenv_values
import httpx

from capacity_keeper_guards import private_broker_snapshot
from run_capacity_series import E, R, PY, clear_only_input_caches, run, write


def now():
    return datetime.now(timezone.utc).isoformat()


def checked(ref):
    path = Path(ref['path'])
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ref['sha256'], str(path)
    if 'bytes' in ref:
        assert len(raw) == ref['bytes']
    return path, raw


def switch_policy(ref, current, evidence):
    _, raw = checked(ref)
    policy = json.loads(raw)
    assert current == E / 'f02/fault-integration/policy-current.json'
    temporary = current.with_suffix('.pending')
    with temporary.open('xb') as stream:
        stream.write(raw)
    os.replace(temporary, current)
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get('http://127.0.0.1:17125/__audit_proxy_health')
        health = response.json()
    write(evidence, {'at': now(), 'policy': ref, 'httpStatus': response.status_code, 'health': health})
    assert response.status_code == 200 and health['status'] == 'READY'
    assert health['epoch'] == policy['epoch'] and health['armed'] is policy['armed']
    assert health['policySha256'] == ref['sha256'] and health['inFlight'] == 0


def main(args):
    request_bytes = Path(args.request).read_bytes()
    request = json.loads(request_bytes)
    role = request['role']
    assert role in {'fault-recovery', 'fault-persistent', 'independent-cold', 'independent-hot'}
    fault = role.startswith('fault-')
    assert request['formal20Denominator'] is False and request['sloSeconds'] == 600
    assert request['cache'] == ('hot' if role == 'independent-hot' else 'cold')
    expected_rc = 1 if role == 'fault-persistent' else 0
    assert request['expectedBenchmarkRc'] == expected_rc
    assert request['expectedCompletionReason'] == ('PARENT_FAILED' if expected_rc else 'BARRIER_PASSED')
    _, protocol_bytes = checked(request['protocol'])
    protocol = json.loads(protocol_bytes)
    assert protocol['formalReady'] is True and protocol['candidateSha'] == request['candidateSha']
    assert protocol['requiredBatches'] == 20 and protocol['sloSeconds'] == 600
    assert protocol['memoryBudgetBytes'] == 6 * 1024**3
    _, complete_raw = checked(request['normal20Complete'])
    complete = json.loads(complete_raw)
    assert complete['required'] == complete['executed'] == complete['businessReportsPassed'] == 20
    for ref in request['toolSources']:
        checked(ref)
    manifest, _ = checked(request['manifest'])
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=R, text=True).strip() == request['candidateSha']
    assert not subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'backend'], cwd=R).strip()
    cfg = dotenv_values(R / 'backend/.env')
    assert cfg['POSTGRES_DB'] == cfg['TDENGINE_DB'] == 'clpm_capacity_20261017_s01'
    assert cfg['REDIS_PORT'] == '17123' and cfg['POSTGRES_PORT'] == '17102'
    assert cfg['TDENGINE_PORT'] == ('17114' if fault else '17104')
    env = os.environ.copy()
    env.update({k: v for k, v in cfg.items() if v is not None})
    env.update(CLPM_TEST_MODE='0', CLPM_SKIP_EXIT_HOOKS='1', CLPM_G5_TRACE='0',
               CLPM_BUILD_CODE_SHA=request['candidateSha'], NO_PROXY='127.0.0.1,localhost,::1',
               PYTHONUNBUFFERED='1')
    out = Path(request['output'])
    assert out.parent == E / 'f02/extra-execution'
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'request-copy.json', request)
    write(out / 'request-identity.json', {'at': now(), 'path': str(Path(args.request).resolve()),
          'sha256': hashlib.sha256(request_bytes).hexdigest(), 'driverSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    app = Celery('f02_keeper', broker='redis://127.0.0.1:17123/1', backend='redis://127.0.0.1:17123/2')
    receiver = collector = None
    observer_handles = []
    armed = False
    try:
        if request['cache'] == 'cold':
            run([PY, E / 'capacity_lifecycle.py', 'stop', role + '-extra-stop'], out / 'stop', env)
            clear_only_input_caches(cfg, set(protocol['eligibleIds']), out / 'cache-clear.json')
            run([PY, E / 'capacity_lifecycle.py', 'restart-td', role + '-extra-td'], out / 'restart-td', env)
            launcher = 'start_capacity_api_fault.py' if fault else 'start_capacity_api_formal.py'
            run([PY, E / launcher, role + '-extra-start'], out / 'start', env)
        state = json.loads((E / 'runtime/current-capacity-api.json').read_bytes())
        assert state['sha'] == request['candidateSha']
        write(out / 'runtime-start-ref.json', {'state': state, 'recordSha256': hashlib.sha256(Path(state['start_record']).read_bytes()).hexdigest()})
        ping = app.control.inspect(timeout=3).ping()
        assert ping and len(ping) == 1
        write(out / 'enable-events.json', {'at': now(), 'destination': list(ping), 'response': app.control.enable_events(destination=list(ping), reply=True, timeout=3)})
        report = out / 'product-report.json'
        stopfile = out / 'observer-stop'
        commands = {
            'receiver': [PY, E / 'f02/receive_capacity_events.py', '--env-file', R / 'backend/.env', '--out', out / 'events', '--stop-file', stopfile, '--max-seconds', '3300'],
            'collector': [PY, E / 'f02/collect_capacity_evidence.py', '--env-file', R / 'backend/.env', '--ts-start', request['tsStart'], '--report', report, '--out', out / 'raw', '--worker-log', R / 'backend/logs/celery-worker.log', '--interval', '2', '--post-seconds', '30', '--max-seconds', '3100', '--containers', 'clpm-mvp-postgres', 'clpm-mvp-tdengine', 'clpm-f02-redis-20261017-s01', '--td-container', 'clpm-mvp-tdengine'],
        }
        for name, command in commands.items():
            command = list(map(str, command))
            handle = (out / (name + '.log')).open('xb'); observer_handles.append(handle)
            child = subprocess.Popen(command, cwd=E / 'f02', env=env, stdout=handle, stderr=subprocess.STDOUT)
            write(out / (name + '-command.json'), {'argv': command, 'pid': child.pid, 'startedAt': now()})
            if name == 'receiver':
                receiver = child
            else:
                collector = child
        start = time.monotonic()
        while time.monotonic() - start < 65:
            assert receiver.poll() is None and collector.poll() is None
            time.sleep(1)
        assert (out / 'raw/bootstrap.json').exists()
        if request['cache'] == 'hot':
            submitted = now()
            warm = app.send_task('app.tasks.kpi_calc.prewarm_cache', kwargs={'ts_start': request['tsStart']}, queue='default', routing_key='default')
            result = warm.get(timeout=1200, propagate=False)
            write(out / 'prewarm-task.json', {'submittedAt': submitted, 'finishedAt': now(), 'taskId': warm.id, 'state': warm.state, 'result': result})
            assert warm.state == 'SUCCESS' and result['total'] == result['succeeded'] == 1000 and result['failed'] == 0
        run([PY, E / 'f02/capture_capacity_cache.py', '--env-file', R / 'backend/.env', '--ts-start', request['tsStart'], '--out', out / 'cache-before'], out / 'cache-before-capture', env)
        run([PY, E / 'capture_capacity_runtime_profile.py', out / 'runtime-profile', request['tsStart']], out / 'runtime-profile-capture', env)
        quiet = private_broker_snapshot()
        write(out / 'broker-before-formal.json', quiet)
        assert quiet['quiescent']
        if fault:
            current = Path(request['faultPolicy']['currentPath'])
            for key, expected in [('arm', True), ('disarm', False)]:
                _, policy_raw = checked(request['faultPolicy'][key])
                policy = json.loads(policy_raw)
                assert policy['armed'] is expected and policy['candidateSha'] == request['candidateSha']
                assert policy['activeWindow'] == ('recovery' if role == 'fault-recovery' else 'exhaustion')
            switch_policy(request['faultPolicy']['arm'], current, out / 'proxy-armed.json')
            armed = True
        command = list(map(str, [PY, '-u', 'scripts/kpi_capacity_benchmark.py', '--mode', 'standard-hourly', '--ts-start', request['tsStart'], '--manifest', manifest, '--expect-eligible', '1000', '--barrier-loop-prefix', 'PERF-T', '--slo-seconds', '600', '--timeout', '2400', '--poll-interval', '2', '--freeze-dir', out / 'freeze', '--report-out', report, '--plan-windows', ','.join(request['remainingWindows'])]))
        record = {'argv': command, 'cwd': str(R / 'backend'), 'startedAt': now(), 'candidateSha': request['candidateSha']}
        write(out / 'benchmark-command.json', record)
        with (out / 'benchmark.stdout.log').open('xb') as raw, (out / 'benchmark.stderr.log').open('xb') as stderr, (out / 'benchmark-timestamped.jsonl').open('x') as timed:
            product = subprocess.Popen(command, cwd=R / 'backend', env=env, stdout=subprocess.PIPE, stderr=stderr)
            for line in iter(product.stdout.readline, b''):
                raw.write(line); raw.flush()
                timed.write(json.dumps({'observedAt': now(), 'line': line.decode('utf-8', 'replace')}) + '\n'); timed.flush()
            rc = product.wait()
        record.update(finishedAt=now(), rc=rc)
        write(out / 'benchmark-command.json', record)
        collector_rc = collector.wait(timeout=120)
        stopfile.write_text(now() + '\n')
        receiver_rc = receiver.wait(timeout=15)
        for handle in observer_handles:
            handle.close()
        product_report = json.loads(report.read_bytes())
        write(out / 'actual-result.json', {'at': now(), 'benchmarkRc': rc, 'collectorRc': collector_rc, 'receiverRc': receiver_rc, 'completionReason': product_report['completionReason'], 'parentState': product_report['parentState'], 'taskRecordStatus': product_report['taskRecordStatus'], 'seconds': product_report['totalSeconds'], 'reportSha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'independentAcceptance': 'PENDING', 'formal20Denominator': False})
        assert collector_rc == receiver_rc == 0 and rc == expected_rc
        assert product_report['completionReason'] == request['expectedCompletionReason']
        if expected_rc:
            assert product_report['parentState'] == 'FAILURE' and product_report['taskRecordStatus'] == 'FAILED'
        else:
            assert product_report['sloPassed'] and product_report['totalSeconds'] <= 600
        if not fault:
            run([PY, E / 'validate_capacity_batch.py', '--batch', out, '--profile', E / 'f02/series-s01/resource-profile-plan.json'], out / 'local-evidence-validation', env)
        if request.get('recoverySeconds', 0):
            assert role == 'independent-hot' and request['recoverySeconds'] == 300
            recovery = list(map(str, [PY, E / 'f02/collect_capacity_evidence.py', '--env-file', R / 'backend/.env', '--ts-start', request['tsStart'], '--report', report, '--out', out / 'final-300s-recovery', '--worker-log', R / 'backend/logs/celery-worker.log', '--interval', '2', '--post-seconds', '300', '--max-seconds', '400', '--containers', 'clpm-mvp-postgres', 'clpm-mvp-tdengine', 'clpm-f02-redis-20261017-s01', '--td-container', 'clpm-mvp-tdengine']))
            run(recovery, out / 'final-recovery', env)
            run([PY, E / 'validate_capacity_batch.py', '--batch', out, '--profile', E / 'f02/series-s01/resource-profile-plan.json', '--recovery', out / 'final-300s-recovery'], out / 'local-recovery-validation', env)
        write(out / 'execution-complete.json', {'finishedAt': now(), 'role': role, 'expectedNegativeCase': bool(expected_rc), 'independentAcceptance': 'PENDING', 'formal20Denominator': False})
    except BaseException as exc:
        write(out / 'execution-failure.json', {'at': now(), 'type': type(exc).__name__, 'message': str(exc), 'status': 'REWORK_OR_BLOCKED', 'noResultOrTaskHistoryDeleted': True})
        raise
    finally:
        if armed:
            switch_policy(request['faultPolicy']['disarm'], Path(request['faultPolicy']['currentPath']), out / 'proxy-disarmed.json')
        for child in (collector, receiver):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass
        for handle in observer_handles:
            if not handle.closed:
                handle.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    main(parser.parse_args())
