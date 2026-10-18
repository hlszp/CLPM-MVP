"""Replay frozen modules and explicitly reconstruct all supplemental inputs."""
import dataclasses
import hashlib
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.identify_arx


old = load('frozen_legacy_arx', 'legacy-arx.py')
fixed = load('frozen_fixed_arx', 'arx.fixed.py')
solve = np.linalg.lstsq
u = np.random.default_rng(13).normal(size=40)
cases = []
for value in [-(2**63), 2**63, -(2**80), 2**80, 2**1024]:
    y = [i % 3 for i in range(40)]
    y[20] = value
    for name, constructor in [('list', list), ('tuple', tuple)]:
        cases.append((f'{name}:{value}', u, constructor(y), 0, 1, 1))
y = np.arange(40.0)
cases.extend([
    ('column-y', u, y[:, None], 0, 1, 1),
    ('column-u', u[:, None], y, 0, 1, 1),
    ('masked-y', u, np.ma.array(y, mask=[False] * 20 + [True] + [False] * 19), 0, 1, 1),
    ('masked-u', np.ma.array(u, mask=[False] * 20 + [True] + [False] * 19), y, 0, 1, 1),
    ('negative-order-legacy', np.arange(50.0), y, -5, -1, 3),
    ('short-u', np.arange(12.0), y, 0, 1, 1),
    ('negative-delay', np.arange(50.0), y, -3, 1, 1),
])
rows = []
for label, u, y, delay, na, nb in cases:
    outcomes = []
    for function in [old, fixed]:
        matrices = []

        def record(matrix, response, **kwargs):
            matrices.append({
                'matrixSha256': hashlib.sha256(matrix.tobytes()).hexdigest(),
                'responseSha256': hashlib.sha256(response.tobytes()).hexdigest(),
                'shape': matrix.shape,
            })
            return solve(matrix, response, **kwargs)

        np.linalg.lstsq = record
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                result = function(u, y, delay, na=na, nb=nb)
            outcomes.append({'kind': 'result', 'value': dataclasses.asdict(result), 'matrices': matrices})
        except Exception as error:
            outcomes.append({'kind': 'error', 'type': type(error).__name__, 'message': str(error), 'matrices': matrices})
        finally:
            np.linalg.lstsq = solve
    equal = json.dumps(outcomes[0], sort_keys=True) == json.dumps(outcomes[1], sort_keys=True)
    rows.append({'case': label, 'same': equal, 'legacy': outcomes[0], 'fixed': outcomes[1]})
result = {'numpy': np.__version__, 'cases': rows, 'allSame': all(row['same'] for row in rows)}
(ROOT / 'fallback-parity-replay.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'cases': len(rows), 'allSame': result['allSame']}))
raise SystemExit(0 if result['allSame'] else 1)
