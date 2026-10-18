"""Final repeatable-read audit of only this verifier's 1000 private rows."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
FIELDS = (
    'control_action_direction', 'op_output_lower_limit', 'op_output_upper_limit',
    'dcs_model_id', 'engineering_template_id', 'engineering_template_version',
)


async def main():
    result = json.loads((HERE / 'api-pg-results.json').read_text())
    baseline = json.loads((HERE / 'baseline-states.json').read_text())
    ids = result['objects']['loops']
    cfg = dotenv_values('/Users/zhangping/DEV/CLPM-MVP/backend/.env')
    db = await asyncpg.connect(
        host=cfg.get('POSTGRES_HOST', 'localhost'),
        port=int(cfg.get('POSTGRES_PORT', 17102)), user=cfg.get('POSTGRES_USER', 'clpm'),
        password=cfg['POSTGRES_PASSWORD'], database=result['database'],
    )
    try:
        async with db.transaction(isolation='repeatable_read', readonly=True):
            actual_db = await db.fetchval('SELECT current_database()')
            assert actual_db == 'clpm_final_reverify_20261017_093240_phase2'
            rows = await db.fetch(
                'SELECT id::text,' + ','.join(FIELDS)
                + ' FROM loop_ledger WHERE id=ANY($1::uuid[]) ORDER BY id', ids,
            )
            states = {
                row['id']: {
                    field: str(row[field]) if field.endswith('_id') and row[field] is not None
                    else row[field] for field in FIELDS
                } for row in rows
            }
            operation_ids = {
                request['request']['operationId'] for request in result['requests']
                if request['path'] == '/loops/batch-config'
                and request['label'].startswith('preview-')
                and len(request.get('request', {}).get('loopIds', [])) == 200
            }
            snapshots = await db.fetch(
                "SELECT id::text,target_id,operation_type,"
                "jsonb_array_length(before_value::jsonb->'entries') AS entry_count "
                "FROM sys_audit_log WHERE operation_type='LOOP_BATCH_SNAPSHOT' "
                "AND id=ANY($1::uuid[]) ORDER BY id", list(operation_ids),
            )
            restore_count = await db.fetchval(
                "SELECT count(*) FROM sys_audit_log WHERE operation_type='LOOP_BATCH_RESTORE' "
                'AND target_id=ANY($1::text[])', ids,
            )
            triggers = await db.fetch("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'audit_f04_%'")
            functions = await db.fetch("SELECT proname FROM pg_proc WHERE proname LIKE 'audit_f04_%'")
            txflags = await db.fetchrow(
                "SELECT current_setting('transaction_read_only') AS readonly,"
                "current_setting('transaction_isolation') AS isolation",
            )
        report = {
            'capturedAt': datetime.now(timezone.utc).isoformat(), 'database': actual_db,
            'transaction': dict(txflags), 'run': result['run'], 'realPgRowCount': len(states),
            'restored1000EqualsBaseline': states == {key: baseline[key] for key in ids},
            'realPgEqualsSavedFinalStates': states == result['final1000States'],
            'snapshots': [dict(row) for row in snapshots], 'restoreAuditCount': restore_count,
            'remainingOwnTriggers': [dict(row) for row in triggers],
            'remainingOwnFunctions': [dict(row) for row in functions], 'states': states,
        }
        (HERE / 'postgres-final-readonly.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        assert report['restored1000EqualsBaseline'] and report['realPgEqualsSavedFinalStates']
        assert len(snapshots) == 5 and all(row['entry_count'] == 200 for row in snapshots)
        assert restore_count == 1000 and not triggers and not functions
        print(json.dumps({key: value for key, value in report.items() if key != 'states'}, ensure_ascii=False))
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
