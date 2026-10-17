import re,json,os,sys
from pathlib import Path
os.environ.setdefault('CLPM_TEST_MODE','1')
os.environ.setdefault('CLPM_SKIP_EXIT_HOOKS','1')
import pytest
class Collector:
 def pytest_collection_finish(self,session):
  self.names=[i.nodeid for i in session.items]
plugin=Collector()
rc=pytest.main(['--collect-only','-q','-p','no:cacheprovider'],plugins=[plugin])
assert rc==0

def sequence(path,hook=False):
 text=Path(path).read_text()
 text=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',text)
 if hook: text=text.split('backend pytest ❯',1)[1].split('backend alembic',1)[0]
 result=[]
 for line in text.splitlines():
  m=re.fullmatch(r'([.sxXF]+)\s*(?:\[\s*\d+%\])?\s*',line)
  if m: result.extend(m[1])
 return result
new=sequence('/private/tmp/g5-normal-push-20261014.log',True)
old=sequence('/private/tmp/g5-full-offline-final-20261014.log')
assert len(new)==len(plugin.names),(len(new),len(plugin.names))
old_names=[n for n in plugin.names if not n.endswith('test_cancel_end_race_cannot_report_negative_resume')]
assert len(old)==len(old_names),(len(old),len(old_names))
before=dict(zip(old_names,old,strict=True)); changes=[]
for name,flag in zip(plugin.names,new,strict=True):
 if before.get(name)!=flag: changes.append({'nodeid':name,'before':before.get(name,'NOT_COLLECTED'),'normalHook':flag})
out={'selectedFirstOffline':len(old),'selectedFinalNormalHook':len(new),'changes':changes,'meaning':'Two environments and one new regression; counts kept separate, no promotion to real G5 acceptance.'}
Path('/private/tmp/g5-pytest-denominator-diff-20261014.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
