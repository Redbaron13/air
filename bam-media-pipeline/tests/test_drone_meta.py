import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import exif_meta, geocode, media_intel as mi, output_schema as osch  # noqa: E402


# ------------------------------------------------------------------ taxonomy
@pytest.mark.parametrize("pitch,angle", [
    (-90, "nadir"), (-86, "nadir"), (-84, "high_oblique"), (-60, "high_oblique"), (-45, "medium_oblique"),
    (-30, "medium_oblique"), (-20, "low_oblique"), (-10, "low_oblique"), (-5, "horizon"), (0, "horizon"), (12, "horizon"),
])
def test_drone_angle_thresholds(pitch, angle):
    got, basis = mi.classify_drone_angle(pitch)
    assert got == angle and str(abs(pitch)).split(".")[0] in basis.replace("-", "")


def test_pitch_none():
    assert mi.classify_drone_angle(None) == (None, None)


def test_thresholds_configurable():
    s = dict(mi.DEFAULT_SETTINGS, drone_nadir_max_off_deg=15)
    assert mi.classify_drone_angle(-80, s)[0] == "nadir"
    assert mi.classify_drone_angle(-80)[0] == "high_oblique"


def test_threshold_settings_validated():
    ok = mi.validate_settings({"drone_nadir_max_off_deg": 10})
    assert ok["drone_nadir_max_off_deg"] == 10
    with pytest.raises(ValueError):
        mi.validate_settings({"drone_medium_oblique_min_down_deg": 70})  # above high-oblique threshold
    with pytest.raises(ValueError):
        mi.validate_settings({"drone_alt_low_max_m": 200, "drone_alt_high_min_m": 50})
    with pytest.raises(ValueError):
        mi.validate_settings({"flight_gap_s": 0})
    for k in ("drone_nadir_max_off_deg", "flight_gap_s", "geocode_enabled", "drone_alt_low_max_m"):
        assert k in mi.DEFAULT_SETTINGS


def test_load_settings_inconsistent_file_falls_back(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"drone_high_oblique_min_down_deg": 20, "drone_medium_oblique_min_down_deg": 50}))
    s = mi.load_settings(str(p))
    assert s["drone_high_oblique_min_down_deg"] == mi.DEFAULT_SETTINGS["drone_high_oblique_min_down_deg"]


def test_legacy_shot_types_preserved():
    f = mi.classify_shot_from_exif
    assert f({"altitude_m": 50, "gimbal_pitch_deg": -90})["shot_type"] == "aerial_nadir"
    assert f({"altitude_m": 50, "gimbal_pitch_deg": -45})["shot_type"] == "aerial_oblique"
    assert f({"altitude_m": 5, "gimbal_pitch_deg": -45})["shot_type"] == "aerial_low_oblique"
    assert f({"altitude_m": 40, "gimbal_pitch_deg": -20})["shot_type"] == "aerial_low_oblique"
    r = f({"altitude_m": 1.5, "gimbal_pitch_deg": -2})
    assert (r["shot_type"], r["camera_perspective"], r["mapping_role"]) == ("ground_level", "ground", "ground")
    assert f({"altitude_m": 40})["shot_type"] == "unknown" and f({})["mapping_role"] is None


def test_compass_and_relative_facing():
    assert [mi.compass_point(d) for d in (0, 44, 90, 135, 180, 225, 270, 359)] == ["N", "NE", "E", "SE", "S", "SW", "W", "N"]
    assert mi.relative_facing(10, 5)[:2] == ("front", None)
    assert mi.relative_facing(185, 5)[0] == "rear"
    assert mi.relative_facing(90, 0)[:2] == ("side", "right")
    assert mi.relative_facing(270, 0)[:2] == ("side", "left")
    assert mi.relative_facing(0, 350)[0] == "front"  # wraps around north
    assert mi.relative_facing(None, 0)[0] is None


