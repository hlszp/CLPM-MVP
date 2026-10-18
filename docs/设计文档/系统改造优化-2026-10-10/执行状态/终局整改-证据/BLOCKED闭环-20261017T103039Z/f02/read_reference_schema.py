"""Identify config-table/FK prerequisites for a fresh capacity PG database."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

HERE=Path(__file__).resolve().parent


async def main():
    cfg=dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
    db=await asyncpg.connect(host=cfg.get('POSTGRES_HOST','localhost'),port=int(cfg.get('POSTGRES_PORT',17102)),user=cfg.get('POSTGRES_USER','clpm'),password=cfg['POSTGRES_PASSWORD'],database=cfg.get('POSTGRES_DB','clpm'))
    try:
        async with db.transaction(readonly=True):
            tables=await db.fetch("SELECT relname,n_live_tup FROM pg_stat_user_tables ORDER BY relname")
            foreign=await db.fetch("SELECT conrelid::regclass::text AS child,confrelid::regclass::text AS parent,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE contype='f' ORDER BY child,parent")
            safe_config=await db.fetch("SELECT key,value FROM sys_config WHERE key IN ('authz.role_matrix','authz.guard.revision') OR key LIKE 'kpi.%' OR key LIKE 'confidence.%' OR key LIKE 'grading.%' OR key LIKE 'diagnosis.%'")
        out={'observedAt':datetime.now(timezone.utc).isoformat(),'tables':[dict(x) for x in tables],'foreignKeys':[dict(x) for x in foreign],'safeConfig':[dict(x) for x in safe_config]}
        (HERE/'reference-schema-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
        print(len(tables),'tables',len(foreign),'foreign keys')
    finally:
        await db.close()


asyncio.run(main())
