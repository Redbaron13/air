"""Watermarked public derivatives. Originals stay untouched so a later watermark can be rebuilt."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pipeline.air.identity import SIZE_TIERS, build_copy_id, parse_compiled_id
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path

PUBLIC_WIDTHS = (2560, 1600, 800, 320)


def _open(root: Path) -> Journal:
    return Journal(journal_path(root))


def derive(root: Path, label: str | None = None) -> dict:
    text = label or os.environ.get("AIR_WATERMARK_TEXT") or "Baron Aerial Media"
    journal = _open(root)
    written = 0
    skipped = 0
    try:
        for asset in journal.assets():
            if not asset["compiled_id"]:
                continue
            if asset["media_kind"] != "still":
                journal.update_asset(asset["temp_id"], skip_reason="no public still derivative for this media kind")
                skipped += 1
                continue
            source = Path(asset["original_store"]) if asset["original_store"] else None
            if source is None or not source.exists():
                skipped += 1
                continue
            written += _render(journal, root, asset, source, text)
        return {"derivatives": written, "skipped": skipped, "watermark": text}
    finally:
        journal.close()


def rewatermark(root: Path, label: str, compiled_id: str | None = None) -> dict:
    journal = _open(root)
    changed = 0
    try:
        assets = journal.assets()
        if compiled_id:
            assets = [row for row in assets if row["compiled_id"] == compiled_id]
        else:
            assets = [row for row in assets if row["compiled_id"]]
        for asset in assets:
            before = {
                "compiled_id": asset["compiled_id"],
                "temp_id": asset["temp_id"],
                "location_id": asset["location_id"],
                "tags": journal.tags_for(asset["temp_id"]),
            }
            source = Path(asset["original_store"]) if asset["original_store"] else None
            if source is None or not source.exists() or asset["media_kind"] != "still":
                continue
            _render(journal, root, asset, source, label)
            after = journal.conn.execute("SELECT * FROM assets WHERE temp_id = ?", (asset["temp_id"],)).fetchone()
            if after["compiled_id"] != before["compiled_id"] or after["temp_id"] != before["temp_id"]:
                raise RuntimeError("rewatermark changed an identifier")
            if after["location_id"] != before["location_id"]:
                raise RuntimeError("rewatermark changed the location id")
            if journal.tags_for(asset["temp_id"]) != before["tags"]:
                raise RuntimeError("rewatermark changed tags")
            changed += 1
        return {"rewatermarked": changed, "watermark": label}
    finally:
        journal.close()


def _render(journal: Journal, root: Path, asset, source: Path, label: str) -> int:
    parsed = parse_compiled_id(asset["compiled_id"])
    folder = root / "Output" / asset["compiled_id"]
    folder.mkdir(parents=True, exist_ok=True)
    count = 0
    for width in PUBLIC_WIDTHS:
        copy_id = build_copy_id(width, parsed["unique"])
        dest = folder / f"{SIZE_TIERS[width]}.webp"
        _write_watermark(source, dest, width, label)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        key = f"bam/web/stills/{asset['compiled_id']}/{SIZE_TIERS[width]}.webp"
        journal.execute(
            """
            INSERT INTO asset_copies(copy_id, temp_id, compiled_id, width, path, object_key, sha256, watermark_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(copy_id) DO UPDATE SET
                path = excluded.path,
                object_key = excluded.object_key,
                sha256 = excluded.sha256,
                watermark_label = excluded.watermark_label
            """,
            (copy_id, asset["temp_id"], asset["compiled_id"], width, str(dest), key, digest, label),
        )
        count += 1
    journal.update_asset(asset["temp_id"], workflow_state="DERIVED")
    return count


def _write_watermark(source: Path, dest: Path, width: int, label: str) -> None:
    from PIL import Image, ImageDraw, ImageStat

    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((width, width))
        draw = ImageDraw.Draw(image)
        w, h = image.size
        x0, y0 = max(0, int(w * 0.02)), max(0, int(h * 0.72))
        x1, y1 = min(w, x0 + 8 + len(label) * 4), h
        sample = image.crop((x0, y0, max(x0 + 1, min(w, int(w * 0.45))), max(y0 + 1, h - 1))).convert("L")
        luminance = ImageStat.Stat(sample).mean[0]
        fill = (255, 255, 255) if luminance < 115 else (32, 32, 32)
        draw.rectangle((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1 - 1)), outline=fill)
        draw.text((x0 + 2, y0 + 2), label, fill=fill)
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, "WEBP", quality=80)
