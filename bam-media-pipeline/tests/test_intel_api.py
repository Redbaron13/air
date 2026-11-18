import copy
import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(int(), str(ROOT))

import psycopg2.extras as _pg_extras  # may be a stub installed by other test modules

if not hasattr(_pg_extras, "Json"):
    class _Json:
        def __init__(self, adapted, dumps=None):
            self.adapted = adapted

    _pg_extras.Json = _Json
if not hasattr(_pg_extras, "RealDictCursor"):
    _pg_extras.RealDictCursor = object

from fastapi import FastAPI  # noqa
from fastapi.testclient import TestClient  # noqa

import server  # noqa
from server import intel_api, output_schema, review_api as ra  # noqa

NF = ra.NOT_FOUND


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.db.calls.append((sql, params))
        self.rows = list(self.db.handler(sql, params) or [])

    def fetchone(self):
        return self.rows[int()] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeDB:
    def __init__(self, handler=None):
        self.handler = handler or (lambda sql, params: [])
        self.calls = []
        self.commits = int()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def matching(self, text):
        return [c for c in self.calls if text in c[int()]]


def _adapted(v):
    return getattr(v, "adapted", v)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(ra, "EXPORTS_DIR", tmp_path)
    monkeypatch.setattr(ra, "SETTINGS_FILE", tmp_path / "review_settings.json")
    app = FastAPI()
    app.include_router(ra.router)
    app.include_router(intel_api.router)
    return TestClient(app)


def use_db(monkeypatch, handler=None):
    db = FakeDB(handler)
    monkeypatch.setattr(ra, "_conn", lambda: db)
    monkeypatch.setattr(ra, "_fetch", lambda sql, params=(): [dict(r) for r in (db.handler(sql, params) or [])])
    return db


def fake_finalize(monkeypatch):
    mod = types.ModuleType("server.finalize")
    mod.examples = []
    mod.record_training_example = lambda conn, asset_id, source, before, after, feedback=None, notes=None: (
        mod.examples.append({"asset_id": asset_id, "source": source, "before": before, "after": after,
                             "feedback": feedback, "notes": notes}) or "ex-1")
    mod.refresh_indicators = lambda conn, settings=None: 7
    mod.finalize_assets = lambda conn, ids, settings=None: {"finalized": len(ids)}
    mod.apply_split = lambda conn, shoot_id, a, b, settings=None, project_name_b=None: {
        "new_shoot_id": "S-B", "moved": len(b), "name": project_name_b}
    mod.reassign_shoot = lambda conn, shoot_id, settings=None, radius_m=None: {"shoot_id": shoot_id, "radius_m": radius_m}
    monkeypatch.setitem(sys.modules, "server.finalize", mod)
    monkeypatch.setattr(server, "finalize", mod, raising=False)
    return mod


# ------------------------------------------------------------------ search
def test_search_result_shape(client, monkeypatch):
    use_db(monkeypatch, lambda sql, p: [{
        "asset_id": "a1", "identifier": "ID-1", "original_filename": "roof_a.jpg", "media_kind": "still",
        "workflow_state": "APPROVED", "shoot_id": "S1", "describes": "a tall roof_a view", "alt": None,
        "notes": None, "latitude": 1.5, "longitude": 2.5, "captured_at": None, "indicators": None,
        "final_output": {"classification": {"shot_type": "aerial", "media_genre": "site"}}, "total_count": 3}])
    body = client.get("/api/review/intel/search", params={"q": "roof_a"}).json()
    assert body["total"] == 3
    res = body["results"][int()]
    assert res["shot_type"] == "aerial" and res["genre"] == "site" and "roof_a" in res["snippet"]


def test_search_params(client, monkeypatch):
    seen = {}

    def fetch(sql, params=()):
        seen["sql"], seen["params"] = sql, params
        return []

    monkeypatch.setattr(ra, "_fetch", fetch)
    r = client.get("/api/review/intel/search", params={"q": "a%_b\\c", "kind": "still", "shot_type": "aerial", "limit": 5})
    assert r.is_success and r.json() == {"results": [], "total": int()}
    assert "%s" in seen["sql"] and "a%_b" not in seen["sql"]
    assert "ILIKE" in seen["sql"] and "ESCAPE" in seen["sql"]
    assert seen["params"][int()] == "%a\\%\\_b\\\\c%"
    assert seen["params"].count("%a\\%\\_b\\\\c%") == 6
    assert "still" in seen["params"] and "aerial" in seen["params"]
    assert seen["params"][-1] == 5
    assert client.get("/api/review/intel/search", params={"limit": 5 * (5 + 5) ** 2 + 1}).status_code == 422


