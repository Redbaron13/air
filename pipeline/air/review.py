"""Tag catalog edits and locked EXIF overrides. The Node console remains the review UI."""

from __future__ import annotations

from pathlib import Path

from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path
from pipeline.air.prep import seed_presets
from pipeline.air.tags import define_tag, override_locked_tag


def add_tag(root: Path, name: str, characteristics: dict) -> dict:
    journal = Journal(journal_path(root))
    try:
        if not journal.tag_catalog():
            seed_presets(journal)
        tag = define_tag(name, characteristics)
        journal.upsert_tag_definition(tag)
        journal.execute("UPDATE tag_proposals SET saved = 1 WHERE name = ?", (tag["name"],))
        return tag
    finally:
        journal.close()


def override_tag(root: Path, temp_id: str, tag_name: str, display_value: str, reason: str) -> dict:
    journal = Journal(journal_path(root))
    try:
        catalog = {item["name"]: item for item in journal.tag_catalog()}
        if tag_name not in catalog or not catalog[tag_name].get("locked"):
            raise ValueError("only a locked EXIF tag can be overridden")
        current = journal.tags_for(temp_id)
        measured = tag_name if tag_name in current else tag_name
        record = override_locked_tag(measured, display_value, reason)
        journal.set_override(temp_id, tag_name, record["measured_value"], record["display_value"], record["reason"])
        return record
    finally:
        journal.close()
