-- LOCAL ONLY: change-capture outbox consumed by server/db_sync.py (Neon sync).
CREATE TABLE IF NOT EXISTS sync_outbox (
    id BIGSERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    op TEXT NOT NULL,
    pk JSONB NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    synced_at TIMESTAMPTZ,
    attempts INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_unsynced ON sync_outbox (id) WHERE synced_at IS NULL;

CREATE OR REPLACE FUNCTION bam_enqueue_sync() RETURNS trigger AS $$
DECLARE
    rec jsonb;
    pkobj jsonb := '{}'::jsonb;
    col text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        rec := to_jsonb(OLD);
    ELSE
        rec := to_jsonb(NEW);
    END IF;
    FOREACH col IN ARRAY TG_ARGV LOOP
        pkobj := pkobj || jsonb_build_object(col, rec -> col);
    END LOOP;
    INSERT INTO sync_outbox (table_name, op, pk, payload)
    VALUES (TG_TABLE_NAME, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
            pkobj, CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE rec END);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bam_sync_shoots ON shoots;
CREATE TRIGGER bam_sync_shoots AFTER INSERT OR UPDATE OR DELETE ON shoots
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('shoot_id');
DROP TRIGGER IF EXISTS bam_sync_assets ON assets;
CREATE TRIGGER bam_sync_assets AFTER INSERT OR UPDATE OR DELETE ON assets
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('asset_id');
DROP TRIGGER IF EXISTS bam_sync_ai_evaluations ON ai_evaluations;
CREATE TRIGGER bam_sync_ai_evaluations AFTER INSERT OR UPDATE OR DELETE ON ai_evaluations
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('eval_id');
DROP TRIGGER IF EXISTS bam_sync_asset_copies ON asset_copies;
CREATE TRIGGER bam_sync_asset_copies AFTER INSERT OR UPDATE OR DELETE ON asset_copies
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('copy_id');
DROP TRIGGER IF EXISTS bam_sync_human_corrections ON human_corrections;
CREATE TRIGGER bam_sync_human_corrections AFTER INSERT OR UPDATE OR DELETE ON human_corrections
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('correction_id');
DROP TRIGGER IF EXISTS bam_sync_training_examples ON training_examples;
CREATE TRIGGER bam_sync_training_examples AFTER INSERT OR UPDATE OR DELETE ON training_examples
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('example_id');
DROP TRIGGER IF EXISTS bam_sync_video_frames ON video_frames;
CREATE TRIGGER bam_sync_video_frames AFTER INSERT OR UPDATE OR DELETE ON video_frames
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('frame_id');
DROP TRIGGER IF EXISTS bam_sync_shoot_links ON shoot_links;
CREATE TRIGGER bam_sync_shoot_links AFTER INSERT OR UPDATE OR DELETE ON shoot_links
    FOR EACH ROW EXECUTE FUNCTION bam_enqueue_sync('shoot_id', 'related_shoot_id');
