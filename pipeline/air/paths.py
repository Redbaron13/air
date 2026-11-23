"""Library folders and the tempID journal path."""

from __future__ import annotations

import os
from pathlib import Path

FOLDERS = (
    "input",
    "staging",
    "duplicate",
    "resize",
    "Renamed",
    "Output",
    "Database",
    "originals",
    "exports",
)


def library_root(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("AIR_LIBRARY") or "library"
    return Path(raw).expanduser().resolve()


def journal_path(root: Path) -> Path:
    return root / "Database" / "tempID.db"


def ensure_folders(root: Path) -> None:
    for name in FOLDERS:
        (root / name).mkdir(parents=True, exist_ok=True)