def test_facing_uses_track_heading_over_nose_and_explains():
    t = {"gimbal_pitch_deg": -40, "gimbal_yaw_deg": 90, "flight_yaw_deg": 90, "rel_altitude_m": 120}
    r = mi.classify_shot_from_exif(t, context={"travel_heading_deg": 90})
    assert (r["facing_compass"], r["facing_relative"], r["facing_reference"]) == ("E", "front", "flight track heading")
    r = mi.classify_shot_from_exif(t, context={"travel_heading_deg": 270})
    assert r["facing_relative"] == "rear"
    r = mi.classify_shot_from_exif(t)  # falls back to the drone nose
    assert (r["facing_relative"], r["facing_reference"]) == ("front", "drone nose heading")
    assert r["altitude_band"] == "high"
    assert any("flight track" in b for b in mi.classify_shot_from_exif(t, context={"travel_heading_deg": 90})["basis"])


def test_altitude_bands():
    assert [mi.altitude_band(x) for x in (0, 30, 31, 89, 90, 250)] == ["low", "low", "medium", "medium", "high", "high"]
    assert mi.altitude_band(None) is None


def test_mapping_roles():
    f = mi.classify_shot_from_exif
    assert f({"gimbal_pitch_deg": -90, "rel_altitude_m": 60})["mapping_role"] == "nadir_grid"
    assert f({"gimbal_pitch_deg": -40, "rel_altitude_m": 60})["mapping_role"] == "oblique_ring"
    assert f({"gimbal_pitch_deg": -40, "rel_altitude_m": 60}, context={"corridor": True})["mapping_role"] == "corridor"
    assert f({"gimbal_pitch_deg": -40, "rel_altitude_m": 60, "shot_hint": "panorama"})["mapping_role"] == "panorama"
    assert f({"gimbal_pitch_deg": -90, "rel_altitude_m": 60, "media_kind": "video"})["mapping_role"] == "video_nadir"
    assert f({"gimbal_pitch_deg": -40, "rel_altitude_m": 60, "media_kind": "video"})["mapping_role"] == "video_oblique"
    # camera north of a subject looking south at it -> orbit/POI, viewed from the north
    t = {"gimbal_pitch_deg": -40, "rel_altitude_m": 60, "gimbal_yaw_deg": 180, "latitude": 40.01, "longitude": 10.0}
    r = f(t, context={"subject_center": {"lat": 40.0, "lon": 10.0}})
    assert (r["mapping_role"], r["viewed_from"]) == ("orbit_poi", "N")
    t["gimbal_yaw_deg"] = 0
    assert f(t, context={"subject_center": {"lat": 40.0, "lon": 10.0}})["mapping_role"] == "oblique_ring"


# ------------------------------------------------------------------ flight reconstruction
BASE = datetime(2025, 5, 10, 10, 0, 0)
LAT0, LON0 = 40.0, -70.0


def _asset(i, sec, dlat=0.0, dlon=0.0, **kw):
    a = {"asset_id": f"A{i}", "captured_at": BASE + timedelta(seconds=sec), "latitude": LAT0 + dlat,
         "longitude": LON0 + dlon}
    a.update(kw)
    return a


def test_flight_track_basic_metrics():
    # three shots heading due east ~80 m apart, 10 s apart (given out of order)
    d = 0.00094
    assets = [_asset(3, 20, dlon=2 * d), _asset(1, 0), _asset(2, 10, dlon=d)]
    tr = mi.build_flight_tracks(assets)
    assert [p["asset_id"] for p in tr["points"]] == ["A1", "A2", "A3"]
    p2 = tr["points"][1]
    assert (p2["seq"], p2["total"], p2["elapsed_s"], p2["dt_prev_s"]) == (2, 3, 10.0, 10.0)
    assert 70 < p2["dist_prev_m"] < 90 and 7 < p2["speed_mps"] < 9
    assert 89 < p2["heading_deg"] < 91 and tr["points"][0]["heading_deg"] == p2["heading_deg"]
    f = tr["flights"][0]
    assert f["count"] == 3 and f["duration_s"] == 20.0 and 150 < f["total_distance_m"] < 180
    assert f["bbox"]["min_lon"] == LON0 and f["is_linear"] is True and f["straightness"] > 0.99
    loc = mi.locate_in_flight(tr, "A2")
    assert (loc["seq"], loc["total"], loc["prev_asset_id"], loc["next_asset_id"], loc["dt_next_s"]) == (2, 3, "A1", "A3", 10.0)
    assert loc["heading_compass"] == "E" and mi.flight_context(tr, "A2")["corridor"] is True