# ---------------------------------------------------------------- settings
def test_settings_specs_roundtrip(client):
    body = client.get("/api/review/settings").json()
    assert {"settings", "defaults", "specs"} <= set(body)
    assert body["specs"] and body["settings"]["group_radius_m"] == body["defaults"]["group_radius_m"]
    r = client.put("/api/review/settings", json={"group_radius_m": 25, "min_alt_length": 12})
    assert r.is_success and r.json()["settings"]["group_radius_m"] == 25
    assert json.loads(ra.SETTINGS_FILE.read_text())["min_alt_length"] == 12
    assert client.get("/api/review/settings").json()["settings"]["group_radius_m"] == 25
    assert client.delete("/api/review/settings").json()["settings"]["group_radius_m"] == body["defaults"]["group_radius_m"]
    assert not ra.SETTINGS_FILE.exists()


@pytest.mark.parametrize("payload", [{"group_radius_m": "abc"}, {"group_radius_m": -1}, {"low_agreement": 5},
                                     {"min_alt_length": 2.5}, {"stuck_minutes": None}])
def test_settings_validation_errors(client, payload):
    assert client.put("/api/review/settings", json=payload).status_code == 422
    assert not ra.SETTINGS_FILE.exists()


# ---------------------------------------------------------------- decisions
def _record():
    rec = output_schema.assemble_record(
        "still", {"asset_id": "a1", "original_filename": "a.jpg"}, {}, output_schema.load_example("still"), {}, {})
    assert output_schema.validate_record(rec) == []
    rec["identifier"] = "BAM-S1-PHO-X"
    rec["identifier_components"] = {"prefix": "BAM", "shoot_id": "S1", "kind_code": "PHO"}
    return rec


def _decision_handler(rec):
    def handler(sql, params):
        if "FROM assets a WHERE asset_id" in sql:
            return [{"workflow_state": "PENDING_HUMAN", "describes": "old", "alt": "old alt", "service_tags": [], "final_output": rec,
                     "media_kind": "still", "judge": None}]
        return []
    return handler


def test_decision_edit_keeps_identifier_and_records_example(client, monkeypatch):
    fin = fake_finalize(monkeypatch)
    rec = _record()
    db = use_db(monkeypatch, _decision_handler(rec))
    edited = copy.deepcopy(rec)
    edited["identifier"] = "HACKED"
    edited["identifier_components"] = {"prefix": "X"}
    edited["content"]["description"] = "A human rewritten description."
    r = client.post("/api/review/assets/a1/decision",
                    json={"action": "approve", "expected_state": "PENDING_HUMAN", "rights_confirmed": True,
                          "metadata_confirmed": True, "final_output": edited, "notes": "n1", "feedback": "good fix",
                          "training_candidate": True})
    assert r.is_success, r.text
    assert r.json()["state"] == "APPROVED"
    upd = db.matching("SET final_output")
    saved = _adapted(upd[int()][1][int()])
    assert saved["identifier"] == "BAM-S1-PHO-X"
    assert saved["identifier_components"] == rec["identifier_components"]
    assert saved["content"]["description"] == "A human rewritten description."
    assert saved["human"]["edited"] is True and saved["human"]["training_candidate"] is True
    assert saved["human"]["notes"] == "n1" and saved["human"]["feedback"][int()]["text"] == "good fix"
    assert [e["source"] for e in fin.examples] == ["edit"]
    assert fin.examples[int()]["feedback"] == "good fix"
    assert db.matching("INSERT INTO human_corrections") and db.commits == 1
    tc = db.matching("training_candidate = %s")
    assert tc and tc[int()][1][2] is True
    sync = db.matching("UPDATE assets SET describes")
    assert sync[int()][1][int()] == "A human rewritten description."


def test_decision_invalid_record_rejected(client, monkeypatch):
    fake_finalize(monkeypatch)
    rec = _record()
    use_db(monkeypatch, _decision_handler(rec))
    bad = copy.deepcopy(rec)
    del bad["shoot"]
    r = client.post("/api/review/assets/a1/decision",
                    json={"action": "approve", "expected_state": "PENDING_HUMAN", "rights_confirmed": True,
                          "metadata_confirmed": True, "final_output": bad})
    assert r.status_code == 422


