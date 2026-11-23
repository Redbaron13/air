"""Copy the SQLite journal into Postgres. Neon is a separate optional target."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path

TABLES = (
    "assets",
    "filename_history",
    "duplicates",
    "keyframes",
    "tag_definitions",
    "tag_assignments",
    "tag_overrides",
    "tag_proposals",
    "ai_passes",
    "locations",
    "before_after_candidates",
    "before_after_members",
    "similar_shot_groups",
    "similar_shot_members",
    "overlap_links",
    "overlap_members",
    "model_view_sets",
    "model_view_members",
    "asset_copies",
    "case_studies",
)


def snapshot(journal: Journal) -> dict[str, list[dict]]:
    payload = {}
    for table in TABLES:
        rows = journal.query(f"SELECT * FROM {table}")
        payload[table] = [{key: row[key] for key in row.keys()} for row in rows]
    relations = []
    for row in payload["duplicates"]:
        relations.append(
            {
                "from_id": row["canonical_temp_id"],
                "to_id": f"dup:{row['id']}",
                "relation": "duplicate",
            }
        )
    for row in payload["keyframes"]:
        relations.append(
            {
                "from_id": row["parent_temp_id"],
                "to_id": row["keyframe_id"],
                "relation": "keyframe",
            }
        )
    payload["asset_relations"] = relations
    return payload


def database_url(neon: bool) -> str | None:
    if neon:
        return os.environ.get("NEON_DATABASE_URL") or None
    return os.environ.get("DATABASE_URL") or None


def sync_library(root: Path, neon: bool = False) -> dict:
    journal = Journal(journal_path(root))
    try:
        payload = snapshot(journal)
    finally:
        journal.close()
    url = database_url(neon)
    target = "neon" if neon else "postgres"
    if not url:
        return {"status": "skipped", "target": target, "reason": f"{target} url is not set", "tables": {name: len(rows) for name, rows in payload.items()}}
    applied = _apply(url, payload)
    return {"status": "applied", "target": target, "tables": applied}


def _apply(url: str, payload: dict[str, list[dict]]) -> dict[str, int]:
    import psycopg

    schema = Path(__file__).resolve().parents[1] / "sql" / "init_schema.sql"
    counts = {}
    with psycopg.connect(url) as conn:
        conn.execute(schema.read_text(encoding="utf-8"))
        for table, rows in payload.items():
            if not rows:
                counts[table] = 0
                continue
            for row in rows:
                columns = list(row)
                values = [_adapt(row[column]) for column in columns]
                marks = ", ".join(["%s"] * len(columns))
                conflict = _pk(table)
                conflict_cols = {part.strip() for part in conflict.split(",")}
                assignments = ", ".join(
                    f"{column} = EXCLUDED.{column}" for column in columns if column not in conflict_cols
                )
                action = f"DO UPDATE SET {assignments}" if assignments else "DO NOTHING"
                sql = (
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks}) "
                    f"ON CONFLICT ({conflict}) {action}"
                )
                conn.execute(sql, values)
            counts[table] = len(rows)
        conn.commit()
    return counts


def _pk(table: str) -> str:
    keys = {
        "filename_history": "id",
        "duplicates": "id",
        "ai_passes": "target_id, scope, stage",
        "tag_assignments": "temp_id, tag_name",
        "tag_overrides": "temp_id, tag_name",
        "before_after_members": "candidate_id, temp_id",
        "similar_shot_members": "group_id, temp_id",
        "overlap_members": "overlap_id, temp_id",
        "model_view_members": "set_id, temp_id",
        "asset_relations": "from_id, to_id, relation",
    }
    if table in keys:
        return keys[table]
    if table == "assets":
        return "temp_id"
    if table.endswith("s") and table not in {"assets", "duplicates", "keyframes", "locations", "tag_definitions", "tag_proposals", "case_studies", "asset_copies", "overlap_links", "model_view_sets", "similar_shot_groups", "before_after_candidates"}:
        return "id"
    primary = {
        "keyframes": "keyframe_id",
        "locations": "location_id",
        "tag_definitions": "name",
        "tag_proposals": "name",
        "before_after_candidates": "candidate_id",
        "similar_shot_groups": "group_id",
        "overlap_links": "overlap_id",
        "model_view_sets": "set_id",
        "case_studies": "case_id",
        "asset_copies": "copy_id",
    }
    return primary[table]


def _adapt(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value
