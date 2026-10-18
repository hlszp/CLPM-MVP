"""Isolated scientific counterfactual; never edits the candidate or changes solvers."""

import contextlib
import hashlib
import json
import math
import os
import platform
import resource
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

ROOT = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT = Path(__file__).resolve().parent
REFERENCE = OUT.parents[1] / 'independent/f02-g3-linux-two-run-originals/profiles-and-source-17.json'
NODES = [
    'TestJointWinnerLowSnrAnnotated::test_joint_winner_annotated_not_silent',
    'TestJointWinnerLowSnrAnnotated::test_joint_annotation_snr_matches_independent_recompute',
    'TestSnapshotRoundTrip::test_joint_low_snr_saved_and_reloaded',
]
events, attempts, errors, reports, phases = [], [], [], [], []
phase, current = None, None
np = None


def value(v):
    if isinstance(v, Enum):
        return value(v.value)
    if is_dataclass(v) and not isinstance(v, type):
        return value(asdict(v))
    if np is not None and isinstance(v, (np.ndarray, np.generic)):
        return value(v.tolist() if isinstance(v, np.ndarray) else v.item())
    if isinstance(v, dict):
        return {str(k): value(w) for k, w in v.items()}
    if isinstance(v, (tuple, list)):
        return [value(w) for w in v]
    if isinstance(v, float) and not math.isfinite(v):
        return {'nonFiniteFloat': repr(v)}
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    raise TypeError(type(v).__name__)


