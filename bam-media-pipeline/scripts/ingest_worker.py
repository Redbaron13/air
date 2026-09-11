import os
import hashlib
import json
import subprocess
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

DB_DSN = os.getenv("DATABASE_URL", "postgresql://bam_admin:bam_secure_super_password_2026@postgres:5432/bam_media_factory")

MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".dng",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".glb", ".gltf", ".obj", ".usdz",
}

def get_db():
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)

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
            "lat": float(data.get("GPSLatitude", 0.0) or 0.0),
            "lon": float(data.get("GPSLongitude", 0.0) or 0.0),
            "alt": float(data.get("GPSAltitude", 0.0) or 0.0),
            "sensor": data.get("Model", "Unknown"),
            "focal_length": data.get("FocalLength", "Unknown"),
            "gimbal_pitch": float(data.get("GimbalPitchDegree", 0.0) or 0.0),
        }
    except Exception:
        return {}

def kind_for(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in {".mp4", ".mov", ".m4v", ".avi", ".mkv"}:
        return "video"
    if ext in {".glb", ".gltf", ".obj", ".usdz"}:
        return "3d_model"
    return "still"

def ingest_file(filepath: str, batch_id="A01"):
    file_hash = compute_sha256(filepath)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_id FROM assets WHERE original_sha256 = %s", (file_hash,))
            if cur.fetchone():
                print(f"[DEDUP] Duplicate discarded: {filepath}")
                return None

            meta = extract_exif(filepath)
            unique4 = file_hash[:4].upper()
            date_stem = datetime.now().strftime("%d%m%y")
            asset_id = f"{date_stem}-001BAMPHO{batch_id}-{unique4}"

            cur.execute("""
                INSERT INTO assets (
                    asset_id, unique_stem, original_filename, source_path,
                    original_sha256, media_kind, captured_at, sensor_model,
                    focal_length, gimbal_pitch, latitude, longitude, altitude_meters,
                    workflow_state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'INGESTED')
            """, (
                asset_id, unique4, os.path.basename(filepath), filepath,
                file_hash, kind_for(filepath), meta.get("captured_at"), meta.get("sensor"),
                meta.get("focal_length"), meta.get("gimbal_pitch"),
                meta.get("lat"), meta.get("lon"), meta.get("alt")
            ))
            conn.commit()
            print(f"[INGESTED] Asset staged: {asset_id}")
            return asset_id

def iter_media(root: str):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if os.path.splitext(name)[1].lower() in MEDIA_EXT:
                yield os.path.join(dirpath, name)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: ingest_worker.py <file-or-folder> [more paths...]")
    for target in sys.argv[1:]:
        for media in iter_media(target):
            ingest_file(media)
