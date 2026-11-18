"""DB-only finalisation: shoot assignment, locked identifiers, indicators, splits, training examples.

All functions take a psycopg2 connection (RealDictCursor rows). Committing is the caller's job.
No model is ever called from here."""
import copy
import logging

try:
    from psycopg2.extras import Json, RealDictCursor
except ImportError:  # psycopg2 missing or stubbed (pure-logic unit tests)
    RealDictCursor = None

    class Json:
        def __init__(self, adapted, dumps=None):
            self.adapted = adapted

from server import media_intel as mi
from server.review_logic import haversine_m, has_gps

logger = logging.getLogger("bam.finalize")

ELIGIBLE_STATES = ("PENDING_HUMAN", "INGESTED")
_ASSET_COLS = ("asset_id, unique_stem, media_kind, shoot_id, captured_at, latitude, longitude, sensor_model, "
               "focal_length, gimbal_pitch, describes, alt, service_tags, workflow_state, identifier, "
               "identifier_components, identifier_locked, final_output, scene_group, source_path, original_sha256")
_UPDATABLE = {"shoot_id", "scene_group", "identifier", "identifier_components", "identifier_locked", "final_output",
              "describes", "alt", "service_tags", "indicators"}
_JSON_COLS = {"identifier_components", "final_output", "service_tags", "indicators"}
TRAINING_SOURCES = ("edit", "flag", "feedback", "rework")


class FinalizeError(RuntimeError):
    pass


class IdentifierCollision(FinalizeError):
    pass


class _DB:
    """Thin SQL layer (swapped for an in-memory fake in unit tests)."""

    def __init__(self, conn):
        self.conn = conn

    def _cur(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)

    def _all(self, sql, params=()):
        with self._cur() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _exec(self, sql, params=()):
        with self._cur() as cur:
            cur.execute(sql, params)

    def asset(self, asset_id):
        rows = self._all(f"SELECT {_ASSET_COLS} FROM assets WHERE asset_id = %s", (asset_id,))
        return rows[0] if rows else None

    def assets_in_shoot(self, shoot_id):
        return self._all(f"SELECT {_ASSET_COLS} FROM assets WHERE shoot_id = %s ORDER BY asset_id", (shoot_id,))

    def all_assets_with_output(self):
        return self._all(f"SELECT {_ASSET_COLS}, indicators FROM assets WHERE final_output IS NOT NULL ORDER BY asset_id")

    def list_shoots(self):
        rows = self._all(
            "SELECT s.shoot_id, s.project_name, s.centroid_lat, s.centroid_lon, s.radius_meters, s.locked, "
            "LEAST(s.first_captured_at, m.mn) AS first_captured_at, GREATEST(s.last_captured_at, m.mx) AS last_captured_at "
            "FROM shoots s LEFT JOIN (SELECT shoot_id, MIN(captured_at) AS mn, MAX(captured_at) AS mx FROM assets "
            "GROUP BY shoot_id) m ON m.shoot_id = s.shoot_id ORDER BY s.shoot_id")
        for r in rows:
            if r["shoot_id"].endswith("-NOGPS") or (not r["centroid_lat"] and not r["centroid_lon"]):
                r["centroid_lat"] = r["centroid_lon"] = None
        return rows

    def insert_shoot(self, shoot):
        lat, lon = shoot.get("centroid_lat"), shoot.get("centroid_lon")
        self._exec(
            "INSERT INTO shoots (shoot_id, project_name, centroid_lat, centroid_lon, radius_meters, "
            "first_captured_at, last_captured_at, locked) VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE)",
            (shoot["shoot_id"], shoot.get("project_name") or f"Shoot {shoot['shoot_id']}",
             lat if lat is not None else 0.0, lon if lon is not None else 0.0, shoot.get("radius_meters") or 300.0,
             shoot.get("first_captured_at"), shoot.get("last_captured_at") or shoot.get("first_captured_at")))

    def update_shoot_geo(self, shoot_id, lat, lon, radius):
        self._exec("UPDATE shoots SET centroid_lat = %s, centroid_lon = %s, radius_meters = %s WHERE shoot_id = %s",
                   (lat if lat is not None else 0.0, lon if lon is not None else 0.0, radius or 300.0, shoot_id))

    def widen_shoot_range(self, shoot_id, when):
        if when is None:
            return
        self._exec("UPDATE shoots SET first_captured_at = LEAST(COALESCE(first_captured_at, %s), %s), "
                   "last_captured_at = GREATEST(COALESCE(last_captured_at, %s), %s) WHERE shoot_id = %s",
                   (when, when, when, when, shoot_id))

    def reset_shoot_range(self, shoot_id):
        self._exec("UPDATE shoots SET first_captured_at = (SELECT MIN(captured_at) FROM assets WHERE shoot_id = %s), "
                   "last_captured_at = (SELECT MAX(captured_at) FROM assets WHERE shoot_id = %s) WHERE shoot_id = %s",
                   (shoot_id, shoot_id, shoot_id))

    def update_asset(self, asset_id, **fields):
        bad = set(fields) - _UPDATABLE
        if bad or not fields:
            raise ValueError(f"bad asset fields: {sorted(bad)}")
        cols = list(fields)
        vals = [Json(fields[c], dumps=_dumps) if c in _JSON_COLS else fields[c] for c in cols]
        self._exec(f"UPDATE assets SET {', '.join(c + ' = %s' for c in cols)} WHERE asset_id = %s", vals + [asset_id])

    def identifier_owner(self, identifier):
        rows = self._all("SELECT asset_id FROM assets WHERE identifier = %s", (identifier,))
        return rows[0]["asset_id"] if rows else None

    def replace_links(self, shoot_id, links):
        self._exec("DELETE FROM shoot_links WHERE shoot_id = %s", (shoot_id,))
        for l in links:
            self._exec("INSERT INTO shoot_links (shoot_id, related_shoot_id, distance_m, days_apart, relation) "
                       "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (shoot_id, related_shoot_id) DO UPDATE SET "
                       "distance_m = EXCLUDED.distance_m, days_apart = EXCLUDED.days_apart, relation = EXCLUDED.relation",
                       (shoot_id, l["shoot_id"], l.get("distance_m"), l.get("days_apart"), l.get("relation")))


