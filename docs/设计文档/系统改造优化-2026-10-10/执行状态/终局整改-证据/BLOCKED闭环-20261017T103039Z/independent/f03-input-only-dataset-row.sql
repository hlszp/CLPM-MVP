\set ON_ERROR_STOP 1
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT jsonb_build_object(
  'section','input-package-transaction', 'database',current_database(),
  'readOnly',current_setting('transaction_read_only'),
  'isolation',current_setting('transaction_isolation'),
  'timezone',current_setting('TimeZone'),
  'transactionTimestampUtc',transaction_timestamp(),
  'observedAtUtc',clock_timestamp(),
  'transactionIdIfAssigned',txid_current_if_assigned(),
  'snapshot',pg_current_snapshot()::text,
  'requestedLoopId', :'loop_id', 'requestedSnapshotId', :'snapshot_id'
);
SELECT to_jsonb(s) AS input_snapshot_row
FROM calculation_dataset_snapshot s
WHERE s.id=:'snapshot_id'::uuid AND s.loop_id=:'loop_id'::uuid;
COMMIT;
