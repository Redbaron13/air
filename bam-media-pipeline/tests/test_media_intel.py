import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import media_intel as mi  # noqa: E402

S = dict(mi.DEFAULT_SETTINGS)
T0 = datetime(2025, 5, 10, 10, 0)
LAT, LON = 40.0, -74.0
M = 0.00001  # about 1.1 m of latitude


def a(i, dlat=0.0, dlon=0.0, t=T0, **kw):
    base = {"asset_id": i, "media_kind": "still", "latitude": LAT + dlat, "longitude": LON + dlon,
            "captured_at": t, "focal_length": "24 mm", "gimbal_pitch": -90, "shoot_id": "S1", "final_output": None}
    base.update(kw)
    return base


class SettingsTests(unittest.TestCase):
    def test_defaults_and_specs(self):
        self.assertEqual(set(mi.DEFAULT_SETTINGS), {s["key"] for s in mi.SETTING_SPECS})
        self.assertEqual(mi.DEFAULT_SETTINGS["group_radius_m"], 150.0)
        self.assertIsNone(mi.DEFAULT_SETTINGS["shoot_radius_m"])
        self.assertEqual(mi.DEFAULT_SETTINGS["video_thumb_edge"], 320)

    def test_validate_ok_and_errors(self):
        self.assertEqual(mi.validate_settings({})["model_min_photos"], 12)
        self.assertEqual(mi.validate_settings({"model_min_photos": 5})["model_min_photos"], 5)
        self.assertIsNone(mi.validate_settings({"shoot_radius_m": None})["shoot_radius_m"])
        with self.assertRaises(ValueError):
            mi.validate_settings({"model_min_photos": 99999})
        with self.assertRaises(ValueError):
            mi.validate_settings({"model_min_photos": 2.5})
        with self.assertRaises(ValueError):
            mi.validate_settings({"stuck_minutes": None})

    def test_load_settings_file_env_and_invalid(self):
        with tempfile.TemporaryDirectory(dir=".") as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps({"model_min_photos": 7, "bogus": 1, "view_radius_m": -5}))
            s = mi.load_settings(str(p))
            self.assertEqual(s["model_min_photos"], 7)
            self.assertEqual(s["view_radius_m"], 15)
            self.assertNotIn("bogus", s)
            p.write_text("not json")
            self.assertEqual(mi.load_settings(str(p)), mi.DEFAULT_SETTINGS)
        self.assertEqual(mi.load_settings("/nonexistent/x.json"), mi.DEFAULT_SETTINGS)


class ClassifyTests(unittest.TestCase):
    def test_nadir(self):
        r = mi.classify_shot_from_exif({"altitude_m": 50, "gimbal_pitch_deg": -90})
        self.assertEqual((r["shot_type"], r["camera_perspective"]), ("aerial_nadir", "nadir"))
        self.assertTrue(r["basis"])

    def test_oblique_and_low_oblique(self):
        self.assertEqual(mi.classify_shot_from_exif({"altitude_m": 50, "gimbal_pitch_deg": -45})["shot_type"], "aerial_oblique")
        self.assertEqual(mi.classify_shot_from_exif({"altitude_m": 5, "gimbal_pitch_deg": -45})["shot_type"], "aerial_low_oblique")

    def test_ground_and_unknown(self):
        self.assertEqual(mi.classify_shot_from_exif({"altitude_m": 1.5})["camera_perspective"], "ground")
        r = mi.classify_shot_from_exif({"altitude_m": 40})
        self.assertEqual(r["camera_perspective"], "unknown")
        self.assertEqual(mi.classify_shot_from_exif({})["shot_type"], "unknown")


