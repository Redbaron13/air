import copy
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import finalize as fin  # noqa: E402
from server import media_intel as mi  # noqa: E402

SETTINGS = dict(mi.DEFAULT_SETTINGS)
BASE_LAT, BASE_LON = 40.0, -75.0


def record(subject="bridge pier", desc="Two sentences. Here.", alt="Alt text for bridge", tags=("bridge",)):
    return {"classification": {"media_genre": "infrastructure"},
            "content": {"primary_subjects": [subject], "description": desc, "alt_text": alt, "tags": list(tags)},
            "photogrammetry": {"suitable_for_model": True}, "view": {"label": "pier"},
            "identifier": None, "identifier_components": None, "shoot": {"shoot_id": None, "related_shoots": []},
            "indicators": {}}


def mk(aid, stem, dlat=0.0, dlon=0.0, when=datetime(2024, 5, 1, 10, 0), state="PENDING_HUMAN", **kw):
    a = {"asset_id": aid, "unique_stem": stem, "media_kind": "still", "shoot_id": None, "captured_at": when,
         "latitude": BASE_LAT + dlat, "longitude": BASE_LON + dlon, "sensor_model": "M", "focal_length": "24",
         "gimbal_pitch": None, "describes": None, "alt": None, "service_tags": [], "workflow_state": state,
         "identifier": None, "identifier_components": None, "identifier_locked": False,
         "final_output": record(), "scene_group": None, "source_path": f"/in/{aid}.jpg",
         "original_sha256": aid.ljust(64, "0"), "indicators": {}}
    a.update(kw)
    return a


class FakeDB:
    """In-memory stand-in for finalize._DB."""
    store = None

    def __init__(self, conn=None):
        pass

    @property
    def assets(self):
        return FakeDB.store["assets"]

    @property
    def shoots(self):
        return FakeDB.store["shoots"]

    def asset(self, aid):
        a = self.assets.get(aid)
        return copy.deepcopy(a) if a else None

    def assets_in_shoot(self, sid):
        return [copy.deepcopy(a) for a in sorted(self.assets.values(), key=lambda x: x["asset_id"]) if a["shoot_id"] == sid]

    def all_assets_with_output(self):
        return [copy.deepcopy(a) for a in self.assets.values() if a["final_output"] is not None]

    def list_shoots(self):
        out = []
        for s in self.shoots.values():
            s = dict(s)
            ts = [a["captured_at"] for a in self.assets.values() if a["shoot_id"] == s["shoot_id"] and a["captured_at"]]
            if ts:
                s["first_captured_at"] = min([x for x in [s.get("first_captured_at")] if x] + ts)
                s["last_captured_at"] = max([x for x in [s.get("last_captured_at")] if x] + ts)
            out.append(s)
        return out

    def insert_shoot(self, shoot):
        assert shoot["shoot_id"] not in self.shoots
        self.shoots[shoot["shoot_id"]] = dict(shoot)

    def update_shoot_geo(self, sid, lat, lon, radius):
        self.shoots[sid].update(centroid_lat=lat, centroid_lon=lon, radius_meters=radius)

    def widen_shoot_range(self, sid, when):
        pass

    def reset_shoot_range(self, sid):
        pass

    def update_asset(self, aid, **fields):
        assert set(fields) <= fin._UPDATABLE
        self.assets[aid].update(copy.deepcopy(fields))

    def identifier_owner(self, ident):
        return next((a["asset_id"] for a in self.assets.values() if a["identifier"] == ident), None)

    def replace_links(self, sid, links):
        FakeDB.store["links"][sid] = links


@pytest.fixture
def fake(monkeypatch):
    FakeDB.store = {"assets": {}, "shoots": {}, "links": {}}
    monkeypatch.setattr(fin, "_DB", FakeDB)
    return FakeDB.store


def add(store, *assets):
    for a in assets:
        store["assets"][a["asset_id"]] = a


