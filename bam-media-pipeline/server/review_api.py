"""Read-mostly API behind the local review console (/review.html)."""
import asyncio
import copy
import json
import logging
import mimetypes
import os
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from uuid import UUID

import httpx
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from server import media_intel, output_schema

from server.review_logic import (
    backend_of, build_groups, derive_group_key, explicit_group_id, nearby_assets,
    stage_progress, summarize, validate_asset, has_gps,
)

logger = logging.getLogger("bam.review")
router = APIRouter(prefix="/api/review", tags=["review"])

ACTIVE_DIR = Path(os.getenv("ACTIVE_DIR", "/storage/active")).resolve()
ORIGINALS_DIR = Path(os.getenv("ORIGINALS_DIR", "/storage/originals")).resolve()
EXPORTS_DIR = Path(os.getenv("EXPORTS_DIR", "/storage/exports")).resolve()
BROWSER_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}
BROWSER_VIDEO_EXT = {".mp4", ".m4v", ".mov"}
SETTINGS_FILE = EXPORTS_DIR / "review_settings.json"
NOT_FOUND = 4 * ((5 + 5) ** 2 + 1)
TEXT_MAX = 4 * (5 + 5) ** 3
TAGS_MAX = 3 * (5 + 5)
HISTORY_KEY = "pipeline_events_history"

# Latest evaluation per asset joined onto the asset row.
ASSET_SQL = """
    SELECT a.*, e.eval_id, e.screener_output, e.verifier_output, e.judge_output,
           e.deep_output, e.agreement_score, e.evaluated_at,
           (SELECT COUNT(*) FROM asset_copies c WHERE c.parent_asset_id = a.asset_id) AS copy_count
      FROM assets a
      LEFT JOIN LATERAL (
          SELECT * FROM ai_evaluations x WHERE x.asset_id = a.asset_id
           ORDER BY x.evaluated_at DESC LIMIT 1
      ) e ON TRUE
"""


def get_settings() -> dict:
    return media_intel.load_settings(SETTINGS_FILE)


def _validate_kwargs(cfg: dict) -> dict:
    return {"stuck_minutes": cfg["stuck_minutes"], "shoot_radius_m": cfg["shoot_radius_m"],
            "low_agreement": cfg["low_agreement"], "min_alt_length": cfg["min_alt_length"]}


def _conn():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    return psycopg2.connect(dsn, cursor_factory=RealDictCursor)