def _dumps(obj):
    import json
    return json.dumps(obj, default=str)


def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def _final(a):
    fo = a.get("final_output")
    return fo if isinstance(fo, dict) else {}


def _sort_key(a):
    d = mi._dt(a.get("captured_at"))
    return (d is None, d or 0, str(a.get("asset_id")))


def _eligible(a):
    return (a is not None and not a.get("identifier_locked") and a.get("workflow_state") in ELIGIBLE_STATES
            and isinstance(a.get("final_output"), dict))


def _scene_code(asset, shoot_assets, settings):
    """(code, neighbours): reuse a neighbour's group, else derive from the earliest similar photo's stem."""
    radius = float(settings.get("view_radius_m", 15))
    neigh = []
    if has_gps(asset):
        for o in shoot_assets:
            if o["asset_id"] == asset["asset_id"] or not has_gps(o):
                continue
            if haversine_m(float(asset["latitude"]), float(asset["longitude"]), float(o["latitude"]), float(o["longitude"])) <= radius:
                neigh.append(o)
    for o in sorted(neigh, key=_sort_key):
        if o.get("scene_group"):
            return o["scene_group"]
    if not neigh:
        return None
    earliest = min(neigh + [asset], key=lambda a: (_sort_key(a)[:2], str(a.get("unique_stem"))))
    stem = str(earliest.get("unique_stem") or earliest.get("asset_id"))
    return stem[:4]


def _related_payload(shoot, shoots, settings):
    return [{"shoot_id": r["shoot_id"], "distance_m": round(r["distance_m"], 1), "days_apart": r["days_apart"],
             "relation": r["relation"]} for r in mi.related_shoots(shoot, shoots, settings)]


def _refresh_related(db, shoot_ids, settings):
    shoots = db.list_shoots()
    by_id = {s["shoot_id"]: s for s in shoots}
    for sid in dict.fromkeys(shoot_ids):
        shoot = by_id.get(sid)
        if shoot is None:
            continue
        rel = _related_payload(shoot, shoots, settings)
        db.replace_links(sid, rel)
        for a in db.assets_in_shoot(sid):
            fo = _final(a)
            if not fo:
                continue
            fo = copy.deepcopy(fo)
            fo.setdefault("shoot", {})["shoot_id"] = sid
            fo["shoot"]["related_shoots"] = rel
            if fo != a["final_output"]:
                db.update_asset(a["asset_id"], final_output=fo)


def _check_unique(db, identifier, asset_id):
    owner = db.identifier_owner(identifier)
    if owner is not None and owner != asset_id:
        raise IdentifierCollision(f"identifier {identifier} already belongs to asset {owner} (cannot assign to {asset_id})")