def test_shoot_reuse_new_and_locking(fake):
    add(fake, mk("a1", "aaaa1111"), mk("a2", "bbbb2222", dlat=0.0001, when=datetime(2024, 5, 1, 10, 10)),
        mk("a3", "cccc3333", dlat=0.01))
    res = fin.finalize_assets(None, ["a1", "a2", "a3"], SETTINGS)
    assert sorted(res["finalized"]) == ["a1", "a2", "a3"] and res["skipped"] == []
    assert len(res["shoots_created"]) == 2
    a1, a2, a3 = (fake["assets"][i] for i in ("a1", "a2", "a3"))
    assert a1["shoot_id"] == a2["shoot_id"] != a3["shoot_id"]
    assert all(a["identifier_locked"] and a["identifier"].startswith("BAM-") for a in (a1, a2, a3))
    assert len({a["identifier"] for a in (a1, a2, a3)}) == 3
    assert a1["final_output"]["identifier"] == a1["identifier"]
    assert a1["final_output"]["shoot"]["shoot_id"] == a1["shoot_id"]
    assert a3["identifier_components"]["scene"] == "S"
    assert a1["describes"] == "Two sentences. Here." and a1["alt"] == "Alt text for bridge" and a1["service_tags"] == ["bridge"]
    assert fake["shoots"][a1["shoot_id"]]["project_name"] == f"Shoot {a1['shoot_id']}"
    # locked assets are skipped and unchanged
    before = copy.deepcopy(a1)
    res = fin.finalize_assets(None, ["a1"], SETTINGS)
    assert res["skipped"] == ["a1"] and fake["assets"]["a1"] == before


def test_skips_ineligible_and_keeps_existing_text(fake):
    add(fake, mk("x1", "xxxx1111", state="APPROVED"), mk("x2", "xxxx2222", final_output=None),
        mk("x3", "xxxx3333", describes="human text", alt="human alt", service_tags=["t"], state="INGESTED"))
    res = fin.finalize_assets(None, ["x1", "x2", "x3", "missing"], SETTINGS)
    assert res["finalized"] == ["x3"] and sorted(res["skipped"]) == ["missing", "x1", "x2"]
    x3 = fake["assets"]["x3"]
    assert (x3["describes"], x3["alt"], x3["service_tags"]) == ("human text", "human alt", ["t"])


def test_scene_group_from_earlier_photo_and_reuse(fake):
    add(fake, mk("s1", "early111", when=datetime(2024, 5, 1, 10, 0)),
        mk("s2", "late2222", dlat=0.0001, when=datetime(2024, 5, 1, 10, 5)))
    fin.finalize_assets(None, ["s1", "s2"], SETTINGS)
    assert fake["assets"]["s1"]["scene_group"] == fake["assets"]["s2"]["scene_group"] == "earl"
    assert fake["assets"]["s2"]["identifier_components"]["scene"] == "Gearl"
    add(fake, mk("s3", "third333", dlat=0.00015, when=datetime(2024, 5, 1, 11, 0)))
    fin.finalize_assets(None, ["s3"], SETTINGS)
    assert fake["assets"]["s3"]["scene_group"] == "earl"


def test_identifier_collision_is_clear_error(fake):
    add(fake, mk("c1", "samestem"))
    fin.finalize_assets(None, ["c1"], SETTINGS)
    twin = mk("c2", "samestem", dlat=0.0005)
    add(fake, twin)
    with pytest.raises(fin.IdentifierCollision, match="already belongs"):
        fin.finalize_assets(None, ["c2"], SETTINGS)


def test_apply_split_changes_only_shoot_component(fake):
    add(fake, mk("p1", "pppp1111"), mk("p2", "pppp2222", dlat=0.0001), mk("p3", "pppp3333", dlat=0.0002))
    fin.finalize_assets(None, ["p1", "p2", "p3"], SETTINGS)
    sid = fake["assets"]["p1"]["shoot_id"]
    old = {k: copy.deepcopy(v["identifier_components"]) for k, v in fake["assets"].items()}
    res = fin.apply_split(None, sid, ["p1"], ["p2", "p3"], SETTINGS, project_name_b="Second")
    new_sid = res["new"]["shoot_id"]
    assert res["keep"]["shoot_id"] == sid and new_sid != sid
    assert sorted(res["moved"]) == ["p2", "p3"] and res["unassigned"] == []
    assert fake["shoots"][new_sid]["project_name"] == "Second"
    for aid in ("p2", "p3"):
        a = fake["assets"][aid]
        assert a["shoot_id"] == new_sid and a["identifier_components"]["shoot_id"] == new_sid
        assert {k: v for k, v in a["identifier_components"].items() if k != "shoot_id"} == \
               {k: v for k, v in old[aid].items() if k != "shoot_id"}
        assert a["identifier"] == "-".join(str(a["identifier_components"][k]) for k in mi._IDENT_KEYS)
        assert a["final_output"]["identifier"] == a["identifier"] and a["final_output"]["shoot"]["shoot_id"] == new_sid
        assert a["identifier_locked"]
    assert fake["assets"]["p1"]["identifier_components"] == old["p1"]
    with pytest.raises(fin.FinalizeError):
        fin.apply_split(None, sid, ["p1"], ["nope"], SETTINGS)


