import os
import hashlib
import json
import subprocess
from datetime import datetime
import sys
import psycopg2
from psycopg2.extras import RealDictCursor

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_SCRIPTS_DIR), _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(int(), _p)

from server import exif_meta  # noqa: E402

DB_DSN = os.environ["DATABASE_URL"]

MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff", ".heic", ".dng",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".glb", ".gltf", ".obj", ".usdz",
}

def get_db():
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)

SUPPORTED_EXTENSIONS = tuple(sorted(MEDIA_EXT))

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
    """Capture/camera/drone metadata plus the raw EXIF/XMP dump. {} when nothing could be read."""
    try:
        dump = exif_meta.read_exif_dump(filepath)
        if not dump:
            return {}
        meta = exif_meta.build_asset_meta(dump)
        return {k: v for k, v in meta.items() if k != "is_drone"} | {"is_drone": meta.get("is_drone")}
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
            unique_stem, asset_id = build_asset_identifiers(file_hash, batch_id=batch_id)

            cur.execute("""
                INSERT INTO assets (
                    asset_id, unique_stem, original_filename, source_path,
                    original_sha256, media_kind, captured_at, sensor_model,
                    focal_length, gimbal_pitch, latitude, longitude, altitude_meters,
                    workflow_state,
                    raw_exif, camera_make, lens_model, iso, shutter_speed, aperture, camera_serial,
                    gimbal_yaw, flight_yaw, flight_roll, rel_altitude, abs_altitude,
                    capture_utc_offset, captured_at_local
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'INGESTED',
                          %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                asset_id, unique_stem, os.path.basename(filepath), filepath,
                file_hash, kind_for(filepath), meta.get("captured_at"), meta.get("sensor"),
                meta.get("focal_length"), meta.get("gimbal_pitch"),
                meta.get("lat"), meta.get("lon"), meta.get("alt"),
                json.dumps(meta.get("raw_exif")) if meta.get("raw_exif") else None,
                meta.get("make"), meta.get("lens_model"), meta.get("iso"), meta.get("shutter_speed"),
                meta.get("aperture"), meta.get("camera_serial"),
                meta.get("gimbal_yaw"), meta.get("flight_yaw"), meta.get("flight_roll"),
                meta.get("rel_altitude"), meta.get("abs_altitude"),
                meta.get("capture_utc_offset"), meta.get("captured_at_local"),
            ))
            conn.commit()
            print(f"[INGESTED] Asset staged: {asset_id}")
            return asset_id

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: ingest_worker.py <file-or-folder> [more paths...]")
    for path in sys.argv[1:]:
        targets = iter_ingest_targets(path)
        if not targets:
            print(f"[SKIP] No supported media files found for: {path}")
        for target in targets:
            ingest_file(target)
