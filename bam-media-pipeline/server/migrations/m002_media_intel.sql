CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

ALTER TABLE assets ADD COLUMN IF NOT EXISTS final_output JSONB;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS identifier TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS identifier_components JSONB;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS identifier_locked BOOLEAN DEFAULT FALSE;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS indicators JSONB DEFAULT '{}'::jsonb;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS feedback TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS training_candidate BOOLEAN DEFAULT FALSE;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS rework_feedback TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS scene_group TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_identifier_unique
    ON assets (identifier) WHERE identifier IS NOT NULL;

ALTER TABLE shoots ADD COLUMN IF NOT EXISTS first_captured_at TIMESTAMP;
ALTER TABLE shoots ADD COLUMN IF NOT EXISTS last_captured_at TIMESTAMP;
ALTER TABLE shoots ADD COLUMN IF NOT EXISTS locked BOOLEAN DEFAULT FALSE;

ALTER TABLE human_corrections ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS video_frames (
    frame_id BIGSERIAL PRIMARY KEY,
    asset_id VARCHAR(64) NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    time_s DOUBLE PRECISION,
    pass_name VARCHAR(16) CHECK (pass_name IN ('base', 'detail')),
    file_path TEXT,
    thumb_path TEXT,
    output JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (asset_id, frame_index)
);

CREATE TABLE IF NOT EXISTS training_examples (
    example_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id VARCHAR(64),
    identifier TEXT,
    media_kind VARCHAR(16),
    source VARCHAR(16) CHECK (source IN ('edit', 'flag', 'feedback', 'rework')),
    before_output JSONB,
    after_output JSONB,
    feedback TEXT,
    notes TEXT,
    original_ref JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS shoot_links (
    shoot_id VARCHAR(64) NOT NULL,
    related_shoot_id VARCHAR(64) NOT NULL,
    distance_m DOUBLE PRECISION,
    days_apart DOUBLE PRECISION,
    relation VARCHAR(24),
    PRIMARY KEY (shoot_id, related_shoot_id)
);