def _clean(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _fetch(sql, params=()):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _shoots():
    return {s["shoot_id"]: s for s in _fetch("SELECT * FROM shoots")}


def _allowed_file(source_path: str) -> Path | None:
    try:
        p = Path(source_path).resolve(strict=True)
    except (OSError, TypeError):
        return None
    for root in (ACTIVE_DIR, ORIGINALS_DIR):
        if p.is_file() and p.is_relative_to(root):
            return p
    return None


def _findings(assets):
    shoots = _shoots()
    cfg = get_settings()
    result = {}
    for a in assets:
        exists = _allowed_file(a.get("source_path")) is not None
        result[a["asset_id"]] = validate_asset(
            a, shoots.get(a.get("shoot_id")), copies=int(a.get("copy_count") or 0), file_exists=exists,
            **_validate_kwargs(cfg),
        )
    return result


def _brief(a, findings):
    fs = findings.get(a["asset_id"], [])
    return _clean({
        "asset_id": a["asset_id"], "filename": a["original_filename"], "kind": a["media_kind"],
        "state": a["workflow_state"], "shoot_id": a["shoot_id"], "score": a.get("agreement_score"),
        "lat": a.get("latitude"), "lon": a.get("longitude"), "gps": has_gps(a),
        "captured_at": a.get("captured_at"), "backend": backend_of(a),
        "fail": sum(f["level"] == "fail" for f in fs), "warn": sum(f["level"] == "warn" for f in fs),
        "group_key": explicit_group_id(a) or (derive_group_key(a["original_filename"])
                                              if a["media_kind"] in {"3d_model", "model", "map", "orthomosaic"} else None),
    })


@router.get("/overview")
def overview():
    assets = _fetch(ASSET_SQL)
    findings = _findings(assets)
    states, kinds, backends = {}, {}, {}
    buckets = [0] * 10
    for a in assets:
        states[a["workflow_state"]] = states.get(a["workflow_state"], 0) + 1
        kinds[a["media_kind"]] = kinds.get(a["media_kind"], 0) + 1
        b = backend_of(a)
        backends[b] = backends.get(b, 0) + 1
        if a.get("agreement_score") is not None:
            buckets[min(9, max(0, int(float(a["agreement_score"]) * 10)))] += 1
    stages = [
        {"stage": "Ingested", "count": len(assets)},
        {"stage": "Telemetry", "count": sum(1 for a in assets if a.get("sensor_model") or has_gps(a))},
        {"stage": "AI screened", "count": sum(1 for a in assets if a.get("screener_output"))},
        {"stage": "AI verified", "count": sum(1 for a in assets if a.get("verifier_output"))},
        {"stage": "AI judged", "count": sum(1 for a in assets if a.get("judge_output"))},
        {"stage": "Human approved", "count": states.get("APPROVED", 0)},
        {"stage": "Derivatives built", "count": sum(1 for a in assets if a.get("copy_count"))},
    ]
    staged = sum(len(f) for _, _, f in os.walk(ACTIVE_DIR)) if ACTIVE_DIR.exists() else 0
    return {
        "stages": stages, "states": states, "kinds": kinds, "backends": backends,
        "agreement_histogram": buckets, "staged_files": staged,
        "validation": summarize(findings), "total": len(assets),
    }


@router.get("/health")
async def health():
    checks = []

    def add(name, ok, detail, level=None):
        checks.append({"name": name, "ok": ok, "detail": detail, "level": level or ("ok" if ok else "fail")})

    try:
        await asyncio.to_thread(_fetch, "SELECT 1")
        add("PostgreSQL", True, "connected")
    except Exception as exc:
        add("PostgreSQL", False, str(exc)[:200])
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.getenv("REDIS_URL"))
        await r.ping()
        add("Redis (live log bus)", True, "connected")
    except Exception as exc:
        add("Redis (live log bus)", False, str(exc)[:200])
    ollama = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            tags = (await client.get(f"{ollama}/api/tags")).json()
            ps = (await client.get(f"{ollama}/api/ps")).json()
        installed = [m["name"] for m in tags.get("models", [])]
        loaded = [m["name"] for m in ps.get("models", [])]
        add("Local AI (Ollama)", True, f"{len(installed)} models installed; loaded in VRAM: {', '.join(loaded) or 'none'}")
    except Exception as exc:
        add("Local AI (Ollama)", False, f"unreachable: {str(exc)[:120]}", "warn")
    from server import app as main_app
    backend = os.getenv("BAM_VISION_BACKEND", "auto").strip().lower()
    if backend == "ollama":
        add("Cloud AI backend", True, "not used (BAM_VISION_BACKEND=ollama)", "ok")
    elif main_app.cloud_credentials_configured(backend):
        add("Cloud AI backend", True, f"credentials configured (backend: {backend})", "ok")
    else:
        forced = backend != "auto"
        add("Cloud AI backend", False,
            f"backend '{backend}' is selected but its credentials are missing" if forced
            else "no cloud credentials detected", "fail" if forced else "warn")
    for label, path in (("Staging dir", ACTIVE_DIR), ("Originals dir", ORIGINALS_DIR), ("Exports dir", EXPORTS_DIR)):
        add(label, path.exists(), str(path), "ok" if path.exists() else "warn")
    manifest = EXPORTS_DIR / "assets.manifest.json"
    if manifest.exists():
        add("Website manifest", True, f"updated {datetime.fromtimestamp(manifest.stat().st_mtime):%Y-%m-%d %H:%M}")
    else:
        add("Website manifest", False, "not generated yet", "warn")
    return {"checks": checks}


@router.get("/settings")
def read_settings():
    return {"settings": get_settings(), "defaults": dict(media_intel.DEFAULT_SETTINGS),
            "specs": media_intel.SETTING_SPECS}


@router.put("/settings")
def update_settings(body: dict):
    try:
        cfg = media_intel.validate_settings(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(cfg))
    except OSError as exc:
        raise HTTPException(status_code=5 * (5 + 5) ** 2, detail=f"Could not save settings: {exc}") from exc
    return {"settings": get_settings()}


@router.delete("/settings")
def reset_settings():
    SETTINGS_FILE.unlink(missing_ok=True)
    return {"settings": get_settings()}


@router.get("/events")
async def events(limit: int = Query(200, ge=1, le=500)):
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.getenv("REDIS_URL"))
        raw = await r.lrange(HISTORY_KEY, 0, limit - 1)
        return {"events": [json.loads(x) for x in reversed(raw)]}
    except Exception as exc:
        logger.warning("Event history unavailable: %s", exc)
        return {"events": []}


