import os
import hashlib
import json
import subprocess
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

DB_DSN = os.getenv("DATABASE_URL", "postgresql://bam_admin:bam_secure_super_password_2026@postgres:5432/bam_media_factory")

def get_db():
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)

SUPPORTED_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic", ".dng",
    ".mp4", ".mov", ".glb", ".gltf", ".obj", ".usdz"
)

def iter_ingest_targets(path: str) -> list[str]:
    if os.path.isfile(path):
        return [path] if path.lower().endswith(SUPPORTED_EXTENSIONS) else []

    if not os.path.isdir(path):
        return []

    targets = []
    for root, _, files in os.walk(path):
        for name in sorted(files):
            if name.lower().endswith(SUPPORTED_EXTENSIONS):
                targets.append(os.path.join(root, name))
    return targets

def build_asset_identifiers(file_hash: str, batch_id: str = "A01", now: datetime | None = None) -> tuple[str, str]:
    timestamp = now or datetime.now()
    unique_stem = file_hash[:8].upper()
    date_stem = timestamp.strftime("%d%m%y")
    asset_id = f"{date_stem}-001BAMPHO{batch_id}-{unique_stem}"
    return unique_stem, asset_id

def compute_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def extract_exif(filepath: str) -> dict:
    cmd = ["exiftool", "-j", "-DateTimeOriginal", "-GPSLatitude", "-GPSLongitude", "-GPSAltitude", "-Model", "-FocalLength", "-GimbalPitchDegree", filepath]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(res.stdout)[0]
        return {
            "captured_at": data.get("DateTimeOriginal", datetime.utcnow().isoformat()),
            "lat": float(data.get("GPSLatitude", 0.0)),
            "lon": float(data.get("GPSLongitude", 0.0)),
            "alt": float(data.get("GPSAltitude", 0.0)),
            "sensor": data.get("Model", "Unknown"),
            "focal_length": data.get("FocalLength", "Unknown"),
            "gimbal_pitch": float(data.get("GimbalPitchDegree", 0.0)),
        }
    except Exception:
        return {}

def ingest_file(filepath: str, batch_id="A01"):
    file_hash = compute_sha256(filepath)
    
    with get_db() as conn:
        with conn.cursor() as cur:
            # Deduplication Check
            cur.execute("SELECT asset_id FROM assets WHERE original_sha256 = %s", (file_hash,))
            if cur.fetchone():
                print(f"[DEDUP] Duplicate discarded: {filepath}")
                return None

            meta = extract_exif(filepath)
            unique_stem, asset_id = build_asset_identifiers(file_hash, batch_id=batch_id)

            cur.execute("""
                INSERT INTO assets (
                    asset_id, unique_stem, original_filename, source_path,
                    original_sha256, media_kind, captured_at, sensor_model, 
                    focal_length, gimbal_pitch, latitude, longitude, altitude_meters,
                    workflow_state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'INGESTED')
            """, (
                asset_id, unique_stem, os.path.basename(filepath), filepath,
                file_hash, "still", meta.get("captured_at"), meta.get("sensor"),
                meta.get("focal_length"), meta.get("gimbal_pitch"),
                meta.get("lat"), meta.get("lon"), meta.get("alt")
            ))
            conn.commit()
            print(f"[INGESTED] Asset staged: {asset_id}")
            return asset_id

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        targets = iter_ingest_targets(sys.argv[1])
        if not targets:
            print(f"[SKIP] No supported media files found for: {sys.argv[1]}")
        for target in targets:
            ingest_file(target)