def finalize_assets(conn, asset_ids, settings=None):
    settings = settings or mi.load_settings()
    db = _DB(conn)
    finalized, skipped, created = [], [], []
    todo = []
    for aid in dict.fromkeys(asset_ids):
        a = db.asset(aid)
        if _eligible(a):
            todo.append(a)
        else:
            skipped.append(aid)
    todo.sort(key=_sort_key)

    # Phase 1: shoot membership for every asset, so scene grouping sees the whole batch.
    shoots = db.list_shoots()
    for a in todo:
        res = mi.assign_shoot(a, shoots, settings)
        if res["is_new"]:
            new = dict(res["new_shoot"])
            new["project_name"] = f"Shoot {new['shoot_id']}"
            new["last_captured_at"] = new.get("first_captured_at")
            db.insert_shoot(new)
            shoots.append(new)
            created.append(new["shoot_id"])
        sid = res["shoot_id"]
        when = mi._dt(a.get("captured_at"))
        db.update_asset(a["asset_id"], shoot_id=sid)
        db.widen_shoot_range(sid, when)
        for s in shoots:
            if s["shoot_id"] == sid and when is not None:
                f, l = mi._dt(s.get("first_captured_at")), mi._dt(s.get("last_captured_at"))
                s["first_captured_at"] = min(f, when) if f else when
                s["last_captured_at"] = max(l, when) if l else when
        a["shoot_id"] = sid

    # Phase 2: scene groups + identifiers.
    touched = []
    for a in todo:
        sid = a["shoot_id"]
        record = copy.deepcopy(_final(a))
        scene = _scene_code(a, db.assets_in_shoot(sid), settings)
        identifier, comps = mi.build_identifier(a, record, sid, scene)
        _check_unique(db, identifier, a["asset_id"])
        record["identifier"] = identifier
        record["identifier_components"] = comps
        record.setdefault("shoot", {})["shoot_id"] = sid
        fields = {"identifier": identifier, "identifier_components": comps, "identifier_locked": True,
                  "final_output": record, "scene_group": scene}
        content = record.get("content") or {}
        if _empty(a.get("describes")) and not _empty(content.get("description")):
            fields["describes"] = content["description"]
        if _empty(a.get("alt")) and not _empty(content.get("alt_text")):
            fields["alt"] = content["alt_text"]
        if _empty(a.get("service_tags")) and not _empty(content.get("tags")):
            fields["service_tags"] = content["tags"]
        db.update_asset(a["asset_id"], **fields)
        finalized.append(a["asset_id"])
        touched.append(sid)
        logger.info("Asset finalized; asset_id=%s identifier=%s", a["asset_id"], identifier)

    _refresh_related(db, touched, settings)
    return {"finalized": finalized, "skipped": skipped, "shoots_created": created}


def refresh_indicators(conn, settings=None):
    settings = settings or mi.load_settings()
    db = _DB(conn)
    assets = db.all_assets_with_output()
    if not assets:
        return 0
    result = mi.compute_indicators(assets, settings)
    updated = 0
    for a in assets:
        ind = result.get(a["asset_id"])
        fo = copy.deepcopy(_final(a))
        if ind is None or (a.get("indicators") == ind and fo.get("indicators") == ind):
            continue
        fo["indicators"] = ind
        db.update_asset(a["asset_id"], indicators=ind, final_output=fo)
        updated += 1
    return updated


def _relocate_asset(db, asset, new_shoot_id):
    """Change ONLY the shoot component of a locked identifier (the one allowed identifier change)."""
    if asset.get("shoot_id") == new_shoot_id:
        return False
    fields = {"shoot_id": new_shoot_id}
    comps = asset.get("identifier_components")
    if asset.get("identifier") and isinstance(comps, dict) and comps:
        identifier, new_comps = mi.relocate_identifier(comps, new_shoot_id)
        if not mi.can_modify_identifier(comps, new_comps):
            raise FinalizeError(f"refusing to change identifier components other than shoot_id for {asset['asset_id']}")
        _check_unique(db, identifier, asset["asset_id"])
        fo = copy.deepcopy(_final(asset))
        fo["identifier"] = identifier
        fo["identifier_components"] = new_comps
        fo.setdefault("shoot", {})["shoot_id"] = new_shoot_id
        fields.update(identifier=identifier, identifier_components=new_comps, final_output=fo)
    elif isinstance(asset.get("final_output"), dict):
        fo = copy.deepcopy(asset["final_output"])
        fo.setdefault("shoot", {})["shoot_id"] = new_shoot_id
        fields["final_output"] = fo
    db.update_asset(asset["asset_id"], **fields)
    return True