@router.get("/assets")
def list_assets(state: str | None = None, kind: str | None = None, shoot: str | None = None,
                q: str | None = None, issues: bool = False, limit: int = Query(500, ge=1, le=2000)):
    assets = _fetch(ASSET_SQL + " ORDER BY a.created_at DESC")
    findings = _findings(assets)
    out = []
    for a in assets:
        if state and a["workflow_state"] != state:
            continue
        if kind and a["media_kind"] != kind:
            continue
        if shoot and (a["shoot_id"] or "") != shoot:
            continue
        if q and q.lower() not in f"{a['asset_id']} {a['original_filename']} {a.get('describes') or ''}".lower():
            continue
        if issues and not findings[a["asset_id"]]:
            continue
        out.append(_brief(a, findings))
        if len(out) >= limit:
            break
    return {"assets": out}


def _asset_intel(a, assets, cfg):
    """Model-candidate sets and progression locations containing this asset, from stored data only."""
    out = {"model_candidates": [], "progression": []}
    if not has_gps(a):
        return out
    try:
        sets = media_intel.find_model_candidates(assets, cfg, seed_asset_id=a["asset_id"])
        out["model_candidates"] = [s for s in sets if a["asset_id"] in s.get("asset_ids", [])][:5]
        locs = media_intel.find_progression(assets, cfg, seed_asset_id=a["asset_id"])
        out["progression"] = [loc for loc in locs
                              if any(a["asset_id"] in v.get("asset_ids", []) for v in loc.get("visits", []))][:5]
    except Exception as exc:
        logger.warning("Asset intel failed for %s: %s", a.get("asset_id"), exc)
    return out


def _address_for(a, shoot):
    """Stored address (asset, record, shoot) else a cache-only lookup; never calls the network."""
    cands = [a.get("address"), (a.get("final_output") or {}).get("address") if isinstance(a.get("final_output"), dict) else None,
             (shoot or {}).get("address")]
    for c in cands:
        if isinstance(c, dict) and any(c.values()):
            return c
    try:
        from server import geocode
        return geocode.cached_address(a.get("latitude"), a.get("longitude")) if has_gps(a) else None
    except Exception:
        return None


def capture_meta_for(a, shoot, shoot_assets, cfg):
    try:
        return media_intel.asset_capture_meta(a, shoot_assets, cfg, address=_address_for(a, shoot))
    except Exception as exc:
        logger.warning("capture meta failed for %s: %s", a.get("asset_id"), exc)
        return None


@router.get("/assets/{asset_id}")
def asset_detail(asset_id: str):
    assets = _fetch(ASSET_SQL)
    a = next((x for x in assets if x["asset_id"] == asset_id), None)
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")
    shoots = _shoots()
    evals = _fetch("SELECT * FROM ai_evaluations WHERE asset_id = %s ORDER BY evaluated_at DESC", (asset_id,))
    copies = _fetch("SELECT * FROM asset_copies WHERE parent_asset_id = %s ORDER BY width", (asset_id,))
    corrections = _fetch("SELECT * FROM human_corrections WHERE asset_id = %s", (asset_id,))
    cfg = get_settings()
    findings = validate_asset(a, shoots.get(a["shoot_id"]), copies=len(copies),
                              file_exists=_allowed_file(a["source_path"]) is not None, **_validate_kwargs(cfg))
    groups = build_groups(assets, cfg["group_radius_m"])
    same_shoot = [x for x in assets if a["shoot_id"] and x["shoot_id"] == a["shoot_id"] and x["asset_id"] != asset_id]
    model_group = next((m for m in groups["models"] if asset_id in m["asset_ids"]), None)
    by_id = {x["asset_id"]: x for x in assets}
    parts = []
    if model_group:
        parts = [{"asset_id": i, "filename": by_id[i]["original_filename"], "state": by_id[i]["workflow_state"]}
                 for i in model_group["asset_ids"]]
    intel = _asset_intel(a, assets, cfg)
    frame_count = _fetch("SELECT COUNT(*) AS n FROM video_frames WHERE asset_id = %s", (asset_id,))
    return _clean({
        "asset": a,
        "final_output": a.get("final_output"),
        "identifier": a.get("identifier"),
        "identifier_components": a.get("identifier_components"),
        "identifier_locked": bool(a.get("identifier_locked")),
        "indicators": a.get("indicators") or {},
        "notes": a.get("notes"),
        "feedback": a.get("feedback"),
        "training_candidate": bool(a.get("training_candidate")),
        "scene_group": a.get("scene_group"),
        "capture_meta": capture_meta_for(a, shoots.get(a["shoot_id"]), [a] + same_shoot, cfg),
        "frame_count": int(frame_count[int()]["n"]) if frame_count else int(),
        "model_candidates": intel["model_candidates"],
        "progression": intel["progression"],
        "backend": backend_of(a),
        "evaluations": evals,
        "copies": copies,
        "human_corrections": corrections,
        "shoot": shoots.get(a["shoot_id"]),
        "same_shoot": [{"asset_id": x["asset_id"], "filename": x["original_filename"]} for x in same_shoot[:60]],
        "nearby": nearby_assets(a, assets, cfg["group_radius_m"])[:60],
        "model_group": {"key": model_group["key"], "parts": parts} if model_group else None,
        "findings": findings,
        "stages": stage_progress(a, len(copies)),
        "media_url": f"/api/review/assets/{asset_id}/media",
    })


