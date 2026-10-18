from pathlib import Path
import importlib.util,sys
sys.path.insert(0,'/Users/zhangping/DEV/clpm-remediation-20261017/backend')
import app.services
import pytest
p=Path(__file__).parent/'baseline/backend/app/services/calc_snapshot.py'
spec=importlib.util.spec_from_file_location('app.services.calc_snapshot',p)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
app.services.calc_snapshot=m
spec.loader.exec_module(m)
raise SystemExit(pytest.main(['tests/test_calc_snapshot_decimal.py::test_decimal_weight_producer_round_trip','-q','--basetemp='+str(Path(__file__).parent/'red-fixed-test-tmp')]))
