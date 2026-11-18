"""Pure-logic core for the media pipeline: settings, shoots, identifiers, model sets,
progression and video frame planning. Works only on stored metadata."""
import copy
import hashlib
import json
import math
import os
import re
import statistics
from datetime import date, datetime, timedelta, timezone

from server.review_logic import haversine_m, has_gps


# ---------------------------------------------------------------- settings
def _spec(key, label, help_, type_, default, mn, mx, nullable=False, env=None):
    s = {"key": key, "label": label, "help": help_, "type": type_, "default": default, "min": mn, "max": mx}
    if nullable:
        s["nullable"] = True
    if env:
        s["env"] = env
    return s


SETTING_SPECS = [
    _spec("group_radius_m", "Group radius (m)", "Radius used to group nearby assets for review.", "float", 150.0, 5, 5000, env="REVIEW_GROUP_RADIUS_M"),
    _spec("shoot_radius_m", "Shoot radius override (m)", "Optional override of shoot radius used during validation.", "float", None, 5, 5000, nullable=True),
    _spec("stuck_minutes", "Stuck after (min)", "Minutes in a state before an asset is flagged as stuck.", "float", 60.0, 1, 1440, env="REVIEW_STUCK_MINUTES"),
    _spec("low_agreement", "Low agreement threshold", "Model agreement below this value is flagged.", "float", 0.5, 0, 1),
    _spec("min_alt_length", "Minimum alt text length", "Minimum characters for alt text.", "int", 10, 1, 500),
    _spec("shoot_new_radius_m", "New shoot radius (m)", "Radius given to newly created shoots.", "float", 300.0, 10, 5000),
    _spec("shoot_time_window_hours", "Shoot time window (h)", "Max gap from a shoot's time range to join it; 0 disables.", "float", 12, 0, 720),
    _spec("related_shoot_radius_m", "Related shoot radius (m)", "Shoots within this distance are related.", "float", 500.0, 10, 20000),
    _spec("model_cluster_radius_m", "Model cluster radius (m)", "Single-linkage radius for 3D model photo sets.", "float", 75, 5, 1000),
    _spec("model_min_photos", "Model min photos", "Photos needed for a model set to be ready.", "int", 12, 3, 500),
    _spec("model_min_angle_coverage_deg", "Model min angle coverage (deg)", "Bearing coverage needed around the subject.", "float", 180, 0, 360),
    _spec("model_max_span_hours", "Model max capture span (h)", "Max capture time span within one set.", "float", 6, 0.1, 168),
    _spec("model_focal_tolerance_pct", "Model focal tolerance (%)", "Allowed focal length deviation from the set median.", "float", 10, 0, 100),
    _spec("progression_radius_m", "Progression radius (m)", "Radius for grouping captures into one location.", "float", 25, 1, 1000),
    _spec("progression_min_days_apart", "Visit min days apart", "Minimum days between distinct visits.", "int", 1, 0, 365),
    _spec("before_after_min_visits", "Before/after min visits", "Visits needed for before/after.", "int", 2, 2, 50),
    _spec("progression_min_visits", "Progression min visits", "Visits needed for site progression.", "int", 3, 2, 50),
    _spec("view_radius_m", "View radius (m)", "Radius for treating captures as the same view.", "float", 15, 1, 500),
    _spec("view_pitch_tolerance_deg", "View pitch tolerance (deg)", "Allowed gimbal pitch difference within a view.", "float", 15, 0, 90),
    _spec("view_subject_overlap", "View subject overlap", "Minimum subject Jaccard similarity within a view.", "float", 0.3, 0, 1),
    _spec("video_base_frames_short", "Video base frames (short)", "Base frames for short videos.", "int", 6, 2, 60),
    _spec("video_base_frames_long", "Video base frames (long)", "Base frames for long videos.", "int", 12, 2, 120),
    _spec("video_long_threshold_s", "Long video threshold (s)", "Videos this long use the long frame count.", "float", 120, 1, 3600),
    _spec("video_detail_interval_s", "Detail frame interval (s)", "Spacing of detail frames.", "float", 2, 0.25, 60),
    _spec("video_max_detail_frames", "Max detail frames", "Cap on detail frames per video.", "int", 60, 0, 500),
    _spec("video_thumb_edge", "Video thumbnail edge (px)", "Long edge of extracted frames.", "int", 320, 32, 2048),
]
_SPEC_BY_KEY = {s["key"]: s for s in SETTING_SPECS}
DEFAULT_SETTINGS = {s["key"]: s["default"] for s in SETTING_SPECS}