class ShootTests(unittest.TestCase):
    def test_make_shoot_id(self):
        sid = mi.make_shoot_id(LAT, LON, T0)
        self.assertRegex(sid, r"^SH250510-[0-9A-F]{4}$")
        self.assertEqual(sid, mi.make_shoot_id(LAT, LON, "2025-05-10T10:00:00"))
        self.assertEqual(mi.make_shoot_id(0, 0, T0), "SH250510-NOGPS")
        self.assertEqual(mi.make_shoot_id(None, None, None), "SH000000-NOGPS")

    def test_first_photo_creates_shoot_and_later_reuse(self):
        r = mi.assign_shoot(a("p1"), [], S)
        self.assertTrue(r["is_new"])
        ns = r["new_shoot"]
        self.assertEqual(ns["radius_meters"], 300.0)
        self.assertEqual(ns["centroid_lat"], LAT)
        shoots = [ns]
        r2 = mi.assign_shoot(a("p2", dlat=10 * M), shoots, S)
        self.assertFalse(r2["is_new"])
        self.assertEqual(r2["shoot_id"], ns["shoot_id"])
        self.assertAlmostEqual(r2["distance_m"], 11., delta=2)
        self.assertIsNone(r2["new_shoot"])

    def test_far_photo_new_shoot(self):
        ns = mi.assign_shoot(a("p1"), [], S)["new_shoot"]
        r = mi.assign_shoot(a("p2", dlat=0.01), [ns], S)
        self.assertTrue(r["is_new"])
        self.assertNotEqual(r["shoot_id"], ns["shoot_id"])

    def test_time_window(self):
        ns = mi.assign_shoot(a("p1"), [], S)["new_shoot"]
        late = a("p2", t=T0 + timedelta(days=3))
        self.assertTrue(mi.assign_shoot(late, [ns], S)["is_new"])
        self.assertFalse(mi.assign_shoot(late, [ns], dict(S, shoot_time_window_hours=0))["is_new"])

    def test_nearest_wins(self):
        s1 = {"shoot_id": "A", "centroid_lat": LAT, "centroid_lon": LON, "radius_meters": 500}
        s2 = {"shoot_id": "B", "centroid_lat": LAT + 10 * M, "centroid_lon": LON, "radius_meters": 500}
        r = mi.assign_shoot(a("p", dlat=8 * M), [s1, s2], S)
        self.assertEqual(r["shoot_id"], "B")

    def test_no_gps(self):
        x = a("n1", latitude=0, longitude=0)
        r = mi.assign_shoot(x, [], S)
        self.assertTrue(r["is_new"])
        self.assertEqual(r["shoot_id"], "SH250510-NOGPS")
        self.assertIsNone(r["new_shoot"]["centroid_lat"])
        r2 = mi.assign_shoot(x, [r["new_shoot"]], S)
        self.assertFalse(r2["is_new"])

    def test_related_shoots(self):
        cur = {"shoot_id": "C", "centroid_lat": LAT, "centroid_lon": LON, "first_captured_at": T0}
        others = [
            {"shoot_id": "E", "centroid_lat": LAT, "centroid_lon": LON, "first_captured_at": T0 - timedelta(days=5)},
            {"shoot_id": "L", "centroid_lat": LAT, "centroid_lon": LON, "first_captured_at": T0 + timedelta(days=2)},
            {"shoot_id": "N", "centroid_lat": LAT, "centroid_lon": LON, "first_captured_at": T0 + timedelta(hours=1)},
            {"shoot_id": "F", "centroid_lat": LAT + 1, "centroid_lon": LON, "first_captured_at": T0},
        ]
        rel = {r["shoot_id"]: r for r in mi.related_shoots(cur, others + [cur], S)}
        self.assertEqual(set(rel), {"E", "L", "N"})
        self.assertEqual(rel["E"]["relation"], "same_place_earlier")
        self.assertEqual(rel["E"]["days_apart"], 5)
        self.assertEqual(rel["L"]["relation"], "same_place_later")
        self.assertEqual(rel["N"]["relation"], "nearby")

    def test_split_shoot(self):
        assets = [a("a1"), a("a2", dlat=M), a("b1", dlat=0.002, t=T0 + timedelta(hours=1)), a("b2", dlat=0.002 + M),
                  a("x1", dlat=0.0019), a("x2", dlat=M), a("nog", latitude=0, longitude=0)]
        r = mi.split_shoot(assets, ["a1", "a2"], ["b1", "b2"], "S1", S)
        self.assertEqual(r["keep"]["shoot_id"], "S1")
        self.assertNotEqual(r["new"]["shoot_id"], "S1")
        self.assertEqual(r["assignments"]["a1"], "S1")
        self.assertEqual(r["assignments"]["b1"], r["new"]["shoot_id"])
        self.assertEqual(r["assignments"]["x1"], r["new"]["shoot_id"])
        self.assertEqual(r["assignments"]["x2"], "S1")
        self.assertEqual(r["unassigned"], ["nog"])
        self.assertGreaterEqual(r["keep"]["radius_meters"], 25)
        r2 = mi.split_shoot(assets, ["a1"], ["b1"], "S1", S, new_shoot_id="NEW")
        self.assertEqual(r2["new"]["shoot_id"], "NEW")


