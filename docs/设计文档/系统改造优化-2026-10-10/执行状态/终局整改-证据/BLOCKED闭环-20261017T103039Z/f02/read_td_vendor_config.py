"""Read upstream source for the exact installed TDengine tag; no local service changes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import httpx

HERE=Path(__file__).resolve().parent
url='https://raw.githubusercontent.com/taosdata/TDengine/ver-3.3.6.6/source/common/src/tglobal.c'
response=httpx.get(url,timeout=15,follow_redirects=True)
report={'capturedAt':datetime.now(timezone.utc).isoformat(),'url':url,'status':response.status_code,'sha256':hashlib.sha256(response.content).hexdigest()}
if response.status_code==200:
    (HERE/'vendor-tglobal-3.3.6.6.c').write_bytes(response.content)
    lines=response.text.splitlines()
    report['excerpts']=[{'line':i+1,'text':'\n'.join(lines[max(0,i-5):min(len(lines),i+7)])} for i,line in enumerate(lines) if 'minReservedMemorySize' in line or 'tsMinReservedMemorySize' in line]
(HERE/'vendor-config-source.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
response.raise_for_status()
