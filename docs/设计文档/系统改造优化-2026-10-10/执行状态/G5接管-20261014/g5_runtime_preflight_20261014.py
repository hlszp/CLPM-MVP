import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, '/private/tmp/clpm-g5-followup-20261014/backend')
from sqlalchemy import text
from app.core.db import AsyncSessionLocal, engine
from app.tasks.celery_app import celery_app

BASE = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014')
REPO = Path('/private/tmp/clpm-g5-followup-20261014')

async def main():
    result = {'observedAtUTC': datetime.now(UTC).isoformat(), 'readOnly': True}
    fixed = json.loads((BASE / '固定候选与门禁.json').read_text())
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    assert sha == fixed['candidateSHA']
    result['candidateSHA'] = sha
    result['candidateFiles'] = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in fixed['frozenFiles']}
    assert result['candidateFiles'] == fixed['frozenFiles']
    result['trackedStatus'] = subprocess.check_output(['git', 'status', '--porcelain', '-uno'], cwd=REPO, text=True).strip()
    assert not result['trackedStatus']
    ps = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,lstart=,command='], text=True)
    result['processes'] = []
    for line in ps.splitlines():
        if ('uvicorn app.main:app' in line and '--port 17101' in line) or 'celery -A app.tasks.celery_app' in line:
            pid = int(line.split()[0])
            cwd = subprocess.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], capture_output=True, text=True)
            result['processes'].append({'pid': pid, 'process': line.strip(), 'cwd': cwd.stdout.strip()})
    inspector = celery_app.control.inspect(timeout=5)
    result['worker'] = {}
    for kind in ('active', 'reserved', 'scheduled'):
        result['worker'][kind] = getattr(inspector, kind)()
    assert result['worker']['active'] is not None, 'No live worker inspect reply'
    with celery_app.connection_for_read() as connection:
        channel = connection.channel()
        result['broker'] = {'prioritySteps': list(channel.priority_steps), 'separator': repr(channel.sep), 'queues': {}}
        for queue in ('default', 'dead_letter'):
            for priority in channel.priority_steps:
                key = channel._q_for_pri(queue, priority)
                result['broker']['queues'][repr(key)] = channel.client.llen(key)
        result['broker']['unackedHash'] = channel.client.hlen(channel.unacked_key)
        result['broker']['unackedIndex'] = channel.client.zcard(channel.unacked_index_key)
        channel.close()
    async with AsyncSessionLocal() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        result['alembicHeads'] = list((await db.execute(text('SELECT version_num FROM alembic_version'))).scalars())
        result['configs'] = [dict(x._mapping) for x in (await db.execute(text("SELECT key,value FROM sys_config WHERE key IN ('data.held_too_long_fast_seconds','data.held_too_long_slow_seconds','kpi.min_observed_ratio','outlier_params.current','algorithm_params.current') OR key LIKE 'fitness.%' OR key LIKE 'config.%' ORDER BY key"))).all()]
        result['pgConnections'] = [dict(x._mapping) for x in (await db.execute(text("SELECT application_name,state,count(*) AS count FROM pg_stat_activity WHERE datname=current_database() GROUP BY application_name,state ORDER BY application_name,state"))).all()]
        loops = (await db.execute(text("SELECT id,tag_name,loop_type,control_type FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' ORDER BY tag_name"))).all()
        result['fixtureLoops'] = [dict(x._mapping) for x in loops]
        now = datetime.now(UTC)
        gaps = await db.execute(text("SELECT id,point_id,session_id,seg_start,seg_end,status FROM history_coverage_segment WHERE status='gap' AND seg_end>:start AND seg_start<:end ORDER BY seg_start"), {'start': now - timedelta(hours=48), 'end': now + timedelta(hours=12)})
        result['gapSegmentsNearMeasurement'] = [dict(x._mapping) for x in gaps.all()]
        result['tablesForConfigAndLedger'] = list((await db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '%config%' OR table_name LIKE '%result_record%' OR table_name LIKE '%hour_kpi%' OR table_name LIKE '%weight%') ORDER BY table_name"))).scalars())
        await db.rollback()
    await engine.dispose()
    result['readOnlyConfigHash'] = hashlib.sha256(json.dumps(result['configs'], ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    path = BASE / 'runtime-preflight-20261014.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps({'report': str(path), 'candidateSHA': sha, 'active': result['worker']['active'], 'reserved': result['worker']['reserved'], 'scheduled': result['worker']['scheduled'], 'broker': result['broker'], 'alembicHeads': result['alembicHeads'], 'configKeys': [x['key'] for x in result['configs']], 'fixtureLoopCount': len(loops), 'gapSegmentCount': len(result['gapSegmentsNearMeasurement']), 'ledgerTables': result['tablesForConfigAndLedger']}, ensure_ascii=False, default=str))

asyncio.run(main())