class IdentifierTests(unittest.TestCase):
    def test_build(self):
        rec = {"content": {"primary_subjects": ["bridge"]}}
        ident, c = mi.build_identifier({"asset_id": "abc-def-1234567800", "media_kind": "still"}, rec, "SH250501-AAAA")
        self.assertEqual(c["kind_code"], "PHO")
        self.assertEqual(c["content_code"], "BRIDGE")
        self.assertEqual(c["scene"], "S")
        self.assertEqual(c["stem"], "12345678")
        self.assertEqual(ident, "BAM-SH250501-AAAA-PHO-BRIDGE-S-12345678")

    def test_content_code_variants(self):
        base = {"asset_id": "x-yyyyyyyy", "media_kind": "video", "unique_stem": "STEM1"}
        _, c = mi.build_identifier(base, {"content": {"primary_subjects": ["construction crane"]}}, "S", "7")
        self.assertLessEqual(len(c["content_code"]), 6)
        self.assertEqual(c["scene"], "G7")
        self.assertEqual(c["kind_code"], "VID")
        self.assertEqual(c["stem"], "STEM1")
        _, c = mi.build_identifier(base, {"classification": {"media_genre": "roof"}}, "S")
        self.assertEqual(c["content_code"], "ROOF")
        _, c = mi.build_identifier(dict(base, media_kind="3d_model"), None, "S")
        self.assertEqual((c["content_code"], c["kind_code"]), ("GEN", "MDL"))

    def test_lock_and_relocate(self):
        _, c = mi.build_identifier({"asset_id": "a-bbbbbbbb"}, None, "SHA")
        ident, c2 = mi.relocate_identifier(c, "SHB")
        self.assertTrue(mi.can_modify_identifier(c, c2))
        self.assertIn("SHB", ident)
        self.assertEqual(c["shoot_id"], "SHA")
        self.assertFalse(mi.can_modify_identifier(c, dict(c, content_code="XYZ")))
        self.assertFalse(mi.can_modify_identifier(c, dict(c, stem="other")))


