-- AIR pipeline facts. Media bytes stay in the library and in R2.
-- temp_id is assigned at prep. compiled_id stays null until the location holds.

CREATE TABLE IF NOT EXISTS assets (
    temp_id TEXT PRIMARY KEY,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('still', 'video', 'video_frame', 'model3d', 'other')),
    original_name TEXT NOT NULL,
    original_path TEXT,
    sha256 TEXT UNIQUE,
    byte_size BIGINT,
    subfolder TEXT,
    sequence INTEGER,
    captured_at TIMESTAMPTZ,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    altitude_m DOUBLE PRECISION,
    gimbal_pitch DOUBLE PRECISION,
    shot_characteristic TEXT,
    workflow_state TEXT NOT NULL DEFAULT 'INGESTED',
    location_id TEXT,
    location_confidence DOUBLE PRECISION,
    compiled_id TEXT UNIQUE,
    alt_text TEXT,
    rights_status TEXT,
    skip_reason TEXT,
    resize_path TEXT,
    staging_path TEXT,
    original_store TEXT,
    agreement_score DOUBLE PRECISION,
    approval_state TEXT
);

CREATE TABLE IF NOT EXISTS filename_history (
    id BIGSERIAL PRIMARY KEY,
    temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    original_name TEXT NOT NULL,
    stored_name TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS duplicates (
    id BIGSERIAL PRIMARY KEY,
    sha256 TEXT NOT NULL,
    canonical_temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    original_name TEXT NOT NULL,
    original_path TEXT,
    subfolder TEXT,
    sequence INTEGER,
    duplicate_path TEXT
);

CREATE TABLE IF NOT EXISTS keyframes (
    keyframe_id TEXT PRIMARY KEY,
    parent_temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    label TEXT NOT NULL,
    timestamp_s DOUBLE PRECISION NOT NULL,
    path TEXT,
    stem TEXT NOT NULL,
    extract_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_definitions (
    name TEXT PRIMARY KEY,
    locked INTEGER NOT NULL,
    source TEXT NOT NULL,
    characteristics_json TEXT NOT NULL,
    saved INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    temp_id TEXT NOT NULL,
    tag_name TEXT NOT NULL REFERENCES tag_definitions(name),
    source TEXT NOT NULL,
    PRIMARY KEY (temp_id, tag_name)
);

CREATE TABLE IF NOT EXISTS tag_overrides (
    temp_id TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    measured_value TEXT NOT NULL,
    display_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (temp_id, tag_name)
);

CREATE TABLE IF NOT EXISTS tag_proposals (
    name TEXT PRIMARY KEY,
    characteristics_json TEXT NOT NULL,
    saved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_passes (
    id BIGSERIAL PRIMARY KEY,
    target_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    stage TEXT NOT NULL,
    backend TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (target_id, scope, stage)
);

CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    confidence DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS before_after_candidates (
    candidate_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    shot_characteristic TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS before_after_members (
    candidate_id TEXT NOT NULL REFERENCES before_after_candidates(candidate_id),
    temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    captured_at TIMESTAMPTZ,
    PRIMARY KEY (candidate_id, temp_id)
);

CREATE TABLE IF NOT EXISTS similar_shot_groups (
    group_id TEXT PRIMARY KEY,
    shot_characteristic TEXT NOT NULL,
    location_id TEXT
);

CREATE TABLE IF NOT EXISTS similar_shot_members (
    group_id TEXT NOT NULL REFERENCES similar_shot_groups(group_id),
    temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    PRIMARY KEY (group_id, temp_id)
);

CREATE TABLE IF NOT EXISTS overlap_links (
    overlap_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    visit_key TEXT NOT NULL,
    status TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    mean_spacing_m DOUBLE PRECISION,
    gimbal_spread DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS overlap_members (
    overlap_id TEXT NOT NULL REFERENCES overlap_links(overlap_id),
    temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    PRIMARY KEY (overlap_id, temp_id)
);

CREATE TABLE IF NOT EXISTS model_view_sets (
    set_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_view_members (
    set_id TEXT NOT NULL REFERENCES model_view_sets(set_id),
    temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    shot_characteristic TEXT,
    PRIMARY KEY (set_id, temp_id)
);

CREATE TABLE IF NOT EXISTS asset_relations (
    id BIGSERIAL PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    UNIQUE (from_id, to_id, relation)
);

CREATE TABLE IF NOT EXISTS case_studies (
    case_id TEXT PRIMARY KEY,
    location_id TEXT,
    title TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS asset_copies (
    copy_id TEXT PRIMARY KEY,
    temp_id TEXT NOT NULL REFERENCES assets(temp_id),
    compiled_id TEXT NOT NULL,
    width INTEGER,
    path TEXT,
    object_key TEXT,
    sha256 TEXT,
    watermark_label TEXT
);

ALTER TABLE assets ADD COLUMN IF NOT EXISTS approval_state TEXT;
ALTER TABLE tag_assignments DROP CONSTRAINT IF EXISTS tag_assignments_temp_id_fkey;

CREATE INDEX IF NOT EXISTS assets_location_idx ON assets(location_id);
CREATE INDEX IF NOT EXISTS assets_compiled_idx ON assets(compiled_id);
CREATE INDEX IF NOT EXISTS assets_sha_idx ON assets(sha256);
