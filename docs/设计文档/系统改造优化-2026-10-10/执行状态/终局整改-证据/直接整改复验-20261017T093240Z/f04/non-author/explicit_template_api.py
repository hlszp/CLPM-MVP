"""Independent real-API checks: explicit unlink/follow clear pin; snapshots restore it."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
DATABASE = 'clpm_final_reverify_20261017_093240_phase2'
REPORT = {'startedAt': datetime.now(timezone.utc).isoformat(), 'requests': [], 'checks': []}


async def main():
    previous = json.loads((HERE / 'api-pg-results.json').read_text())
    objects = previous['objects']
    loop_id = objects['auxLoops'][7]
    cfg = dotenv_values('/Users/zhangping/DEV/CLPM-MVP/backend/.env')
    db = await asyncpg.connect(
        host=cfg.get('POSTGRES_HOST', 'localhost'), port=int(cfg.get('POSTGRES_PORT', 17102)),
        user=cfg.get('POSTGRES_USER', 'clpm'), password=cfg['POSTGRES_PASSWORD'], database=DATABASE,
    )
    client = httpx.AsyncClient(base_url=previous['api'], trust_env=False, timeout=30)
    token = None
    try:
        assert await db.fetchval('SELECT current_database()') == DATABASE
        username = await db.fetchval('SELECT username FROM sys_user WHERE id=$1::uuid', objects['users']['owner'])
        REPORT.update(database=DATABASE, api=previous['api'], loopId=loop_id, run=previous['run'])

        async def call(label, path, payload):
            response = await client.post(path, json=payload, headers={'Authorization': 'Bearer '+token} if token else {})
            body = response.json()
            REPORT['requests'].append({
                'label': label, 'method': 'POST', 'path': path,
                'request': {**payload, 'password': 'REDACTED'} if path == '/auth/login' else payload,
                'status': response.status_code,
                'response': {'code': body.get('code'), 'tokens': 'REDACTED'} if path == '/auth/login' else body,
                'responseSha256': hashlib.sha256(response.content).hexdigest(),
            })
            assert response.status_code == 200 and body.get('code') in (0, '0'), label
            return body['data']

        async def state():
            async with db.transaction(readonly=True):
                row = await db.fetchrow(
                    'SELECT engineering_template_id::text AS template, engineering_template_version AS version '
                    'FROM loop_ledger WHERE id=$1::uuid', loop_id,
                )
                return dict(row)

        def check(name, passed, evidence):
            REPORT['checks'].append({'name': name, 'passed': bool(passed), 'evidence': evidence})
            assert passed, name

        async def execute(label, updates):
            payload = {'operationId': str(uuid4()), 'loopIds': [loop_id], 'updates': updates, 'dryRun': True}
            preview = await call(label+'-preview', '/loops/batch-config', payload)
            payload.update(dryRun=False, expectedStateTokens={row['loopId']: row['stateToken'] for row in preview['results']})
            result = await call(label+'-execute', '/loops/batch-config', payload)
            assert result['affected'] == 1
            return result['snapshotId']

        async def restore(label, snapshot):
            result = await call(label, '/loops/batch-snapshots/'+snapshot+'/restore', {'reason': previous['run']+' explicit pin check', 'dryRun': False})
            assert result['affected'] == 1 and result['results'][0]['status'] == 'OK'

        login = await call('login-owner', '/auth/login', {'username': username, 'password': 'Audit-test-20261017!'})
        token = login['accessToken']
        initial = await state()
        assert initial == {'template': objects['templates']['b'], 'version': None}, initial
        # First create an A/v2 pin via the real operationId API, rather than DB mutation.
        await execute('seed-A-pin2', {'engineeringTemplateId': objects['templates']['a'], 'engineeringTemplateVersion': 2})
        pinned = await state()
        check('seed_is_actual_A_pin2', pinned == {'template': objects['templates']['a'], 'version': 2}, pinned)

        unlinked_snapshot = await execute('explicit-unlink-no-version-field', {'engineeringTemplateId': None})
        unlinked = await state()
        check('explicit_unlink_clears_template_and_pin', unlinked == {'template': None, 'version': None}, unlinked)
        await restore('restore-unlink-to-A-pin2', unlinked_snapshot)
        restored = await state()
        check('snapshot_restores_A_pin2_after_unlink', restored == pinned, restored)

        follow_snapshot = await execute('switch-B-follow-no-version-field', {'engineeringTemplateId': objects['templates']['b']})
        followed = await state()
        check('switch_to_B_follow_clears_A_pin2', followed == {'template': objects['templates']['b'], 'version': None}, followed)
        await restore('restore-follow-to-A-pin2', follow_snapshot)
        restored = await state()
        check('snapshot_restores_A_pin2_after_B_follow', restored == pinned, restored)
        # Leave auxiliary fixture at its own original B/follow state using a fresh, explicit API operation.
        await execute('restore-aux-fixture-original-B-follow', {'engineeringTemplateId': objects['templates']['b']})
        check('aux_fixture_original_state_restored', await state() == initial, await state())
        REPORT['status'] = 'PASS'
    except BaseException as exc:
        REPORT['status'] = 'REWORK_OR_HARNESS_FAILURE'
        REPORT['fatal'] = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    finally:
        REPORT['finishedAt'] = datetime.now(timezone.utc).isoformat()
        (HERE / 'explicit-template-api-results.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2)+'\n')
        await client.aclose()
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
