import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.review_logic import (  # noqa: E402
    build_groups, derive_group_key, haversine_m, nearby_assets, stage_progress, summarize, validate_asset,
)


def asset(i, **kw):
    base = {"asset_id": i, "original_filename": f"{i}.jpg", "media_kind": "still", "workflow_state": "PENDING_HUMAN",
            "latitude": 40.0, "longitude": -74.0, "sensor_model": "FC", "shoot_id": "S1"}
    base.update(kw)
    return base


class GroupKeyTests(unittest.TestCase):
    def test_part_suffixes_share_key(self):
        keys = {derive_group_key(n) for n in ["Bridge_part1.glb", "Bridge_part2.glb", "bridge-p3.glb", "Bridge_LOD0.glb", "Bridge (2).glb", "Bridge_2of4.glb"]}
        self.assertEqual(keys, {"bridge"})

    def test_tile_row_col(self):
        self.assertEqual(derive_group_key("site_tile_2_4.tif"), derive_group_key("site_tile_3_1.tif"))

    def test_different_models_differ(self):
        self.assertNotEqual(derive_group_key("tower_part1.glb"), derive_group_key("bridge_part1.glb"))


class GeoTests(unittest.TestCase):
    def test_haversine(self):
        self.assertAlmostEqual(haversine_m(0, 0, 0, 1), 111195, delta=300)

    def test_nearby_and_clusters(self):
        a, b, c = asset("a"), asset("b", latitude=40.0005), asset("c", latitude=41.0)
        self.assertEqual([n["asset_id"] for n in nearby_assets(a, [a, b, c], 150)], ["b"])
        g = build_groups([a, b, c, asset("z", latitude=0.0, longitude=0.0)], 150)
        self.assertEqual(sorted(len(l["asset_ids"]) for l in g["locations"]), [1, 2])

    def test_model_groups_use_explicit_id_first(self):
        parts = [asset("m1", media_kind="3d_model", original_filename="tower_part1.glb"),
                 asset("m2", media_kind="3d_model", original_filename="tower_part2.glb"),
                 asset("m3", media_kind="3d_model", original_filename="other.glb", judge_output={"group_id": "TOWER"})]
        g = build_groups(parts)
        self.assertEqual({m["key"]: len(m["asset_ids"]) for m in g["models"]}, {"tower": 3})


class ValidationTests(unittest.TestCase):
    def codes(self, a, **kw):
        return {f["code"] for f in validate_asset(a, **kw)}

    def test_pending_without_eval_fails(self):
        self.assertIn("no_ai_eval", self.codes(asset("a")))

    def test_incomplete_and_mismatch(self):
        a = asset("a", screener_output={"x": 1}, agreement_score=0.9, judge_output={"agreementScore": 0.2})
        c = self.codes(a)
        self.assertIn("eval_incomplete", c)

        a = asset("a", screener_output={"x": 1}, verifier_output={"y": 1}, judge_output={"agreementScore": 0.2}, agreement_score=0.9)
        self.assertIn("score_mismatch", self.codes(a))

    def test_low_score_and_range(self):
        full = dict(screener_output={"a": 1}, verifier_output={"a": 1}, judge_output={"a": 1})
        self.assertIn("low_agreement", self.codes(asset("a", agreement_score=0.3, **full)))
        self.assertIn("score_range", self.codes(asset("a", agreement_score=1.5, **full)))
        self.assertEqual(self.codes(asset("a", agreement_score=0.9, **full)), set())

    def test_gps_and_shoot_radius(self):
        self.assertIn("no_gps", self.codes(asset("a", latitude=0.0, longitude=0.0)))
        shoot = {"centroid_lat": 41.0, "centroid_lon": -74.0, "radius_meters": 300}
        self.assertIn("outside_shoot", self.codes(asset("a"), shoot=shoot))

    def test_stuck_and_approved_checks(self):
        old = datetime.utcnow() - timedelta(hours=3)
        self.assertIn("stuck_ingested", self.codes(asset("a", workflow_state="INGESTED", created_at=old)))
        c = self.codes(asset("a", workflow_state="APPROVED", alt="x"), copies=0, file_exists=False)
        self.assertTrue({"short_alt", "no_derivatives", "file_missing", "no_describes"} <= c)

    def test_summary_and_stages(self):
        f = {"a": validate_asset(asset("a")), "b": []}
        s = summarize(f)
        self.assertEqual(s["assets_with_failures"], 1)
        st = stage_progress(asset("a", screener_output={"a": 1}))
        self.assertEqual([x["status"] for x in st][2:5], ["done", "pending", "pending"])


if __name__ == "__main__":
    unittest.main()