def test_flight_split_on_gap_and_untimed():
    assets = [_asset(1, 0), _asset(2, 30), _asset(3, 30 + 700, dlat=0.01), {"asset_id": "X", "captured_at": None}]
    tr = mi.build_flight_tracks(assets)
    assert [f["count"] for f in tr["flights"]] == [2, 1] and tr["untimed"] == ["X"]
    assert tr["points"][2]["flight_index"] == 2 and tr["points"][2]["seq"] == 1 and tr["points"][2]["dist_prev_m"] is None
    assert len(mi.build_flight_tracks(assets, {"flight_gap_s": 2000})["flights"]) == 1
    assert mi.locate_in_flight(tr, "X") is None
    assert mi.build_flight_tracks([])["flights"] == []


def test_burst_shots_hold_heading_and_no_gps():
    d = 0.00094
    assets = [_asset(1, 0), _asset(2, 10, dlat=d), _asset(3, 11, dlat=d), _asset(4, 20, latitude=None, longitude=None)]
    tr = mi.build_flight_tracks(assets)
    assert tr["points"][2]["heading_deg"] == tr["points"][1]["heading_deg"]  # stationary burst keeps heading
    assert tr["points"][3]["dist_prev_m"] is None and tr["points"][3]["latitude"] is None


def test_capture_view_timezone():
    v = mi.capture_view({"captured_at": datetime(2025, 5, 10, 12, 0, 0), "capture_utc_offset": "+02:00"})
    assert v["captured_at"] == "2025-05-10T14:00:00+02:00" and v["captured_at_utc"] == "2025-05-10T12:00:00Z"
    assert v["tz_known"] and "14:00:00" in v["local_display"] and "UTC+02:00" in v["local_display"]
    v = mi.capture_view({"captured_at": "2025-05-10T12:00:00"})
    assert not v["tz_known"] and v["captured_at"].endswith("Z") and "unknown" in v["local_display"]
    assert mi.capture_view({})["captured_at"] is None


def test_asset_capture_meta_payload_shape():
    d = 0.00094
    shoot = [_asset(i, i * 10, dlon=i * d, gimbal_pitch=-90, rel_altitude=60.5, flight_yaw=90.0, gimbal_yaw=90.0,
                    raw_exif='{"Make": "DJI"}', camera_make="DJI", sensor_model="FC3030", iso=100, aperture=2.8,
                    altitude_meters=150.0, capture_utc_offset="-04:00") for i in range(4)]
    m = mi.asset_capture_meta(shoot[1], shoot, address={"city": "X"})
    assert set(m) == {"capture", "location", "camera", "drone", "flight", "raw_exif"}
    assert m["capture"]["utc_offset"] == "-04:00" and m["location"]["address"] == {"city": "X"}
    assert m["location"]["latitude"] == LAT0 and m["camera"]["make"] == "DJI" and m["raw_exif"] == {"Make": "DJI"}
    assert m["drone"]["drone_angle"] == "nadir" and m["drone"]["mapping_role"] == "nadir_grid"
    assert m["drone"]["facing_relative"] == "front" and m["flight"]["seq"] == 2 and m["flight"]["total"] == 4
    json.dumps(m, default=str)


# ------------------------------------------------------------------ output schema
def test_shot_type_aliases_and_new_enums():
    n = lambda v: osch.normalize_model_output("still", {"classification": {"shot_type": v}})["classification"]["shot_type"]
    assert (n("Aerial"), n("nadir"), n("high-oblique"), n("horizon"), n("weird")) == (
        "aerial_oblique", "aerial_nadir", "aerial_oblique", "aerial_low_oblique", "unknown")
    for k in ("drone_angle", "altitude_band", "facing_relative", "mapping_role"):
        assert k in osch.ENUMS
    assert "-90" in osch.build_prompt("still", {}, stage="verifier")


