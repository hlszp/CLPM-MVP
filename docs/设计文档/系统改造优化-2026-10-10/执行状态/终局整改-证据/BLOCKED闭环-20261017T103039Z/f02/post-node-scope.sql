-- Keeper-only read-only scope evidence, after the chosen batch/whole series.
-- Run only in clpm_capacity_20261017_s01; keep original SQL/stdout/rc/SHA.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT jsonb_build_object(
  'observedAt', clock_timestamp(),
  'database', current_database(),
  'readonly', current_setting('transaction_read_only'),
  'isolation', current_setting('transaction_isolation'),
  'snapshot', txid_current_snapshot()::text,
  'tree', (SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id), '[]'::jsonb)
           FROM (SELECT id::text, parent_id::text, is_kpi_enabled FROM plant_node) t),
  'loops', (SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id), '[]'::jsonb)
            FROM (SELECT id::text, unit_id::text, complex_loop_group_id::text
                  FROM loop_ledger WHERE tag_name LIKE 'PERF-T%') t)
)::text;
COMMIT;
