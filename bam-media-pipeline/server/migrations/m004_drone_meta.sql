-- Drone/EXIF metadata, reverse-geocoded address and flight position.
-- Idempotent. The sync_outbox triggers from m003 serialise whole rows (to_jsonb(NEW)), so these columns flow to Neon
-- automatically; db_sync only copies columns that exist remotely, hence this file is applied to BOTH databases.
ALTER TABLE assets ADD COLUMN IF NOT EXISTS raw_exif JSONB;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS camera_make TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS lens_model TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS iso INTEGER;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS shutter_speed TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS aperture DOUBLE PRECISION;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS camera_serial TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS gimbal_yaw DOUBLE PRECISION;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS flight_yaw DOUBLE PRECISION;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS flight_roll DOUBLE PRECISION;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS rel_altitude DOUBLE PRECISION;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS abs_altitude DOUBLE PRECISION;
-- captured_at stays a naive UTC timestamp; these keep the camera's local wall time and UTC offset
ALTER TABLE assets ADD COLUMN IF NOT EXISTS capture_utc_offset TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS captured_at_local TEXT;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS address JSONB;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS flight_index INTEGER;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS flight_seq INTEGER;
ALTER TABLE assets ADD COLUMN IF NOT EXISTS flight_total INTEGER;

ALTER TABLE shoots ADD COLUMN IF NOT EXISTS address JSONB;
