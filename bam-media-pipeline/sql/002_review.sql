CREATE TABLE IF NOT EXISTS bam_review_state (
  asset_id varchar(64) PRIMARY KEY REFERENCES assets(asset_id),
  revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
  rights_confirmed boolean NOT NULL DEFAULT false,
  metadata_confirmed boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bam_review_events (
  id uuid PRIMARY KEY,
  asset_id varchar(64) NOT NULL REFERENCES assets(asset_id),
  action text NOT NULL,
  actor_id text NOT NULL,
  notes text NOT NULL DEFAULT '',
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bam_review_events_asset_time ON bam_review_events(asset_id,created_at DESC);
CREATE INDEX IF NOT EXISTS bam_review_events_time ON bam_review_events(created_at DESC);
CREATE TABLE IF NOT EXISTS bam_ai_provenance (
  eval_id uuid PRIMARY KEY REFERENCES ai_evaluations(eval_id),
  evidence jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bam_output_verifications (
  copy_id varchar(64) PRIMARY KEY REFERENCES asset_copies(copy_id),
  verified_sha256 char(64) NOT NULL CHECK(verified_sha256 ~ '^[a-f0-9]{64}$'),
  verified_at timestamptz NOT NULL
);
