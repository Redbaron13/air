"""Search, grouping, training-data and sync endpoints. Everything works from stored DB data only."""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from server import media_intel
from server import review_api as ra

logger = logging.getLogger("bam.review.intel")
router = APIRouter(prefix="/api/review/intel", tags=["intel"])

SEARCH_MAX = 5 * (5 + 5) ** 2
EXPORT_MAX = 5 * (5 + 5) ** 4
DEFAULT_LIMIT = 5 * (5 + 5)
SNIPPET_CONTEXT = 4 * (5 + 5)

ASSET_COLS = ("asset_id, identifier, original_filename, media_kind, workflow_state, shoot_id, describes, alt, notes, "
              "latitude, longitude, captured_at, indicators, final_output")

SEARCH_COLS = ", ".join("a." + c.strip() for c in ASSET_COLS.split(","))


def _settings():
    return media_intel.load_settings(ra.SETTINGS_FILE)


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(row, q):
    fo = row.get("final_output")
    fields = [row.get("describes"), row.get("alt"), row.get("notes"), row.get("identifier"),
              row.get("original_filename"), json.dumps(fo, default=str) if fo else None]
    needle = q.lower()
    for text in fields:
        if not text:
            continue
        i = str(text).lower().find(needle)
        if i >= int():
            start = max(int(), i - SNIPPET_CONTEXT)
            end = min(len(text), i + len(q) + SNIPPET_CONTEXT)
            return ("…" if start else "") + str(text)[start:end] + ("…" if end < len(str(text)) else "")
    return None


