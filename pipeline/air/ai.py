"""Per-file vision passes, then a combined pass for a video or a model-view set."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pipeline.air.backends import complete_json, resolve_backend
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path
from pipeline.air.prep import seed_presets
from pipeline.air.shots import altitude_band, drone_angle
from pipeline.air.tags import propose_tag, select_tags

AGREEMENT_ACCEPT = 0.85


def _open(root: Path) -> Journal:
    journal = Journal(journal_path(root))
    if not journal.tag_catalog():
        seed_presets(journal)
    return journal


def exif(root: Path) -> dict:
    journal = _open(root)
    updated = 0
    try:
        targets = []
        for asset in journal.assets():
            if asset["staging_path"]:
                targets.append((asset["temp_id"], asset["staging_path"], "asset"))
        for frame in journal.keyframes():
            if frame["path"]:
                targets.append((frame["keyframe_id"], frame["path"], "keyframe"))
        for target_id, path, kind in targets:
            measured = read_exif(Path(path))
            angle = drone_angle(measured.get("gimbal_pitch"))
            band = altitude_band(measured.get("altitude_m"))
            if kind == "asset":
                journal.update_asset(
                    target_id,
                    latitude=measured.get("latitude"),
                    longitude=measured.get("longitude"),
                    altitude_m=measured.get("altitude_m"),
                    gimbal_pitch=measured.get("gimbal_pitch"),
                    shot_characteristic=angle,
                    captured_at=measured.get("captured_at") or _asset_captured(journal, target_id),
                    workflow_state="EXIF_READY",
                )
            _assign_locked(journal, target_id, angle)
            _assign_locked(journal, target_id, _altitude_tag(band))
            journal.add_pass(target_id, kind, "exif", "exiftool", measured | {"shot_characteristic": angle, "altitude_band": band})
            updated += 1
    finally:
        journal.close()
    return {"exif": updated}


def _asset_captured(journal: Journal, temp_id: str):
    row = journal.conn.execute("SELECT captured_at FROM assets WHERE temp_id = ?", (temp_id,)).fetchone()
    return row["captured_at"] if row else None


def _altitude_tag(band: str | None) -> str | None:
    return {"low": "low altitude", "medium": "medium altitude", "high": "high altitude"}.get(band or "")


def _assign_locked(journal: Journal, temp_id: str, tag_name: str | None) -> None:
    if not tag_name:
        return
    journal.assign_tag(temp_id, tag_name, "exif")
    existing = journal.override_for(temp_id, tag_name)
    if existing:
        journal.set_override(
            temp_id,
            tag_name,
            tag_name,
            existing["display_value"],
            existing["reason"],
        )


def read_exif(path: Path) -> dict:
    """Read numeric EXIF. Missing GPS stays null. The model is not asked for these values."""
    empty = {"latitude": None, "longitude": None, "altitude_m": None, "gimbal_pitch": None, "captured_at": None}
    try:
        result = subprocess.run(
            ["exiftool", "-json", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return empty | {"status": "exiftool_unavailable"}
    if result.returncode != 0 or not result.stdout.strip():
        return empty | {"status": "exiftool_empty"}
    try:
        payload = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return empty | {"status": "exiftool_unparsed"}
    latitude = _float_or_none(payload.get("GPSLatitude"))
    longitude = _float_or_none(payload.get("GPSLongitude"))
    if latitude == 0 and longitude == 0:
        latitude = None
        longitude = None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": _float_or_none(payload.get("RelativeAltitude") or payload.get("GPSAltitude")),
        "gimbal_pitch": _float_or_none(payload.get("GimbalPitchDegree")),
        "captured_at": _normalize_exif_time(payload.get("DateTimeOriginal") or payload.get("CreateDate")),
        "status": "read",
    }


def _normalize_exif_time(value):
    if not isinstance(value, str) or len(value) < 19:
        return None
    text = value.strip()
    if text[4] == ":" and text[7] == ":":
        text = f"{text[:4]}-{text[5:7]}-{text[8:]}"
    return text.replace(" ", "T", 1)[:19]


def _float_or_none(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def describe(root: Path) -> dict:
    return _per_item(root, "describe")


def reason(root: Path) -> dict:
    return _per_item(root, "reason")


def validate(root: Path) -> dict:
    return _per_item(root, "validate")


def _targets(journal: Journal) -> list[tuple[str, str, str | None, str]]:
    found = []
    for asset in journal.assets():
        if asset["media_kind"] == "still":
            found.append((asset["temp_id"], "still", asset["staging_path"], asset["media_kind"]))
        elif asset["media_kind"] == "model3d":
            found.append((asset["temp_id"], "view", asset["staging_path"], asset["media_kind"]))
    for frame in journal.keyframes():
        found.append((frame["keyframe_id"], "frame", frame["path"], "video_frame"))
    return found


def _per_item(root: Path, stage: str) -> dict:
    backend = resolve_backend()
    journal = _open(root)
    count = 0
    try:
        catalog = journal.tag_catalog()
        for target_id, scope, path, kind in _targets(journal):
            if stage == "describe":
                payload = _describe_one(backend, kind, path)
            elif stage == "reason":
                payload = _reason_one(journal, backend, target_id, scope, catalog)
            else:
                payload = _validate_one(journal, backend, target_id, scope)
            journal.add_pass(target_id, scope, stage, backend, payload)
            if scope in {"still", "view"} and stage == "validate":
                _apply_validation(journal, target_id, payload)
            count += 1
    finally:
        journal.close()
    return {stage: count, "backend": backend}


def _describe_one(backend: str, kind: str, path: str | None) -> dict:
    if backend == "skip":
        return {
            "description": None,
            "alt_text": None,
            "note": "Vision backend skipped. No description was generated.",
            "media_kind": kind,
        }
    prompt = (
        "Describe this single aerial media file. Return JSON with description and alt_text. "
        "Do not infer GPS, capture time, or gimbal angle. "
        f"Media kind: {kind}."
    )
    result = complete_json(backend, prompt, path)
    return {
        "description": result.get("description"),
        "alt_text": result.get("alt_text"),
        "media_kind": kind,
        "raw": result,
    }


def _reason_one(journal: Journal, backend: str, target_id: str, scope: str, catalog: list[dict]) -> dict:
    locked = [name for name in journal.tags_for(target_id) if _is_locked(catalog, name)]
    if backend == "skip":
        return {"tags": locked, "proposed": [], "note": "Vision backend skipped. No content tags were added."}
    names = [item["name"] for item in catalog if not item.get("locked")]
    prompt = (
        "Choose tags from this catalog only. Return JSON with keys tags (array of catalog names) "
        "and proposed (array of new multiword names). Do not shorten a tag into a coarse service slug. "
        f"Catalog: {names}"
    )
    result = complete_json(backend, prompt, None)
    requested = [str(item) for item in result.get("tags") or []]
    accepted, rejected = select_tags(catalog, requested)
    for name in accepted:
        if not _is_locked(catalog, name):
            journal.assign_tag(target_id, name, "model")
    proposed = []
    for name in list(result.get("proposed") or []) + rejected:
        if name in accepted:
            continue
        proposal = propose_tag(str(name), {"visible_subject": str(name)})
        journal.add_proposal(proposal["name"], proposal["characteristics"])
        proposed.append(proposal["name"])
    return {"tags": journal.tags_for(target_id), "proposed": proposed, "scope": scope}


def _is_locked(catalog: list[dict], name: str) -> bool:
    for item in catalog:
        if item["name"] == name:
            return bool(item.get("locked"))
    return False


def _validate_one(journal: Journal, backend: str, target_id: str, scope: str) -> dict:
    described = journal.passes_for(target_id, scope)
    prior = next((row for row in described if row["stage"] == "describe"), None)
    if backend == "skip":
        return {"agreement_score": 0.0, "alt_text": None, "note": "Vision backend skipped."}
    prompt = "Compare your own visual read with this JSON and return agreement_score from 0 to 1 and alt_text."
    if prior:
        prompt += " " + prior["payload_json"]
    result = complete_json(backend, prompt, None)
    try:
        score = float(result.get("agreement_score", 0))
    except (TypeError, ValueError):
        score = 0.0
    return {"agreement_score": max(0.0, min(1.0, score)), "alt_text": result.get("alt_text"), "scope": scope}


def _apply_validation(journal: Journal, temp_id: str, payload: dict) -> None:
    score = float(payload.get("agreement_score") or 0)
    alt = payload.get("alt_text")
    journal.update_asset(temp_id, agreement_score=score, alt_text=alt, workflow_state=_state_for(journal, temp_id, score, alt))


def _state_for(journal: Journal, temp_id: str, score: float, alt: str | None) -> str:
    asset = journal.conn.execute("SELECT * FROM assets WHERE temp_id = ?", (temp_id,)).fetchone()
    if asset is None:
        return "PENDING_HUMAN"
    tags = journal.tags_for(temp_id)
    content_tags = [tag for tag in tags if tag not in {"nadir", "high_oblique", "medium_oblique", "low_oblique", "horizon", "low altitude", "medium altitude", "high altitude"}]
    ready = score >= AGREEMENT_ACCEPT and bool(alt) and len(content_tags) >= 10 and asset["rights_status"] == "cleared"
    if asset["media_kind"] == "video":
        frames = journal.keyframes(temp_id)
        combined = journal.passes_for(temp_id, "video")
        if len(frames) < 1 or not any(row["stage"] == "synthesize" for row in combined):
            ready = False
    if not ready:
        return "PENDING_HUMAN"
    return "AUTO_ACCEPTED"


def synthesize_video(root: Path) -> dict:
    backend = resolve_backend()
    journal = _open(root)
    written = 0
    try:
        for asset in journal.assets("video"):
            frames = journal.keyframes(asset["temp_id"])
            if not frames:
                continue
            parts = []
            missing = False
            for frame in frames:
                described = [row for row in journal.passes_for(frame["keyframe_id"], "frame") if row["stage"] == "describe"]
                if not described:
                    missing = True
                    break
                parts.append({"keyframe_id": frame["keyframe_id"], "label": frame["label"], "payload": json.loads(described[-1]["payload_json"])})
            if missing:
                continue
            payload = {
                "parts": parts,
                "summary": _combined_summary(backend, "video", parts),
                "part_count": len(parts),
            }
            journal.add_pass(asset["temp_id"], "video", "synthesize", backend, payload)
            journal.update_asset(asset["temp_id"], workflow_state="PENDING_HUMAN")
            written += 1
    finally:
        journal.close()
    return {"video_sets": written, "backend": backend}


def synthesize_model(root: Path) -> dict:
    backend = resolve_backend()
    journal = _open(root)
    written = 0
    try:
        sets = journal.query("SELECT * FROM model_view_sets")
        for item in sets:
            members = journal.query(
                "SELECT * FROM model_view_members WHERE set_id = ?",
                (item["set_id"],),
            )
            parts = []
            missing = False
            for member in members:
                described = [row for row in journal.passes_for(member["temp_id"], "still") if row["stage"] == "describe"]
                view_passes = [row for row in journal.passes_for(member["temp_id"], "view") if row["stage"] == "describe"]
                chosen = described or view_passes
                if not chosen:
                    missing = True
                    break
                parts.append(
                    {
                        "temp_id": member["temp_id"],
                        "shot_characteristic": member["shot_characteristic"],
                        "payload": json.loads(chosen[-1]["payload_json"]),
                    }
                )
            if missing or len(parts) < 2:
                continue
            payload = {
                "parts": parts,
                "summary": _combined_summary(backend, "model", parts),
                "part_count": len(parts),
            }
            journal.add_pass(item["set_id"], "model_set", "synthesize", backend, payload)
            written += 1
    finally:
        journal.close()
    return {"model_sets": written, "backend": backend}


def _combined_summary(backend: str, kind: str, parts: list[dict]) -> str:
    if backend == "skip":
        return f"Combined {len(parts)} {kind} parts. Vision backend skipped. Individual records were kept."
    result = complete_json(
        backend,
        f"Read these {kind} parts together and return JSON with summary. Parts: {json.dumps(parts)[:4000]}",
        None,
    )
    return str(result.get("summary") or result.get("note") or "Combined read recorded.")
