"""Offline prep. Walk input/, write temp ids, and keep original bytes."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from pipeline.air.identity import build_keyframe_id, build_temp_id, parse_temp_id, unique4
from pipeline.air.journal import Journal
from pipeline.air.paths import ensure_folders, journal_path
from pipeline.air.tags import load_presets

STILL_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
MODEL_EXT = {".obj", ".stl", ".glb", ".gltf", ".fbx", ".usdz", ".ply"}
OTHER_EXT = {".las", ".laz", ".e57", ".xyz", ".pnts"}
OTHER_NAME_MARKERS = ("ortho", "panorama", "pano", "pointcloud", "point_cloud")

KEYFRAME_PLAN = (("V5", 15.0), ("V3", 30.0), ("V4", 45.0), ("V1", 60.0))
SUB_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def init_library(root: Path) -> Journal:
    ensure_folders(root)
    journal = Journal(journal_path(root))
    seed_presets(journal)
    return journal


def seed_presets(journal: Journal) -> None:
    for tag in load_presets():
        journal.upsert_tag_definition({**tag, "saved": True})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def media_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    stem = path.stem.lower()
    if any(marker in stem for marker in OTHER_NAME_MARKERS):
        return "other"
    if suffix in OTHER_EXT:
        return "other"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in MODEL_EXT:
        return "model3d"
    if suffix in STILL_EXT:
        return "still"
    return None


def next_subfolder_code(used: set[str]) -> str:
    number = 0
    while number < len(SUB_ALPHABET) ** 2:
        code = SUB_ALPHABET[number // 36] + SUB_ALPHABET[number % 36]
        if code not in used:
            return code
        number += 1
    raise RuntimeError("subfolder codes are exhausted")


def planned_keyframes(duration: float) -> list[tuple[str, float]]:
    points = []
    for label, timestamp in KEYFRAME_PLAN:
        if label == "V1" and duration <= 60:
            continue
        if timestamp < duration:
            points.append((label, timestamp))
    if duration > 10:
        points.append(("V0", round(duration - 10.0, 3)))
    return points


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def extract_frame(source: Path, timestamp: float, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(dest),
            ],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and dest.exists()


def _resize_still(source: Path, dest: Path) -> str:
    try:
        from PIL import Image
    except ImportError:
        shutil.copy2(source, dest)
        return "copied"
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((1600, 1600))
        image.save(dest, "JPEG", quality=85)
    return "resized"


def _stage_name(root: Path, temp_id: str, suffix: str) -> Path:
    existing = [path for path in (root / "staging").iterdir() if path.is_file()]
    return root / "staging" / f"{len(existing) + 1:04d}_{temp_id}{suffix}"


def _store_original(root: Path, source: Path, digest: str) -> Path:
    dest = root / "originals" / f"{digest}{source.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(source, dest)
    return dest


def scan(root: Path) -> dict:
    journal = init_library(root)
    input_root = root / "input"
    counts = {"assets": 0, "duplicates": 0, "keyframes": 0, "unknown": 0}
    files = sorted(path for path in input_root.rglob("*") if path.is_file())
    for source in files:
        kind = media_kind(source)
        if kind is None:
            journal.execute(
                "INSERT INTO unknown_files(path, reason) VALUES (?, ?)",
                (str(source), "unrecognized media type"),
            )
            counts["unknown"] += 1
            continue
        digest = sha256_file(source)
        parent = source.parent.relative_to(input_root).as_posix()
        if parent == ".":
            parent = ""
        subfolder = journal.subfolder_code(parent, next_subfolder_code)
        sequence = journal.next_sequence(subfolder)
        existing = journal.asset_by_sha(digest)
        if existing is not None:
            dest = root / "duplicate" / source.name
            if dest.exists():
                dest = root / "duplicate" / f"{sequence}_{source.name}"
            shutil.move(str(source), dest)
            journal.add_duplicate(
                {
                    "sha256": digest,
                    "canonical_temp_id": existing["temp_id"],
                    "original_name": source.name,
                    "original_path": str(source),
                    "subfolder": subfolder,
                    "sequence": sequence,
                    "duplicate_path": str(dest),
                }
            )
            counts["duplicates"] += 1
            continue
        captured = datetime.fromtimestamp(source.stat().st_mtime).replace(microsecond=0)
        token = unique4(digest, journal.taken_uniques())
        temp_id = build_temp_id(kind, token, captured, subfolder, sequence)
        original_store = _store_original(root, source, digest)
        resize_path = None
        if kind == "still":
            resize_path = root / "resize" / f"{temp_id}.jpg"
            _resize_still(source, resize_path)
        staged = _stage_name(root, temp_id, source.suffix.lower())
        shutil.copy2(source, staged)
        skip_reason = None if kind == "video" else "keyframes apply to video only"
        journal.insert_asset(
            {
                "temp_id": temp_id,
                "media_kind": kind,
                "original_name": source.name,
                "original_path": str(source),
                "sha256": digest,
                "byte_size": source.stat().st_size,
                "subfolder": subfolder,
                "sequence": sequence,
                "captured_at": captured.isoformat(timespec="seconds"),
                "workflow_state": "INGESTED",
                "skip_reason": skip_reason,
                "resize_path": str(resize_path) if resize_path else None,
                "staging_path": str(staged),
                "original_store": str(original_store),
            }
        )
        journal.add_history(temp_id, source.name, staged.name)
        counts["assets"] += 1
        if kind == "video":
            counts["keyframes"] += _write_keyframes(journal, root, source, temp_id)
    summary = {**counts, "journal": str(journal.path)}
    journal.close()
    return summary


def _write_keyframes(journal: Journal, root: Path, source: Path, temp_id: str) -> int:
    parsed = parse_temp_id(temp_id)
    duration = probe_duration(source)
    if duration is None:
        journal.update_asset(temp_id, skip_reason="video duration unavailable")
        return 0
    written = 0
    for index, (label, timestamp) in enumerate(planned_keyframes(duration), start=1):
        keyframe_id = build_keyframe_id(parsed["unique"], index)
        dest = root / "staging" / f"{keyframe_id}.png"
        ok = extract_frame(source, timestamp, dest)
        journal.add_keyframe(
            {
                "keyframe_id": keyframe_id,
                "parent_temp_id": temp_id,
                "label": label,
                "timestamp_s": timestamp,
                "path": str(dest) if ok else None,
                "stem": parsed["unique"][:2],
                "extract_status": "extracted" if ok else "skipped",
            }
        )
        written += 1
    return written
