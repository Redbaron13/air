import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_finalize import FakeDB, SETTINGS, add, fake, mk  # noqa: F401,E402
from server import finalize as fin  # noqa: E402

D = 0.00094
T0 = datetime(2024, 5, 1, 10, 0)


def test_finalize_writes_flight_drone_fields_and_address(fake, monkeypatch):
    FakeDB.set_shoot_address = lambda self, sid, addr: self.shoots[sid].__setitem__("address", addr)
    monkeypatch.setattr(fin, "_resolve_address", lambda lat, lon, s: {"city": "Town", "country": "US"})
    add(fake, *[mk(f"a{i}", f"stem{i}xxx", dlon=i * D, when=T0 + timedelta(seconds=10 * i), gimbal_pitch=-90.0,
                   gimbal_yaw=90.0, flight_yaw=90.0, rel_altitude=50.0) for i in range(3)])
    fin.finalize_assets(None, ["a0", "a1", "a2"], SETTINGS)
    a1 = fake["assets"]["a1"]
    assert (a1["flight_index"], a1["flight_seq"], a1["flight_total"]) == (1, 2, 3)
    assert a1["address"] == {"city": "Town", "country": "US"}
    fo = a1["final_output"]
    assert fo["flight"]["seq"] == 2 and fo["flight"]["total"] == 3 and fo["flight"]["dt_prev_s"] == 10.0
    assert fo["address"]["city"] == "Town" and fo["address"]["road"] is None
    c = fo["classification"]
    assert (c["drone_angle"], c["mapping_role"], c["facing_compass"], c["facing_relative"]) == (
        "nadir", "nadir_grid", "E", "front")
    assert c["shot_type_from_exif"] == "aerial_nadir" and c["exif_basis"]
    assert fake["shoots"][a1["shoot_id"]]["address"]["city"] == "Town"


def test_finalize_without_geocoder_leaves_address_empty(fake):
    add(fake, mk("b0", "stemb000"))
    fin.finalize_assets(None, ["b0"], SETTINGS)
    assert fake["assets"]["b0"]["final_output"]["address"]["city"] is None
    assert fake["assets"]["b0"].get("address") is None
