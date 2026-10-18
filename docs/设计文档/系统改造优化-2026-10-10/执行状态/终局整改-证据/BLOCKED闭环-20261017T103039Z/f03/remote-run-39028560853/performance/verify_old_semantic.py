"""Run the new semantic guards against the frozen old function in this process."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, '/Users/zhangping/DEV/clpm-remediation-20261017/backend')
from app.services.tuning_identification import arx
import pytest

source = Path(__file__).with_name('arx.before.py').read_text()
tree = ast.parse(source)
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'identify_arx')
namespace = dict(vars(arx))
exec(compile(ast.Module(body=[function], type_ignores=[]), '<frozen-old-arx>', 'exec'), namespace)
arx.identify_arx = namespace['identify_arx']
raise SystemExit(pytest.main(['-q', 'tests/test_arx_vectorized_equivalence.py', '-p', 'no:cacheprovider']))
