"""Case studies for one place over time.

A case study points at the before-and-after candidates and model-view sets
already stored for a location. The case id is derived from that location id.
Photos stay on the location.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path


def case_id_for(location_id: str) -> str:
    """Stable id for the case study of one location. A later build reuses it."""
    return f"CS-{location_id}"


def build_cases(root: Path) -> dict:
    journal = _open(root)
    try:
        locations = journal.query("SELECT * FROM locations ORDER BY location_id")
        candidates = journal.query("SELECT * FROM before_after_candidates ORDER BY candidate_id")
        candidate_members = journal.query(
            "SELECT * FROM before_after_members ORDER BY candidate_id, temp_id"
        )
        view_sets = journal.query("SELECT * FROM model_view_sets ORDER BY set_id")
        view_members = journal.query("SELECT * FROM model_view_members ORDER BY set_id, temp_id")

        members_by_candidate: dict[str, list[str]] = defaultdict(list)
        for row in candidate_members:
            members_by_candidate[row["candidate_id"]].append(row["temp_id"])
        members_by_set: dict[str, list[str]] = defaultdict(list)
        for row in view_members:
            members_by_set[row["set_id"]].append(row["temp_id"])
        candidates_by_location: dict[str, list[str]] = defaultdict(list)
        for row in candidates:
            candidates_by_location[row["location_id"]].append(row["candidate_id"])
        sets_by_location: dict[str, list[str]] = defaultdict(list)
        for row in view_sets:
            sets_by_location[row["location_id"]].append(row["set_id"])

        written = 0
        for location in locations:
            location_id = location["location_id"]
            candidate_ids = candidates_by_location.get(location_id, [])
            set_ids = sets_by_location.get(location_id, [])
            if not candidate_ids and not set_ids:
                continue
            journal.execute(
                """
                INSERT INTO case_studies(case_id, location_id, title, notes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    location_id = excluded.location_id,
                    title = excluded.title,
                    notes = excluded.notes
                """,
                (
                    case_id_for(location_id),
                    location_id,
                    f"Location {location_id}",
                    _notes(candidate_ids, set_ids, members_by_candidate, members_by_set),
                ),
            )
            written += 1
        return {"cases": written}
    finally:
        journal.close()


def _notes(
    candidate_ids: list[str],
    set_ids: list[str],
    members_by_candidate: dict[str, list[str]],
    members_by_set: dict[str, list[str]],
) -> str:
    candidate_text = ", ".join(
        f"{candidate_id} ({_member_count(len(members_by_candidate.get(candidate_id, [])))})"
        for candidate_id in candidate_ids
    ) or "none"
    set_text = ", ".join(
        f"{set_id} ({_member_count(len(members_by_set.get(set_id, [])))})" for set_id in set_ids
    ) or "none"
    return f"before-and-after candidates: {candidate_text}\nmodel-view sets: {set_text}"


def _member_count(count: int) -> str:
    noun = "member" if count == 1 else "members"
    return f"{count} {noun}"


def _open(root: Path) -> Journal:
    return Journal(journal_path(root))
