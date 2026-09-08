CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE shoots (
    shoot_id VARCHAR(64) PRIMARY KEY, 
    project_name VARCHAR(255) NOT NULL,
    centroid_lat DOUBLE PRECISION NOT NULL,
    centroid_lon DOUBLE PRECISION NOT NULL,
    radius_meters DOUBLE PRECISION DEFAULT 300.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE assets (
    asset_id VARCHAR(64) PRIMARY KEY, 
    unique_stem VARCHAR(8) NOT NULL UNIQUE,
    original_filename VARCHAR(255) NOT NULL,
    source_path TEXT NOT NULL,
    original_sha256 CHAR(64) NOT NULL UNIQUE,
    media_kind VARCHAR(16) NOT NULL,
    shoot_id VARCHAR(64),
    captured_at TIMESTAMP,
    sensor_model VARCHAR(128),
    focal_length VARCHAR(32),
    gimbal_pitch DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    altitude_meters DOUBLE PRECISION,
    describes TEXT,
    alt TEXT,
    service_tags JSONB DEFAULT '[]'::jsonb,
    workflow_state VARCHAR(32) DEFAULT 'INGESTED', 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ai_evaluations (
    eval_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id VARCHAR(64) REFERENCES assets(asset_id) ON DELETE CASCADE,
    screener_output JSONB,
    verifier_output JSONB,
    judge_output JSONB,
    deep_output JSONB,
    agreement_score DOUBLE PRECISION,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE asset_copies (
    copy_id VARCHAR(64) PRIMARY KEY, 
    parent_asset_id VARCHAR(64) REFERENCES assets(asset_id) ON DELETE CASCADE,
    size_tier VARCHAR(8) NOT NULL,  
    version_code VARCHAR(8) NOT NULL, 
    format VARCHAR(16) NOT NULL,    
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    byte_size BIGINT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    r2_storage_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE human_corrections (
    correction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_id VARCHAR(64),
    prior_ai_output JSONB,
    human_approved_output JSONB,
    correction_notes TEXT
);