BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT json_build_object('section','before','database',current_database(),'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),'snapshot',txid_current_snapshot()::text);
SELECT jsonb_build_object('section','synthetic-roster','rows',(SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id) FROM loop_ledger l WHERE l.tag_name LIKE 'PERF-T%'));
SELECT jsonb_build_object('section','bindings','rows',(SELECT jsonb_agg(to_jsonb(h) ORDER BY h.id) FROM loop_tag_binding_history h JOIN loop_ledger l ON l.id=h.loop_id WHERE l.tag_name LIKE 'PERF-T%'));
SELECT json_build_object('section','after','database',current_database(),'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),'snapshot',txid_current_snapshot()::text);
COMMIT;