class ModelTests(unittest.TestCase):
    def ring(self, n, prefix="r", span=30, radius_m=20):
        import math
        out = []
        for k in range(n):
            ang = 2 * math.pi * k / n
            out.append(a(f"{prefix}{k}", dlat=radius_m * M * math.cos(ang), dlon=radius_m * M * math.sin(ang) / math.cos(math.radians(LAT)),
                         t=T0 + timedelta(minutes=span * k / max(n, 1))))
        return out

    def test_ready(self):
        sets = mi.find_model_candidates(self.ring(14), S)
        self.assertEqual(len(sets), 1)
        s = sets[0]
        self.assertTrue(s["ready"])
        self.assertEqual(s["missing"], [])
        self.assertEqual(s["stats"]["photo_count"], 14)
        self.assertGreater(s["stats"]["angle_coverage_deg"], 300)
        self.assertTrue(s["set_key"].startswith("MS-"))
        self.assertEqual(s["set_key"], mi.find_model_candidates(list(reversed(self.ring(14))), S)[0]["set_key"])

    def test_not_ready_missing(self):
        sets = mi.find_model_candidates(self.ring(8), S)
        self.assertFalse(sets[0]["ready"])
        self.assertIn("need 4 more photos", sets[0]["missing"])
        self.assertLess(sets[0]["score"], 1)

    def test_narrow_angle(self):
        line = [a(f"l{k}", dlat=k * M, t=T0) for k in range(14)]
        s = mi.find_model_candidates(line, dict(S, model_min_angle_coverage_deg=270))[0]
        self.assertFalse(s["ready"])
        self.assertTrue(any("angle" in m for m in s["missing"]))

    def test_filters(self):
        ring = self.ring(14)
        ring[0]["final_output"] = {"photogrammetry": {"suitable_for_model": False}}
        ring[1]["media_kind"] = "video"
        ring[2]["latitude"] = ring[2]["longitude"] = 0
        ring[3]["focal_length"] = 70
        s = mi.find_model_candidates(ring, S)[0]
        self.assertEqual(s["stats"]["photo_count"], 10)
        self.assertEqual(mi.find_model_candidates(ring[:2], S), [])

    def test_span_and_scope(self):
        ring = self.ring(12)
        ring[0]["captured_at"] = T0 - timedelta(days=2)
        self.assertEqual(mi.find_model_candidates(ring, S)[0]["stats"]["photo_count"], 11)
        self.assertEqual(mi.find_model_candidates(ring, S, shoot_id="nope"), [])
        self.assertEqual(len(mi.find_model_candidates(ring, S, seed_asset_id="r5")), 1)
        far = [dict(x, asset_id="f" + x["asset_id"], latitude=x["latitude"] + 0.1, shoot_id="S2") for x in self.ring(12)]
        both = mi.find_model_candidates(ring + far, S)
        self.assertEqual(len(both), 2)
        self.assertEqual(len(mi.find_model_candidates(ring + far, S, shoot_id="S2")), 1)
        self.assertEqual(len(mi.find_model_candidates(ring + far, S, center=(LAT, LON), radius_m=200)), 1)


class ViewAndProgressionTests(unittest.TestCase):
    def test_group_views(self):
        subj = lambda *s: {"content": {"primary_subjects": list(s)}}
        items = [a("v1", final_output=subj("tower", "crane")), a("v2", dlat=M, final_output=subj("tower")),
                 a("v3", dlat=2 * M, gimbal_pitch=-30), a("v4", dlat=3 * M, final_output=subj("pool")),
                 a("far", dlat=0.01), a("nog", latitude=0, longitude=0)]
        groups = mi.group_views(items, S)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["asset_ids"], ["v1", "v2"])
        self.assertEqual(mi.group_views([], S), [])

    def visits(self, days):
        items = []
        for d in days:
            for k in range(2):
                items.append(a(f"d{d}_{k}", dlat=k * M, t=T0 + timedelta(days=d, minutes=k)))
        return items

    def test_two_visits_before_after(self):
        locs = mi.find_progression(self.visits([0, 5]), S)
        self.assertEqual(len(locs), 1)
        self.assertEqual(locs[0]["indicator"], "before_after")
        pair = locs[0]["view_groups"][0]["pairs"][0]
        self.assertEqual(pair["days_apart"], 5)

    def test_three_visits_site_progression(self):
        loc = mi.find_progression(self.visits([0, 5, 12]), S)[0]
        self.assertEqual(loc["indicator"], "site_progression")
        self.assertEqual([v["date"] for v in loc["visits"]], ["2025-05-10", "2025-05-15", "2025-05-22"])
        self.assertEqual(len(loc["view_groups"][0]["pairs"]), 3)

    def test_same_day_one_visit_and_string_dates(self):
        items = self.visits([0])
        items[1]["captured_at"] = "2025-05-10T15:00:00Z"
        loc = mi.find_progression(items, S)[0]
        self.assertEqual(loc["indicator"], "none")
        self.assertEqual(len(loc["visits"]), 1)
        self.assertEqual(mi.find_progression([], S), [])

    def test_shoot_scope_includes_other_shoots(self):
        items = self.visits([0, 5])
        for x in items:
            x["shoot_id"] = "S1" if "d0" in x["asset_id"] else "S2"
        loc = mi.find_progression(items, S, shoot_id="S1")
        self.assertEqual(len(loc[0]["visits"]), 2)
        self.assertEqual(mi.find_progression(items, S, shoot_id="none"), [])

    def test_compute_indicators(self):
        items = self.visits([0, 5, 12])
        items.append(a("lonely", dlat=0.01))
        ind = mi.compute_indicators(items, S)
        self.assertEqual(ind["d0_0"]["progression"]["indicator"], "site_progression")
        self.assertEqual(ind["d0_0"]["progression"]["visit_count"], 3)
        self.assertEqual(ind["d0_0"]["view_group"]["size"], 6)
        self.assertIsNone(ind["d0_0"]["model_candidate"])
        self.assertIsNone(ind["lonely"]["view_group"])
        ring = ModelTests().ring(12)
        mc = mi.compute_indicators(ring, S)["r0"]["model_candidate"]
        self.assertEqual(mc["set_size"], 12)