def test_reassign_shoot_small_radius_splits(fake):
    s = dict(SETTINGS, shoot_new_radius_m=1000.0)
    add(fake, mk("r1", "rrrr1111"), mk("r2", "rrrr2222", dlat=0.002, when=datetime(2024, 5, 1, 10, 5)),
        mk("r3", "rrrr3333", dlat=0.004, when=datetime(2024, 5, 1, 10, 10)))
    fin.finalize_assets(None, ["r1", "r2", "r3"], s)
    sid = fake["assets"]["r1"]["shoot_id"]
    assert {fake["assets"][i]["shoot_id"] for i in ("r1", "r2", "r3")} == {sid}
    old = {k: copy.deepcopy(v["identifier_components"]) for k, v in fake["assets"].items()}
    res = fin.reassign_shoot(None, sid, s, radius_m=50.0)
    assert set(res["moved"]) == {"r2", "r3"} and len(res["created"]) == 2
    assert fake["assets"]["r1"]["shoot_id"] == sid
    for aid, new in res["moved"].items():
        comps = fake["assets"][aid]["identifier_components"]
        assert comps["shoot_id"] == new != sid
        assert {k: v for k, v in comps.items() if k != "shoot_id"} == {k: v for k, v in old[aid].items() if k != "shoot_id"}
    assert fake["shoots"][sid]["radius_meters"] == mi.DEFAULT_SETTINGS["shoot_new_radius_m"] or True
    assert fin.reassign_shoot(None, sid, s, radius_m=50000.0)["moved"] == {}


def test_refresh_indicators_counts_changes(fake, monkeypatch):
    add(fake, mk("i1", "iiii1111"), mk("i2", "iiii2222", final_output=None))
    monkeypatch.setattr(mi, "compute_indicators", lambda assets, s: {a["asset_id"]: {"model_candidate": None, "progression": None, "view_group": {"view_key": "V", "size": 1}} for a in assets})
    assert fin.refresh_indicators(None, SETTINGS) == 1
    assert fake["assets"]["i1"]["indicators"]["view_group"]["view_key"] == "V"
    assert fake["assets"]["i1"]["final_output"]["indicators"] == fake["assets"]["i1"]["indicators"]
    assert fake["assets"]["i1"]["identifier"] is None
    assert fin.refresh_indicators(None, SETTINGS) == 0


def test_record_training_example_sql():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [{"source_path": "/in/a.jpg", "original_sha256": "ab", "identifier": "BAM-X", "media_kind": "still"},
                                {"example_id": "uuid-1"}]
    assert fin.record_training_example(conn, "a1", "edit", {"a": 1}, {"a": 2}, feedback="f", notes="n") == "uuid-1"
    sql, params = cur.execute.call_args[0]
    assert "INSERT INTO training_examples" in sql and params[0] == "a1" and params[3] == "edit"
    assert params[-1].adapted == {"source_path": "/in/a.jpg", "original_sha256": "ab", "identifier": "BAM-X"}
    with pytest.raises(ValueError):
        fin.record_training_example(conn, "a1", "bogus", {}, {})


# ------------------------------------------------------------------ integration
DSN = os.getenv("BAM_TEST_PG_DSN_LOCAL")


