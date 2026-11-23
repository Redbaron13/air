"""SQLite journal. Offline stages read and write this file. Postgres is a later copy."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ASSET_COLUMNS = (
    "temp_id",
    "media_kind",
    "original_name",
    "original_path",
    "sha256",
    "byte_size",
    "subfolder",
    "sequence",
    "captured_at",
    "latitude",
    "longitude",
    "altitude_m",
    "gimbal_pitch",
    "shot_characteristic",
    "workflow_state",
    "location_id",
    "location_confidence",
    "compiled_id",
    "alt_text",
    "rights_status",
    "skip_reason",
    "resize_path",
    "staging_path",
    "original_store",
    "agreement_score",
    "approval_state",
)

ASSET_UPDATES = {
    "media_kind": "UPDATE assets SET media_kind = ? WHERE temp_id = ?",
    "original_name": "UPDATE assets SET original_name = ? WHERE temp_id = ?",
    "original_path": "UPDATE assets SET original_path = ? WHERE temp_id = ?",
    "sha256": "UPDATE assets SET sha256 = ? WHERE temp_id = ?",
    "byte_size": "UPDATE assets SET byte_size = ? WHERE temp_id = ?",
    "subfolder": "UPDATE assets SET subfolder = ? WHERE temp_id = ?",
    "sequence": "UPDATE assets SET sequence = ? WHERE temp_id = ?",
    "captured_at": "UPDATE assets SET captured_at = ? WHERE temp_id = ?",
    "latitude": "UPDATE assets SET latitude = ? WHERE temp_id = ?",
    "longitude": "UPDATE assets SET longitude = ? WHERE temp_id = ?",
    "altitude_m": "UPDATE assets SET altitude_m = ? WHERE temp_id = ?",
    "gimbal_pitch": "UPDATE assets SET gimbal_pitch = ? WHERE temp_id = ?",
    "shot_characteristic": "UPDATE assets SET shot_characteristic = ? WHERE temp_id = ?",
    "workflow_state": "UPDATE assets SET workflow_state = ? WHERE temp_id = ?",
    "location_id": "UPDATE assets SET location_id = ? WHERE temp_id = ?",
    "location_confidence": "UPDATE assets SET location_confidence = ? WHERE temp_id = ?",
    "compiled_id": "UPDATE assets SET compiled_id = ? WHERE temp_id = ?",
    "alt_text": "UPDATE assets SET alt_text = ? WHERE temp_id = ?",
    "rights_status": "UPDATE assets SET rights_status = ? WHERE temp_id = ?",
    "skip_reason": "UPDATE assets SET skip_reason = ? WHERE temp_id = ?",
    "resize_path": "UPDATE assets SET resize_path = ? WHERE temp_id = ?",
    "staging_path": "UPDATE assets SET staging_path = ? WHERE temp_id = ?",
    "original_store": "UPDATE assets SET original_store = ? WHERE temp_id = ?",
    "agreement_score": "UPDATE assets SET agreement_score = ? WHERE temp_id = ?",
    "approval_state": "UPDATE assets SET approval_state = ? WHERE temp_id = ?",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    temp_id TEXT PRIMARY KEY,
    media_kind TEXT NOT NULL,
    original_name TEXT NOT NULL,
    original_path TEXT,
    sha256 TEXT,
    byte_size INTEGER,
    subfolder TEXT,
    sequence INTEGER,
    captured_at TEXT,
    latitude REAL,
    longitude REAL,
    altitude_m REAL,
    gimbal_pitch REAL,
    shot_characteristic TEXT,
    workflow_state TEXT NOT NULL DEFAULT 'INGESTED',
    location_id TEXT,
    location_confidence REAL,
    compiled_id TEXT,
    alt_text TEXT,
    rights_status TEXT,
    skip_reason TEXT,
    resize_path TEXT,
    staging_path TEXT,
    original_store TEXT,
    agreement_score REAL,
    approval_state TEXT,
    UNIQUE(sha256)
);

CREATE TABLE IF NOT EXISTS filename_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    temp_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL,
    canonical_temp_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    original_path TEXT,
    subfolder TEXT,
    sequence INTEGER,
    duplicate_path TEXT
);

CREATE TABLE IF NOT EXISTS keyframes (
    keyframe_id TEXT PRIMARY KEY,
    parent_temp_id TEXT NOT NULL,
    label TEXT NOT NULL,
    timestamp_s REAL NOT NULL,
    path TEXT,
    stem TEXT NOT NULL,
    extract_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unknown_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subfolders (
    path TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tag_definitions (
    name TEXT PRIMARY KEY,
    locked INTEGER NOT NULL,
    source TEXT NOT NULL,
    characteristics_json TEXT NOT NULL,
    saved INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    temp_id TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (temp_id, tag_name)
);

CREATE TABLE IF NOT EXISTS tag_overrides (
    temp_id TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    measured_value TEXT NOT NULL,
    display_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (temp_id, tag_name)
);

CREATE TABLE IF NOT EXISTS tag_proposals (
    name TEXT PRIMARY KEY,
    characteristics_json TEXT NOT NULL,
    saved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_passes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    stage TEXT NOT NULL,
    backend TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(target_id, scope, stage)
);

CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    latitude REAL,
    longitude REAL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS before_after_candidates (
    candidate_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL,
    shot_characteristic TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS before_after_members (
    candidate_id TEXT NOT NULL,
    temp_id TEXT NOT NULL,
    captured_at TEXT,
    PRIMARY KEY (candidate_id, temp_id)
);

CREATE TABLE IF NOT EXISTS similar_shot_groups (
    group_id TEXT PRIMARY KEY,
    shot_characteristic TEXT NOT NULL,
    location_id TEXT
);

CREATE TABLE IF NOT EXISTS similar_shot_members (
    group_id TEXT NOT NULL,
    temp_id TEXT NOT NULL,
    PRIMARY KEY (group_id, temp_id)
);

CREATE TABLE IF NOT EXISTS overlap_links (
    overlap_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL,
    visit_key TEXT NOT NULL,
    status TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    mean_spacing_m REAL,
    gimbal_spread REAL
);

CREATE TABLE IF NOT EXISTS overlap_members (
    overlap_id TEXT NOT NULL,
    temp_id TEXT NOT NULL,
    PRIMARY KEY (overlap_id, temp_id)
);

CREATE TABLE IF NOT EXISTS model_view_sets (
    set_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_view_members (
    set_id TEXT NOT NULL,
    temp_id TEXT NOT NULL,
    shot_characteristic TEXT,
    PRIMARY KEY (set_id, temp_id)
);

CREATE TABLE IF NOT EXISTS asset_copies (
    copy_id TEXT PRIMARY KEY,
    temp_id TEXT NOT NULL,
    compiled_id TEXT NOT NULL,
    width INTEGER,
    path TEXT,
    object_key TEXT,
    sha256 TEXT,
    watermark_label TEXT
);

CREATE TABLE IF NOT EXISTS case_studies (
    case_id TEXT PRIMARY KEY,
    location_id TEXT,
    title TEXT,
    notes TEXT
);
"""


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(assets)")}
        if "approval_state" not in columns:
            self.conn.execute("ALTER TABLE assets ADD COLUMN approval_state TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def taken_uniques(self) -> set[str]:
        rows = self.query("SELECT temp_id FROM assets")
        found = set()
        for row in rows:
            temp_id = row["temp_id"]
            if len(temp_id) >= 5:
                found.add(temp_id[1:5])
        return found

    def subfolder_code(self, folder: str, allocator) -> str:
        row = self.conn.execute("SELECT code FROM subfolders WHERE path = ?", (folder,)).fetchone()
        if row:
            return row["code"]
        used = {item["code"] for item in self.query("SELECT code FROM subfolders")}
        code = allocator(used)
        self.execute("INSERT INTO subfolders(path, code) VALUES (?, ?)", (folder, code))
        return code

    def next_sequence(self, subfolder: str) -> int:
        asset_max = self.conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM assets WHERE subfolder = ?",
            (subfolder,),
        ).fetchone()[0]
        dup_max = self.conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM duplicates WHERE subfolder = ?",
            (subfolder,),
        ).fetchone()[0]
        return int(max(asset_max, dup_max)) + 1

    def asset_by_sha(self, sha256: str):
        return self.conn.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()

    def insert_asset(self, row: dict) -> None:
        unknown = set(row) - set(ASSET_COLUMNS)
        if unknown:
            raise ValueError(f"unknown asset column: {sorted(unknown)[0]}")
        self.execute(
            """
            INSERT INTO assets(
                temp_id, media_kind, original_name, original_path, sha256, byte_size,
                subfolder, sequence, captured_at, latitude, longitude, altitude_m,
                gimbal_pitch, shot_characteristic, workflow_state, location_id,
                location_confidence, compiled_id, alt_text, rights_status, skip_reason,
                resize_path, staging_path, original_store, agreement_score, approval_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(row.get(name) if name != "workflow_state" else row.get(name) or "INGESTED" for name in ASSET_COLUMNS),
        )

    def update_asset(self, temp_id: str, **fields) -> None:
        for name, value in fields.items():
            statement = ASSET_UPDATES.get(name)
            if statement is None:
                raise ValueError(f"unknown asset column: {name}")
            self.execute(statement, (value, temp_id))

    def add_history(self, temp_id: str, original_name: str, stored_name: str | None) -> None:
        self.execute(
            "INSERT INTO filename_history(temp_id, original_name, stored_name) VALUES (?, ?, ?)",
            (temp_id, original_name, stored_name),
        )

    def add_duplicate(self, row: dict) -> None:
        self.execute(
            """
            INSERT INTO duplicates(
                sha256, canonical_temp_id, original_name, original_path, subfolder, sequence, duplicate_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("sha256"),
                row.get("canonical_temp_id"),
                row.get("original_name"),
                row.get("original_path"),
                row.get("subfolder"),
                row.get("sequence"),
                row.get("duplicate_path"),
            ),
        )

    def add_keyframe(self, row: dict) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO keyframes(
                keyframe_id, parent_temp_id, label, timestamp_s, path, stem, extract_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("keyframe_id"),
                row.get("parent_temp_id"),
                row.get("label"),
                row.get("timestamp_s"),
                row.get("path"),
                row.get("stem"),
                row.get("extract_status"),
            ),
        )

    def upsert_tag_definition(self, tag: dict) -> None:
        self.execute(
            """
            INSERT INTO tag_definitions(name, locked, source, characteristics_json, saved)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                locked = excluded.locked,
                source = excluded.source,
                characteristics_json = excluded.characteristics_json,
                saved = excluded.saved
            """,
            (
                tag["name"],
                1 if tag.get("locked") else 0,
                tag.get("source") or "preset",
                json.dumps(tag.get("characteristics") or {}),
                1 if tag.get("saved", True) else 0,
            ),
        )

    def tag_catalog(self) -> list[dict]:
        rows = self.query("SELECT * FROM tag_definitions WHERE saved = 1")
        return [
            {
                "name": row["name"],
                "locked": bool(row["locked"]),
                "source": row["source"],
                "characteristics": json.loads(row["characteristics_json"]),
            }
            for row in rows
        ]

    def assign_tag(self, temp_id: str, tag_name: str, source: str) -> None:
        self.execute(
            """
            INSERT INTO tag_assignments(temp_id, tag_name, source) VALUES (?, ?, ?)
            ON CONFLICT(temp_id, tag_name) DO UPDATE SET source = excluded.source
            """,
            (temp_id, tag_name, source),
        )

    def tags_for(self, temp_id: str) -> list[str]:
        rows = self.query(
            "SELECT tag_name FROM tag_assignments WHERE temp_id = ? ORDER BY tag_name",
            (temp_id,),
        )
        return [row["tag_name"] for row in rows]

    def set_override(self, temp_id: str, tag_name: str, measured: str, display: str, reason: str) -> None:
        self.execute(
            """
            INSERT INTO tag_overrides(temp_id, tag_name, measured_value, display_value, reason)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(temp_id, tag_name) DO UPDATE SET
                measured_value = excluded.measured_value,
                display_value = excluded.display_value,
                reason = excluded.reason
            """,
            (temp_id, tag_name, measured, display, reason),
        )

    def override_for(self, temp_id: str, tag_name: str):
        return self.conn.execute(
            "SELECT * FROM tag_overrides WHERE temp_id = ? AND tag_name = ?",
            (temp_id, tag_name),
        ).fetchone()

    def add_proposal(self, name: str, characteristics: dict) -> None:
        self.execute(
            """
            INSERT INTO tag_proposals(name, characteristics_json, saved) VALUES (?, ?, 0)
            ON CONFLICT(name) DO UPDATE SET characteristics_json = excluded.characteristics_json, saved = 0
            """,
            (name, json.dumps(characteristics)),
        )

    def add_pass(self, target_id: str, scope: str, stage: str, backend: str, payload: dict) -> None:
        self.execute(
            """
            INSERT INTO ai_passes(target_id, scope, stage, backend, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(target_id, scope, stage) DO UPDATE SET
                backend = excluded.backend,
                payload_json = excluded.payload_json
            """,
            (target_id, scope, stage, backend, json.dumps(payload)),
        )

    def passes_for(self, target_id: str, scope: str | None = None) -> list[sqlite3.Row]:
        if scope is None:
            return self.query("SELECT * FROM ai_passes WHERE target_id = ? ORDER BY id", (target_id,))
        return self.query(
            "SELECT * FROM ai_passes WHERE target_id = ? AND scope = ? ORDER BY id",
            (target_id, scope),
        )

    def assets(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind is None:
            return self.query("SELECT * FROM assets ORDER BY temp_id")
        return self.query("SELECT * FROM assets WHERE media_kind = ? ORDER BY temp_id", (kind,))

    def keyframes(self, parent_temp_id: str | None = None) -> list[sqlite3.Row]:
        if parent_temp_id is None:
            return self.query("SELECT * FROM keyframes ORDER BY keyframe_id")
        return self.query(
            "SELECT * FROM keyframes WHERE parent_temp_id = ? ORDER BY timestamp_s",
            (parent_temp_id,),
        )
