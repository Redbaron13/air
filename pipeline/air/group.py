"""Location membership and the candidate sets drawn from it."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pipeline.air.identity import (
    build_compiled_id,
    location_id_for,
    overlap_id_for,
    parse_temp_id,
    unique4,
)
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path

LOCATION_RADIUS_M = 150.0
HOLD_DIAMETER_M = 400.0
VISIT_GAP_S = 600.0
OVERLAP_SPACING_M = 30.0
OVERLAP_MIN_MEMBERS = 3


def _open(root: Path) -> Journal:
    return Journal(journal_path(root))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def group_location(root: Path) -> dict:
    journal = _open(root)
    try:
        assets = [
            row
            for row in journal.assets()
            if row["latitude"] is not None and row["longitude"] is not None
        ]
        parent = {row["temp_id"]: row["temp_id"] for row in assets}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(left: str, right: str) -> None:
            ra, rb = find(left), find(right)
            if ra != rb:
                parent[rb] = ra

        for index, left in enumerate(assets):
            for right in assets[index + 1 :]:
                distance = haversine_m(left["latitude"], left["longitude"], right["latitude"], right["longitude"])
                if distance <= LOCATION_RADIUS_M:
                    union(left["temp_id"], right["temp_id"])
        clusters = defaultdict(list)
        for row in assets:
            clusters[find(row["temp_id"])].append(row)
        assigned = 0
        held = 0
        for members in clusters.values():
            diameter = 0.0
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    diameter = max(
                        diameter,
                        haversine_m(left["latitude"], left["longitude"], right["latitude"], right["longitude"]),
                    )
            if diameter > HOLD_DIAMETER_M:
                for member in members:
                    journal.update_asset(member["temp_id"], location_id=None, location_confidence=0.5)
                held += len(members)
                continue
            lat = sum(member["latitude"] for member in members) / len(members)
            lon = sum(member["longitude"] for member in members) / len(members)
            location_id = location_id_for(lat, lon)
            journal.execute(
                """
                INSERT INTO locations(location_id, latitude, longitude, confidence, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    confidence = excluded.confidence,
                    status = excluded.status
                """,
                (location_id, lat, lon, 1.0, "assigned"),
            )
            for member in members:
                journal.update_asset(member["temp_id"], location_id=location_id, location_confidence=1.0)
            assigned += len(members)
        return {"assigned": assigned, "held": held}
    finally:
        journal.close()


def group_shots(root: Path) -> dict:
    journal = _open(root)
    try:
        groups = 0
        for asset in journal.assets():
            characteristic = asset["shot_characteristic"]
            if not characteristic:
                continue
            for group_id, location_id in (
                (f"SH-{characteristic}", None),
                (f"SH-{characteristic}-{asset['location_id']}", asset["location_id"]),
            ):
                if location_id is None and asset["location_id"] is None:
                    continue
                journal.execute(
                    """
                    INSERT INTO similar_shot_groups(group_id, shot_characteristic, location_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(group_id) DO NOTHING
                    """,
                    (group_id, characteristic, location_id),
                )
                journal.execute(
                    """
                    INSERT INTO similar_shot_members(group_id, temp_id) VALUES (?, ?)
                    ON CONFLICT(group_id, temp_id) DO NOTHING
                    """,
                    (group_id, asset["temp_id"]),
                )
                groups += 1
        return {"memberships": groups}
    finally:
        journal.close()


def group_before_after(root: Path) -> dict:
    journal = _open(root)
    try:
        buckets = defaultdict(list)
        for asset in journal.assets():
            if asset["location_id"] and asset["shot_characteristic"] and asset["captured_at"]:
                buckets[(asset["location_id"], asset["shot_characteristic"])].append(asset)
        created = 0
        for (location_id, characteristic), members in buckets.items():
            dates = {member["captured_at"][:10] for member in members}
            if len(dates) < 2:
                continue
            candidate_id = "BA" + unique4(f"{location_id}|{characteristic}")
            journal.execute(
                """
                INSERT INTO before_after_candidates(candidate_id, location_id, shot_characteristic, status)
                VALUES (?, ?, ?, 'candidate')
                ON CONFLICT(candidate_id) DO UPDATE SET status = 'candidate'
                """,
                (candidate_id, location_id, characteristic),
            )
            for member in members:
                journal.execute(
                    """
                    INSERT INTO before_after_members(candidate_id, temp_id, captured_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(candidate_id, temp_id) DO UPDATE SET captured_at = excluded.captured_at
                    """,
                    (candidate_id, member["temp_id"], member["captured_at"]),
                )
            created += 1
        return {"candidates": created}
    finally:
        journal.close()


def group_overlap(root: Path) -> dict:
    journal = _open(root)
    try:
        by_location = defaultdict(list)
        for asset in journal.assets():
            if asset["location_id"] and asset["captured_at"]:
                by_location[asset["location_id"]].append(asset)
        written = 0
        for location_id, members in by_location.items():
            ordered = sorted(members, key=lambda row: row["captured_at"])
            visits = _split_visits(ordered)
            for visit in visits:
                visit_key = visit[0]["captured_at"]
                overlap_id = overlap_id_for(location_id, visit_key)
                spacing = _mean_spacing(visit)
                pitches = [row["gimbal_pitch"] for row in visit if row["gimbal_pitch"] is not None]
                spread = (max(pitches) - min(pitches)) if len(pitches) >= 2 else None
                status = "insufficient"
                if len(visit) >= OVERLAP_MIN_MEMBERS and spacing is not None and spacing <= OVERLAP_SPACING_M:
                    status = "photogrammetry"
                journal.execute(
                    """
                    INSERT INTO overlap_links(
                        overlap_id, location_id, visit_key, status, member_count, mean_spacing_m, gimbal_spread
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(overlap_id) DO UPDATE SET
                        status = excluded.status,
                        member_count = excluded.member_count,
                        mean_spacing_m = excluded.mean_spacing_m,
                        gimbal_spread = excluded.gimbal_spread
                    """,
                    (overlap_id, location_id, visit_key, status, len(visit), spacing, spread),
                )
                for member in visit:
                    journal.execute(
                        """
                        INSERT INTO overlap_members(overlap_id, temp_id) VALUES (?, ?)
                        ON CONFLICT(overlap_id, temp_id) DO NOTHING
                        """,
                        (overlap_id, member["temp_id"]),
                    )
                written += 1
        return {"links": written}
    finally:
        journal.close()


def group_model(root: Path) -> dict:
    journal = _open(root)
    try:
        by_location = defaultdict(list)
        for asset in journal.assets("still"):
            if asset["location_id"] and asset["shot_characteristic"]:
                by_location[asset["location_id"]].append(asset)
        created = 0
        for location_id, members in by_location.items():
            characteristics = {member["shot_characteristic"] for member in members}
            if len(characteristics) < 2:
                continue
            set_id = "MV" + unique4(location_id + "|views")
            journal.execute(
                """
                INSERT INTO model_view_sets(set_id, location_id, status)
                VALUES (?, ?, 'various_views')
                ON CONFLICT(set_id) DO UPDATE SET status = 'various_views'
                """,
                (set_id, location_id),
            )
            for member in members:
                journal.execute(
                    """
                    INSERT INTO model_view_members(set_id, temp_id, shot_characteristic)
                    VALUES (?, ?, ?)
                    ON CONFLICT(set_id, temp_id) DO UPDATE SET shot_characteristic = excluded.shot_characteristic
                    """,
                    (set_id, member["temp_id"], member["shot_characteristic"]),
                )
            created += 1
        return {"sets": created}
    finally:
        journal.close()


def assign_ids(root: Path, batch: str = "A01") -> dict:
    journal = _open(root)
    try:
        ready = [
            row
            for row in journal.assets()
            if row["location_id"] and (row["location_confidence"] or 0) >= 0.7 and not row["compiled_id"]
        ]
        counters = defaultdict(int)
        written = 0
        for asset in sorted(ready, key=lambda row: (row["captured_at"] or "", row["temp_id"])):
            parsed = parse_temp_id(asset["temp_id"])
            when = parsed["captured_at"]
            key = (when.date().isoformat(), asset["location_id"])
            counters[key] += 1
            compiled = build_compiled_id(
                when,
                counters[key],
                asset["location_id"],
                asset["media_kind"] if asset["media_kind"] in {"still", "video", "model3d", "other"} else "other",
                batch,
                parsed["unique"],
            )
            journal.update_asset(asset["temp_id"], compiled_id=compiled, workflow_state="IDENTIFIED")
            renamed = _rename_placeholder(root, asset, compiled)
            if renamed:
                journal.add_history(asset["temp_id"], asset["original_name"], renamed.name)
            written += 1
        return {"compiled": written}
    finally:
        journal.close()


def _rename_placeholder(root: Path, asset, compiled: str) -> Path | None:
    folder = root / "Renamed"
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(asset["original_store"]) if asset["original_store"] else None
    if source is None or not source.exists():
        return None
    dest = folder / f"{compiled}{source.suffix.lower()}"
    if not dest.exists():
        dest.write_bytes(source.read_bytes())
    return dest


def _split_visits(members: list) -> list[list]:
    visits = []
    current = []
    previous = None
    for member in members:
        stamp = _parse_time(member["captured_at"])
        if previous is not None and stamp is not None and (stamp - previous).total_seconds() > VISIT_GAP_S:
            visits.append(current)
            current = []
        current.append(member)
        previous = stamp or previous
    if current:
        visits.append(current)
    return visits


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _mean_spacing(members: list) -> float | None:
    if len(members) < 2:
        return None
    nearest = []
    for index, left in enumerate(members):
        if left["latitude"] is None or left["longitude"] is None:
            continue
        distances = []
        for other_index, right in enumerate(members):
            if index == other_index or right["latitude"] is None:
                continue
            distances.append(haversine_m(left["latitude"], left["longitude"], right["latitude"], right["longitude"]))
        if distances:
            nearest.append(min(distances))
    if not nearest:
        return None
    return sum(nearest) / len(nearest)