def test_decision_flag_without_changes(client, monkeypatch):
    fin = fake_finalize(monkeypatch)
    rec = _record()
    use_db(monkeypatch, _decision_handler(rec))
    r = client.post("/api/review/assets/a1/decision",
                    json={"action": "approve", "expected_state": "PENDING_HUMAN", "rights_confirmed": True,
                          "metadata_confirmed": True, "final_output": rec, "training_candidate": True})
    assert r.is_success, r.text
    assert [e["source"] for e in fin.examples] == ["flag"]


def test_decision_rework_with_feedback(client, monkeypatch):
    fin = fake_finalize(monkeypatch)
    db = use_db(monkeypatch, _decision_handler(_record()))
    r = client.post("/api/review/assets/a1/decision",
                    json={"action": "rework", "expected_state": "PENDING_HUMAN",
                          "reprocess_feedback": "look closer"})
    assert r.is_success and r.json()["state"] == "INGESTED"
    rw = db.matching("rework_feedback")
    assert rw and rw[int()][1][int()] == "look closer"
    assert [(e["source"], e["feedback"]) for e in fin.examples] == [("rework", "look closer")]


def test_decision_unknown_asset(client, monkeypatch):
    use_db(monkeypatch)
    assert client.post("/api/review/assets/zz/decision",
                       json={"action": "reject", "expected_state": "PENDING_HUMAN"}).status_code == NF


# ------------------------------------------------------------ split/reassign
def _shoot_handler(sql, params):
    if "FROM shoots WHERE" in sql:
        return [{"shoot_id": params[int()]}] if params[int()] == "S1" else []
    if "FROM assets WHERE asset_id = ANY" in sql:
        return [{"asset_id": i, "shoot_id": "S1"} for i in params[int()] if i != "ghost"]
    return []


def test_split_validation_and_success(client, monkeypatch):
    fake_finalize(monkeypatch)
    db = use_db(monkeypatch, _shoot_handler)
    url = "/api/review/intel/shoots/split"
    assert client.post(url, json={"shoot_id": "S1", "group_a": [], "group_b": ["b"]}).status_code == 422
    assert client.post(url, json={"shoot_id": "S1", "group_a": ["a"], "group_b": ["a"]}).status_code == 422
    assert client.post(url, json={"shoot_id": "S1", "group_a": ["a"], "group_b": ["ghost"]}).status_code == 422
    assert client.post(url, json={"shoot_id": "nope", "group_a": ["a"], "group_b": ["b"]}).status_code == NF
    assert db.commits == int()
    r = client.post(url, json={"shoot_id": "S1", "group_a": ["a"], "group_b": ["b"], "project_name_b": "P"})
    assert r.is_success and r.json()["new_shoot_id"] == "S-B" and db.commits == 1


def test_reassign(client, monkeypatch):
    fake_finalize(monkeypatch)
    db = use_db(monkeypatch, _shoot_handler)
    url = "/api/review/intel/shoots/reassign"
    assert client.post(url, json={"shoot_id": "nope"}).status_code == NF
    assert client.post(url, json={"shoot_id": "S1", "radius_m": -3}).status_code == 422
    r = client.post(url, json={"shoot_id": "S1", "radius_m": 25})
    assert r.is_success and r.json()["radius_m"] == 25 and db.commits == 1


def test_batch_finalize_and_refresh(client, monkeypatch):
    fake_finalize(monkeypatch)
    use_db(monkeypatch, lambda sql, p: [{"asset_id": "q1"}, {"asset_id": "q2"}] if "APPROVED" in sql else [])
    r = client.post("/api/review/intel/batch/finalize", json={"asset_ids": ["x"]})
    assert r.is_success and r.json()["requested"] == 1 and r.json()["updated"] == 7
    assert client.post("/api/review/intel/batch/finalize", json={}).json()["requested"] == 2
    assert client.post("/api/review/intel/batch/finalize", json={"asset_ids": []}).status_code == 422
    assert client.post("/api/review/intel/refresh").json() == {"updated": 7}