def test_assemble_record_has_drone_fields_and_validates():
    tel = {"latitude": 40.0, "longitude": 10.0, "gimbal_pitch": -90, "gimbal_yaw": 90, "flight_yaw": 90,
           "rel_altitude": 40, "make": "DJI", "iso": 100, "address": {"city": "Town", "country": "US"}}
    rec = osch.assemble_record("still", {"asset_id": "a"}, tel, {}, {}, {})
    c = rec["classification"]
    assert (c["shot_type_from_exif"], c["drone_angle"], c["mapping_role"], c["facing_compass"]) == (
        "aerial_nadir", "nadir", "nadir_grid", "E")
    assert rec["capture"]["camera_make"] == "DJI" and rec["capture"]["rel_altitude_m"] == 40.0
    assert rec["address"]["city"] == "Town" and rec["flight"]["seq"] is None
    assert osch.validate_record(rec) == []
    legacy = {k: v for k, v in rec.items() if k not in ("address", "flight")}
    assert osch.validate_record(legacy) == []  # additive: older stored records stay valid


# ------------------------------------------------------------------ EXIF / XMP
XMP = (b'junk<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description drone-dji:GimbalPitchDegree="-90.00" '
       b'drone-dji:GimbalYawDegree="+120.4" drone-dji:FlightYawDegree="+110.0" drone-dji:FlightRollDegree="-0.2" '
       b'drone-dji:RelativeAltitude="+61.200" drone-dji:AbsoluteAltitude="+150.5" drone-dji:CameraSerialNumber="SN1" '
       b'tiff:Model="FC3030"/></rdf:RDF></x:xmpmeta>tail')


def test_parse_xmp_and_exif_dump(tmp_path):
    assert exif_meta.parse_xmp_attrs(b"nothing") == {}
    x = exif_meta.parse_xmp_attrs(XMP)
    assert x["GimbalPitchDegree"] == "-90.00" and x["CameraSerialNumber"] == "SN1"
    dump = dict(x, Make="DJI", Model="FC3030", LensModel="L", FocalLength=24.0, ISO=100, ExposureTime=0.002,
                FNumber=2.8, DateTimeOriginal="2025:05:10 12:00:00", OffsetTimeOriginal="+02:00",
                GPSLatitude=40.5, GPSLongitude=-70.25, GPSAltitude=150.5)
    m = exif_meta.parse_exif_dump(dump)
    assert (m["gimbal_pitch"], m["gimbal_yaw"], m["flight_yaw"], m["flight_roll"]) == (-90.0, 120.4, 110.0, -0.2)
    assert (m["rel_altitude"], m["abs_altitude"], m["camera_serial"]) == (61.2, 150.5, "SN1")
    assert (m["make"], m["sensor"], m["lens_model"], m["iso"], m["shutter_speed"], m["aperture"]) == (
        "DJI", "FC3030", "L", 100, "1/500", 2.8)
    assert m["captured_at"] == "2025-05-10 10:00:00" and m["capture_utc_offset"] == "+02:00"
    assert m["captured_at_local"] == "2025-05-10 12:00:00" and (m["lat"], m["lon"]) == (40.5, -70.25)
    assert m["is_drone"] is True and m["focal_length"] == "24 mm"
    f = tmp_path / "a.jpg"
    f.write_bytes(b"\xff\xd8" + XMP)
    raw = exif_meta.read_exif_dump(str(f))  # exiftool optional: XMP fallback still yields drone fields
    assert raw["GimbalYawDegree"] == "+120.4"
    json.dumps(raw)
    assert exif_meta.read_exif_dump(str(tmp_path / "missing.jpg")) == {}


def test_exif_hemisphere_invalid_and_json_safe():
    m = exif_meta.parse_exif_dump({"GPSLatitude": 30.0, "GPSLatitudeRef": "S", "GPSLongitude": 50.0, "GPSLongitudeRef": "W"})
    assert (m["lat"], m["lon"]) == (-30.0, -50.0) and m["is_drone"] is False
    assert exif_meta.parse_exif_dump({"GPSLatitude": 99, "GPSLongitude": 1})["lat"] is None
    assert exif_meta.parse_exif_dump(None)["captured_at"] is None
    safe = exif_meta.json_safe({"b": b"xx", "n": float("nan"), "s": "(Binary data 5 bytes)", "t": "x" * 5000, "ok": 1})
    assert safe["ok"] == 1 and "b" not in safe and "n" not in safe and len(safe["t"]) <= 2048
    json.dumps(safe)


def test_parse_capture_time_variants():
    assert exif_meta.parse_capture_time("2025:05:10 12:00:00", "-07:00")["utc"] == "2025-05-10 19:00:00"
    assert exif_meta.parse_capture_time("2025-05-10T12:00:00+01:30")["offset"] == "+01:30"
    assert exif_meta.parse_capture_time("garbage")["utc"] is None
    assert exif_meta.parse_capture_time(None)["iso"] is None


