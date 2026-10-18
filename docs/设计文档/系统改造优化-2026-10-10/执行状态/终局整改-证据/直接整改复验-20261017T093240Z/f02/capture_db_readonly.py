"""Read-only recovery of immutable D02 run evidence; never imports app/lifespan."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
RUNTIME = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend')
REPORTS = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5-D01-04-实证-20261015/D02-batches')


def serialise(value):
    if isinstance(value, asyncpg.Record):
        return {k: serialise(v) for k, v in dict(value).items()}
    if isinstance(value, list):
        return [serialise(v) for v in value]
    if isinstance(value, dict):
        return {k: serialise(v) for k, v in value.items()}
    if isinstance(value, str) and value.startswith(('{', '[')):
        try:
            return json.loads(value)
        except ValueError:
            return value
    if isinstance(value, (str, int, float, type(None), bool)):
        return value
    return str(value)


async def main():
    cfg = dotenv_values(RUNTIME / '.env')
    conn = await asyncpg.connect(
        host=cfg.get('POSTGRES_HOST', 'localhost'),
        port=int(cfg.get('POSTGRES_PORT', '7102')),
        user=cfg.get('POSTGRES_USER', 'clpm'),
        password=cfg.get('POSTGRES_PASSWORD'),
        database=cfg.get('POSTGRES_DB', 'clpm'),
        server_settings={
            'default_transaction_read_only': 'on',
            'statement_timeout': '15000',
            'application_name': 'clpm_f02_readonly_audit',
        },
    )
    out = {'observedAt': datetime.now(timezone.utc).isoformat(), 'transaction': 'REPEATABLE READ READ ONLY', 'batches': []}
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            out['readOnlyConfirmed'] = await conn.fetchval('SHOW transaction_read_only')
            out['schema'] = await conn.fetchval('SELECT version_num FROM alembic_version')
            for number in (4, 5, 7):
                report_path = REPORTS / f'batch{number}-report.json'
                report = json.loads(report_path.read_text())
                freeze_path = RUNTIME / report['freezePath']
                freeze = json.loads(freeze_path.read_text())
                manifest_path = RUNTIME / freeze['manifest']['path']
                manifest = json.loads(manifest_path.read_text())
                for label, path in [('freeze', freeze_path), ('manifest', manifest_path)]:
                    (HERE / f'batch{number}-{label}.json').write_bytes(path.read_bytes())
                eligible = [row['loopId'] for row in freeze['eligible']]
                expected_nodes = [str(row) for row in freeze['predictedNodes']]
                if expected_nodes and isinstance(freeze['predictedNodes'][0], dict):
                    expected_nodes = [str(row.get('nodeId', row.get('id'))) for row in freeze['predictedNodes']]
                run_id = report['parentTaskId']
                # Append-only records recover exact run, independently of later hourly UPSERTs.
                failures = await conn.fetch('''
                    SELECT r.id::text AS record_id, r.logical_run_id::text,
                           l.id::text AS loop_id, l.tag_name, l.unit_id::text,
                           r.ts_start, r.ts_end, r.algorithm_version AS record_algorithm_version,
                           r.config_revision, r.dataset_snapshot_id::text, r.created_at,
                           r.status AS record_status,
                           r.payload->>'snapshotStatus' AS snapshot_status,
                           r.payload->>'algorithm_version' AS payload_algorithm_version,
                           r.payload->'fitness_tags' AS fitness_tags,
                           r.payload->'fitness_detail' AS fitness_detail,
                           r.payload->'data_lineage' AS data_lineage,
                           r.payload->>'source' AS source,
                           r.payload->>'source_task_id' AS source_task_id
                    FROM calculation_result_record r
                    JOIN loop_ledger l ON l.id=r.object_id
                    WHERE r.logical_run_id=$1::uuid AND r.object_kind='LOOP'
                      AND l.id=ANY($2::uuid[])
                      AND (r.payload->>'algorithm_version' IS NULL
                           OR r.payload->>'snapshotStatus' IS DISTINCT FROM 'SUCCESS')
                    ORDER BY l.tag_name
                ''', run_id, eligible)
                counts = await conn.fetch('''
                    SELECT r.payload->>'snapshotStatus' AS snapshot_status,
                           r.payload->>'algorithm_version' AS payload_algorithm_version,
                           r.status AS record_status, count(*) AS count
                    FROM calculation_result_record r
                    WHERE r.logical_run_id=$1::uuid AND r.object_kind='LOOP'
                      AND r.object_id=ANY($2::uuid[])
                    GROUP BY 1,2,3 ORDER BY 1,2,3
                ''', run_id, eligible)
                failed_ids = [str(row['loop_id']) for row in failures]
                mappings = await conn.fetch('''
                    SELECT l.id::text AS loop_id, l.tag_name AS loop_tag,
                           l.control_type, m.tag_role, t.id::text AS tag_id,
                           t.tag_name, t.tdengine_tag_id, m.created_at
                    FROM loop_ledger l JOIN loop_tag_mapping m ON m.loop_id=l.id
                    JOIN tag_registry t ON t.id=m.tag_id
                    WHERE l.id=ANY($1::uuid[]) AND m.tag_role IN ('PV','OP','SP','MODE')
                    ORDER BY l.tag_name,m.tag_role
                ''', failed_ids)
                nodes = await conn.fetch('''
                    SELECT p.id::text AS node_id,p.name,p.parent_id::text,p.is_kpi_enabled,
                           n.status,n.algorithm_version,n.created_at,
                           n.result_record_id::text,
                           r.logical_run_id::text AS node_logical_run_id,
                           r.created_at AS record_created_at,
                           jsonb_array_length(r.payload->'loopRecordIds') AS loop_record_count
                    FROM plant_node p
                    LEFT JOIN kpi_node_snapshot_hourly n ON n.plant_node_id=p.id AND n.ts_start=$2
                    LEFT JOIN calculation_result_record r ON r.id=n.result_record_id
                    WHERE p.id=ANY($1::uuid[]) ORDER BY p.name
                ''', expected_nodes, datetime.fromisoformat(report['window']['tsStart']))
                historical_nodes = await conn.fetch('''
                    SELECT p.id::text AS node_id,p.name,r.id::text AS record_id,
                           r.logical_run_id::text,r.created_at,r.algorithm_version,r.status,
                           jsonb_array_length(r.payload->'loopRecordIds') AS loop_record_count
                    FROM calculation_result_record r
                    JOIN plant_node p ON p.id=r.object_id
                    WHERE r.object_kind='NODE' AND r.object_id=ANY($1::uuid[])
                      AND r.ts_start=$2 ORDER BY p.name,r.created_at
                ''', expected_nodes, datetime.fromisoformat(report['window']['tsStart']))
                historical_bindings = await conn.fetch('''
                    SELECT h.id::text,h.loop_id::text,h.tag_role,h.tag_id::text,
                           h.valid_from,h.valid_to,t.tdengine_tag_id,t.tag_name
                    FROM loop_tag_binding_history h JOIN tag_registry t ON t.id=h.tag_id
                    WHERE h.loop_id=ANY($1::uuid[]) AND h.valid_from<$3
                      AND (h.valid_to IS NULL OR h.valid_to>$2)
                    ORDER BY h.loop_id,h.tag_role,h.valid_from
                ''', failed_ids, datetime.fromisoformat(report['window']['tsStart']).replace(tzinfo=timezone.utc),
                     datetime.fromisoformat(report['window']['tsEnd']).replace(tzinfo=timezone.utc))
                out['batches'].append({
                    'batch': number, 'parentTaskId': run_id, 'window': report['window'],
                    'reportObservedAt': report['observedAt'],
                    'reportSha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
                    'freezeSha256': hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
                    'manifestSha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    'manifestFrozenSha256': freeze['manifest']['sha256'],
                    'eligible': len(eligible), 'predictedNodes': expected_nodes,
                    'exactRunCounts': serialise(counts), 'exactRunFailures': serialise(failures),
                    'currentTagMappings': serialise(mappings), 'currentNodeProjection': serialise(nodes),
                    'historicalNodeRecords': serialise(historical_nodes),
                    'historicalBindingsForWindow': serialise(historical_bindings),
                    'tagMappingBoundary': 'Current PG metadata, not a frozen original tag mapping or original raw input snapshot.',
                })
    finally:
        await conn.close()
    (HERE / 'readonly-db-recovery.json').write_text(json.dumps(serialise(out),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'batches': [{'batch': b['batch'],'counts': b['exactRunCounts'],'failureRows': len(b['exactRunFailures']), 'nodesNow': len(b['currentNodeProjection'])} for b in out['batches']], 'readOnly': out['readOnlyConfirmed']},ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
