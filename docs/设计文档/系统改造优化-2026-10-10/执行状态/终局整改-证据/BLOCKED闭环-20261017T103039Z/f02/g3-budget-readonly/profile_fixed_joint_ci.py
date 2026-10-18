"""Read-only, single-thread, network-denied fixed G3 input probe."""

from __future__ import annotations

import ast
import cProfile
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import platform
import pstats
import resource
import sys
import time


ROOT = Path(sys.argv[2]).resolve()
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
os.nice(10)
resource.setrlimit(resource.RLIMIT_CPU, (45, 50))
network_attempts = []


def deny_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname'):
        network_attempts.append(event)
        raise RuntimeError('Network forbidden in pure algorithm diagnosis')
    if event == 'import' and args[0] == 'app.main':
        raise RuntimeError('app.main forbidden in pure algorithm diagnosis')


sys.addaudithook(deny_network)
sys.path.insert(0, str(ROOT / 'backend'))
import numpy as np
import scipy
from scipy import signal as sps
from app.services.tuning_identification import pipeline


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


source = ROOT / 'backend/tests/test_g3_app_applicability.py'
tree = ast.parse(source.read_text())
names = {'_seg_fopdt', '_two_segments_fopdt', '_independent_joint_snr_db'}
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(nodes) == 3
fixture_namespace = {'math': math, 'np': np, 'sps': sps}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), fixture_namespace)
op, pv = fixture_namespace['_two_segments_fopdt'](1.4, 700, 521)
np.save(OUT / 'op.npy', op, allow_pickle=False)
np.save(OUT / 'pv.npy', pv, allow_pickle=False)
write_json('input-identity.json', {
    'fixtureSource': str(source), 'fixtureSourceSha256': sha(source.read_bytes()),
    'generatorFunctions': sorted(names), 'functionAstSha256': sha(ast.dump(ast.Module(body=nodes, type_ignores=[])).encode()),
    'noiseStd': 1.4, 'segN': 700, 'seedU': 521, 'secondSeedU': 571, 'gap': 200,
    'opBytesSha256': sha(op.tobytes()), 'pvBytesSha256': sha(pv.tobytes()),
    'opNpySha256': sha((OUT / 'op.npy').read_bytes()), 'pvNpySha256': sha((OUT / 'pv.npy').read_bytes()),
    'pointCount': len(op), 'opFinite': int(np.isfinite(op).sum()), 'pvFinite': int(np.isfinite(pv).sum()),
})
write_json('environment.json', {
    'platform': platform.platform(), 'python': sys.version, 'executable': sys.executable,
    'numpy': np.__version__, 'scipy': scipy.__version__, 'numpyConfig': np.show_config(mode='dicts'),
    'threads': {k: os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')},
    'CLPM_TEST_MODE': os.environ.get('CLPM_TEST_MODE'), 'CLPM_SKIP_EXIT_HOOKS': os.environ.get('CLPM_SKIP_EXIT_HOOKS'),
    'networkGuardInstalled': True, 'noAppMain': 'app.main' not in sys.modules,
    'diagnosticCpuSoftLimitSeconds': 45, 'niceIncrement': 10,
})
logging.basicConfig(filename=OUT / 'algorithm-debug.log', level=logging.DEBUG, format='%(asctime)s|%(name)s|%(levelname)s|%(message)s')
selection_events = []
armax_returns = []


def candidate(c):
    if c is None:
        return None
    return {
        'modelType': str(c.params.model_type), 'method': str(c.identify_method),
        'fittingScore': c.fitting_score, 'confidence': str(c.confidence),
        'sortKey': pipeline._candidate_sort_key(c), 'residualPassed': c.residual_test_passed,
        'joint': c.evidence.joint_estimate if c.evidence is not None else None,
        'lowSnr': c.evidence.low_snr if c.evidence is not None else None,
        'reason': c.reason,
    }


def trace_armax_return(frame, event, arg):
    if event == 'return':
        values = frame.f_locals
        index = len(armax_returns) + 1
        inputs = []
        for role in ('u', 'y'):
            array = values.get(role)
            if isinstance(array, np.ndarray):
                filename = 'armax-fit-' + str(index).zfill(2) + '-' + role + '.npy'
                np.save(OUT / filename, array, allow_pickle=False)
                raw = (OUT / filename).read_bytes()
                inputs.append({'role': role, 'file': filename, 'bytes': len(raw), 'sha256': sha(raw), 'arrayBytesSha256': sha(array.tobytes())})
        delta, tol = values.get('max_diff'), values.get('tol')
        armax_returns.append({
            'sourceFilename': frame.f_code.co_filename, 'sourceSha256': sha(Path(frame.f_code.co_filename).read_bytes()),
            'uYInputs': inputs, 'na': values.get('na'), 'nb': values.get('nb'), 'nc': values.get('nc'), 'd': values.get('d'),
            'maxIter': values.get('max_iter'), 'tol': tol, 'iterationsDone': values.get('iterations_done'),
            'lastMaxDiff': delta, 'metActualConvergenceCriterion': bool(delta < tol) if delta is not None and tol is not None else None,
            'a': values.get('a_coeffs'), 'b': values.get('b_coeffs'), 'c': values.get('c_coeffs'),
            'returnedResultType': type(arg).__name__,
        })
    return trace_armax_return


def trace_calls(frame, event, arg):
    if event == 'call' and frame.f_code is pipeline.identify_from_history.__code__:
        return trace_lines
    if event == 'call' and frame.f_code.co_name == 'identify_armax' and frame.f_code.co_filename.endswith('/armax.py'):
        frame.f_trace_lines = False
        return trace_armax_return
    return None


def trace_lines(frame, event, arg):
    if event == 'line' and frame.f_lineno == 1655:
        values = frame.f_locals
        selection_events.append({
            'sourceLine': 1655, 'singleBest': candidate(values['best_r'].best_model),
            'jointBest': candidate(values.get('joint_best')),
            'jointCandidates': [candidate(c) for c in values.get('joint_cands', [])],
            'windowBest': [{'start': s, 'end': e, 'key': k, 'candidate': candidate(r.best_model)} for k, r, s, e in values.get('_ok', [])],
            'jointAudit': values.get('joint_audit'),
        })
    return trace_lines


profiler = cProfile.Profile()
started = time.monotonic()
sys.settrace(trace_calls)
profiler.enable()
try:
    result = pipeline.identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
finally:
    profiler.disable()
    sys.settrace(None)
elapsed = time.monotonic() - started
profiler.dump_stats(OUT / 'algorithm.cprofile')
stream = io.StringIO()
pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(60)
(OUT / 'profile-cumulative.txt').write_text(stream.getvalue())
write_json('selection-events.json', selection_events)
write_json('armax-return-observations.json', armax_returns)
write_json('result.json', result.to_dict())
mw = (result.preprocessing or {}).get('multiWindow') or {}
segments = (mw.get('jointEstimate') or {}).get('segments') or []
independent = fixture_namespace['_independent_joint_snr_db']([pv[s['start']:s['end']] for s in segments], 1.0)
summary = {
    'evidenceRole': 'PURE_ALGORITHM_LOCAL_DIAGNOSIS_NOT_REMOTE_CI_REPLAY',
    'elapsedSeconds': elapsed, 'success': result.success, 'best': candidate(result.best_model),
    'jointSelected': result.best_model.evidence.joint_estimate is not None if result.best_model and result.best_model.evidence else None,
    'independentJointSnrDb': independent,
    'observedLowSnr': result.best_model.evidence.low_snr if result.best_model and result.best_model.evidence else None,
    'budgetTermination': mw.get('budgetTermination'), 'windowBudgetTermination': (result.preprocessing or {}).get('windowSearch', {}).get('budgetTermination'),
    'networkAttempts': network_attempts, 'appMainImported': 'app.main' in sys.modules,
    'selectionEventCount': len(selection_events),
}
write_json('summary.json', summary)
print(json.dumps(summary, ensure_ascii=False, default=str))