# ------------------------------------------------------------------ frames
def test_frame_path_traversal_rejected(client, monkeypatch, tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "ok.jpg").write_bytes(b"jpegdata")
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    paths = {1: str(frames / "ok.jpg"), 2: str(outside), 3: str(frames / ".." / "secret.jpg"), 4: "frames/ok.jpg"}

    def handler(sql, params):
        if "file_path, thumb_path" in sql:
            idx = params[1]
            return [{"file_path": paths[idx], "thumb_path": paths[idx]}] if idx in paths else []
        return []

    use_db(monkeypatch, handler)
    base = "/api/review/assets/a1/frames"
    assert client.get(f"{base}/1/thumb").content == b"jpegdata"
    assert client.get(f"{base}/4/image").content == b"jpegdata"
    for i in (2, 3):
        assert client.get(f"{base}/{i}/thumb").status_code == NF
        assert client.get(f"{base}/{i}/image").status_code == NF
    assert client.get(f"{base}/9/image").status_code == NF


def test_frames_listing(client, monkeypatch):
    use_db(monkeypatch, lambda sql, p: [{"frame_id": 5, "frame_index": 2, "time_s": 1.5, "pass_name": "base",
                                         "output": {"a": 1}}])
    f = client.get("/api/review/assets/a1/frames").json()["frames"][int()]
    assert f["pass"] == "base" and f["thumb_url"].endswith("/2/thumb") and f["image_url"].endswith("/2/image")


def test_media_supports_range(client, monkeypatch, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(bytes(range(64)))
    monkeypatch.setattr(ra, "ACTIVE_DIR", tmp_path.resolve())
    use_db(monkeypatch, lambda sql, p: [{"source_path": str(media)}])
    r = client.get("/api/review/assets/a1/media", headers={"Range": "bytes=4-7"})
    assert r.status_code == 2 * (5 + 5) ** 2 + 6
    assert r.content == bytes([4, 5, 6, 7])


# ---------------------------------------------------------------- training
def test_training_export_ndjson(client, monkeypatch):
    rows = [{"asset_id": "a1", "identifier": "I1", "media_kind": "still", "source": "edit",
             "before_output": {"x": 1}, "after_output": {"x": 2}, "feedback": "f", "notes": None,
             "original_ref": None, "created_at": None},
            {"asset_id": "a2", "identifier": None, "media_kind": "video", "source": "flag", "before_output": None,
             "after_output": None, "feedback": None, "notes": "n", "original_ref": None, "created_at": None}]
    seen = {}

    def fetch(sql, params=()):
        seen["sql"] = sql
        return rows

    monkeypatch.setattr(ra, "_fetch", fetch)
    r = client.get("/api/review/intel/training/export", params={"only": "edited"})
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = r.text.strip().split("\n")
    assert len(lines) == 2 and json.loads(lines[int()])["after_output"] == {"x": 2}
    assert "source = 'edit'" in seen["sql"]
    assert client.get("/api/review/intel/training/export", params={"only": "bogus"}).status_code == 422


def test_training_stats(client, monkeypatch):
    def fetch(sql, params=()):
        if "GROUP BY source" in sql:
            return [{"source": "edit", "n": 3}, {"source": "flag", "n": 2}]
        return [{"n": 4}]

    monkeypatch.setattr(ra, "_fetch", fetch)
    assert client.get("/api/review/intel/training/stats").json() == {
        "by_source": {"edit": 3, "flag": 2}, "flagged_assets": 4, "total": 5}


# -------------------------------------------------------------------- sync
def test_sync_unconfigured(client, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    st = client.get("/api/review/intel/sync/status").json()
    assert st["configured"] is False and st["pending"] == int()
    r = client.post("/api/review/intel/sync/run")
    assert r.is_success and r.json()["remote"] == "unconfigured"


def test_sync_run_never_raises(client, monkeypatch):
    from server import db_sync

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(db_sync, "run_sync", boom)
    r = client.post("/api/review/intel/sync/run")
    assert r.is_success and r.json()["remote"] == "unreachable"


# ----------------------------------------------------- optional real Postgres
@pytest.mark.skipif(not os.getenv("BAM_TEST_PG_DSN_LOCAL"), reason="BAM_TEST_PG_DSN_LOCAL not set")
class TestPostgresIntegration:
    def test_search_runs_against_real_schema(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", os.environ["BAM_TEST_PG_DSN_LOCAL"])
        r = client.get("/api/review/intel/search", params={"q": "x%_\\", "limit": 3})
        assert r.is_success and "results" in r.json()
        assert client.get("/api/review/intel/shoots").is_success
        assert client.get("/api/review/intel/training/stats").is_success
