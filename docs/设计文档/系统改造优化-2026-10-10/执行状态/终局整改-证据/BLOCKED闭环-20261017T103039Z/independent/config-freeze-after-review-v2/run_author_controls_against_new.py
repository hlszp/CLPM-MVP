import importlib.util,sys,pytest
from pathlib import Path
base=Path(__file__).resolve().parents[2]
source=base/'f02/freeze_capacity_configuration_v2.py'
spec=importlib.util.spec_from_file_location('freeze_capacity_configuration',source)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
sys.modules['freeze_capacity_configuration']=module
raise SystemExit(pytest.main(['-q','-o','addopts=','-p','no:cacheprovider','--confcutdir='+str(base/'f02'),'--basetemp='+str(Path(__file__).with_name('author-controls-raw')),str(base/'f02/test_freeze_capacity_configuration.py'),str(base/'f02/test_freeze_configuration_after_snapshot.py')]))
