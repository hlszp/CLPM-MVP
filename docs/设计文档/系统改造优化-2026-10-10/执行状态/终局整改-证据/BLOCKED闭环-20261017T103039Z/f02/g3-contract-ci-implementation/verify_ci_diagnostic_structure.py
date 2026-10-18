from pathlib import Path
import ast
import copy
from dataclasses import dataclass
from enum import Enum
import importlib.util
import json
import subprocess
import sys

import numpy as np
import yaml

ROOT = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT = Path(__file__).resolve().parent
SCRIPT = ROOT / 'backend/scripts/profile_identification_numerics.py'
spec = importlib.util.spec_from_file_location('g3_observer_under_review', SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.np = np
checks = []

def check(name, predicate):
    assert predicate, name
    checks.append({'name': name, 'status': 'PASS'})

# Only serializers are executed. Neither main() nor any algorithm is called.
values = np.arange(1401, dtype=np.float64) / 17
payload = module.json_value({'a': values, 'b': np.array([0.0, 1.2345678901234567]), 'c': np.array([[True, False]])})
restored = json.loads(json.dumps(payload, allow_nan=False))
check('full_numeric_arrays_not_repr_or_truncated', restored['a'] == values.tolist() and len(restored['a']) == 1401 and isinstance(restored['a'][1000], float))
check('precision_zero_and_boolean_preserved', restored['b'] == [0.0, 1.2345678901234567] and restored['c'] == [[True, False]])

class Example(Enum):
    VALUE = 'ENUM_VALUE'
@dataclass
class Result:
    array: object
    kind: Example
check('dataclass_and_enum_complete', module.json_value(Result(np.array([2., 3.]), Example.VALUE)) == {'array': [2., 3.], 'kind': 'ENUM_VALUE'})
nonfinite = module.json_value(np.array([np.nan, np.inf, -np.inf, 0.]))
check('nonfinite_explicit_not_fake_numbers', nonfinite == [{'nonFiniteFloat': 'NAN'}, {'nonFiniteFloat': 'POS_INF'}, {'nonFiniteFloat': 'NEG_INF'}, 0.])
try:
    module.json_value(object())
except TypeError:
    checks.append({'name':'unknown_types_refuse_instead_of_default_str', 'status':'PASS'})
else:
    raise AssertionError('Unsupported object silently coerced')

text = SCRIPT.read_text(); tree = ast.parse(text)
check('no_default_str_array_escape', not any(isinstance(n, ast.keyword) and n.arg == 'default' for n in ast.walk(tree)))
main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
net = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == 'no_network')
ns = {'network_attempts': []}
exec(compile(ast.Module(body=[net], type_ignores=[]), str(SCRIPT), 'exec'), ns)
secret = 'DIAGNOSTIC_FAKE_SECRET_NOT_LOGGED'
for event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname'):
    try: ns['no_network'](event, (secret,))
    except RuntimeError: pass
    else: raise AssertionError('Network event allowed: ' + event)
try: ns['no_network']('import', ('app.main',))
except RuntimeError: pass
else: raise AssertionError('app.main allowed')
check('network_and_main_guard_no_argument_leak', secret not in json.dumps(ns['network_attempts']) and len(ns['network_attempts']) == 3)

original = subprocess.run(['git','show','9f356782ef4076dabb91a86f56e054e514e341ea:.github/workflows/ci.yml'], cwd=ROOT, capture_output=True, check=True).stdout.decode()
baseline = yaml.load(original, Loader=yaml.BaseLoader)
actual = yaml.load((ROOT/'.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
backend = actual['jobs']['backend-ci']; steps = backend['steps']
pytest_step = next(s for s in steps if s['name'] == 'Pytest with coverage')
old_pytest = next(s for s in baseline['jobs']['backend-ci']['steps'] if s['name'] == 'Pytest with coverage')
copy_pytest = copy.deepcopy(pytest_step); copy_pytest.pop('id')
check('original_pytest_command_and_environment_identical', copy_pytest == old_pytest)
diag = next(s for s in steps if s.get('id') == 'g3_numeric_diagnosis')
upload = next(s for s in steps if s['name'] == 'Upload G3 numerical originals')
check('post_test_same_job_and_always_upload', steps.index(diag) > steps.index(pytest_step) and steps.index(upload) > steps.index(diag) and 'always()' in diag['if'] and 'always()' in upload['if'])
check('no_new_continue_on_error_or_swallowed_rc', 'continue-on-error' not in diag and 'continue-on-error' not in upload and '|| true' not in diag['run'] and diag['run'].rstrip().endswith('exit "$g3_rc"') and 'timeout --signal=TERM 60s' in diag['run'])
check('only_diagnostic_job_wall_budget_extended', backend['timeout-minutes'] == '17' and baseline['jobs']['backend-ci']['timeout-minutes'] == '15')
restored_workflow = copy.deepcopy(actual)
b = restored_workflow['jobs']['backend-ci']; b['timeout-minutes'] = baseline['jobs']['backend-ci']['timeout-minutes']; b['steps'] = [s for s in b['steps'] if s['name'] not in (diag['name'], upload['name'])]
next(s for s in b['steps'] if s['name'] == 'Pytest with coverage').pop('id')
check('all_other_workflow_jobs_settings_identical', restored_workflow == baseline)
(OUT/'diagnostic-step.sh').write_text(diag['run'])
subprocess.run(['bash','-n',str(OUT/'diagnostic-step.sh')], check=True)
check('diagnostic_shell_parses', True)
check('no_algorithm_or_app_main_imported', not any(name.startswith('app.services.tuning_identification') or name == 'app.main' for name in sys.modules))
(OUT/'structure-results.json').write_text(json.dumps({'scope':'SERIALIZATION_AST_YAML_GUARD_ONLY_NO_ALGORITHM_RUN','checks':checks,'passed':len(checks)},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':len(checks),'noAlgorithmRun':True}, ensure_ascii=False))
