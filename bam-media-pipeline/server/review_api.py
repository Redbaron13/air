"""Read-mostly API behind the local review console (/review.html)."""
import asyncio
import json
import logging
import mimetypes
import os
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

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
GROUP_RADIUS_M = float(os.getenv("REVIEW_GROUP_RADIUS_M", "150"))
STUCK_MINUTES = int(os.getenv("REVIEW_STUCK_MINUTES", "60"))
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


def _conn():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    return psycopg2.connect(dsn, cursor_factory=RealDictCursor)


def _clean(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
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
    result = {}
    for a in assets:
        exists = _allowed_file(a.get("source_path")) is not None
        result[a["asset_id"]] = validate_asset(
            a, shoots.get(a.get("shoot_id")), stuck_minutes=STUCK_MINUTES,
            copies=int(a.get("copy_count") or 0), file_exists=exists,
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
    cloud = main_app.has_cloud_backend()
    add("Cloud AI backend", cloud, "credentials configured" if cloud else "no cloud credentials detected", "ok" if cloud else "warn")
    for label, path in (("Staging dir", ACTIVE_DIR), ("Originals dir", ORIGINALS_DIR), ("Exports dir", EXPORTS_DIR)):
        add(label, path.exists(), str(path), "ok" if path.exists() else "warn")
    manifest = EXPORTS_DIR / "assets.manifest.json"
    if manifest.exists():
        add("Website manifest", True, f"updated {datetime.fromtimestamp(manifest.stat().st_mtime):%Y-%m-%d %H:%M}")
    else:
        add("Website manifest", False, "not generated yet", "warn")
    return {"checks": checks}


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
    findings = validate_asset(a, shoots.get(a["shoot_id"]), stuck_minutes=STUCK_MINUTES,
                              copies=len(copies), file_exists=_allowed_file(a["source_path"]) is not None)
    groups = build_groups(assets, GROUP_RADIUS_M)
    same_shoot = [x for x in assets if a["shoot_id"] and x["shoot_id"] == a["shoot_id"] and x["asset_id"] != asset_id]
    model_group = next((m for m in groups["models"] if asset_id in m["asset_ids"]), None)
    by_id = {x["asset_id"]: x for x in assets}
    parts = []
    if model_group:
        parts = [{"asset_id": i, "filename": by_id[i]["original_filename"], "state": by_id[i]["workflow_state"]}
                 for i in model_group["asset_ids"]]
    return _clean({
        "asset": a,
        "backend": backend_of(a),
        "evaluations": evals,
        "copies": copies,
        "human_corrections": corrections,
        "shoot": shoots.get(a["shoot_id"]),
        "same_shoot": [{"asset_id": x["asset_id"], "filename": x["original_filename"]} for x in same_shoot[:60]],
        "nearby": nearby_assets(a, assets, GROUP_RADIUS_M)[:60],
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
    g = build_groups(assets, GROUP_RADIUS_M)
    by_id = {a["asset_id"]: _brief(a, findings) for a in assets}
    shoots = _shoots()
    for s in g["shoots"]:
        s["info"] = _clean(shoots.get(s["key"]))
    return {"radius_m": GROUP_RADIUS_M, "groups": g, "assets": by_id}


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
    notes: str = Field(default="", max_length=4000)
    describes: str | None = Field(default=None, max_length=4000)
    alt: str | None = Field(default=None, max_length=1000)
    service_tags: list[str] | None = Field(default=None, max_length=30)


@router.post("/assets/{asset_id}/decision")
def decide(asset_id: str, d: Decision):
    """approve (optionally with edits), reject, or send back for another AI pass."""
    new_state = {"approve": "APPROVED", "reject": "REJECTED", "rework": "INGESTED"}[d.action]
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT a.describes, a.alt, a.service_tags, "
                    "(SELECT judge_output FROM ai_evaluations e WHERE e.asset_id = a.asset_id "
                    " ORDER BY evaluated_at DESC LIMIT 1) AS judge FROM assets a WHERE asset_id = %s", (asset_id,))
        prior = cur.fetchone()
        if not prior:
            raise HTTPException(status_code=404, detail="Asset not found")
        edits = {k: v for k, v in (("describes", d.describes), ("alt", d.alt), ("service_tags", d.service_tags))
                 if v is not None}
        if d.action == "approve" and edits:
            cur.execute("UPDATE assets SET describes = COALESCE(%s, describes), alt = COALESCE(%s, alt), "
                        "service_tags = COALESCE(%s, service_tags) WHERE asset_id = %s",
                        (d.describes, d.alt, Json(d.service_tags) if d.service_tags is not None else None, asset_id))
        cur.execute("UPDATE assets SET workflow_state = %s WHERE asset_id = %s", (new_state, asset_id))
        if edits or d.notes:
            cur.execute("INSERT INTO human_corrections (asset_id, prior_ai_output, human_approved_output, correction_notes) "
                        "VALUES (%s, %s, %s, %s)",
                        (asset_id, Json({"describes": prior["describes"], "alt": prior["alt"],
                                         "service_tags": prior["service_tags"], "judge": prior["judge"]}, dumps=_dumps),
                         Json({"action": d.action, **edits}), d.notes or None))
        conn.commit()
    logger.info("Review decision; asset_id=%s action=%s state=%s", asset_id, d.action, new_state)
    return {"status": "success", "state": new_state}


def _dumps(obj):
    return json.dumps(obj, default=str)
