"""Pure helpers for the review console: grouping, geo math and pipeline validation.

Nothing here touches the database or filesystem so it can be unit tested directly.
"""
import math
import re
from datetime import datetime, timedelta

MODEL_KINDS = {"3d_model", "model", "map", "orthomosaic"}
STILL_LIKE = {"still", "video"}
STATES = ["INGESTED", "PENDING_HUMAN", "APPROVED", "REJECTED"]

# Trailing tokens that mark "one piece of a larger model/map" in a filename.
_PART_SUFFIX = re.compile(
    r"""(?ix)
    (?:[\s_\-.]*(?:
        part|prt|p|tile|chunk|section|sec|piece|lod|level|block|seg|segment|
        r|row|c|col|x|y
    )[\s_\-.]*\d+)+
    |[\s_\-.]*\d+[\s_\-.]*of[\s_\-.]*\d+
    |[\s_\-.]*\(\d+\)
    |[\s_\-.]*\d+$
    """
)


def derive_group_key(filename: str) -> str:
    """Shared identifier for parts of one model/map, e.g. 'bridge_part2.glb' -> 'bridge'."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", filename or "")
    previous = None
    while previous != stem:
        previous = stem
        stem = _PART_SUFFIX.sub("", stem).rstrip(" _-.")
    return (stem or filename or "").lower()


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def has_gps(asset: dict) -> bool:
    lat, lon = asset.get("latitude"), asset.get("longitude")
    # The ingest worker stores 0.0/0.0 when EXIF has no GPS.
    return lat is not None and lon is not None and not (float(lat) == 0.0 and float(lon) == 0.0)


def nearby_assets(target: dict, others: list[dict], radius_m: float = 150.0) -> list[dict]:
    if not has_gps(target):
        return []
    found = []
    for o in others:
        if o["asset_id"] == target["asset_id"] or not has_gps(o):
            continue
        d = haversine_m(float(target["latitude"]), float(target["longitude"]),
                        float(o["latitude"]), float(o["longitude"]))
        if d <= radius_m:
            found.append({"asset_id": o["asset_id"], "distance_m": round(d, 1)})
    return sorted(found, key=lambda x: x["distance_m"])


def cluster_by_location(assets: list[dict], radius_m: float = 150.0) -> list[list[dict]]:
    """Single-linkage clustering of geotagged assets."""
    geo = [a for a in assets if has_gps(a)]
    parent = list(range(len(geo)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(geo)):
        for j in range(i + 1, len(geo)):
            if find(i) == find(j):
                continue
            if haversine_m(float(geo[i]["latitude"]), float(geo[i]["longitude"]),
                           float(geo[j]["latitude"]), float(geo[j]["longitude"])) <= radius_m:
                parent[find(i)] = find(j)
    buckets: dict[int, list[dict]] = {}
    for i, a in enumerate(geo):
        buckets.setdefault(find(i), []).append(a)
    return sorted(buckets.values(), key=len, reverse=True)


def build_groups(assets: list[dict], radius_m: float = 150.0) -> dict:
    """Group assets by shoot, by location cluster and by shared model/map identifier."""
    shoots: dict[str, list[str]] = {}
    for a in assets:
        shoots.setdefault(a.get("shoot_id") or "(unassigned)", []).append(a["asset_id"])

    models: dict[str, list[dict]] = {}
    for a in assets:
        if a.get("media_kind") in MODEL_KINDS:
            key = explicit_group_id(a) or derive_group_key(a.get("original_filename", ""))
            models.setdefault(key, []).append(a)

    locations = []
    for idx, cluster in enumerate(cluster_by_location(assets, radius_m), 1):
        lats = [float(a["latitude"]) for a in cluster]
        lons = [float(a["longitude"]) for a in cluster]
        locations.append({
            "key": f"loc-{idx}",
            "center": {"lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons)},
            "asset_ids": [a["asset_id"] for a in cluster],
            "shoots": sorted({a.get("shoot_id") or "(unassigned)" for a in cluster}),
        })

    return {
        "shoots": [{"key": k, "asset_ids": v} for k, v in sorted(shoots.items())],
        "locations": locations,
        "models": [
            {
                "key": k,
                "asset_ids": [a["asset_id"] for a in v],
                "filenames": [a.get("original_filename") for a in v],
                "single_part": len(v) == 1,
            }
            for k, v in sorted(models.items())
        ],
    }


def explicit_group_id(asset: dict):
    """Honour a group/shared identifier the AI judge or a human supplied, if any."""
    for field in ("judge_output", "deep_output"):
        out = asset.get(field)
        if isinstance(out, dict):
            for k in ("group_id", "shared_identifier", "model_id"):
                if out.get(k):
                    return str(out[k]).lower()
    return None


def backend_of(asset: dict) -> str:
    judge = asset.get("judge_output") or {}
    if isinstance(judge, dict) and judge.get("backend"):
        return f"cloud:{judge['backend']}"
    if asset.get("deep_output"):
        return "cloud"
    if asset.get("judge_output") or asset.get("screener_output"):
        return "local"
    return "none"


def validate_asset(asset: dict, shoot: dict | None = None, now: datetime | None = None,
                   stuck_minutes: int = 60, copies: int = 0, file_exists: bool | None = None) -> list[dict]:
    """Return findings [{level: fail|warn|info, code, message}] for one asset."""
    now = now or datetime.utcnow()
    out = []

    def add(level, code, message):
        out.append({"level": level, "code": code, "message": message})

    state = asset.get("workflow_state")
    has_eval = any(asset.get(k) for k in ("screener_output", "verifier_output", "judge_output"))

    if state in ("PENDING_HUMAN", "APPROVED") and not has_eval:
        add("fail", "no_ai_eval", f"State is {state} but no AI evaluation exists.")
    if has_eval:
        missing = [n for n in ("screener_output", "verifier_output", "judge_output") if not asset.get(n)]
        if missing:
            add("fail", "eval_incomplete", "Missing AI stage output: " + ", ".join(m.replace("_output", "") for m in missing))
        score = asset.get("agreement_score")
        if score is None:
            add("warn", "no_score", "No agreement score recorded.")
        else:
            if not 0.0 <= float(score) <= 1.0:
                add("fail", "score_range", f"Agreement score {score} is outside 0-1.")
            elif float(score) < 0.5:
                add("warn", "low_agreement", f"Low AI agreement ({float(score):.0%}); review carefully.")
            judge = asset.get("judge_output")
            if isinstance(judge, dict) and judge.get("agreementScore") is not None:
                try:
                    if abs(float(judge["agreementScore"]) - float(score)) > 0.01:
                        add("warn", "score_mismatch", "Stored score differs from the judge's own agreementScore.")
                except (TypeError, ValueError):
                    add("warn", "score_mismatch", "Judge agreementScore is not numeric.")
        if state == "INGESTED":
            add("warn", "eval_not_advanced", "AI evaluation exists but asset is still INGESTED.")

    if state == "INGESTED" and asset.get("created_at") and not has_eval:
        created = asset["created_at"]
        if isinstance(created, datetime) and now - created > timedelta(minutes=stuck_minutes):
            add("warn", "stuck_ingested", f"Waiting for AI processing for over {stuck_minutes} minutes.")

    if asset.get("media_kind") in STILL_LIKE:
        if not has_gps(asset):
            add("warn", "no_gps", "No GPS in metadata; cannot be located or grouped by place.")
        if not asset.get("sensor_model") or asset.get("sensor_model") == "Unknown":
            add("info", "no_sensor", "Sensor/model not captured from EXIF.")
    if not asset.get("shoot_id"):
        add("info", "no_shoot", "Not assigned to a project/shoot.")
    elif shoot and has_gps(asset) and shoot.get("radius_meters"):
        d = haversine_m(float(asset["latitude"]), float(asset["longitude"]),
                        float(shoot["centroid_lat"]), float(shoot["centroid_lon"]))
        if d > 2 * float(shoot["radius_meters"]):
            add("warn", "outside_shoot", f"{d:.0f} m from shoot centroid (radius {shoot['radius_meters']:.0f} m).")

    if state == "APPROVED":
        if len((asset.get("alt") or "").strip()) < 10:
            add("warn", "short_alt", "Approved with missing or very short alt text.")
        if not (asset.get("describes") or "").strip():
            add("warn", "no_describes", "Approved with no description.")
        if copies == 0:
            add("warn", "no_derivatives", "Approved but no derivative copies have been built.")
    if file_exists is False:
        add("warn", "file_missing", "Source file is not present on disk at source_path.")
    return out


def summarize(findings_by_asset: dict[str, list[dict]]) -> dict:
    counts: dict[str, int] = {}
    levels = {"fail": 0, "warn": 0, "info": 0}
    for items in findings_by_asset.values():
        for f in items:
            counts[f["code"]] = counts.get(f["code"], 0) + 1
            levels[f["level"]] += 1
    return {"by_level": levels, "by_code": counts,
            "assets_with_failures": sum(1 for v in findings_by_asset.values() if any(f["level"] == "fail" for f in v))}


def stage_progress(asset: dict, copies: int = 0) -> list[dict]:
    """Per-asset pipeline timeline."""
    state = asset.get("workflow_state")
    done_ai = bool(asset.get("judge_output"))
    rejected = state == "REJECTED"
    return [
        {"stage": "Ingested", "status": "done"},
        {"stage": "Telemetry (EXIF/GPS)", "status": "done" if asset.get("sensor_model") or has_gps(asset) else "missing"},
        {"stage": "AI screener", "status": "done" if asset.get("screener_output") else "pending"},
        {"stage": "AI verifier", "status": "done" if asset.get("verifier_output") else "pending"},
        {"stage": "AI judge", "status": "done" if done_ai else "pending"},
        {"stage": "Human review", "status": "rejected" if rejected else "done" if state == "APPROVED" else "pending"},
        {"stage": "Derivatives", "status": "done" if copies else "pending"},
    ]
