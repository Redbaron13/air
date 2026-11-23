"""Website manifest. Detailed tags stay. Public files do not include GPS or a guessed city."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path
from pipeline.air.tags import display_tag

PUBLISHABLE = {"APPROVED", "AUTO_ACCEPTED"}
FORBIDDEN = {"latitude", "longitude", "gps", "altitude_m", "gimbal_pitch", "city", "address"}


def build_manifest(root: Path) -> dict:
    journal = Journal(journal_path(root))
    try:
        assets = []
        for asset in journal.assets():
            if asset["approval_state"] not in PUBLISHABLE:
                continue
            if not asset["alt_text"]:
                continue
            copies = journal.query("SELECT * FROM asset_copies WHERE temp_id = ?", (asset["temp_id"],))
            if asset["media_kind"] == "still" and not any(str(copy["path"] or "").endswith(".webp") for copy in copies):
                continue
            tags = []
            for name in journal.tags_for(asset["temp_id"]):
                override = journal.override_for(asset["temp_id"], name)
                tags.append(display_tag(name, dict(override) if override else None))
            record = {
                "temp_id": asset["temp_id"],
                "compiled_id": asset["compiled_id"],
                "location_id": asset["location_id"],
                "media_kind": asset["media_kind"],
                "alt": asset["alt_text"],
                "tags": tags,
                "copies": [
                    {
                        "copy_id": copy["copy_id"],
                        "key": copy["object_key"],
                        "width": copy["width"],
                        "sha256": copy["sha256"],
                    }
                    for copy in copies
                ],
            }
            leaked = FORBIDDEN.intersection(record)
            if leaked:
                raise RuntimeError(f"manifest record includes private fields: {sorted(leaked)}")
            assets.append(record)
        document = {"assets": assets}
        dest = root / "exports" / "website.manifest.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return {"path": str(dest), "assets": len(assets)}
    finally:
        journal.close()
