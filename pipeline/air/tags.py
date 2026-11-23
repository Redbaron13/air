"""Preset tag catalog, user-defined tags, and locked EXIF overrides."""

from __future__ import annotations

import json
import re
from pathlib import Path

COARSE_SLUGS = {
    "real-estate",
    "construction",
    "inspections",
    "damage",
    "roof-solar",
    "mapping",
    "events",
}

PRESET_PATH = Path(__file__).resolve().parents[1] / "tags" / "presets.json"


def normalize_tag_name(name: str) -> str:
    """Keep every word. Adjust case and spacing only."""
    parts = re.split(r"\s+", (name or "").strip())
    words = [part for part in parts if part]
    if not words:
        raise ValueError("tag name is empty")
    if all(word.isupper() or word.islower() or "_" in word for word in words) and any("_" in word or word.islower() for word in words):
        return " ".join(word.strip() for word in words)
    return " ".join(word.lower() if word.isupper() else word for word in words)


def load_presets(path: Path | None = None) -> list[dict]:
    raw = json.loads((path or PRESET_PATH).read_text(encoding="utf-8"))
    tags = []
    for item in raw["tags"]:
        tags.append(
            {
                "name": item["name"],
                "locked": bool(item.get("locked")),
                "source": item.get("source") or "preset",
                "characteristics": dict(item.get("characteristics") or {}),
            }
        )
    return tags


def catalog_index(tags: list[dict]) -> dict[str, dict]:
    return {item["name"]: item for item in tags}


def select_tags(catalog: list[dict], requested: list[str]) -> tuple[list[str], list[str]]:
    """Accept catalog names. Undefined names and coarse replacements are rejected."""
    index = catalog_index(catalog)
    accepted: list[str] = []
    rejected: list[str] = []
    for name in requested:
        cleaned = name.strip()
        if cleaned in COARSE_SLUGS:
            rejected.append(cleaned)
            continue
        if cleaned in index:
            if cleaned not in accepted:
                accepted.append(cleaned)
            continue
        rejected.append(cleaned)
    return accepted, rejected


def define_tag(name: str, characteristics: dict) -> dict:
    cleaned = normalize_tag_name(name)
    if cleaned in COARSE_SLUGS or cleaned.replace(" ", "-") in COARSE_SLUGS:
        raise ValueError("a coarse service slug cannot replace a detailed tag")
    if not characteristics:
        raise ValueError("a new tag needs a characteristic definition")
    return {
        "name": cleaned,
        "locked": False,
        "source": "user",
        "characteristics": dict(characteristics),
        "saved": True,
    }


def propose_tag(name: str, characteristics: dict | None = None) -> dict:
    """A proposal stays out of the catalog until the user saves a definition."""
    return {
        "name": normalize_tag_name(name),
        "characteristics": dict(characteristics or {}),
        "saved": False,
    }


def override_locked_tag(measured: str, new_value: str, reason: str) -> dict:
    if not reason or not reason.strip():
        raise ValueError("an EXIF tag override needs a reason")
    if not new_value or not new_value.strip():
        raise ValueError("an override needs a display value")
    return {
        "measured_value": measured,
        "display_value": new_value.strip(),
        "reason": reason.strip(),
    }


def display_tag(measured: str, override: dict | None) -> str:
    if override and override.get("display_value"):
        return str(override["display_value"])
    return measured