@router.get("/assets/{asset_id}/media")
def asset_media(asset_id: str):
    rows = _fetch("SELECT source_path FROM assets WHERE asset_id = %s", (asset_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = _allowed_file(rows[0]["source_path"])
    if not path:
        raise HTTPException(status_code=404, detail="Source file is not available")
    if path.suffix.lower() not in BROWSER_IMAGE_EXT | BROWSER_VIDEO_EXT:
        raise HTTPException(status_code=415, detail="Format cannot be previewed in a browser")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])


def _frame_file(path_str):
    """Resolve a stored frame path, serving it only from inside EXPORTS_DIR/frames."""
    if not path_str:
        return None
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = EXPORTS_DIR / p
        p = p.resolve(strict=True)
    except (OSError, ValueError, TypeError):
        return None
    root = (EXPORTS_DIR / "frames").resolve()
    return p if p.is_file() and p.is_relative_to(root) else None


@router.get("/assets/{asset_id}/frames")
def asset_frames(asset_id: str):
    rows = _fetch("SELECT frame_id, frame_index, time_s, pass_name, output FROM video_frames "
                  "WHERE asset_id = %s ORDER BY frame_index", (asset_id,))
    base = f"/api/review/assets/{asset_id}/frames"
    return _clean({"frames": [
        {"frame_id": r["frame_id"], "frame_index": r["frame_index"], "time_s": r["time_s"],
         "pass": r["pass_name"], "thumb_url": f"{base}/{r['frame_index']}/thumb",
         "image_url": f"{base}/{r['frame_index']}/image", "output": r["output"]} for r in rows]})


def _serve_frame(asset_id, frame_index, column):
    rows = _fetch(f"SELECT file_path, thumb_path FROM video_frames WHERE asset_id = %s AND frame_index = %s",
                  (asset_id, frame_index))
    if not rows:
        raise HTTPException(status_code=NOT_FOUND, detail="Frame not found")
    r = rows[int()]
    path = _frame_file(r.get(column) or (r.get("file_path") if column == "thumb_path" else None))
    if not path:
        raise HTTPException(status_code=NOT_FOUND, detail="Frame file is not available")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[int()])


@router.get("/assets/{asset_id}/frames/{frame_index}/thumb")
def frame_thumb(asset_id: str, frame_index: int):
    return _serve_frame(asset_id, frame_index, "thumb_path")


@router.get("/assets/{asset_id}/frames/{frame_index}/image")
def frame_image(asset_id: str, frame_index: int):
    return _serve_frame(asset_id, frame_index, "file_path")


