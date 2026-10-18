-- Keeper-only execution; own disposable local F03 database. All statements read-only.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT current_database() AS database, current_setting('transaction_read_only') AS transaction_read_only, now() AS observed_at, txid_current_snapshot() AS snapshot;
SELECT row_to_json(t) FROM loop_ledger t WHERE id='c87ea558-f4bd-4c97-9607-435297fcf3a7'::uuid;
SELECT row_to_json(t) FROM loop_tag_mapping t WHERE loop_id='c87ea558-f4bd-4c97-9607-435297fcf3a7'::uuid ORDER BY tag_role;
SELECT row_to_json(t), (valid_from <= '2026-10-17T12:00:00+00'::timestamptz AND (valid_to IS NULL OR valid_to > '2026-10-17T11:00:00+00'::timestamptz)) AS intersects_closed_window FROM loop_tag_binding_history t WHERE loop_id='c87ea558-f4bd-4c97-9607-435297fcf3a7'::uuid ORDER BY tag_role,valid_from;
SELECT row_to_json(t) FROM history_layout_manifest t WHERE is_active AND (scope_type='global' OR scope_id='c87ea558-f4bd-4c97-9607-435297fcf3a7') ORDER BY valid_from;
SELECT key,value FROM sys_config WHERE key IN ('history.storage_mode','datasource.type','data_source.type') ORDER BY key;
SELECT row_to_json(t) FROM history_coverage_segment t WHERE (point_id IN ('30da49c1-2d33-4da5-9487-23027bc9e155','4431bb12-1530-423c-a3bc-1a01a8c8f27c','e9ad0a29-9428-4651-8ae3-cd9ce9f11b29','82f3ed25-172b-4c0e-8708-3b8741e757a0','7da14413-761e-4246-a460-8ac5c60ef9ac','3da633fc-e678-495c-839c-e33a9a1ac978','ce950fe9-e70a-4fcd-9739-1cffaa43f8ad') OR session_id IS NOT NULL) AND seg_start <= '2026-10-17T12:00:00+00'::timestamptz AND seg_end > '2026-10-17T11:00:00+00'::timestamptz ORDER BY point_id,seg_start;
COMMIT;