# ------------------------------------------------------------------ geocode (HTTP mocked)
class _Resp:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("http")

    def json(self):
        return self.data


NOMINATIM = {"display_name": "1 Main St, Town", "address": {"road": "Main St", "town": "Town", "county": "C",
                                                           "state": "S", "postcode": "123", "country": "US"}}


@pytest.fixture
def geo(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOCODE_CACHE_FILE", str(tmp_path / "gc.json"))
    monkeypatch.setenv("GEOCODE_ENABLED", "1")
    monkeypatch.delenv("GEOCODE_URL", raising=False)
    monkeypatch.setattr(geocode, "MIN_INTERVAL_S", 0.0)
    geocode.reset_state()
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _Resp(NOMINATIM)

    monkeypatch.setattr(geocode.httpx, "get", fake_get)
    yield calls
    geocode.reset_state()


def test_geocode_shape_cache_and_user_agent(geo, tmp_path):
    a = geocode.reverse_geocode(40.12341, -70.12341)
    assert set(a) == set(geocode.FIELDS) and a["city"] == "Town" and a["road"] == "Main St"
    assert geocode.reverse_geocode(40.12344, -70.12339) == a  # same rounded key -> cached
    assert len(geo) == 1 and "BAM-Media-Pipeline" in geo[0]["headers"]["User-Agent"]
    assert (tmp_path / "gc.json").exists()
    geocode.reset_state()  # survives restart via the JSON file
    assert geocode.reverse_geocode(40.12341, -70.12341) == a and len(geo) == 1
    assert geocode.cached_address(40.12341, -70.12341) == a


def test_geocode_custom_url_and_disabled(geo, monkeypatch):
    monkeypatch.setenv("GEOCODE_URL", "http://nom.local/reverse")
    geocode.reverse_geocode(1.5, 2.5)
    assert geo[0]["url"] == "http://nom.local/reverse"
    assert geocode.reverse_geocode(3.5, 4.5, enabled=False) is None and len(geo) == 1
    monkeypatch.setenv("GEOCODE_ENABLED", "0")
    assert geocode.reverse_geocode(5.5, 6.5) is None and len(geo) == 1


def test_geocode_never_raises(geo, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("slow")

    monkeypatch.setattr(geocode.httpx, "get", boom)
    assert geocode.reverse_geocode(1.1, 2.2) is None
    assert geocode.reverse_geocode(1.1, 2.2) is None  # failure remembered, no hammering
    for bad in ((None, None), ("x", 1), (0, 0), (95, 1), (float("nan"), 1)):
        assert geocode.reverse_geocode(*bad) is None
    monkeypatch.setattr(geocode.httpx, "get", lambda *a, **k: _Resp({"error": "Unable to geocode"}))
    assert geocode.reverse_geocode(7.7, 8.8) is None
    monkeypatch.setattr(geocode.httpx, "get", lambda *a, **k: _Resp({}, 503))
    assert geocode.reverse_geocode(9.9, 1.1) is None


def test_migration_covers_new_columns_idempotently():
    import re
    from server import schema_migrations as sm
    sql = (sm.MIGRATIONS_DIR / sm.DRONE_META).read_text()
    stmts = [l for l in sql.splitlines() if l.startswith("ALTER TABLE")]
    assert stmts and all("IF NOT EXISTS" in l for l in stmts)
    added = set(re.findall(r"ALTER TABLE assets ADD COLUMN IF NOT EXISTS (\w+)", sql))
    needed = {"raw_exif", "gimbal_yaw", "flight_yaw", "flight_roll", "rel_altitude", "abs_altitude", "capture_utc_offset",
              "captured_at_local", "address", "flight_index", "flight_seq", "flight_total", "camera_make", "lens_model",
              "iso", "shutter_speed", "aperture", "camera_serial"}
    assert needed <= added
    assert "ALTER TABLE shoots ADD COLUMN IF NOT EXISTS address" in sql
    assert sm.DRONE_META > sm.SYNC_OUTBOX  # applied after the outbox triggers; triggers serialise whole rows
