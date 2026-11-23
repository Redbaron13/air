"""Per-file vision passes, then a combined pass for a video or a model-view set."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pipeline.air.backends import complete_json, resolve_backend, sanitize_model_output
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path
from pipeline.air.prep import seed_presets
from pipeline.air.shots import SHOT_TAGS, altitude_band, drone_angle
from pipeline.air.tags import propose_tag, select_tags

AGREEMENT_ACCEPT = 0.85
SCHEMA_VERSION = "1.1.0"
ALT_TEXT_LIMIT = 125
LOCKED_TAG_NAMES = set(SHOT_TAGS) | {"low altitude", "medium altitude", "high altitude"}
_LEGACY_SHOT = {
    "nadir": "aerial_nadir",
    "high_oblique": "aerial_oblique",
    "medium_oblique": "aerial_oblique",
    "low_oblique": "aerial_low_oblique",
}
_SHOT_ALIASES = {
    "aerial_nadir": "aerial_nadir",
    "aerial_oblique": "aerial_oblique",
    "aerial_low_oblique": "aerial_low_oblique",
    "ground_level": "ground_level",
    "unknown": "unknown",
    "aerial": "aerial_oblique",
    "nadir": "aerial_nadir",
    "high_oblique": "aerial_oblique",
    "medium_oblique": "aerial_oblique",
    "low_oblique": "aerial_low_oblique",
    "horizon": "aerial_low_oblique",
}


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


def split_model_tags(catalog: list[dict], requested: list, proposed: list | None = None) -> tuple[list[str], list[str]]:
    """Catalog tags may be applied. Locked EXIF tags are not rewritten. Unknown names stay proposals."""
    accepted, rejected = select_tags(catalog, [str(item) for item in requested if str(item).strip()])
    saved = {item["name"] for item in catalog}
    assignable = [name for name in accepted if not _is_locked(catalog, name) and name not in LOCKED_TAG_NAMES]
    proposals: list[str] = []
    seen = set(assignable)
    for name in list(proposed or []) + rejected:
        if name in seen or _is_locked(catalog, name) or name in LOCKED_TAG_NAMES:
            continue
        try:
            proposal = propose_tag(str(name), {"visible_subject": str(name)})
        except ValueError:
            continue
        cleaned = proposal["name"]
        if cleaned in saved or cleaned in LOCKED_TAG_NAMES or cleaned in seen:
            continue
        seen.add(cleaned)
        proposals.append(cleaned)
    return assignable, proposals


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
                payload = _describe_one(journal, backend, target_id, scope, kind, path)
            elif stage == "reason":
                payload = _reason_one(journal, backend, target_id, scope, kind, catalog, path)
            else:
                payload = _validate_one(journal, backend, target_id, scope, kind, path)
            journal.add_pass(target_id, scope, stage, backend, payload)
            if scope in {"still", "view"} and stage == "validate":
                _apply_validation(journal, target_id, payload)
            count += 1
    finally:
        journal.close()
    return {stage: count, "backend": backend}


def _describe_one(journal: Journal, backend: str, target_id: str, scope: str, kind: str, path: str | None) -> dict:
    note = "Vision backend skipped. No description was generated."
    model = None
    description = None
    alt_text = None
    if backend != "skip":
        note = None
        prompt = (
            "Describe this single aerial media file. Return JSON with content.description, "
            "content.alt_text (125 characters or fewer), and content.tags. "
            "Do not include latitude, longitude, capture time, gimbal pitch, or a shot characteristic. "
            f"Media kind: {kind}."
        )
        model = sanitize_model_output(complete_json(backend, prompt, path))
        description = _model_text(model, "description")
        alt_text = _clip(_model_text(model, "alt_text"), ALT_TEXT_LIMIT)
    return _file_record(
        journal,
        target_id,
        scope,
        kind,
        description=description,
        alt_text=alt_text,
        note=note,
        model=model,
    )


def _reason_one(
    journal: Journal,
    backend: str,
    target_id: str,
    scope: str,
    kind: str,
    catalog: list[dict],
    path: str | None,
) -> dict:
    prior = _describe_payload(journal, target_id, scope)
    prior_data = _payload_dict(prior)
    note = "Vision backend skipped. No content tags were added."
    model = None
    proposals: list[str] = []
    if backend != "skip":
        note = None
        names = [item["name"] for item in catalog if not item.get("locked") and item["name"] not in LOCKED_TAG_NAMES]
        prompt = (
            "Choose tags for this one file from this catalog only. Return JSON with keys tags (array of catalog names) "
            "and proposed (array of new multiword names). Do not rewrite locked EXIF tags "
            "(nadir, high_oblique, medium_oblique, low_oblique, horizon, or altitude bands). "
            "Do not include GPS, capture time, or gimbal pitch. "
            "Do not shorten a tag into a coarse service slug. "
            f"Catalog: {names}. Prior description JSON: {prior or 'none'}."
        )
        model = sanitize_model_output(complete_json(backend, prompt, path))
        requested = model.get("tags")
        if not isinstance(requested, list):
            nested = model.get("content") if isinstance(model.get("content"), dict) else {}
            requested = nested.get("tags") if isinstance(nested.get("tags"), list) else []
        proposed_names = model.get("proposed") if isinstance(model.get("proposed"), list) else []
        assignable, proposals = split_model_tags(catalog, requested, proposed_names)
        for name in assignable:
            journal.assign_tag(target_id, name, "model")
        for name in proposals:
            proposal = propose_tag(name, {"visible_subject": name})
            journal.add_proposal(proposal["name"], proposal["characteristics"])
    record = _file_record(
        journal,
        target_id,
        scope,
        kind,
        description=_nested_text(prior_data, "description"),
        alt_text=_nested_text(prior_data, "alt_text"),
        note=note,
        model=None,
    )
    record["tags"] = list(record["content"]["tags"])
    record["proposed"] = proposals
    return record


def _is_locked(catalog: list[dict], name: str) -> bool:
    if name in LOCKED_TAG_NAMES:
        return True
    for item in catalog:
        if item["name"] == name:
            return bool(item.get("locked"))
    return False


def _describe_payload(journal: Journal, target_id: str, scope: str) -> str | None:
    described = [row for row in journal.passes_for(target_id, scope) if row["stage"] == "describe"]
    if not described:
        return None
    return described[-1]["payload_json"]


def _validate_one(journal: Journal, backend: str, target_id: str, scope: str, kind: str, path: str | None) -> dict:
    prior = _describe_payload(journal, target_id, scope)
    prior_data = _payload_dict(prior)
    note = "Vision backend skipped."
    score = 0.0
    alt_text = None
    reasoning = note
    if backend != "skip":
        note = None
        prompt = (
            "Look at this file again and compare that visual read with the prior description JSON. "
            "Return agreement_score from 0 to 1 and alt_text. "
            "Do not include GPS, capture time, or gimbal pitch. "
            f"Prior description JSON: {prior or 'none'}."
        )
        result = sanitize_model_output(complete_json(backend, prompt, path))
        score = _clamp_unit(result.get("agreement_score"))
        alt_text = _clip(_model_text(result, "alt_text"), ALT_TEXT_LIMIT)
        reasoning = _model_text(result, "reasoning")
    record = _file_record(
        journal,
        target_id,
        scope,
        kind,
        description=_nested_text(prior_data, "description"),
        alt_text=alt_text,
        note=note,
        model=None,
    )
    record["agreement_score"] = score
    record["alt_text"] = alt_text
    record["content"]["alt_text"] = alt_text
    record["review"] = {"agreement_score": score, "backend": backend, "reasoning": reasoning}
    return record


def _apply_validation(journal: Journal, temp_id: str, payload: dict) -> None:
    score = float(payload.get("agreement_score") or 0)
    alt = payload.get("alt_text")
    journal.update_asset(
        temp_id,
        agreement_score=score,
        alt_text=alt,
        workflow_state="VALIDATED",
        approval_state=_state_for(journal, temp_id, score, alt),
    )


def _state_for(journal: Journal, temp_id: str, score: float, alt: str | None) -> str:
    asset = journal.conn.execute("SELECT * FROM assets WHERE temp_id = ?", (temp_id,)).fetchone()
    if asset is None:
        return "PENDING_HUMAN"
    tags = journal.tags_for(temp_id)
    content_tags = [tag for tag in tags if tag not in LOCKED_TAG_NAMES]
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
                if not described or not _payload_dict(described[-1]["payload_json"]):
                    missing = True
                    break
                parts.append(
                    {
                        "keyframe_id": frame["keyframe_id"],
                        "label": frame["label"],
                        "scope": "frame",
                        "stage": "describe",
                    }
                )
            if missing:
                continue
            summary = _combined_summary(backend, "video", parts)
            payload = _combined_record(journal, asset["temp_id"], "video", "video", parts, summary, backend)
            journal.add_pass(asset["temp_id"], "video", "synthesize", backend, payload)
            journal.update_asset(asset["temp_id"], workflow_state="SYNTHESIZED")
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
                if not chosen or not _payload_dict(chosen[-1]["payload_json"]):
                    missing = True
                    break
                parts.append(
                    {
                        "temp_id": member["temp_id"],
                        "shot_characteristic": member["shot_characteristic"],
                        "scope": "still" if described else "view",
                        "stage": "describe",
                    }
                )
            if missing or len(parts) < 2:
                continue
            summary = _combined_summary(backend, "model", parts)
            payload = _combined_record(journal, item["set_id"], "model_set", "model3d", parts, summary, backend)
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
        "Read these parts together and return JSON with summary. "
        "Do not include GPS, capture time, or gimbal pitch. Do not drop the individual parts. "
        f"Kind: {kind}. Parts: {json.dumps(parts)[:4000]}",
        None,
    )
    return str(result.get("summary") or result.get("note") or "Combined read recorded.")


def _combined_record(journal: Journal, target_id: str, scope: str, media_kind: str, parts: list[dict], summary: str, backend: str) -> dict:
    record = _file_record(
        journal,
        target_id,
        scope,
        media_kind,
        description=None,
        alt_text=None,
        note=summary if backend == "skip" else None,
        model=None,
    )
    record["parts"] = parts
    record["part_count"] = len(parts)
    record["summary"] = summary
    if media_kind == "video":
        record["video"] = {
            "duration_s": None,
            "frames": [{"keyframe_id": part.get("keyframe_id"), "label": part.get("label")} for part in parts],
            "timeline": [],
            "summary": summary,
            "activity": None,
            "scene_changes": None,
            "camera_motion": None,
            "subjects": [],
        }
    else:
        record["model3d"] = _model3d_block(None)
    return record


def _file_record(
    journal: Journal,
    target_id: str,
    scope: str,
    media_kind: str,
    *,
    description: str | None,
    alt_text: str | None,
    note: str | None,
    model: dict | None,
) -> dict:
    facts = _stored_facts(journal, target_id, scope)
    content = _empty_content()
    content["description"] = description
    content["alt_text"] = _clip(alt_text, ALT_TEXT_LIMIT) if alt_text else None
    content["tags"] = _content_tags(journal, target_id)
    _fill_model_content(content, model)
    record = {
        "schema_version": SCHEMA_VERSION,
        "media_kind": media_kind,
        "identifier": None,
        "identifier_components": None,
        "capture": _capture_block(facts),
        "classification": _classification_block(facts, model),
        "address": _empty_address(),
        "flight": _empty_flight(),
        "content": content,
        "photogrammetry": _photogrammetry_block(model),
        "view": _view_block(model),
        "quality": _quality_block(model),
        "indicators": {"model_candidate": None, "progression": None, "view_group": None},
        "shoot": {"shoot_id": None, "related_shoots": []},
        "human": {"notes": None, "feedback": None, "training_candidate": None, "edited": None},
        "confidence": _confidence(model),
        "description": description,
        "alt_text": content["alt_text"],
    }
    if media_kind == "video_frame":
        record["changes_since_previous"] = _model_text(model or {}, "changes_since_previous")
    if media_kind == "model3d" and scope != "model_set":
        record["model3d"] = _model3d_block(model)
    if note:
        record["note"] = note
    return record


def _content_tags(journal: Journal, target_id: str) -> list[str]:
    return list(journal.tags_for(target_id))


def _stored_facts(journal: Journal, target_id: str, scope: str) -> dict:
    asset = None
    if scope in {"still", "view", "video"}:
        asset = journal.conn.execute("SELECT * FROM assets WHERE temp_id = ?", (target_id,)).fetchone()
    exif = _exif_payload(journal, target_id)
    latitude = _prefer(exif.get("latitude"), asset["latitude"] if asset else None)
    longitude = _prefer(exif.get("longitude"), asset["longitude"] if asset else None)
    latitude, longitude = _null_origin(latitude, longitude)
    altitude = _float_or_none(_prefer(exif.get("altitude_m"), asset["altitude_m"] if asset else None))
    pitch = _float_or_none(_prefer(exif.get("gimbal_pitch"), asset["gimbal_pitch"] if asset else None))
    captured = _prefer(exif.get("captured_at"), asset["captured_at"] if asset else None)
    if not isinstance(captured, str):
        captured = None
    angle = exif.get("shot_characteristic")
    if angle not in SHOT_TAGS and asset is not None:
        angle = asset["shot_characteristic"]
    if angle not in SHOT_TAGS:
        angle = _exif_tag(journal, target_id, set(SHOT_TAGS))
    if angle not in SHOT_TAGS:
        angle = drone_angle(pitch)
    band = exif.get("altitude_band")
    if band not in {"low", "medium", "high"}:
        band = altitude_band(altitude)
    if band not in {"low", "medium", "high"}:
        tagged = _exif_tag(journal, target_id, {"low altitude", "medium altitude", "high altitude"})
        band = {"low altitude": "low", "medium altitude": "medium", "high altitude": "high"}.get(tagged or "")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": altitude,
        "gimbal_pitch": pitch,
        "captured_at": captured,
        "shot_characteristic": angle if angle in SHOT_TAGS else None,
        "altitude_band": band if band in {"low", "medium", "high"} else None,
    }


def _exif_tag(journal: Journal, target_id: str, names: set[str]) -> str | None:
    rows = journal.query(
        "SELECT tag_name FROM tag_assignments WHERE temp_id = ? AND source = 'exif' ORDER BY tag_name",
        (target_id,),
    )
    for row in rows:
        if row["tag_name"] in names:
            return row["tag_name"]
    return None


def _exif_payload(journal: Journal, target_id: str) -> dict:
    rows = [row for row in journal.passes_for(target_id) if row["stage"] == "exif"]
    if not rows:
        return {}
    try:
        payload = json.loads(rows[-1]["payload_json"])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _prefer(primary, fallback):
    if primary is not None and primary != "":
        return primary
    return fallback


def _null_origin(latitude, longitude):
    latitude = _float_or_none(latitude)
    longitude = _float_or_none(longitude)
    if latitude == 0 and longitude == 0:
        return None, None
    return latitude, longitude


def _capture_block(facts: dict) -> dict:
    latitude = facts.get("latitude")
    longitude = facts.get("longitude")
    return {
        "captured_at": facts.get("captured_at"),
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": facts.get("altitude_m"),
        "gps_valid": latitude is not None and longitude is not None,
        "sensor_model": None,
        "focal_length_mm": None,
        "gimbal_pitch_deg": facts.get("gimbal_pitch"),
        "orientation": None,
        "heading_deg": None,
        "captured_at_local": None,
        "utc_offset": None,
        "gimbal_yaw_deg": None,
        "flight_yaw_deg": None,
        "flight_roll_deg": None,
        "rel_altitude_m": None,
        "camera_make": None,
        "lens_model": None,
        "iso": None,
        "shutter_speed": None,
        "aperture": None,
        "camera_serial": None,
    }


def _classification_block(facts: dict, model: dict | None) -> dict:
    model = model or {}
    nested = model.get("classification") if isinstance(model.get("classification"), dict) else {}
    angle = facts.get("shot_characteristic")
    altitude = facts.get("altitude_m")
    legacy = _legacy_shot(angle, altitude)
    model_shot = _model_shot_type(nested.get("shot_type") if "shot_type" in nested else model.get("shot_type"))
    matches = None
    if model_shot and legacy:
        matches = model_shot == legacy
    return {
        "media_genre": _model_genre(nested.get("media_genre") if "media_genre" in nested else model.get("media_genre")),
        "shot_type": model_shot,
        "scene_type": _model_token(nested.get("scene_type") if "scene_type" in nested else model.get("scene_type")),
        "camera_perspective": None,
        "shot_type_from_exif": legacy,
        "exif_basis": _exif_basis(angle, facts.get("gimbal_pitch"), altitude, facts.get("altitude_band")),
        "shot_type_matches_exif": matches,
        "drone_angle": angle,
        "shot_characteristic": angle,
        "facing_compass": None,
        "facing_relative": None,
        "facing_side": None,
        "facing_reference": None,
        "viewed_from": None,
        "altitude_band": facts.get("altitude_band"),
        "mapping_role": None,
    }


def _legacy_shot(angle: str | None, altitude_m: float | None) -> str | None:
    if angle == "horizon":
        if altitude_m is None:
            return None
        return "aerial_low_oblique" if altitude_m > 15 else "ground_level"
    if angle in {"high_oblique", "medium_oblique"} and altitude_m is not None and altitude_m < 15:
        return "aerial_low_oblique"
    if angle in _LEGACY_SHOT:
        return _LEGACY_SHOT[angle]
    if angle is not None:
        return None
    if altitude_m is None:
        return None
    if altitude_m > 15:
        return "unknown"
    if altitude_m <= 3:
        return "ground_level"
    return None


def _exif_basis(angle, pitch, altitude, band) -> str | None:
    if angle is None and pitch is None and altitude is None:
        return None
    details = []
    if isinstance(pitch, (int, float)):
        details.append(f"gimbal pitch {pitch:g} degrees")
    if isinstance(altitude, (int, float)):
        details.append(f"altitude {altitude:g} m")
    if band:
        details.append(f"altitude band {band}")
    measured = ", ".join(details) if details else "stored EXIF"
    if angle:
        return f"Shot characteristic {angle} comes from EXIF ({measured})."
    return "EXIF did not include a gimbal pitch, so no shot characteristic was set."


def _empty_content() -> dict:
    return {
        "primary_subjects": [],
        "objects": [],
        "structures": [],
        "materials": [],
        "dominant_colors": [],
        "visual_mood": None,
        "description": None,
        "alt_text": None,
        "tags": [],
    }


def _empty_address() -> dict:
    return {
        "display_name": None,
        "road": None,
        "city": None,
        "county": None,
        "state": None,
        "postcode": None,
        "country": None,
    }


def _empty_flight() -> dict:
    return {
        "flight_index": None,
        "seq": None,
        "total": None,
        "elapsed_s": None,
        "dt_prev_s": None,
        "dist_prev_m": None,
        "speed_mps": None,
        "heading_deg": None,
    }


def _fill_model_content(content: dict, model: dict | None) -> None:
    if not model:
        return
    nested = model.get("content") if isinstance(model.get("content"), dict) else {}
    for key in ("primary_subjects", "objects", "structures", "materials", "dominant_colors"):
        values = nested.get(key)
        if isinstance(values, list):
            content[key] = [str(item) for item in values if isinstance(item, str) and item.strip()]
    mood = nested.get("visual_mood")
    if isinstance(mood, str) and mood.strip():
        content["visual_mood"] = mood.strip()


def _photogrammetry_block(model: dict | None) -> dict:
    source = _section(model, "photogrammetry")
    return {
        "suitable_for_model": source.get("suitable_for_model") if isinstance(source.get("suitable_for_model"), bool) else None,
        "subject_coverage": _model_text(source, "subject_coverage"),
        "motion_blur": source.get("motion_blur") if isinstance(source.get("motion_blur"), bool) else None,
        "texture_quality": _model_text(source, "texture_quality"),
        "notes": _model_text(source, "notes"),
    }


def _view_block(model: dict | None) -> dict:
    source = _section(model, "view")
    objects = source.get("key_objects")
    return {
        "label": _model_text(source, "label"),
        "facing": _model_text(source, "facing"),
        "key_objects": [str(item) for item in objects if isinstance(item, str)] if isinstance(objects, list) else [],
    }


def _quality_block(model: dict | None) -> dict:
    source = _section(model, "quality")
    sharpness = source.get("sharpness")
    try:
        sharpness = float(sharpness) if sharpness is not None else None
    except (TypeError, ValueError):
        sharpness = None
    return {
        "sharpness": sharpness,
        "exposure": _model_text(source, "exposure"),
        "usable": source.get("usable") if isinstance(source.get("usable"), bool) else None,
    }


def _model3d_block(model: dict | None) -> dict:
    source = _section(model, "model3d")
    rotations = source.get("suggested_axis_rotations")
    elements = source.get("structural_and_design_elements")
    return {
        "is_correctly_oriented": source.get("is_correctly_oriented") if isinstance(source.get("is_correctly_oriented"), bool) else None,
        "suggested_axis_rotations": rotations if isinstance(rotations, list) else None,
        "orientation_notes": _model_text(source, "orientation_notes"),
        "structural_and_design_elements": elements if isinstance(elements, list) else None,
    }


def _section(model: dict | None, key: str) -> dict:
    if not model:
        return {}
    value = model.get(key)
    return value if isinstance(value, dict) else {}


def _confidence(model: dict | None):
    if not model or "confidence" not in model:
        return None
    return _clamp_unit(model.get("confidence"))


def _model_text(model: dict, key: str) -> str | None:
    if not isinstance(model, dict):
        return None
    content = model.get("content") if isinstance(model.get("content"), dict) else {}
    value = model.get(key)
    if not isinstance(value, str) or not value.strip():
        value = content.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _payload_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _nested_text(data: dict, key: str) -> str | None:
    return _model_text(data, key)


def _clip(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _clamp_unit(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, min(1.0, number))


def _model_genre(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return "other"
    text = value.strip().lower()
    if not text or any(character.isspace() for character in text) or len(text) > 40:
        return "other"
    return text


def _model_shot_type(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return "unknown"
    return _SHOT_ALIASES.get(value.strip().lower(), "unknown")


def _model_token(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return "unknown"
    text = value.strip().lower()
    if not text or len(text) > 40:
        return "unknown"
    return text