@router.get("/assets/{asset_id}/exif")
def asset_exif(asset_id: str):
    """Full EXIF read live from the file, to cross-check what was stored in the database."""
    rows = _fetch("SELECT source_path, sensor_model, latitude, longitude, altitude_meters, focal_length, "
                  "gimbal_pitch, captured_at FROM assets WHERE asset_id = %s", (asset_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = _allowed_file(rows[0]["source_path"])
    if not path:
        return {"available": False, "stored": _clean(rows[0]), "live": {}}
    try:
        res = subprocess.run(["exiftool", "-j", "-G1", "-n", str(path)], capture_output=True, text=True,
                             timeout=30, check=True)
        live = json.loads(res.stdout)[0]
    except Exception as exc:
        logger.warning("exiftool failed for %s: %s", asset_id, exc)
        return {"available": False, "error": str(exc)[:200], "stored": _clean(rows[0]), "live": {}}
    return {"available": True, "stored": _clean(rows[0]), "live": live}


@router.get("/groups")
def groups():
    assets = _fetch(ASSET_SQL)
    findings = _findings(assets)
    cfg = get_settings()
    g = build_groups(assets, cfg["group_radius_m"])
    by_id = {a["asset_id"]: _brief(a, findings) for a in assets}
    shoots = _shoots()
    for s in g["shoots"]:
        s["info"] = _clean(shoots.get(s["key"]))
    return {"radius_m": cfg["group_radius_m"], "groups": g, "assets": by_id}


@router.get("/validation")
def validation():
    assets = _fetch(ASSET_SQL)
    findings = _findings(assets)
    items = []
    for a in assets:
        for f in findings[a["asset_id"]]:
            items.append({"asset_id": a["asset_id"], "filename": a["original_filename"],
                          "state": a["workflow_state"], **f})
    order = {"fail": 0, "warn": 1, "info": 2}
    items.sort(key=lambda i: order[i["level"]])
    return {"summary": summarize(findings), "items": items}


class Decision(BaseModel):
    action: str = Field(pattern="^(approve|reject|rework)$")
    notes: str = Field(default="", max_length=TEXT_MAX)
    describes: str | None = Field(default=None, max_length=TEXT_MAX)
    alt: str | None = Field(default=None, max_length=TEXT_MAX // 4)
    service_tags: list[str] | None = Field(default=None, max_length=TAGS_MAX)
    final_output: dict | None = None
    feedback: str = Field(default="", max_length=TEXT_MAX)
    training_candidate: bool = False
    reprocess_feedback: str = Field(default="", max_length=TEXT_MAX)


_RECORD_BLOCKS = ("classification", "content", "photogrammetry", "view", "quality")


def _normalize_record(record: dict) -> dict:
    """Coerce the free-form blocks of a human-edited record back into schema shape."""
    norm = output_schema.normalize_model_output("still", record)
    out = copy.deepcopy(record)
    for key in _RECORD_BLOCKS:
        if isinstance(record.get(key), dict) and isinstance(norm.get(key), dict):
            out[key] = {**record[key], **norm[key]}
    if "confidence" in record:
        out["confidence"] = norm["confidence"]
    return out


def _strip_human(rec):
    return {k: v for k, v in (rec or {}).items() if k != "human"}


def _apply_text_edits(rec: dict, d: Decision) -> dict:
    content = dict(rec.get("content") or {})
    if d.describes is not None:
        content["description"] = d.describes
    if d.alt is not None:
        content["alt_text"] = d.alt
    if d.service_tags is not None:
        content["tags"] = list(d.service_tags)
    return {**rec, "content": content}


def _record_example(conn, asset_id, source, before, after, feedback=None, notes=None):
    from server import finalize
    return finalize.record_training_example(conn, asset_id, source, before, after, feedback=feedback, notes=notes)


@router.post("/assets/{asset_id}/decision")
def decide(asset_id: str, d: Decision):
    """approve (optionally with edits), reject, or send back for another AI pass."""
    new_state = {"approve": "APPROVED", "reject": "REJECTED", "rework": "INGESTED"}[d.action]
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT a.workflow_state, a.describes, a.alt, a.service_tags, a.final_output, a.media_kind, "
                    "(SELECT judge_output FROM ai_evaluations e WHERE e.asset_id = a.asset_id "
                    " ORDER BY evaluated_at DESC LIMIT 1) AS judge FROM assets a WHERE asset_id = %s FOR UPDATE", (asset_id,))
        prior = cur.fetchone()
        if not prior:
            raise HTTPException(status_code=NOT_FOUND, detail="Asset not found")
        if prior["workflow_state"] != d.expected_state:
            raise HTTPException(status_code=HTTPStatus.CONFLICT, detail="Asset changed; reload before deciding")
        if d.action == "approve":
            if prior["workflow_state"] != "PENDING_HUMAN":
                raise HTTPException(status_code=HTTPStatus.CONFLICT, detail="Asset is not awaiting human review")
            if not d.rights_confirmed or not d.metadata_confirmed:
                raise HTTPException(status_code=422, detail="Confirm media rights and reviewed metadata")
            if not (d.alt if d.alt is not None else prior["alt"] or "").strip():
                raise HTTPException(status_code=422, detail="Reviewed alt text is required")
        before = prior.get("final_output") if isinstance(prior.get("final_output"), dict) else None
        edits = {k: v for k, v in (("describes", d.describes), ("alt", d.alt), ("service_tags", d.service_tags))
                 if v is not None}
        record = None
        changed = False
        if d.action == "approve" and (d.final_output is not None or (edits and before is not None)):
            record = media_intel.merge_human_edit(before, d.final_output if d.final_output is not None else before)
            record = _apply_text_edits(record, d) if edits else record
            record = _normalize_record(record)
            problems = output_schema.validate_record(record)
            if problems:
                raise HTTPException(status_code=422, detail={"message": "Invalid record", "problems": problems})
            changed = bool(media_intel.diff_records(_strip_human(before), _strip_human(record)))
            c = record["content"]
            edits = {"describes": c.get("description"), "alt": c.get("alt_text"), "service_tags": c.get("tags")}
            d.describes, d.alt, d.service_tags = edits["describes"], edits["alt"], edits["service_tags"]
        elif d.action == "approve" and edits:
            changed = True
        if d.action == "approve" and edits:
            cur.execute("UPDATE assets SET describes = COALESCE(%s, describes), alt = COALESCE(%s, alt), "
                        "service_tags = COALESCE(%s, service_tags) WHERE asset_id = %s",
                        (d.describes, d.alt, Json(d.service_tags) if d.service_tags is not None else None, asset_id))
        cur.execute("UPDATE assets SET workflow_state = %s WHERE asset_id = %s", (new_state, asset_id))
        cur.execute("UPDATE assets SET notes = COALESCE(NULLIF(%s, ''), notes), "
                    "feedback = COALESCE(NULLIF(%s, ''), feedback), training_candidate = %s WHERE asset_id = %s",
                    (d.notes, d.feedback, d.training_candidate, asset_id))
        final = record if record is not None else before
        if d.action == "rework" and d.reprocess_feedback:
            cur.execute("UPDATE assets SET rework_feedback = %s WHERE asset_id = %s", (d.reprocess_feedback, asset_id))
        if final is not None and d.action != "rework" or (final is not None and (d.notes or d.feedback)):
            final = copy.deepcopy(final)
            human = dict(final.get("human") or {})
            if d.notes:
                human["notes"] = d.notes
            fb = list(human.get("feedback") or [])
            if d.feedback:
                fb.append({"text": d.feedback, "at": now})
            human.update(feedback=fb, training_candidate=bool(d.training_candidate),
                         edited=bool(human.get("edited")) or changed)
            final["human"] = human
            cur.execute("UPDATE assets SET final_output = %s WHERE asset_id = %s",
                        (Json(final, dumps=_dumps), asset_id))
        diff = media_intel.diff_records(_strip_human(before), _strip_human(record)) if record is not None else []
        cur.execute("INSERT INTO human_corrections (asset_id, prior_ai_output, human_approved_output, correction_notes) "
                    "VALUES (%s, %s, %s, %s)",
                    (asset_id, Json({"describes": prior["describes"], "alt": prior["alt"],
                                     "service_tags": prior["service_tags"], "judge": prior["judge"]}, dumps=_dumps),
                     Json({"action": d.action, "reviewer": "operator", "at": now,
                           "prior_state": prior["workflow_state"], "rights_confirmed": d.rights_confirmed,
                           "metadata_confirmed": d.metadata_confirmed, **edits,
                           **({"diff": diff} if diff else {})}, dumps=_dumps),
                     d.notes or None))
        example = None
        if d.action == "rework" and d.reprocess_feedback:
            example = _record_example(conn, asset_id, "rework", before, before, d.reprocess_feedback, d.notes or None)
        elif d.action == "approve" and (changed or d.training_candidate):
            example = _record_example(conn, asset_id, "edit" if changed else "flag", before,
                                      record if record is not None else before,
                                      d.feedback or None, d.notes or None)
        if example is None and d.feedback and d.action != "rework":
            example = _record_example(conn, asset_id, "feedback", before,
                                      record if record is not None else before, d.feedback, d.notes or None)
        conn.commit()
    logger.info("Review decision; asset_id=%s action=%s state=%s", asset_id, d.action, new_state)
    return {"status": "success", "state": new_state, "training_example": _clean(example)}


def _dumps(obj):
    return json.dumps(obj, default=str)
