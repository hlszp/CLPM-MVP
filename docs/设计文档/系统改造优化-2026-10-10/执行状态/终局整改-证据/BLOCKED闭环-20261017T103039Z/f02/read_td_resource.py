"""At most four serial metadata SELECT/SHOW requests. No data scan or DDL."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from dotenv import dotenv_values
import httpx

HERE = Path(__file__).resolve().parent
cfg = dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
endpoint = f"http://{cfg.get('TDENGINE_HOST','localhost')}:{int(cfg.get('TDENGINE_PORT','17104'))+11}/rest/sql"
report = {'observedAt': datetime.now(timezone.utc).isoformat(), 'endpoint': endpoint, 'queries': []}
with httpx.Client(auth=(cfg.get('TDENGINE_USER','root'),cfg.get('TDENGINE_PASSWORD','')), timeout=10,trust_env=False) as client:
    for sql in ('SELECT SERVER_VERSION()', 'SHOW VARIABLES', 'SHOW QUERIES', 'SELECT COUNT(*) FROM performance_schema.perf_queries'):
        assert sql.startswith(('SELECT ', 'SHOW '))
        response = client.post(endpoint,content=sql.encode())
        payload=response.json()
        entry={'sql':sql,'httpStatus':response.status_code,'responseSha256':hashlib.sha256(response.content).hexdigest()}
        if sql=='SHOW VARIABLES' and payload.get('data'):
            payload={**payload,'data':[row for row in payload['data'] if re.search(r'mem|query|thread|core|cache|ratio|pool|concurr|vnode|log|version',str(row[0]),re.I) and not re.search(r'password|token|secret',str(row[0]),re.I)],'filter':'only resource-related variables; credential values are not saved'}
        entry['response']=payload
        report['queries'].append(entry)
(HERE/'td-resource-query-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
for q in report['queries']:
    print(q['sql'],q['httpStatus'],q['response'].get('code'),len(q['response'].get('data',[])))
if any(q['response'].get('code',0) not in (None,0) for q in report['queries']):
    raise SystemExit(1)