def apply_split(conn, shoot_id, group_a, group_b, settings=None, project_name_b=None):
    settings = settings or mi.load_settings()
    db = _DB(conn)
    shoots = db.list_shoots()
    if not any(s["shoot_id"] == shoot_id for s in shoots):
        raise FinalizeError(f"unknown shoot {shoot_id}")
    members = db.assets_in_shoot(shoot_id)
    member_ids = {a["asset_id"] for a in members}
    if not (set(group_b) & member_ids):
        raise FinalizeError("group_b has no assets in the shoot")
    existing = {s["shoot_id"] for s in shoots}
    res = mi.split_shoot(members, group_a, group_b, shoot_id, settings)
    new_id = res["new"]["shoot_id"]
    if new_id in existing:
        base, n = new_id, 2
        while f"{base}-{n}" in existing:
            n += 1
        res = mi.split_shoot(members, group_a, group_b, shoot_id, settings, new_shoot_id=f"{base}-{n}")
    keep, new = res["keep"], res["new"]
    by_id = {a["asset_id"]: a for a in members}
    moved = []
    when = [mi._dt(by_id[i].get("captured_at")) for i, s in res["assignments"].items() if s == new["shoot_id"]]
    when = [w for w in when if w]
    db.insert_shoot({"shoot_id": new["shoot_id"], "project_name": project_name_b or f"Shoot {new['shoot_id']}",
                     "centroid_lat": new["centroid_lat"], "centroid_lon": new["centroid_lon"],
                     "radius_meters": new["radius_meters"],
                     "first_captured_at": min(when) if when else None, "last_captured_at": max(when) if when else None})
    db.update_shoot_geo(shoot_id, keep["centroid_lat"], keep["centroid_lon"], keep["radius_meters"])
    for aid, target in res["assignments"].items():
        if _relocate_asset(db, by_id[aid], target):
            moved.append(aid)
    db.reset_shoot_range(shoot_id)
    db.reset_shoot_range(new["shoot_id"])
    _refresh_related(db, [shoot_id, new["shoot_id"]], settings)
    return {"keep": keep, "new": new, "moved": moved, "unassigned": res["unassigned"]}


def reassign_shoot(conn, shoot_id, settings=None, radius_m=None):
    settings = settings or mi.load_settings()
    db = _DB(conn)
    shoots = db.list_shoots()
    shoot = next((s for s in shoots if s["shoot_id"] == shoot_id), None)
    if shoot is None:
        raise FinalizeError(f"unknown shoot {shoot_id}")
    radius = radius_m if radius_m is not None else (settings.get("shoot_radius_m") or shoot.get("radius_meters"))
    if radius is None:
        radius = settings.get("shoot_new_radius_m", 300)
    radius = float(radius)
    members = sorted(db.assets_in_shoot(shoot_id), key=_sort_key)
    # radius override is temporary: only the in-memory copy is narrowed, the DB row keeps its radius
    work = [dict(s, radius_meters=radius) if s["shoot_id"] == shoot_id else s for s in shoots]
    new_settings = dict(settings, shoot_new_radius_m=max(radius, 1.0))
    moved, created, touched = {}, [], {shoot_id}
    for a in members:
        if not has_gps(a) or shoot.get("centroid_lat") is None:
            continue
        if haversine_m(float(shoot["centroid_lat"]), float(shoot["centroid_lon"]), float(a["latitude"]), float(a["longitude"])) <= radius:
            continue
        res = mi.assign_shoot(a, work, new_settings)
        target = res["shoot_id"]
        if res["is_new"]:
            new = dict(res["new_shoot"])
            new["project_name"] = f"Shoot {target}"
            new["last_captured_at"] = new.get("first_captured_at")
            db.insert_shoot(new)
            work.append(new)
            created.append(target)
        if target == shoot_id:
            continue
        if _relocate_asset(db, a, target):
            moved[a["asset_id"]] = target
            touched.add(target)
    for sid in touched:
        db.reset_shoot_range(sid)
    if moved:
        _refresh_related(db, list(touched), settings)
    return {"moved": moved, "created": created}


def record_training_example(conn, asset_id, source, before, after, feedback=None, notes=None):
    if source not in TRAINING_SOURCES:
        raise ValueError(f"source must be one of {TRAINING_SOURCES}")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT source_path, original_sha256, identifier, media_kind FROM assets WHERE asset_id = %s", (asset_id,))
        row = cur.fetchone()
        if not row:
            raise FinalizeError(f"unknown asset {asset_id}")
        ref = {"source_path": row["source_path"], "original_sha256": row["original_sha256"], "identifier": row["identifier"]}
        cur.execute(
            "INSERT INTO training_examples (asset_id, identifier, media_kind, source, before_output, after_output, "
            "feedback, notes, original_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING example_id",
            (asset_id, row["identifier"], row["media_kind"], source, Json(before, dumps=_dumps), Json(after, dumps=_dumps),
             feedback, notes, Json(ref, dumps=_dumps)))
        return str(cur.fetchone()["example_id"])
