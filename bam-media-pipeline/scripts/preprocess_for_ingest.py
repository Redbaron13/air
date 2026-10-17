#!/usr/bin/env python3
"""Shrink home-library photos/videos into an Azure-ready staging folder.

Run this on the home computer. It does not call Azure and does not need keys.

  python3 preprocess_for_ingest.py --source ~/Pictures/site-keepers --out ~/bam-staging

Output:
  preview/     JPEG long-edge 1600 (stills + video keyframes)
  manifest.jsonl
  rsync.cmd    copy-paste or run rsync_to_azure.sh

Upload only preview/ + manifest.jsonl. Keep originals on the NAS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

STILL_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".dng"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required. Install it, then rerun.")
    return ffmpeg


def iter_media(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in STILL_EXT | VIDEO_EXT:
            yield path


def probe_duration(src: Path, ffmpeg: str) -> float:
    ffprobe = shutil.which("ffprobe") or ffmpeg
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(src),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip() or "0")
    except ValueError:
        return 0.0


def make_preview_still(ffmpeg: str, src: Path, dest: Path, max_edge: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale='min({max_edge},iw)':-2"
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-vf", vf, "-q:v", "3", str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(result.stderr[-400:])


def make_video_keyframes(
    ffmpeg: str, src: Path, dest_dir: Path, max_edge: int
) -> List[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(src, ffmpeg)
    frames: List[Path] = []
    for idx, frac in enumerate((0.1, 0.5, 0.9)):
        t = max(0.1, duration * frac) if duration > 0 else 1.0 + idx
        dest = dest_dir / f"{src.stem}_k{idx:02d}.jpg"
        cmd = [
            ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", str(src),
            "-vframes", "1",
            "-vf", f"scale='min({max_edge},iw)':-2",
            str(dest),
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        if dest.is_file() and dest.stat().st_size > 0:
            frames.append(dest)
    return frames


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preprocess media for BAM Azure ingest")
    parser.add_argument("--source", required=True, help="folder of originals on this PC")
    parser.add_argument("--out", required=True, help="staging folder to upload")
    parser.add_argument("--max-edge", type=int, default=1600)
    parser.add_argument("--host", default="bam-azure", help="Tailscale hostname of the Azure box")
    parser.add_argument("--remote-dir", default="~/bam-storage/active")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    preview = out / "preview"
    preview.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_ffmpeg()

    manifest_path = out / "manifest.jsonl"
    rows: List[Dict[str, Any]] = []
    seen = set()

    for src in iter_media(source):
        digest = sha256_file(src)
        if digest in seen:
            print(f"[DEDUP] skip {src}")
            continue
        seen.add(digest)
        rel = src.relative_to(source).as_posix()
        ext = src.suffix.lower()
        try:
            if ext in STILL_EXT:
                dest = preview / f"{digest[:12]}_{src.stem}.jpg"
                make_preview_still(ffmpeg, src, dest, args.max_edge)
                rows.append(
                    {
                        "kind": "still",
                        "original_name": src.name,
                        "original_relpath": rel,
                        "original_sha256": digest,
                        "preview": dest.relative_to(out).as_posix(),
                        "byte_size": src.stat().st_size,
                    }
                )
                print(f"[STILL] {rel} -> {dest.name}")
            else:
                dest_dir = preview / f"{digest[:12]}_{src.stem}"
                frames = make_video_keyframes(ffmpeg, src, dest_dir, args.max_edge)
                if not frames:
                    print(f"[WARN] no keyframes {rel}")
                    continue
                rows.append(
                    {
                        "kind": "video",
                        "original_name": src.name,
                        "original_relpath": rel,
                        "original_sha256": digest,
                        "previews": [p.relative_to(out).as_posix() for p in frames],
                        "byte_size": src.stat().st_size,
                    }
                )
                print(f"[VIDEO] {rel} -> {len(frames)} frames")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] {rel}: {exc}")

    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    rsync = (
        f"rsync -avP --partial {out}/preview/ {args.host}:{args.remote_dir}/preview/\n"
        f"rsync -avP {manifest_path} {args.host}:{args.remote_dir}/manifest.jsonl\n"
    )
    (out / "rsync.cmd").write_text(rsync, encoding="utf-8")
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "items": len(rows),
        "stills": sum(1 for r in rows if r["kind"] == "still"),
        "videos": sum(1 for r in rows if r["kind"] == "video"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {manifest_path}")
    print("Upload preview/ and manifest.jsonl only. Originals stay here.")
    print(rsync)
    return 0


if __name__ == "__main__":
    sys.exit(main())
