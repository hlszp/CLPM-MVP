\set ON_ERROR_STOP 1
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT jsonb_build_object('section','discovery-transaction','database',current_database(),
 'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),
 'timezone',current_setting('TimeZone'));
SELECT jsonb_build_object('section','dataset-candidates','rows',
 COALESCE(jsonb_agg(to_jsonb(s) ORDER BY s.id),'[]'::jsonb))
FROM calculation_dataset_snapshot s WHERE s.loop_id=:'loop_id'::uuid
 AND s.kind='KPI_GRID' AND s.ts_start=:'ts_start'::timestamp AND s.ts_end=:'ts_end'::timestamp;
COMMIT;