def write(name, v):
    (OUT / name).write_text(json.dumps(value(v), ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def emit(row):
    events.append(row)
    with (OUT / 'observations.live.jsonl').open('a') as stream:
        stream.write(json.dumps(value(row), ensure_ascii=False, allow_nan=False) + '\n')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(p):
    b = p.read_bytes()
    return {'file': str(p.relative_to(OUT)), 'bytes': len(b), 'sha256': sha(b)}


def fingerprints():
    return [{**row, 'actualSha256': sha((ROOT / row['file']).read_bytes())}
            for row in json.loads(REFERENCE.read_text())['source17']]


def network_guard(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                 'socket.gethostbyaddr', 'socket.getnameinfo', 'socket.sendto', 'socket.sendmsg'}:
        attempts.append({'event': event, 'phase': phase, 'test': current})
        raise RuntimeError('No network in isolated hypothesis experiment')
    if event == 'import' and args[0] == 'app.main':
        attempts.append({'event': 'import app.main', 'phase': phase})
        raise RuntimeError('app.main forbidden')


def main():
    global np, phase, current
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    sys.addaudithook(network_guard)
    before = fingerprints()
    write('source-before.json', before)
    if any(r['sha256'] != r['actualSha256'] for r in before):
        raise RuntimeError('Frozen 17-source identity mismatch')
    for r in before:
        target = OUT / 'source-originals' / r['file']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / r['file']).read_bytes())
    sys.path.insert(0, str(ROOT / 'backend'))
    import numpy
    import pytest
    import scipy
    from app.services.tuning_identification import armax, arx, pipeline
    np = numpy
    real_armax = armax.identify_armax
    if pipeline.identify_armax is not real_armax:
        raise RuntimeError('Unexpected preexisting pipeline alias')
    if sys.gettrace() is not None or sys.getprofile() is not None:
        raise RuntimeError('Existing instrumentation incompatible with isolated return observer')
    write('environment.json', {
        'startedAt': datetime.now(UTC).isoformat(), 'cwd': os.getcwd(),
        'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
        'numpy': np.__version__, 'scipy': scipy.__version__, 'numpyConfig': np.show_config(mode='dicts'),
        'cpuFeatures': np._core._multiarray_umath.__cpu_features__,
        'threadEnvironmentUnmodified': {k: os.environ.get(k) for k in (
            'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
            'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')},
        'cpuHardLimitSeconds': 45, 'testMode': os.environ.get('CLPM_TEST_MODE'),
        'skipExitHooks': os.environ.get('CLPM_SKIP_EXIT_HOOKS'),
        'evidenceRole': 'ISOLATED_CANDIDATE_QUALIFICATION_HYPOTHESIS_NOT_FIX_OR_ACCEPTANCE',
    })
    (OUT / 'arrays').mkdir(exist_ok=True)

    def array_identity(a):
        a = np.asarray(a)
        key = sha(a.tobytes())
        p = OUT / 'arrays' / (key + '.npy')
        if not p.exists():
            np.save(p, a, allow_pickle=False)
        return {**ref(p), 'rawSha256': key, 'shape': list(a.shape), 'dtype': str(a.dtype)}

    def returned(frame, event, result):
        if event != 'return':
            return returned
        v = frame.f_locals
        if frame.f_code is real_armax.__code__:
            row = {'kind': 'ARMAX_RETURN', 'phase': phase, 'test': current,
                   'u': array_identity(v['u']), 'y': array_identity(v['y']),
                   'config': {k: v.get(k) for k in ('d', 'na', 'nb', 'nc', 'max_iter', 'tol')},
                   'iterationsDone': v.get('iterations_done'), 'lastMaxDiff': v.get('max_diff'),
                   'result': value(result), 'candidateBoundaryRejected': False}
            delta, tol = v.get('max_diff'), v.get('tol')
            row['metActualABCriterion'] = bool(delta < tol) if delta is not None and tol is not None else None
            emit(row)
        elif frame.f_code is arx.identify_arx.__code__:
            emit({'kind': 'ARX_RETURN', 'phase': phase, 'test': current,
                           'uRawSha256': sha(np.asarray(v['u']).tobytes()),
                           'yRawSha256': sha(np.asarray(v['y']).tobytes()),
                           'config': {k: v.get(k) for k in ('d', 'na', 'nb')}, 'result': value(result),
                           'caller': frame.f_back.f_code.co_name if frame.f_back else None})
        else:
            emit({'kind': 'PIPELINE_RETURN', 'phase': phase, 'test': current,
                           'caller': frame.f_back.f_code.co_name if frame.f_back else None,
                           'result': value(result)})
        return returned

    def trace(frame, event, arg):
        if event == 'call' and frame.f_code in {
                real_armax.__code__, arx.identify_arx.__code__, pipeline.identify_from_history.__code__}:
            frame.f_trace_lines = False
            frame.f_trace_opcodes = False
            return returned
        return None

    def candidate_call(*args, **kwargs):
        prior = len(events)
        result = real_armax(*args, **kwargs)
        rows = [r for r in events[prior:] if r['kind'] == 'ARMAX_RETURN']
        if len(rows) != 1 or rows[0]['metActualABCriterion'] is None:
            errors.append('Actual convergence state unobservable')
            raise RuntimeError(errors[-1])
        row = rows[0]
        if row['config']['max_iter'] != 200 or row['config']['tol'] != 1e-6:
            errors.append('Unexpected original algorithm budget')
            raise RuntimeError(errors[-1])
        if phase == 'experimental' and not row['metActualABCriterion']:
            row['candidateBoundaryRejected'] = True
            raise ValueError('EXPERIMENT_ONLY_NONCONVERGED_CANDIDATE_AFTER_REAL_SOLVE')
        return result

    class Plugin:
        def pytest_runtest_setup(self, item):
            global current
            current = item.nodeid

        def pytest_runtest_logreport(self, report):
            reports.append({'phase': phase, 'nodeid': report.nodeid, 'when': report.when,
                            'outcome': report.outcome, 'duration': report.duration,
                            'longrepr': str(report.longrepr) if report.longrepr else None})

    original_alias = pipeline.identify_armax
    pipeline.identify_armax = candidate_call
    began = time.process_time()
    try:
        sys.settrace(trace)
        for phase in ('baseline', 'experimental'):
            args = ['-c', str(ROOT / 'backend/pyproject.toml'), '-q', '-p', 'no:cacheprovider',
                    '--basetemp=' + str(OUT / (phase + '-tmp')),
                    '--junitxml=' + str(OUT / (phase + '.junit.xml'))]
            args += [str(ROOT / 'backend/tests/test_g3_app_applicability.py') + '::' + n for n in NODES]
            write(phase + '.command.json', {'argv': [sys.executable, 'pytest.main', *args],
                                          'originalTestNodes': NODES, 'startedAt': datetime.now(UTC).isoformat()})
            t0, cpu0 = time.monotonic(), time.process_time()
            with (OUT / (phase + '.stdout.log')).open('w') as so, (OUT / (phase + '.stderr.log')).open('w') as se:
                with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
                    rc = int(pytest.main(args, plugins=[Plugin()]))
            phases.append({'phase': phase, 'pytestRc': rc, 'wallSeconds': time.monotonic() - t0,
                           'cpuSeconds': time.process_time() - cpu0})
            write(phase + '.exit.json', phases[-1])
            write('observations.json', events)
            write('test-reports.json', reports)
    finally:
        sys.settrace(None)
        pipeline.identify_armax = original_alias
        after = fingerprints()
        write('source-after.json', after)
        write('summary.json', {'phases': phases, 'cpuSecondsMeasured': time.process_time() - began,
                              'source17ByteIdentical': before == after,
                              'originalArmaxModuleFunctionUnchanged': armax.identify_armax is real_armax,
                              'pipelineAliasRestored': pipeline.identify_armax is original_alias,
                              'networkAttempts': attempts, 'contractErrors': errors,
                              'claim': 'HYPOTHESIS_ONLY_NOT_LINUX_COUNTERFACTUAL_OR_ACCEPTED_FIX'})
        write('observations.json', events)
        write('test-reports.json', reports)
    return 2 if errors or attempts or before != after else max(p['pytestRc'] for p in phases)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        (OUT / 'unhandled-exception.log').write_text(traceback.format_exc())
        raise
