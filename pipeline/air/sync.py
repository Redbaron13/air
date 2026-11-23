"""Copy the SQLite journal into Postgres. Neon is a separate optional target."""

from __future__ import annotations

import json
import os
import re
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
    for table, statement in _SNAPSHOT_SQL.items():
        rows = journal.query(statement)
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
                _check_identifiers(table, columns)
                values = [_adapt(row[column]) for column in columns]
                conflict = [part.strip() for part in _pk(table).split(",")]
                assignments = [column for column in columns if column not in set(conflict)]
                statement = _upsert(table, columns, conflict, assignments)
                conn.execute(statement, values)
            counts[table] = len(rows)
        conn.commit()
    return counts


_SNAPSHOT_SQL = {
    "assets": "SELECT * FROM assets",
    "filename_history": "SELECT * FROM filename_history",
    "duplicates": "SELECT * FROM duplicates",
    "keyframes": "SELECT * FROM keyframes",
    "tag_definitions": "SELECT * FROM tag_definitions",
    "tag_assignments": "SELECT * FROM tag_assignments",
    "tag_overrides": "SELECT * FROM tag_overrides",
    "tag_proposals": "SELECT * FROM tag_proposals",
    "ai_passes": "SELECT * FROM ai_passes",
    "locations": "SELECT * FROM locations",
    "before_after_candidates": "SELECT * FROM before_after_candidates",
    "before_after_members": "SELECT * FROM before_after_members",
    "similar_shot_groups": "SELECT * FROM similar_shot_groups",
    "similar_shot_members": "SELECT * FROM similar_shot_members",
    "overlap_links": "SELECT * FROM overlap_links",
    "overlap_members": "SELECT * FROM overlap_members",
    "model_view_sets": "SELECT * FROM model_view_sets",
    "model_view_members": "SELECT * FROM model_view_members",
    "asset_copies": "SELECT * FROM asset_copies",
    "case_studies": "SELECT * FROM case_studies",
}

_COLUMN_NAME = re.compile(r"^[a-z_]+$")


def _check_identifiers(table: str, columns: list[str]) -> None:
    allowed = set(_SNAPSHOT_SQL) | {"asset_relations"}
    if table not in allowed:
        raise ValueError("unknown sync table")
    for column in columns:
        if _COLUMN_NAME.fullmatch(column) is None:
            raise ValueError("unknown sync column")


def _upsert(table: str, columns: list[str], conflict: list[str], assignments: list[str]):
    from psycopg import sql

    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) ").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(sql.Identifier(column) for column in conflict),
    )
    if not assignments:
        return statement + sql.SQL("DO NOTHING")
    updates = sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
        for column in assignments
    )
    return statement + sql.SQL("DO UPDATE SET {}").format(updates)


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