class VideoTests(unittest.TestCase):
    def test_plan(self):
        p = mi.plan_video_frames(30, S)
        self.assertEqual(len(p["base"]), 6)
        self.assertLess(p["base"][0], 0.5)
        self.assertLess(p["base"][-1], 30)
        self.assertGreater(p["base"][-1], 29)
        self.assertTrue(all(t < 30 for t in p["detail"]))
        self.assertTrue(all(abs(t - b) >= 0.25 for t in p["detail"] for b in p["base"]))

    def test_long_and_caps(self):
        p = mi.plan_video_frames(600, S)
        self.assertEqual(len(p["base"]), 12)
        self.assertLessEqual(len(p["detail"]), 60)

    def test_very_short_and_zero(self):
        p = mi.plan_video_frames(0.4, S)
        self.assertTrue(2 <= len(p["base"]) <= 6)
        self.assertTrue(all(0 <= t < 0.4 for t in p["base"] + p["detail"]))
        self.assertEqual(mi.plan_video_frames(0, S)["base"], [])


class RecordTests(unittest.TestCase):
    def test_diff(self):
        b = {"a": {"b": 1, "c": [1, 2]}, "x": 1}
        c = {"a": {"b": 2, "c": [1, 2, 3]}, "y": 5}
        d = {r["path"]: r for r in mi.diff_records(b, c)}
        self.assertEqual(set(d), {"a.b", "a.c", "x", "y"})
        self.assertEqual(d["a.c"]["after"], [1, 2, 3])
        self.assertEqual(d["y"]["before"], None)
        self.assertEqual(mi.diff_records(b, b), [])

    def test_merge_human_edit(self):
        before = {"identifier": "A", "identifier_components": {"k": 1}, "schema_version": 2, "media_kind": "still", "capture": {"x": 1}}
        after = {"identifier": "HACK", "identifier_components": {}, "schema_version": 9, "media_kind": "video", "capture": {"x": 2}, "note": "hi"}
        m = mi.merge_human_edit(before, after)
        self.assertEqual(m["identifier"], "A")
        self.assertEqual(m["identifier_components"], {"k": 1})
        self.assertEqual(m["schema_version"], 2)
        self.assertEqual(m["media_kind"], "still")
        self.assertEqual(m["capture"], {"x": 2})
        self.assertEqual(m["note"], "hi")
        self.assertEqual(after["identifier"], "HACK")


if __name__ == "__main__":
    unittest.main()
