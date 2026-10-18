\set ON_ERROR_STOP 1
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';

SELECT jsonb_build_object(
  'section','transaction-before', 'database',current_database(),
  'readOnly',current_setting('transaction_read_only'),
  'isolation',current_setting('transaction_isolation'),
  'timezone',current_setting('TimeZone'), 'backendPid',pg_backend_pid(),
  'serverAddress',inet_server_addr(), 'serverPort',inet_server_port(),
  'sessionUser',session_user, 'currentUser',current_user,
  'transactionTimestampUtc',transaction_timestamp(),
  'observedAtUtc',clock_timestamp(),
  'transactionIdIfAssigned',txid_current_if_assigned(),
  'snapshot',pg_current_snapshot()::text
);

WITH requested(key) AS (
  VALUES ('data.held_too_long_fast_seconds'),
         ('data.held_too_long_slow_seconds')
)
SELECT jsonb_build_object(
  'section','held-policy', 'requestedKey',requested.key,
  'rowExists',s.key IS NOT NULL, 'rawValue',s.value,
  'description',s.description, 'updatedBy',s.updated_by,
  'updatedAtUtc',s.updated_at
)
FROM requested LEFT JOIN sys_config s ON s.key=requested.key
ORDER BY requested.key;

SELECT jsonb_build_object(
  'section','synthetic-loop-input',
  'requestedLoopId', :'loop_id',
  'rows', COALESCE(jsonb_agg(to_jsonb(l)), '[]'::jsonb)
)
FROM loop_ledger l WHERE l.id=:'loop_id'::uuid;

SELECT jsonb_build_object(
  'section','configuration-revision',
  'maxRevision',COALESCE(max(revision),0), 'publicationRows',count(*)
) FROM config_publication;

SELECT jsonb_build_object(
  'section','active-release-input',
  'rows',COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),'[]'::jsonb)
) FROM assessment_release_registry r WHERE r.status='ACTIVE';

SELECT jsonb_build_object(
  'section','algorithm-parameter-input',
  'rows',COALESCE(jsonb_agg(to_jsonb(p) ORDER BY to_jsonb(p)::text),'[]'::jsonb)
) FROM algorithm_parameter p;

SELECT jsonb_build_object(
  'section','metric-config-input',
  'rows',COALESCE(jsonb_agg(to_jsonb(m) ORDER BY to_jsonb(m)::text),'[]'::jsonb)
) FROM metric_config m;

SELECT jsonb_build_object(
  'section','enabled-configuration-overrides',
  'rows',COALESCE(jsonb_agg(to_jsonb(o) ORDER BY to_jsonb(o)::text),'[]'::jsonb)
) FROM config_override o WHERE o.is_enabled IS TRUE;

SELECT jsonb_build_object(
  'section','loop-type-weights-input',
  'rows',COALESCE(jsonb_agg(to_jsonb(w) ORDER BY to_jsonb(w)::text),'[]'::jsonb)
) FROM loop_type_weight w;

SELECT jsonb_build_object(
  'section','transaction-after', 'database',current_database(),
  'readOnly',current_setting('transaction_read_only'),
  'isolation',current_setting('transaction_isolation'),
  'timezone',current_setting('TimeZone'),
  'transactionTimestampUtc',transaction_timestamp(),
  'observedAtUtc',clock_timestamp(),
  'transactionIdIfAssigned',txid_current_if_assigned(),
  'snapshot',pg_current_snapshot()::text
);
COMMIT;
