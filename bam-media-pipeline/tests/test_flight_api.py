import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_intel_api import client, use_db  # noqa: F401,E402
from server import db_sync, geocode, review_api as ra  # noqa: E402

BASE = datetime(2025, 5, 10, 10, 0, 0)
D = 0.00094


def _rows(n=4, shoot="S1"):
    return [{"asset_id": f"A{i}", "original_filename": f"DJI_{i}.JPG", "shoot_id": shoot,
             "captured_at": BASE + timedelta(seconds=10 * i), "latitude": 40.0, "longitude": -70.0 + D * i,
             "rel_altitude": 60.0, "capture_utc_offset": "+02:00", "media_kind": "still", "gimbal_pitch": -45.0,
             "gimbal_yaw": 90.0, "flight_yaw": 90.0, "raw_exif": {"Make": "DJI"}, "workflow_state": "APPROVED",
             "source_path": "/nope", "final_output": None} for i in range(n)]


def test_flight_for_asset(client, monkeypatch):
    rows = _rows()

    def handler(sql, params):
        if "SELECT shoot_id FROM assets WHERE asset_id" in sql:
            return [{"shoot_id": "S1"}]
        if "FROM assets WHERE shoot_id" in sql:
            return rows
        return []

    use_db(monkeypatch, handler)
    r = client.get("/api/review/intel/flight/A1")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"asset_id", "shoot_id", "flights", "flight", "points", "position", "neighbors", "untimed"}
    assert j["shoot_id"] == "S1" and len(j["points"]) == 4 and j["points"][0]["filename"] == "DJI_0.JPG"
    pos = j["position"]
    assert (pos["seq"], pos["total"], pos["prev_asset_id"], pos["next_asset_id"]) == (2, 4, "A0", "A2")
    assert pos["elapsed_s"] == 10.0 and pos["dt_prev_s"] == 10.0 and pos["heading_compass"] == "E"
    assert j["neighbors"]["prev"] == {"asset_id": "A0", "filename": "DJI_0.JPG"}
    assert j["flight"]["count"] == 4 and j["flight"]["bbox"]["min_lat"] == 40.0
    assert client.get("/api/review/intel/flight/A1", params={"shoot_id": "S1"}).status_code == 200


def test_flight_for_asset_not_found_and_by_shoot(client, monkeypatch):
    use_db(monkeypatch, lambda sql, p: _rows() if "FROM assets WHERE shoot_id" in sql else [])
    assert client.get("/api/review/intel/flight/ZZ").status_code == ra.NOT_FOUND
    assert client.get("/api/review/intel/flight/ZZ", params={"shoot_id": "S1"}).status_code == ra.NOT_FOUND
    j = client.get("/api/review/intel/flight", params={"shoot_id": "S1"}).json()
    assert j["position"] is None and j["shoot_id"] == "S1" and len(j["flights"]) == 1 and len(j["points"]) == 4


def test_asset_detail_capture_meta(client, monkeypatch, tmp_path):
    rows = _rows()
    for r in rows:
        r["identifier"] = None
        r["address"] = None
    monkeypatch.setenv("GEOCODE_CACHE_FILE", str(tmp_path / "gc.json"))
    geocode.reset_state()
    (tmp_path / "gc.json").write_text('{"40.0000,-69.9991": {"city": "Town", "country": "US"}}')

    def handler(sql, p):
        if "FROM assets a" in sql:
            return rows
        return []

    use_db(monkeypatch, handler)
    j = client.get("/api/review/assets/A1").json()
    cm = j["capture_meta"]
    assert set(cm) == {"capture", "location", "camera", "drone", "flight", "raw_exif"}
    assert cm["capture"]["captured_at"].endswith("+02:00") and cm["capture"]["captured_at_utc"].endswith("Z")
    assert cm["location"]["latitude"] == 40.0 and cm["location"]["address"]["city"] == "Town"
    assert cm["drone"]["drone_angle"] == "medium_oblique" and cm["drone"]["facing_relative"] == "front"
    assert cm["flight"]["seq"] == 2 and cm["flight"]["total"] == 4 and cm["raw_exif"] == {"Make": "DJI"}
    geocode.reset_state()


def test_sync_status_does_not_leak_errors(client, monkeypatch):
    monkeypatch.setattr(db_sync, "_local_dsn", lambda: "0host/db")
    monkeypatch.setattr(db_sync, "_remote_dsn", lambda: None)

    def boom(*a, **k):
        raise RuntimeError("could not connect with password secret at /srv/app.py line 4")

    monkeypatch.setattr(db_sync, "_connect", boom)
    j = client.get("/api/review/intel/sync/status").json()
    assert j["last_error"] == "local db unavailable" and "secret" not in str(j)


def test_sync_run_error_is_generic(client, monkeypatch):
    monkeypatch.setattr(db_sync, "run_sync", lambda: {"pushed": 0, "pending": 1, "remote": "unreachable",
                                                       "error": "psycopg2 trace secret"})
    j = client.post("/api/review/intel/sync/run").json()
    assert j["error"] == "sync failed; see server log" and "secret" not in str(j)