def _coerce(spec, value):
    if value is None:
        if spec.get("nullable"):
            return None
        raise ValueError(f"{spec['key']} must not be null")
    if isinstance(value, bool):
        raise ValueError(f"{spec['key']} must be a number")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"{spec['key']} must be a number")
    if not isinstance(value, (int, float)) or isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{spec['key']} must be a number")
    if spec["type"] == "int":
        if int(value) != value:
            raise ValueError(f"{spec['key']} must be an integer")
        value = int(value)
    else:
        value = float(value)
    if value < spec["min"] or value > spec["max"]:
        raise ValueError(f"{spec['key']} must be between {spec['min']} and {spec['max']}")
    return value


def validate_settings(d) -> dict:
    d = d or {}
    out = {}
    for spec in SETTING_SPECS:
        k = spec["key"]
        out[k] = _coerce(spec, d[k]) if k in d else spec["default"]
    return out


def load_settings(path=None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for spec in SETTING_SPECS:
        env = spec.get("env")
        if env and os.getenv(env) not in (None, ""):
            try:
                settings[spec["key"]] = _coerce(spec, os.getenv(env))
            except ValueError:
                pass
    if path is None:
        path = os.path.join(os.getenv("EXPORTS_DIR", "/storage/exports"), "review_settings.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return settings
    if isinstance(data, dict):
        for k, v in data.items():
            spec = _SPEC_BY_KEY.get(k)
            if spec is None:
                continue
            try:
                settings[k] = _coerce(spec, v)
            except ValueError:
                pass
    return settings


# ---------------------------------------------------------------- helpers
def _dt(v):
    """Parse datetime/ISO string to naive UTC datetime, or None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        d = datetime(v.year, v.month, v.day)
    else:
        s = str(v).strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            try:
                d = datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
            except ValueError:
                return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def _gps(a):
    return has_gps(a)


def _dist(a, b):
    return haversine_m(float(a["latitude"]), float(a["longitude"]), float(b["latitude"]), float(b["longitude"]))


def _mean_center(items):
    pts = [a for a in items if _gps(a)]
    if not pts:
        return None
    return {"lat": sum(float(a["latitude"]) for a in pts) / len(pts), "lon": sum(float(a["longitude"]) for a in pts) / len(pts)}


def _hash6(parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:6].upper()


def _final(a):
    fo = a.get("final_output")
    return fo if isinstance(fo, dict) else {}


def _subjects(a):
    subs = (_final(a).get("content") or {}).get("primary_subjects") or []
    out = set()
    for s in subs:
        if isinstance(s, dict):
            s = s.get("name") or s.get("label")
        if s:
            out.add(str(s).strip().lower())
    return out


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, i, j):
        self.p[self.find(i)] = self.find(j)


def _single_linkage(items, radius_m):
    uf = _UF(len(items))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _dist(items[i], items[j]) <= radius_m:
                uf.union(i, j)
    groups = {}
    for i, a in enumerate(items):
        groups.setdefault(uf.find(i), []).append(a)
    return list(groups.values())


def _scope(assets, shoot_id, seed_asset_id, center, radius_m, default_radius, filter_by_shoot=True):
    items = list(assets or [])
    if shoot_id is not None and filter_by_shoot:
        items = [a for a in items if a.get("shoot_id") == shoot_id]
    ref = None
    if seed_asset_id is not None:
        seed = next((a for a in (assets or []) if a.get("asset_id") == seed_asset_id), None)
        if seed is None or not _gps(seed):
            return []
        ref = (float(seed["latitude"]), float(seed["longitude"]))
    elif center is not None:
        ref = (float(center[0]), float(center[1]))
    if ref is not None:
        r = float(radius_m) if radius_m is not None else float(default_radius)
        items = [a for a in items if _gps(a) and haversine_m(ref[0], ref[1], float(a["latitude"]), float(a["longitude"])) <= r]
    return items


# ---------------------------------------------------------------- shot classification
def classify_shot_from_exif(telemetry: dict) -> dict:
    t = telemetry or {}
    alt = _num(t.get("altitude_m"))
    pitch = _num(t.get("gimbal_pitch_deg"))
    basis = []
    if pitch is not None:
        if pitch <= -80:
            basis.append(f"gimbal pitch {pitch:g} deg is near straight down")
            return {"shot_type": "aerial_nadir", "camera_perspective": "nadir", "basis": basis}
        if pitch <= -20:
            if alt is not None and alt < 15:
                basis.append(f"gimbal pitch {pitch:g} deg at low altitude {alt:g} m")
                return {"shot_type": "aerial_low_oblique", "camera_perspective": "oblique", "basis": basis}
            basis.append(f"gimbal pitch {pitch:g} deg is oblique")
            return {"shot_type": "aerial_oblique", "camera_perspective": "oblique", "basis": basis}
        if alt is not None and alt >= 15:
            basis.append(f"near-horizontal pitch {pitch:g} deg at altitude {alt:g} m")
            return {"shot_type": "aerial_low_oblique", "camera_perspective": "oblique", "basis": basis}
        basis.append(f"near-horizontal pitch {pitch:g} deg at low/unknown altitude")
        return {"shot_type": "ground_level", "camera_perspective": "ground", "basis": basis}
    if alt is not None and alt > 15:
        basis.append(f"altitude {alt:g} m suggests aerial; no gimbal pitch")
        return {"shot_type": "unknown", "camera_perspective": "unknown", "basis": basis}
    if alt is not None and alt <= 3:
        basis.append(f"altitude {alt:g} m and no gimbal pitch suggest ground level")
        return {"shot_type": "ground_level", "camera_perspective": "ground", "basis": basis}
    basis.append("insufficient telemetry")
    return {"shot_type": "unknown", "camera_perspective": "unknown", "basis": basis}


# ---------------------------------------------------------------- shoots
def make_shoot_id(lat, lon, captured_at) -> str:
    d = _dt(captured_at)
    ds = d.strftime("%y%m%d") if d else "000000"
    if lat is None or lon is None or (float(lat) == 0.0 and float(lon) == 0.0):
        return f"SH{ds}-NOGPS"
    h = hashlib.sha1(f"{round(float(lat), 4):.4f},{round(float(lon), 4):.4f},{ds}".encode()).hexdigest()[:4].upper()
    return f"SH{ds}-{h}"


def _time_ok(asset_time, shoot, window_h):
    if not window_h or asset_time is None:
        return True
    first = _dt(shoot.get("first_captured_at"))
    last = _dt(shoot.get("last_captured_at")) or first
    if first is None:
        return True
    w = timedelta(hours=float(window_h))
    return first - w <= asset_time <= max(first, last) + w


def assign_shoot(asset: dict, shoots: list, settings: dict) -> dict:
    shoots = shoots or []
    when = _dt(asset.get("captured_at"))
    if not _gps(asset):
        sid = make_shoot_id(None, None, when)
        if any(s.get("shoot_id") == sid for s in shoots):
            return {"shoot_id": sid, "is_new": False, "new_shoot": None, "distance_m": None}
        new = {"shoot_id": sid, "project_name": sid, "centroid_lat": None, "centroid_lon": None,
               "radius_meters": None, "first_captured_at": when}
        return {"shoot_id": sid, "is_new": True, "new_shoot": new, "distance_m": None}
    window = settings.get("shoot_time_window_hours", 12)
    best, best_d = None, None
    for s in shoots:
        if s.get("centroid_lat") is None or s.get("centroid_lon") is None:
            continue
        d = haversine_m(float(s["centroid_lat"]), float(s["centroid_lon"]), float(asset["latitude"]), float(asset["longitude"]))
        radius = s.get("radius_meters")
        if radius is None:
            radius = settings.get("shoot_new_radius_m", 300)
        if d <= float(radius) and _time_ok(when, s, window) and (best_d is None or d < best_d):
            best, best_d = s, d
    if best is not None:
        return {"shoot_id": best["shoot_id"], "is_new": False, "new_shoot": None, "distance_m": best_d}
    sid = make_shoot_id(asset["latitude"], asset["longitude"], when)
    existing = {s.get("shoot_id") for s in shoots}
    base, n = sid, 2
    while sid in existing:
        sid = f"{base}-{n}"
        n += 1
    new = {"shoot_id": sid, "project_name": sid, "centroid_lat": float(asset["latitude"]),
           "centroid_lon": float(asset["longitude"]), "radius_meters": float(settings.get("shoot_new_radius_m", 300)),
           "first_captured_at": when}
    return {"shoot_id": sid, "is_new": True, "new_shoot": new, "distance_m": 0.0}


def related_shoots(shoot: dict, shoots: list, settings: dict) -> list:
    if shoot.get("centroid_lat") is None or shoot.get("centroid_lon") is None:
        return []
    limit = settings.get("related_shoot_radius_m", 500)
    mine = _dt(shoot.get("first_captured_at"))
    out = []
    for s in shoots or []:
        if s.get("shoot_id") == shoot.get("shoot_id") or s.get("centroid_lat") is None or s.get("centroid_lon") is None:
            continue
        d = haversine_m(float(shoot["centroid_lat"]), float(shoot["centroid_lon"]), float(s["centroid_lat"]), float(s["centroid_lon"]))
        if d > limit:
            continue
        theirs = _dt(s.get("first_captured_at"))
        days = (theirs.date() - mine.date()).days if mine and theirs else None
        if days is None or days == 0:
            rel = "nearby"
        else:
            rel = "same_place_earlier" if days < 0 else "same_place_later"
        out.append({"shoot_id": s["shoot_id"], "distance_m": d, "days_apart": abs(days) if days is not None else None, "relation": rel})
    out.sort(key=lambda r: (r["distance_m"], r["shoot_id"]))
    return out


def split_shoot(assets, group_a_ids, group_b_ids, shoot_id, settings, new_shoot_id=None) -> dict:
    a_ids, b_ids = set(group_a_ids), set(group_b_ids)
    by_id = {a["asset_id"]: a for a in assets}
    ga = [by_id[i] for i in a_ids if i in by_id]
    gb = [by_id[i] for i in b_ids if i in by_id]

    if new_shoot_id is None:
        dated = sorted((a for a in gb if _dt(a.get("captured_at"))), key=lambda a: _dt(a["captured_at"]))
        first = dated[0] if dated else (sorted(gb, key=lambda a: a["asset_id"])[0] if gb else {})
        new_shoot_id = make_shoot_id(first.get("latitude"), first.get("longitude"), first.get("captured_at")) if first else make_shoot_id(None, None, None)
        if new_shoot_id == shoot_id:
            new_shoot_id += "-2"

    def describe(sid, group):
        c = _mean_center(group)
        if c is None:
            return {"shoot_id": sid, "centroid_lat": None, "centroid_lon": None, "radius_meters": None}
        r = max([haversine_m(c["lat"], c["lon"], float(a["latitude"]), float(a["longitude"])) for a in group if _gps(a)])
        return {"shoot_id": sid, "centroid_lat": c["lat"], "centroid_lon": c["lon"], "radius_meters": max(r * 1.2, 25.0)}

    keep, new = describe(shoot_id, ga), describe(new_shoot_id, gb)
    assignments = {i: shoot_id for i in a_ids if i in by_id}
    assignments.update({i: new_shoot_id for i in b_ids if i in by_id})
    unassigned = []
    for a in assets:
        if a["asset_id"] in assignments or a.get("shoot_id") != shoot_id:
            continue
        if not _gps(a) or keep["centroid_lat"] is None and new["centroid_lat"] is None:
            unassigned.append(a["asset_id"])
            continue
        da = _dist(a, {"latitude": keep["centroid_lat"], "longitude": keep["centroid_lon"]}) if keep["centroid_lat"] is not None else math.inf
        db = _dist(a, {"latitude": new["centroid_lat"], "longitude": new["centroid_lon"]}) if new["centroid_lat"] is not None else math.inf
        assignments[a["asset_id"]] = shoot_id if da <= db else new_shoot_id
    return {"keep": keep, "new": new, "assignments": assignments, "unassigned": unassigned}


# ---------------------------------------------------------------- identifiers
_KIND_CODES = {"still": "PHO", "video": "VID", "3d_model": "MDL", "model": "MDL"}
_IDENT_KEYS = ("prefix", "shoot_id", "kind_code", "content_code", "scene", "stem")


def _content_code(record):
    record = record or {}
    subs = (record.get("content") or {}).get("primary_subjects") or []
    raw = None
    if subs:
        raw = subs[0]
        if isinstance(raw, dict):
            raw = raw.get("name") or raw.get("label")
    if not raw:
        raw = (record.get("classification") or {}).get("media_genre")
    code = re.sub(r"[^A-Z0-9]", "", str(raw or "").upper())
    if not code:
        return "GEN"
    if len(code) > 6:
        devowelled = code[:1] + re.sub(r"[AEIOU]", "", code[1:])
        code = devowelled[:6]
    return code


def build_identifier(asset: dict, record: dict, shoot_id: str, scene_code=None):
    stem = asset.get("unique_stem")
    if not stem:
        tail = re.split(r"[-_]", str(asset.get("asset_id", "")))[-1]
        stem = tail[:8]
    comps = {
        "prefix": "BAM",
        "shoot_id": shoot_id,
        "kind_code": _KIND_CODES.get(asset.get("media_kind"), "PHO"),
        "content_code": _content_code(record),
        "scene": f"G{scene_code}" if scene_code else "S",
        "stem": stem,
    }
    return "-".join(str(comps[k]) for k in _IDENT_KEYS), comps


def can_modify_identifier(old_components, new_components) -> bool:
    old, new = old_components or {}, new_components or {}
    keys = (set(old) | set(new)) - {"shoot_id"}
    return all(old.get(k) == new.get(k) for k in keys)


def relocate_identifier(components, new_shoot_id):
    comps = dict(components)
    comps["shoot_id"] = new_shoot_id
    return "-".join(str(comps[k]) for k in _IDENT_KEYS if k in comps), comps


# ---------------------------------------------------------------- 3D model candidates
def _angle_coverage(center, photos):
    bearings = []
    for a in photos:
        if haversine_m(center["lat"], center["lon"], float(a["latitude"]), float(a["longitude"])) < 0.5:
            continue
        la1, la2 = math.radians(center["lat"]), math.radians(float(a["latitude"]))
        dl = math.radians(float(a["longitude"]) - center["lon"])
        y = math.sin(dl) * math.cos(la2)
        x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
        bearings.append(math.degrees(math.atan2(y, x)) % 360)
    if len(bearings) < 2:
        return 0.0
    bearings.sort()
    gaps = [bearings[i + 1] - bearings[i] for i in range(len(bearings) - 1)] + [bearings[0] + 360 - bearings[-1]]
    return max(0.0, 360 - max(gaps))


def _best_time_window(items, max_hours):
    dated = sorted((a for a in items if _dt(a.get("captured_at"))), key=lambda a: _dt(a["captured_at"]))
    undated = [a for a in items if not _dt(a.get("captured_at"))]
    best, lo = [], 0
    span = timedelta(hours=max_hours)
    for hi in range(len(dated)):
        while _dt(dated[hi]["captured_at"]) - _dt(dated[lo]["captured_at"]) > span:
            lo += 1
        if hi - lo + 1 > len(best):
            best = dated[lo:hi + 1]
    return best + undated


def find_model_candidates(assets, settings, shoot_id=None, seed_asset_id=None, center=None, radius_m=None) -> list:
    items = _scope(assets, shoot_id, seed_asset_id, center, radius_m, settings["model_cluster_radius_m"])
    items = [a for a in items if a.get("media_kind", "still") == "still" and _gps(a)
             and (_final(a).get("photogrammetry") or {}).get("suitable_for_model") is not False]
    out = []
    for cluster in _single_linkage(items, settings["model_cluster_radius_m"]):
        reasons = []
        focals = [_num(a.get("focal_length")) for a in cluster]
        known = [f for f in focals if f]
        if known:
            med = statistics.median(known)
            tol = med * settings["model_focal_tolerance_pct"] / 100
            kept = [a for a, f in zip(cluster, focals) if f is None or abs(f - med) <= tol]
            if len(kept) < len(cluster):
                reasons.append(f"{len(cluster) - len(kept)} photo(s) excluded for differing focal length")
            cluster = kept
        windowed = _best_time_window(cluster, settings["model_max_span_hours"])
        if len(windowed) < len(cluster):
            reasons.append(f"{len(cluster) - len(windowed)} photo(s) excluded for exceeding capture span")
        cluster = windowed
        if len(cluster) < 3:
            continue
        c = _mean_center(cluster)
        cov = _angle_coverage(c, cluster)
        times = [_dt(a.get("captured_at")) for a in cluster if _dt(a.get("captured_at"))]
        span_h = (max(times) - min(times)).total_seconds() / 3600 if times else 0.0
        n = len(cluster)
        need_n = max(0, settings["model_min_photos"] - n)
        need_a = max(0.0, settings["model_min_angle_coverage_deg"] - cov)
        missing = []
        if need_n:
            missing.append(f"need {need_n} more photo{'s' if need_n != 1 else ''}")
        if need_a > 0:
            missing.append(f"need {need_a:.0f} more degrees of angle coverage")
        ready = not missing
        if ready:
            reasons.append(f"{n} photos covering {cov:.0f} degrees")
        score_n = min(1.0, n / settings["model_min_photos"])
        mc = settings["model_min_angle_coverage_deg"]
        score_a = min(1.0, cov / mc) if mc > 0 else 1.0
        ids = sorted(a["asset_id"] for a in cluster)
        out.append({
            "set_key": "MS-" + _hash6(ids), "asset_ids": ids, "ready": ready,
            "score": round((score_n + score_a) / 2, 3),
            "shoot_ids": sorted({a.get("shoot_id") for a in cluster if a.get("shoot_id")}),
            "center": c, "reasons": reasons, "missing": missing,
            "stats": {"photo_count": n, "angle_coverage_deg": round(cov, 1), "span_hours": round(span_h, 3)},
        })
    out.sort(key=lambda s: (-s["score"], s["set_key"]))
    return out


# ---------------------------------------------------------------- views / progression
def group_views(assets, settings) -> list:
    items = [a for a in (assets or []) if _gps(a)]
    uf = _UF(len(items))
    subs = [_subjects(a) for a in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _dist(items[i], items[j]) > settings["view_radius_m"]:
                continue
            pi, pj = _num(items[i].get("gimbal_pitch")), _num(items[j].get("gimbal_pitch"))
            if pi is not None and pj is not None and abs(pi - pj) > settings["view_pitch_tolerance_deg"]:
                continue
            if subs[i] and subs[j]:
                if len(subs[i] & subs[j]) / len(subs[i] | subs[j]) < settings["view_subject_overlap"]:
                    continue
            uf.union(i, j)
    groups = {}
    for i, a in enumerate(items):
        groups.setdefault(uf.find(i), []).append(a)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        ids = sorted(a["asset_id"] for a in g)
        out.append({"view_key": "VW-" + _hash6(ids), "asset_ids": ids, "center": _mean_center(g)})
    out.sort(key=lambda v: v["view_key"])
    return out


def _visits(cluster, min_days):
    dated = sorted((a for a in cluster if _dt(a.get("captured_at"))), key=lambda a: (_dt(a["captured_at"]), a["asset_id"]))
    visits = []
    for a in dated:
        d = _dt(a["captured_at"]).date()
        if visits and (d - visits[-1]["_date"]).days < min_days:
            visits[-1]["asset_ids"].append(a["asset_id"])
        else:
            visits.append({"_date": d, "asset_ids": [a["asset_id"]]})
    return visits


def find_progression(assets, settings, shoot_id=None, seed_asset_id=None, center=None, radius_m=None) -> list:
    pool = [a for a in (assets or []) if _gps(a)]
    r = settings["progression_radius_m"]
    if shoot_id is not None:
        anchors = [a for a in pool if a.get("shoot_id") == shoot_id]
        items = [a for a in pool if any(_dist(a, s) <= r for s in anchors)]
    else:
        items = pool
    if seed_asset_id is not None or center is not None:
        scoped = {a["asset_id"] for a in _scope(pool, None, seed_asset_id, center, radius_m, r)}
        items = [a for a in items if a["asset_id"] in scoped]
    by_id = {a["asset_id"]: a for a in items}
    out = []
    for cluster in _single_linkage(items, r):
        visits = _visits(cluster, settings["progression_min_days_apart"])
        if not visits:
            continue
        n = len(visits)
        indicator = "site_progression" if n >= settings["progression_min_visits"] else (
            "before_after" if n >= settings["before_after_min_visits"] else "none")
        visit_of = {i: k for k, v in enumerate(visits) for i in v["asset_ids"]}
        view_groups = []
        for vg in group_views(cluster, settings):
            buckets = {}
            for i in vg["asset_ids"]:
                if i in visit_of:
                    buckets.setdefault(visit_of[i], []).append(i)
            ks = sorted(buckets)
            if not ks:
                continue
            label = next((_final(by_id[i]).get("view", {}).get("label") for i in vg["asset_ids"]
                          if (_final(by_id[i]).get("view") or {}).get("label")), None) or vg["view_key"]
            pair_idx = [(ks[0], ks[-1])] if len(ks) >= 2 else []
            pair_idx += list(zip(ks, ks[1:]))
            pairs, seen = [], set()
            for x, y in pair_idx:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                pairs.append({"before_asset_id": buckets[x][0], "after_asset_id": buckets[y][0],
                              "days_apart": (visits[y]["_date"] - visits[x]["_date"]).days})
            view_groups.append({
                "view_key": vg["view_key"], "label": label,
                "visits": [{"date": visits[k]["_date"].isoformat(), "asset_ids": buckets[k]} for k in ks],
                "pairs": pairs,
            })
        ids = sorted(a["asset_id"] for a in cluster)
        out.append({
            "location_key": "LC-" + _hash6(ids), "center": _mean_center(cluster),
            "visits": [{"date": v["_date"].isoformat(), "asset_ids": v["asset_ids"]} for v in visits],
            "indicator": indicator, "view_groups": view_groups,
        })
    out.sort(key=lambda l: l["location_key"])
    return out


def compute_indicators(assets, settings) -> dict:
    res = {a["asset_id"]: {"model_candidate": None, "progression": None, "view_group": None} for a in (assets or [])}
    for s in find_model_candidates(assets, settings):
        for i in s["asset_ids"]:
            cur = res[i]["model_candidate"]
            if cur is None or (s["ready"], s["score"]) > (cur["ready"], cur["_score"]):
                res[i]["model_candidate"] = {"set_key": s["set_key"], "set_size": len(s["asset_ids"]), "ready": s["ready"], "_score": s["score"]}
    for v in res.values():
        if v["model_candidate"]:
            v["model_candidate"].pop("_score")
    for loc in find_progression(assets, settings):
        for v in loc["visits"]:
            for i in v["asset_ids"]:
                res[i]["progression"] = {"location_key": loc["location_key"], "indicator": loc["indicator"], "visit_count": len(loc["visits"])}
    for vg in group_views(assets, settings):
        for i in vg["asset_ids"]:
            res[i]["view_group"] = {"view_key": vg["view_key"], "size": len(vg["asset_ids"])}
    return res


# ---------------------------------------------------------------- video
def plan_video_frames(duration_s, settings) -> dict:
    d = float(duration_s or 0)
    if d <= 0:
        return {"duration_s": 0.0, "base": [], "detail": []}
    n = settings["video_base_frames_long"] if d >= settings["video_long_threshold_s"] else settings["video_base_frames_short"]
    n = max(2, min(n, int(d / 0.5) + 1))
    edge = min(0.1, d * 0.05)
    t0, t1 = edge, d - edge
    pts = [t0 + (t1 - t0) * k / (n - 1) for k in range(n)]
    cap = d - 0.001
    base = sorted({round(min(max(p, 0.0), cap), 3) for p in pts})
    detail = []
    max_d = settings["video_max_detail_frames"]
    if max_d > 0:
        step = max(float(settings["video_detail_interval_s"]), d / (max_d + 1))
        k = 1
        while k * step < d:
            t = round(k * step, 3)
            if t < d and all(abs(t - b) >= 0.25 for b in base):
                detail.append(t)
            k += 1
    return {"duration_s": d, "base": base, "detail": detail}


# ---------------------------------------------------------------- records
def diff_records(before, after) -> list:
    out = []

    def walk(b, a, path):
        if isinstance(b, dict) and isinstance(a, dict):
            for k in list(b) + [k for k in a if k not in b]:
                walk(b.get(k), a.get(k), f"{path}.{k}" if path else str(k))
        elif b != a:
            out.append({"path": path, "before": b, "after": a})

    walk(before or {}, after or {}, "")
    return out


_LOCKED = ("identifier", "identifier_components", "schema_version", "media_kind")


def merge_human_edit(before, after):
    merged = copy.deepcopy(after) if isinstance(after, dict) else {}
    for k in _LOCKED:
        if isinstance(before, dict) and k in before:
            merged[k] = copy.deepcopy(before[k])
    return merged
