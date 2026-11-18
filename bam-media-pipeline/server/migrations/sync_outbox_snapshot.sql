-- LOCAL ONLY: seed the outbox once after installing change capture.
CREATE TABLE IF NOT EXISTS bam_local_migrations (
    name TEXT PRIMARY KEY
);

DO $$
BEGIN
    INSERT INTO bam_local_migrations (name) VALUES ('sync_outbox_snapshot')
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    LOCK TABLE shoots, assets, ai_evaluations, asset_copies, human_corrections,
        video_frames, training_examples, shoot_links IN SHARE MODE;

    -- The snapshot supersedes queued upserts, including children whose parents
    -- were never queued. Keep deletes for rows no longer in the library.
    DELETE FROM sync_outbox WHERE synced_at IS NULL AND op = 'upsert';

    -- Enqueue parents first even when the snapshot spans multiple batches.
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'shoots', 'upsert', jsonb_build_object('shoot_id', r.shoot_id), to_jsonb(r) FROM shoots r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'assets', 'upsert', jsonb_build_object('asset_id', r.asset_id), to_jsonb(r) FROM assets r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'ai_evaluations', 'upsert', jsonb_build_object('eval_id', r.eval_id), to_jsonb(r) FROM ai_evaluations r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'asset_copies', 'upsert', jsonb_build_object('copy_id', r.copy_id), to_jsonb(r) FROM asset_copies r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'human_corrections', 'upsert', jsonb_build_object('correction_id', r.correction_id), to_jsonb(r)
        FROM human_corrections r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'video_frames', 'upsert', jsonb_build_object('frame_id', r.frame_id), to_jsonb(r) FROM video_frames r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'training_examples', 'upsert', jsonb_build_object('example_id', r.example_id), to_jsonb(r)
        FROM training_examples r;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
        SELECT 'shoot_links', 'upsert', jsonb_build_object('shoot_id', r.shoot_id,
            'related_shoot_id', r.related_shoot_id), to_jsonb(r) FROM shoot_links r;
END;
$$;
