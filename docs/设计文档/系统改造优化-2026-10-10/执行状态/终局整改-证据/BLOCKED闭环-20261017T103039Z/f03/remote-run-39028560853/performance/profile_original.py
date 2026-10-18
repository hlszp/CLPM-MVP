"""External profiler: call original 22-case test without modifying its source."""

import cProfile
import dataclasses
import enum
import importlib.util
import json
import pstats
import sys
import time
from pathlib import Path

import numpy as np

CANDIDATE = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUTPUT = Path(__file__).resolve().parent
sys.path.insert(0, str(CANDIDATE / 'backend'))
spec = importlib.util.spec_from_file_location(
    'original_annotated_cases',
    CANDIDATE / 'backend/tests/test_annotated_loops_evaluation.py',
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
evaluate = module._evaluate_loop
identify = module.identify_from_history
loop_results = []
identifications = []


def serializable(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f'Unserializable result field {type(value)!r}')


def record_identification(**kwargs):
    result = identify(**kwargs)
    identifications.append(json.loads(json.dumps(result, default=serializable)))
    return result


def record_loop(loop, tolerance, index):
    start = time.perf_counter()
    result = evaluate(loop, tolerance, index)
    loop_results.append({
        'id': loop['id'], 'seconds': time.perf_counter() - start, 'result': result,
    })
    return result


module._evaluate_loop = record_loop
module.identify_from_history = record_identification
start = time.perf_counter()
profile = cProfile.Profile()
profile.enable()
module.test_core_loops_identification_accuracy()
profile.disable()
elapsed = time.perf_counter() - start
profile.dump_stats(str(OUTPUT / 'original.cprofile'))
with (OUTPUT / 'original-profile.txt').open('w') as output:
    pstats.Stats(profile, stream=output).sort_stats('cumulative').print_stats(55)
with (OUTPUT / 'original-self-profile.txt').open('w') as output:
    pstats.Stats(profile, stream=output).sort_stats('tottime').print_stats(40)
(OUTPUT / 'original-loop-results.json').write_text(json.dumps({
    'elapsedSeconds': elapsed, 'loops': loop_results,
    'identifications': identifications,
    'scope': 'External cProfile original full test function; profiling overhead, not a timed pytest PASS.',
}, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'profileSeconds': elapsed, 'loops': len(loop_results),
                  'identifications': len(identifications)}))
