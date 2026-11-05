import os
import hashlib
import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

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
    logger.info("Computing SHA-256; path=%s", filepath)
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    logger.info("SHA-256 complete; path=%s sha256=%s", filepath, digest)
    return digest

def extract_exif(filepath: str) -> dict:
    cmd = ["exiftool", "-j", "-DateTimeOriginal", "-GPSLatitude", "-GPSLongitude", "-GPSAltitude", "-Model", "-FocalLength", "-GimbalPitchDegree", filepath]
    try:
        logger.info("Extracting EXIF metadata; path=%s", filepath)
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        data = json.loads(res.stdout)[0]
        metadata = {
            "captured_at": data.get("DateTimeOriginal", datetime.utcnow().isoformat()),
            "lat": float(data.get("GPSLatitude", 0.0) or 0.0),
            "lon": float(data.get("GPSLongitude", 0.0) or 0.0),
            "alt": float(data.get("GPSAltitude", 0.0) or 0.0),
            "sensor": data.get("Model", "Unknown"),
            "focal_length": data.get("FocalLength", "Unknown"),
            "gimbal_pitch": float(data.get("GimbalPitchDegree", 0.0) or 0.0),
        }
        logger.info("EXIF extraction complete; path=%s sensor=%s", filepath, metadata["sensor"])
        return metadata
    except Exception as exc:
        logger.warning("EXIF extraction failed; path=%s error=%s", filepath, exc)
        return {}

def kind_for(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in {".mp4", ".mov", ".m4v", ".avi", ".mkv"}:
        return "video"
    if ext in {".glb", ".gltf", ".obj", ".usdz"}:
        return "3d_model"
    return "still"

def ingest_file(filepath: str, batch_id="A01"):
    logger.info("Ingest started; requested_path=%s batch_id=%s", filepath, batch_id)
    requested_path = Path(filepath)
    if requested_path.is_symlink():
        raise ValueError("Symbolic links cannot be ingested")
    resolved_path = requested_path.resolve(strict=True)
    if not resolved_path.is_file() or not resolved_path.is_relative_to(ACTIVE_DIR):
        raise ValueError("Files must be regular files inside the staging directory")

    filepath = str(resolved_path)
    logger.info("Validated staged file; path=%s", filepath)
    file_hash = compute_sha256(filepath)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_id FROM assets WHERE original_sha256 = %s", (file_hash,))
            if cur.fetchone():
                logger.info("Duplicate discarded; path=%s sha256=%s", filepath, file_hash)
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
                file_hash, kind_for(filepath), meta.get("captured_at"), meta.get("sensor"),
                meta.get("focal_length"), meta.get("gimbal_pitch"),
                meta.get("lat"), meta.get("lon"), meta.get("alt")
            ))
            conn.commit()
            logger.info("Asset ingested; asset_id=%s path=%s", asset_id, filepath)
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
