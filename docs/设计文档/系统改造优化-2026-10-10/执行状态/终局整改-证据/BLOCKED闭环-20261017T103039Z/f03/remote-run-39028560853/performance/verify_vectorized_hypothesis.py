"""Before any source edit, test a slice-only version of the current ARX code."""

import dataclasses
import importlib.util
import inspect
import json
import sys
import time
from pathlib import Path

import numpy as np

CANDIDATE = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUTPUT = Path(__file__).resolve().parent
sys.path.insert(0, str(CANDIDATE / 'backend'))
from app.services.tuning_identification import arx

old = arx.identify_arx
source = inspect.getsource(old)
old_loop = '''    for i in range(rows):
        idx = max_lag + i
        for j in range(na):
            Phi[i, j] = -y[idx - 1 - j]
        for j in range(nb):
            Phi[i, na + j] = u[idx - d - j]
        y_reg[i] = y[idx]
'''
new_loop = '''    for j in range(na):
        Phi[:, j] = -np.asarray(y[max_lag - 1 - j : n - 1 - j])
    for j in range(nb):
        Phi[:, na + j] = u[max_lag - d - j : n - d - j]
    y_reg[:] = y[max_lag:]
'''
assert source.count(old_loop) == 1
namespace = dict(vars(arx))
exec(compile(source.replace(old_loop, new_loop), '<vectorized-ARX-hypothesis>', 'exec'), namespace)
new = namespace['identify_arx']
original_lstsq = np.linalg.lstsq
captured = []


def spy(matrix, response, **kwargs):
    captured.append((matrix.copy(), response.copy(), matrix.flags.c_contiguous))
    return original_lstsq(matrix, response, **kwargs)


rng = np.random.default_rng(20261017)
cases = 0
successes = 0
for n in [1, 18, 71, 300]:
    base_u = rng.normal(size=n * 2)
    base_y = rng.normal(size=n * 2)
    for dtype in [np.float32, np.float64, np.int64, list]:
        for stride in [1, 2]:
            if dtype is list:
                u = base_u[::stride][:n].tolist()
                y = base_y[::stride][:n].tolist()
            else:
                u = (base_u * 8).astype(dtype)[::stride][:n]
                y = (base_y * 8).astype(dtype)[::stride][:n]
            for na in [0, 1, 2, 4]:
                for nb in [0, 1, 3]:
                    for delay in [0, 1, 7, 33]:
                        outcomes = []
                        captured.clear()
                        np.linalg.lstsq = spy
                        try:
                            for function in [old, new]:
                                try:
                                    outcomes.append(('result', dataclasses.asdict(function(u, y, delay, na=na, nb=nb))))
                                except Exception as error:
                                    outcomes.append(('error', type(error).__name__, str(error)))
                        finally:
                            np.linalg.lstsq = original_lstsq
                        assert outcomes[0] == outcomes[1], (n, dtype, stride, na, nb, delay, outcomes)
                        if captured:
                            assert len(captured) == 2
                            for before, after in zip(captured[0][:2], captured[1][:2], strict=True):
                                assert before.dtype == after.dtype == np.dtype('float64')
                                assert before.shape == after.shape
                                assert before.tobytes() == after.tobytes()
                            assert captured[0][2] == captured[1][2]
                            successes += 1
                        cases += 1

timings = {}
for name, function in [('old', old), ('slice', new)]:
    u = rng.normal(size=1200)
    y = rng.normal(size=1200)
    start = time.perf_counter()
    for _ in range(500):
        function(u, y, 12, na=2, nb=1)
    timings[name] = time.perf_counter() - start
result = {'hypothesisConfirmed': True, 'cases': cases, 'successfulMatrixComparisons': successes,
          'allResultsAndErrorsExactlyEqual': True, 'matrixBytesAndLayoutExactlyEqual': True,
          'microbench500CallsSeconds': timings, 'scope': 'external prototype only; source not edited'}
(OUTPUT / 'vectorized-hypothesis.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))

# Re-use the external original-test harness, substituting only the ARX function
# object in loaded modules. No source files or running application are changed.
for name, module in list(sys.modules.items()):
    if name.startswith('app.services.tuning_identification') and getattr(module, 'identify_arx', None) is old:
        module.identify_arx = new
harness = (OUTPUT / 'profile_original.py').read_text()
harness = harness.replace('original.cprofile', 'prototype.cprofile')
harness = harness.replace('original-profile.txt', 'prototype-profile.txt')
harness = harness.replace('original-self-profile.txt', 'prototype-self-profile.txt')
harness = harness.replace('original-loop-results.json', 'prototype-loop-results.json')
exec(compile(harness, str(OUTPUT / 'profile_original.py'), 'exec'), {'__file__': str(OUTPUT / 'profile_original.py')})
before = json.loads((OUTPUT / 'original-loop-results.json').read_text())
after = json.loads((OUTPUT / 'prototype-loop-results.json').read_text())
result['full22IdentificationResultsExactlyEqual'] = before['identifications'] == after['identifications']
result['full22AccuracyRowsExactlyEqual'] = [x['result'] for x in before['loops']] == [x['result'] for x in after['loops']]
result['profileSeconds'] = {'original': before['elapsedSeconds'], 'prototype': after['elapsedSeconds']}
(OUTPUT / 'vectorized-hypothesis.json').write_text(json.dumps(result, indent=2) + '\n')
assert result['full22IdentificationResultsExactlyEqual']
assert result['full22AccuracyRowsExactlyEqual']
print(json.dumps(result))