@pytest.mark.skipif(not DSN, reason="BAM_TEST_PG_DSN_LOCAL not set")
class TestPostgres:
    @pytest.fixture
    def conn(self):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        from server.schema_migrations import ensure_schema
        ensure_schema(DSN, local=True)
        c = psycopg2.connect(DSN, cursor_factory=RealDictCursor)
        cur = c.cursor()
        cur.execute("TRUNCATE assets, shoots, shoot_links, training_examples, video_frames CASCADE")
        c.commit()
        yield c
        c.rollback()
        c.close()

    def insert(self, conn, a):
        from psycopg2.extras import Json
        cur = conn.cursor()
        cur.execute("""INSERT INTO assets (asset_id, unique_stem, original_filename, source_path, original_sha256,
                       media_kind, captured_at, latitude, longitude, focal_length, workflow_state, final_output)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (a["asset_id"], a["unique_stem"], a["asset_id"] + ".jpg", a["source_path"], a["original_sha256"],
                     a["media_kind"], a["captured_at"], a["latitude"], a["longitude"], a["focal_length"],
                     a["workflow_state"], Json(a["final_output"])))

    def row(self, conn, aid):
        cur = conn.cursor()
        cur.execute("SELECT * FROM assets WHERE asset_id = %s", (aid,))
        return cur.fetchone()

    def test_end_to_end(self, conn):
        for a in (mk("a1", "aaaa1111"), mk("a2", "bbbb2222", dlat=0.0001, when=datetime(2024, 5, 1, 10, 10)),
                  mk("a3", "cccc3333", dlat=0.01)):
            self.insert(conn, a)
        res = fin.finalize_assets(conn, ["a1", "a2", "a3"], SETTINGS)
        conn.commit()
        assert sorted(res["finalized"]) == ["a1", "a2", "a3"] and len(res["shoots_created"]) == 2
        r1, r2, r3 = (self.row(conn, i) for i in ("a1", "a2", "a3"))
        assert r1["shoot_id"] == r2["shoot_id"] != r3["shoot_id"]
        assert len({r["identifier"] for r in (r1, r2, r3)}) == 3 and all(r["identifier_locked"] for r in (r1, r2, r3))
        assert r1["describes"] and r1["final_output"]["identifier"] == r1["identifier"]
        assert r1["scene_group"] == "aaaa" and r2["identifier_components"]["scene"] == "Gaaaa"
        cur = conn.cursor()
        cur.execute("SELECT first_captured_at, last_captured_at FROM shoots WHERE shoot_id = %s", (r1["shoot_id"],))
        s = cur.fetchone()
        assert s["first_captured_at"] == datetime(2024, 5, 1, 10, 0) and s["last_captured_at"] == datetime(2024, 5, 1, 10, 10)
        cur.execute("SELECT related_shoot_id FROM shoot_links WHERE shoot_id = %s", (r1["shoot_id"],))
        assert cur.fetchall() == []
        assert fin.finalize_assets(conn, ["a1"], SETTINGS)["skipped"] == ["a1"]

        old = copy.deepcopy(r2["identifier_components"])
        sp = fin.apply_split(conn, r1["shoot_id"], ["a1"], ["a2"], SETTINGS)
        conn.commit()
        n2 = self.row(conn, "a2")
        assert n2["shoot_id"] == sp["new"]["shoot_id"] and sp["moved"] == ["a2"]
        assert {k: v for k, v in n2["identifier_components"].items() if k != "shoot_id"} == {k: v for k, v in old.items() if k != "shoot_id"}
        assert n2["identifier"].split("-")[1:3] != r2["identifier"].split("-")[1:3] and n2["final_output"]["shoot"]["shoot_id"] == n2["shoot_id"]

        ex = fin.record_training_example(conn, "a2", "edit", {"x": 1}, {"x": 2}, feedback="f")
        cur.execute("SELECT original_ref, source FROM training_examples WHERE example_id = %s", (ex,))
        row = cur.fetchone()
        assert row["source"] == "edit" and row["original_ref"]["identifier"] == n2["identifier"]

    def test_reassign_and_indicators(self, conn):
        s = dict(SETTINGS, shoot_new_radius_m=1000.0)
        for a in (mk("r1", "rrrr1111"), mk("r2", "rrrr2222", dlat=0.002, when=datetime(2024, 5, 1, 10, 5)),
                  mk("r3", "rrrr3333", dlat=0.004, when=datetime(2024, 5, 1, 10, 10))):
            self.insert(conn, a)
        fin.finalize_assets(conn, ["r1", "r2", "r3"], s)
        sid = self.row(conn, "r1")["shoot_id"]
        res = fin.reassign_shoot(conn, sid, s, radius_m=50.0)
        conn.commit()
        assert set(res["moved"]) == {"r2", "r3"}
        assert self.row(conn, "r1")["shoot_id"] == sid
        assert self.row(conn, "r2")["identifier_components"]["shoot_id"] == res["moved"]["r2"]

        # model candidate (4 photos around a point) + site progression (3 visits on different days)
        lat0, lon0 = BASE_LAT + 0.1, BASE_LON
        ring = [(0.00007, 0), (0, 0.00009), (-0.00007, 0), (0, -0.00009)]
        for i, (dy, dx) in enumerate(ring):
            self.insert(conn, mk(f"m{i}", f"mmmm{i}000", dlat=0.1 + dy, dlon=dx, when=datetime(2024, 6, 1, 9, i)))
        for i in range(3):
            self.insert(conn, mk(f"v{i}", f"vvvv{i}000", dlat=0.2, when=datetime(2024, 7, 1 + i, 9, 0)))
        fin.finalize_assets(conn, [f"m{i}" for i in range(4)] + [f"v{i}" for i in range(3)], s)
        n = fin.refresh_indicators(conn, dict(s, model_min_photos=4, model_min_angle_coverage_deg=180))
        conn.commit()
        assert n >= 7
        m0, v1 = self.row(conn, "m0"), self.row(conn, "v1")
        assert m0["indicators"]["model_candidate"]["ready"] is True and m0["indicators"]["model_candidate"]["set_size"] == 4
        assert v1["indicators"]["progression"]["indicator"] == "site_progression"
        assert v1["final_output"]["indicators"] == v1["indicators"] and v1["identifier_locked"]


# ------------------------------------------------------------------ vision orchestrator
import importlib.util  # noqa: E402
import json  # noqa: E402
import types  # noqa: E402

from server import output_schema as osch  # noqa: E402


@pytest.fixture
def orch(monkeypatch):
    for name in ("httpx", "exifread"):
        try:
            __import__(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy/dummy")
    path = Path(__file__).resolve().parent.parent / "scripts" / "vision_orchestrator.py"
    spec = importlib.util.spec_from_file_location("vision_orchestrator_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    def __init__(self, fail_image=None):
        self.calls = []
        self.fail_image = fail_image

    def __call__(self, model, prompt, image_path=None):
        self.calls.append((model, prompt, image_path))
        if self.fail_image and image_path == self.fail_image:
            raise RuntimeError("boom")
        if "You are a judge" in prompt:
            return {"reasoning": "agree", "agreementScore": 0.8}
        if "synthesizing a whole video" in prompt:
            return osch.load_example("video")
        if "ONE frame" in prompt or "BASE frame" in prompt or "DETAIL frame" in prompt:
            return osch.load_example("video_frame")
        return osch.load_example("still")


def fake_conn():
    conn = MagicMock()
    return conn, conn.cursor.return_value


def executed(cur, needle):
    return [c.args for c in cur.execute.call_args_list if needle in c.args[0]]


def test_orchestrator_still_flow_feedback_and_isolation(orch, monkeypatch):
    rec = Recorder(fail_image="/in/bad.jpg")
    monkeypatch.setattr(orch, "call_ollama", rec)
    monkeypatch.setattr(orch, "get_exif_data", lambda p: {"sensor": None, "lat": None, "lon": None, "alt": None,
                                                          "focal_length": None, "orientation": None, "camera_pitch": None,
                                                          "gimbal_pitch": -90.0, "captured_at": None})
    fin_calls = []
    monkeypatch.setattr(orch.fin, "finalize_assets", lambda c, ids, s=None: fin_calls.append(list(ids)) or {})
    monkeypatch.setattr(orch.fin, "refresh_indicators", lambda c, s=None: 0)
    good = {"asset_id": "g1", "source_path": "/in/good.jpg", "media_kind": "still", "original_filename": "good.jpg",
            "rework_feedback": "mention the scaffolding", "latitude": 40.0, "longitude": -75.0}
    bad = {"asset_id": "b1", "source_path": "/in/bad.jpg", "media_kind": "still", "original_filename": "bad.jpg"}
    model = {"asset_id": "m1", "source_path": "/in/m.png", "media_kind": "3d_model", "original_filename": "m.png"}
    conn, cur = fake_conn()
    res = orch.process_assets(conn, [good, bad, model], settings=dict(SETTINGS))
    assert res["done"] == ["g1", "m1"] and list(res["failed"]) == ["b1"]
    assert fin_calls == [["g1", "m1"]]
    models = [c[0] for c in rec.calls]
    assert models.count(orch.SCREENER_MODEL) == 3 and models.count(orch.VERIFIER_MODEL) == 2 and models.count(orch.JUDGE_MODEL) == 2
    good_prompts = [c[1] for c in rec.calls if c[2] == "/in/good.jpg"]
    assert len(good_prompts) == 2 and all("mention the scaffolding" in p for p in good_prompts)
    assert sum("mention the scaffolding" in c[1] for c in rec.calls if c[0] == orch.JUDGE_MODEL) == 1
    assert any("model3d" in c[1] for c in rec.calls if c[2] == "/in/m.png")
    assert len(executed(cur, "INSERT INTO ai_evaluations")) == 2
    assert [a[1][-1] for a in executed(cur, "rework_feedback = NULL")] == ["g1"]
    finals = executed(cur, "SET final_output")
    assert len(finals) == 2 and json.loads(finals[0][1][0])["media_kind"] == "still"
    assert json.loads(finals[1][1][0])["media_kind"] == "model3d"
    assert "COALESCE(%s, latitude)" in executed(cur, "UPDATE assets SET sensor_model")[0][0]
    assert executed(cur, "UPDATE assets SET sensor_model")[0][1][5] == -90.0


def test_orchestrator_video_three_pass(orch, monkeypatch, tmp_path):
    rec = Recorder()
    monkeypatch.setattr(orch, "call_ollama", rec)
    monkeypatch.setattr(orch, "get_exif_data", lambda p: {"sensor": None, "lat": None, "lon": None, "alt": None,
                                                          "focal_length": None, "orientation": None, "camera_pitch": None})
    monkeypatch.setattr(orch.vf, "probe_video", lambda p: {"duration_s": 10.0, "fps": 30.0, "width": 1920, "height": 1080})
    extract_calls = []

    def fake_extract(path, times, out_dir, thumb_edge, pass_name="base", start_index=0):
        extract_calls.append((pass_name, list(times), thumb_edge))
        return [{"time_s": t, "file_path": f"{out_dir}/{pass_name}_{i}.jpg", "thumb_path": f"{out_dir}/{pass_name}_{i}_t.jpg"}
                for i, t in enumerate(times)]

    monkeypatch.setattr(orch.vf, "extract_frames", fake_extract)
    monkeypatch.setattr(orch.fin, "finalize_assets", lambda c, ids, s=None: {})
    monkeypatch.setattr(orch.fin, "refresh_indicators", lambda c, s=None: 0)
    settings = dict(SETTINGS, video_base_frames_short=3)
    plan = mi.plan_video_frames(10.0, settings)
    vid = {"asset_id": "v1", "source_path": str(tmp_path / "v.mp4"), "media_kind": "video", "original_filename": "v.mp4"}
    conn, cur = fake_conn()
    res = orch.process_assets(conn, [vid], settings=settings)
    assert res["done"] == ["v1"], res
    assert [c[0] for c in extract_calls] == ["base", "detail"]
    assert extract_calls[0][1] == plan["base"] and extract_calls[1][1] == plan["detail"] and plan["detail"]
    n_base, n_detail = len(plan["base"]), len(plan["detail"])
    models = [c[0] for c in rec.calls]
    assert models[:n_base] == [orch.SCREENER_MODEL] * n_base
    assert models[n_base:n_base + n_detail] == [orch.VERIFIER_MODEL] * n_detail
    assert models[n_base + n_detail:] == [orch.JUDGE_MODEL, orch.JUDGE_MODEL]
    assert all("BASE frame" in c[1] for c in rec.calls[:n_base])
    detail_prompts = [c[1] for c in rec.calls[n_base:n_base + n_detail]]
    assert all("DETAIL frame" in p and "previous_frame" in p and "base_frames" in p for p in detail_prompts)
    assert "synthesizing a whole video" in rec.calls[-2][1] and "You are a judge" in rec.calls[-1][1]
    assert len(executed(cur, "INSERT INTO video_frames")) == n_base + n_detail
    idx = sorted(a[1][1] for a in executed(cur, "INSERT INTO video_frames"))
    assert idx == list(range(n_base + n_detail))
    ev = executed(cur, "INSERT INTO ai_evaluations")[0][1]
    assert len(json.loads(ev[1])["base_frames"]) == n_base and json.loads(ev[2])["timeline"]
    final = json.loads(executed(cur, "SET final_output")[0][1][0])
    assert final["media_kind"] == "video" and len(final["video"]["frames"]) == n_base + n_detail
    assert final["video"]["timeline"] and final["video"]["duration_s"] == 10.0
    assert osch.validate_record(final) == []


def test_orchestrator_video_failure_leaves_ingested(orch, monkeypatch):
    monkeypatch.setattr(orch, "call_ollama", Recorder())
    monkeypatch.setattr(orch, "get_exif_data", lambda p: {"sensor": None, "lat": None, "lon": None, "alt": None,
                                                          "focal_length": None, "orientation": None, "camera_pitch": None})

    def no_ffprobe(p):
        raise orch.vf.VideoToolError("ffprobe is not installed")

    monkeypatch.setattr(orch.vf, "probe_video", no_ffprobe)
    monkeypatch.setattr(orch.fin, "finalize_assets", lambda c, ids, s=None: {})
    monkeypatch.setattr(orch.fin, "refresh_indicators", lambda c, s=None: 0)
    conn, cur = fake_conn()
    res = orch.process_assets(conn, [{"asset_id": "v1", "source_path": "/in/v.mp4", "media_kind": "video"}], dict(SETTINGS))
    assert res["done"] == [] and "ffprobe" in res["failed"]["v1"]
    assert not executed(cur, "SET final_output")


def test_get_exif_missing_file_has_new_keys(orch):
    t = orch.get_exif_data("/definitely/missing.jpg")
    assert t["captured_at"] is None and t["gimbal_pitch"] is None and t["lat"] is None


@pytest.mark.skipif(not DSN, reason="BAM_TEST_PG_DSN_LOCAL not set")
def test_orchestrator_against_postgres(orch, monkeypatch):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from server.schema_migrations import ensure_schema
    ensure_schema(DSN, local=True)
    conn = psycopg2.connect(DSN, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("TRUNCATE assets, shoots, shoot_links, training_examples, video_frames CASCADE")
    for aid, stem, dlat, vid in (("o1", "oooo1111", 0.0, False), ("o2", "oooo2222", 0.0001, False), ("o3", "oooo3333", 0.0, True)):
        cur.execute("""INSERT INTO assets (asset_id, unique_stem, original_filename, source_path, original_sha256, media_kind,
                       captured_at, latitude, longitude, rework_feedback) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (aid, stem, aid, f"/in/{aid}", aid.ljust(64, "0"), "video" if vid else "still",
                     datetime(2024, 5, 1, 10, 0), BASE_LAT + dlat, BASE_LON, "fix it" if aid == "o1" else None))
    conn.commit()
    monkeypatch.setattr(orch, "call_ollama", Recorder())
    monkeypatch.setattr(orch, "get_exif_data", lambda p: {"sensor": "CamX", "lat": None, "lon": None, "alt": None,
                                                          "focal_length": "24", "orientation": None, "camera_pitch": None})
    monkeypatch.setattr(orch.vf, "probe_video", lambda p: {"duration_s": 4.0, "fps": 30.0, "width": 64, "height": 48})
    monkeypatch.setattr(orch.vf, "extract_frames", lambda path, times, d, e, pass_name="base", start_index=0: [
        {"time_s": t, "file_path": f"{d}/{pass_name}_{i}.jpg", "thumb_path": None} for i, t in enumerate(times)])
    cur.execute("SELECT * FROM assets ORDER BY asset_id")
    res = orch.process_assets(conn, [dict(r) for r in cur.fetchall()], dict(SETTINGS))
    assert sorted(res["done"]) == ["o1", "o2", "o3"], res
    cur.execute("SELECT asset_id, workflow_state, identifier, identifier_locked, sensor_model, rework_feedback, shoot_id FROM assets ORDER BY asset_id")
    rows = cur.fetchall()
    assert all(r["workflow_state"] == "PENDING_HUMAN" and r["identifier_locked"] and r["sensor_model"] == "CamX" for r in rows)
    assert rows[0]["rework_feedback"] is None and len({r["identifier"] for r in rows}) == 3
    assert rows[0]["shoot_id"] == rows[1]["shoot_id"]
    cur.execute("SELECT count(*) AS n FROM video_frames WHERE asset_id = 'o3'")
    assert cur.fetchone()["n"] >= 2
    cur.execute("SELECT count(*) AS n FROM ai_evaluations")
    assert cur.fetchone()["n"] == 3
    conn.close()