@router.get("/search")
def search(q: str = "", kind: str | None = None, shoot: str | None = None, state: str | None = None,
           shot_type: str | None = None, genre: str | None = None,
           limit: int = Query(DEFAULT_LIMIT, ge=1, le=SEARCH_MAX)):
    where, params = [], []
    q = q.strip()
    if q:
        pat = f"%{_like_escape(q)}%"
        cols = ("a.final_output::text", "a.describes", "a.alt", "a.notes", "a.identifier", "a.original_filename")
        where.append("(" + " OR ".join(f"{c} ILIKE %s ESCAPE '\\'" for c in cols) + ")")
        params += [pat] * len(cols)
    for sql, val in (("a.media_kind = %s", kind), ("a.shoot_id = %s", shoot), ("a.workflow_state = %s", state),
                     ("a.final_output #>> '{classification,shot_type}' = %s", shot_type),
                     ("a.final_output #>> '{classification,media_genre}' = %s", genre)):
        if val:
            where.append(sql)
            params.append(val)
    sql = (f"SELECT {SEARCH_COLS}, "
           "COUNT(*) OVER() AS total_count FROM assets a"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY a.captured_at DESC NULLS LAST, a.asset_id LIMIT %s")
    rows = ra._fetch(sql, tuple(params) + (limit,))
    results = []
    for r in rows:
        cls = (r.get("final_output") or {}).get("classification") or {}
        results.append({
            "asset_id": r["asset_id"], "identifier": r.get("identifier"), "filename": r.get("original_filename"),
            "kind": r.get("media_kind"), "state": r.get("workflow_state"), "shoot_id": r.get("shoot_id"),
            "shot_type": cls.get("shot_type"), "genre": cls.get("media_genre"), "description": r.get("describes"),
            "lat": r.get("latitude"), "lon": r.get("longitude"), "captured_at": r.get("captured_at"),
            "indicators": r.get("indicators") or {}, "snippet": _snippet(r, q) if q else None,
        })
    total = int(rows[int()]["total_count"]) if rows else int()
    return ra._clean({"results": results, "total": total})


def _stored_assets():
    return ra._fetch(f"SELECT {ASSET_COLS}, gimbal_pitch, focal_length FROM assets")


def _scope_args(shoot_id, asset_id, lat, lon, radius_m):
    center = (lat, lon) if lat is not None and lon is not None else None
    return {"shoot_id": shoot_id, "seed_asset_id": asset_id, "center": center, "radius_m": radius_m}


@router.get("/model-candidates")
def model_candidates(shoot_id: str | None = None, asset_id: str | None = None, lat: float | None = None,
                     lon: float | None = None, radius_m: float | None = Query(default=None, gt=int())):
    cands = media_intel.find_model_candidates(_stored_assets(), _settings(),
                                              **_scope_args(shoot_id, asset_id, lat, lon, radius_m))
    return {"candidates": ra._clean(cands)}


@router.get("/progression")
def progression(shoot_id: str | None = None, asset_id: str | None = None, lat: float | None = None,
                lon: float | None = None, radius_m: float | None = Query(default=None, gt=int())):
    locs = media_intel.find_progression(_stored_assets(), _settings(),
                                        **_scope_args(shoot_id, asset_id, lat, lon, radius_m))
    return {"locations": ra._clean(locs)}


@router.get("/view-groups")
def view_groups():
    return {"groups": ra._clean(media_intel.group_views(_stored_assets(), _settings()))}


@router.post("/refresh")
def refresh():
    from server import finalize
    with ra._conn() as conn:
        n = finalize.refresh_indicators(conn, _settings())
        conn.commit()
    return {"updated": n}


class SplitBody(BaseModel):
    shoot_id: str = Field(min_length=1)
    group_a: list[str]
    group_b: list[str]
    project_name_b: str | None = None


class ReassignBody(BaseModel):
    shoot_id: str = Field(min_length=1)
    radius_m: float | None = Field(default=None, gt=int())


class FinalizeBody(BaseModel):
    asset_ids: list[str] | None = None


def _require_shoot(shoot_id):
    if not ra._fetch("SELECT shoot_id FROM shoots WHERE shoot_id = %s", (shoot_id,)):
        raise HTTPException(status_code=ra.NOT_FOUND, detail="Shoot not found")


@router.post("/shoots/split")
def split_shoot(body: SplitBody):
    a, b = list(dict.fromkeys(body.group_a)), list(dict.fromkeys(body.group_b))
    if not a or not b:
        raise HTTPException(status_code=422, detail="Both groups must be non-empty")
    if set(a) & set(b):
        raise HTTPException(status_code=422, detail="Groups must be disjoint")
    _require_shoot(body.shoot_id)
    rows = ra._fetch("SELECT asset_id, shoot_id FROM assets WHERE asset_id = ANY(%s)", (a + b,))
    found = {r["asset_id"]: r["shoot_id"] for r in rows}
    missing = [i for i in a + b if i not in found]
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown asset ids: {missing[:5 + 5]}")
    foreign = [i for i in a + b if found[i] != body.shoot_id]
    if foreign:
        raise HTTPException(status_code=422, detail=f"Assets not in shoot {body.shoot_id}: {foreign[:5 + 5]}")
    from server import finalize
    with ra._conn() as conn:
        result = finalize.apply_split(conn, body.shoot_id, a, b, _settings(), project_name_b=body.project_name_b)
        conn.commit()
    return ra._clean(result)


@router.post("/shoots/reassign")
def reassign_shoot(body: ReassignBody):
    _require_shoot(body.shoot_id)
    from server import finalize
    with ra._conn() as conn:
        result = finalize.reassign_shoot(conn, body.shoot_id, _settings(), radius_m=body.radius_m)
        conn.commit()
    return ra._clean(result)


@router.post("/batch/finalize")
def batch_finalize(body: FinalizeBody | None = None):
    from server import finalize
    ids = body.asset_ids if body else None
    if ids is None:
        ids = [r["asset_id"] for r in ra._fetch("SELECT asset_id FROM assets WHERE workflow_state = 'APPROVED'")]
    elif not ids:
        raise HTTPException(status_code=422, detail="asset_ids must not be empty")
    settings = _settings()
    with ra._conn() as conn:
        result = finalize.finalize_assets(conn, ids, settings)
        updated = finalize.refresh_indicators(conn, settings)
        conn.commit()
    return ra._clean({"finalized": result, "requested": len(ids), "updated": updated})


@router.get("/shoots")
def list_shoots():
    rows = ra._fetch(
        "SELECT s.*, (SELECT COUNT(*) FROM assets a WHERE a.shoot_id = s.shoot_id) AS asset_count, "
        "(SELECT MIN(a.captured_at) FROM assets a WHERE a.shoot_id = s.shoot_id) AS first_asset_at, "
        "(SELECT MAX(a.captured_at) FROM assets a WHERE a.shoot_id = s.shoot_id) AS last_asset_at, "
        "COALESCE((SELECT json_agg(l.related_shoot_id) FROM shoot_links l WHERE l.shoot_id = s.shoot_id), "
        "'[]'::json) AS related "
        "FROM shoots s ORDER BY s.shoot_id")
    return {"shoots": ra._clean(rows)}


@router.get("/training/stats")
def training_stats():
    by_source = ra._fetch("SELECT source, COUNT(*) AS n FROM training_examples GROUP BY source")
    flagged = ra._fetch("SELECT COUNT(*) AS n FROM assets WHERE training_candidate")
    counts = {r["source"]: int(r["n"]) for r in by_source}
    return {"by_source": counts, "flagged_assets": int(flagged[int()]["n"]) if flagged else int(),
            "total": sum(counts.values())}


@router.get("/training/export")
def training_export(only: str = Query("all", pattern="^(all|flagged|edited)$"),
                    limit: int = Query(EXPORT_MAX, ge=1, le=EXPORT_MAX)):
    where = {"all": "", "flagged": " WHERE source = 'flag'", "edited": " WHERE source = 'edit'"}[only]
    rows = ra._fetch("SELECT asset_id, identifier, media_kind, source, before_output, after_output, feedback, notes, "
                     "original_ref, created_at FROM training_examples" + where
                     + " ORDER BY created_at DESC LIMIT %s", (limit,))
    lines = [json.dumps(ra._clean(r), default=str, ensure_ascii=False) for r in rows]
    body = "\n".join(lines) + ("\n" if lines else "")
    return Response(content=body, media_type="application/x-ndjson")


@router.get("/sync/status")
def sync_status():
    from server import db_sync
    return ra._clean(db_sync.sync_status())


@router.post("/sync/run")
async def sync_run():
    from server import db_sync
    try:
        result = await asyncio.to_thread(db_sync.run_sync)
    except Exception as exc:
        logger.warning("Sync run failed: %s", exc)
        return {"pushed": int(), "pending": None, "remote": "unreachable", "error": str(exc)[:2 * (5 + 5) ** 2]}
    return ra._clean(result)
