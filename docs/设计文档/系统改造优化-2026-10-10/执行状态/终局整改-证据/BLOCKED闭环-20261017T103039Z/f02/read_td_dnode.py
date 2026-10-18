"""Two serial read-only dnode metadata queries, no source-series scan."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from dotenv import dotenv_values
import httpx

HERE=Path(__file__).resolve().parent
cfg=dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
endpoint=f"http://{cfg.get('TDENGINE_HOST','localhost')}:{int(cfg.get('TDENGINE_PORT','17104'))+11}/rest/sql"
out={'observedAt':datetime.now(timezone.utc).isoformat(),'queries':[]}
with httpx.Client(auth=(cfg.get('TDENGINE_USER','root'),cfg.get('TDENGINE_PASSWORD','')),timeout=10,trust_env=False) as client:
    for sql in ('SHOW DNODES','SHOW DNODE 1 VARIABLES'):
        response=client.post(endpoint,content=sql.encode());data=response.json()
        if 'VARIABLES' in sql and data.get('data'):
            data={**data,'data':[row for row in data['data'] if any(re.search('mem|query|thread|core|cache|ratio|pool|concurr|vnode|log|reserve',str(value),re.I) for value in row[:2]) and not any(re.search('password|token|secret',str(value),re.I) for value in row[:2])],'filter':'resource-related dnode variables only'}
        out['queries'].append({'sql':sql,'httpStatus':response.status_code,'responseSha256':hashlib.sha256(response.content).hexdigest(),'response':data})
(HERE/'td-dnode-query-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
for query in out['queries']: print(query['sql'],query['response'].get('code'),len(query['response'].get('data',[])))
if any(query['response'].get('code',0) not in (None,0) for query in out['queries']):raise SystemExit(1)
