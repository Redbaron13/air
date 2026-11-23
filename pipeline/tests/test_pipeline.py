import json
import os
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

from pipeline.air import ai, derive, group, manifest, prep, sync
from pipeline.air.identity import build_compiled_id, build_temp_id, location_id_for, parse_temp_id
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path
from pipeline.air.review import add_tag, override_tag


WHEN = datetime(2026, 1, 21, 13, 15, 21)
LATER = datetime(2026, 6, 21, 13, 15, 21)


def _run(cmd, **kwargs):
    subprocess.run(cmd, check=True, capture_output=True, **kwargs)


class PrepTests(unittest.TestCase):
    def test_scan_links_duplicates_keyframes_and_models(self):
        root = Path(self.id().split(".")[-1])
        library = Path("pipeline/tests/_work_prep")
        if library.exists():
            import shutil

            shutil.rmtree(library)
        library.mkdir(parents=True)
        folder_a = library / "input" / "site-a"
        folder_b = library / "input" / "site-b"
        folder_a.mkdir(parents=True)
        folder_b.mkdir(parents=True)
        _run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x48", "-frames:v", "1", str(folder_a / "photo.jpg")],
        )
        (folder_b / "photo.jpg").write_bytes((folder_a / "photo.jpg").read_bytes())
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x48:d=20",
                "-t",
                "20",
                str(folder_a / "clip.mp4"),
            ]
        )
        (folder_a / "tower.obj").write_text("o tower\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
        (folder_a / "notes.txt").write_text("leave me", encoding="utf-8")

        summary = prep.scan(library)
        self.assertEqual(summary["duplicates"], 1)
        self.assertGreaterEqual(summary["keyframes"], 2)
        journal = Journal(journal_path(library))
        stills = [row for row in journal.assets() if row["media_kind"] == "still"]
        videos = [row for row in journal.assets() if row["media_kind"] == "video"]
        models = [row for row in journal.assets() if row["media_kind"] == "model3d"]
        self.assertEqual(len(stills), 1)
        self.assertEqual(len(videos), 1)
        self.assertEqual(len(models), 1)
        self.assertEqual(journal.keyframes(videos[0]["temp_id"])[0]["parent_temp_id"], videos[0]["temp_id"])
        stem = parse_temp_id(videos[0]["temp_id"])["unique"][:2]
        self.assertTrue(all(frame["stem"] == stem for frame in journal.keyframes(videos[0]["temp_id"])))
        self.assertEqual(len(journal.keyframes(models[0]["temp_id"])), 0)
        self.assertTrue((library / "originals").iterdir())
        self.assertEqual(len(list((library / "originals").iterdir())), 3)
        staged_stills = [path for path in (library / "staging").glob("*.jpg")]
        self.assertEqual(len(staged_stills), 1)
        self.assertTrue((folder_a / "notes.txt").exists())
        duplicates = journal.query("SELECT * FROM duplicates")
        self.assertEqual(duplicates[0]["canonical_temp_id"], stills[0]["temp_id"])
        journal.close()


class GroupTests(unittest.TestCase):
    def test_before_after_and_weak_overlap(self):
        root = Path("pipeline/tests/_work_group")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        (root / "Database").mkdir(parents=True)
        journal = Journal(journal_path(root))
        place_lat, place_lon = 40.7484, -73.9857
        nadir_a = build_temp_id("still", "AAAA", WHEN, "AA", 1)
        nadir_b = build_temp_id("still", "AAAB", LATER, "AA", 2)
        oblique = build_temp_id("still", "AAAC", WHEN, "AA", 3)
        for temp_id, when, shot in (
            (nadir_a, WHEN, "nadir"),
            (nadir_b, LATER, "nadir"),
            (oblique, WHEN, "high_oblique"),
        ):
            journal.insert_asset(
                {
                    "temp_id": temp_id,
                    "media_kind": "still",
                    "original_name": f"{temp_id}.jpg",
                    "captured_at": when.isoformat(),
                    "latitude": place_lat,
                    "longitude": place_lon,
                    "gimbal_pitch": -90 if shot == "nadir" else -60,
                    "shot_characteristic": shot,
                }
            )
        journal.close()
        group.group_location(root)
        group.group_before_after(root)
        group.group_overlap(root)
        journal = Journal(journal_path(root))
        location_ids = {row["location_id"] for row in journal.assets()}
        self.assertEqual(len(location_ids), 1)
        self.assertEqual(location_ids.pop(), location_id_for(place_lat, place_lon))
        members = journal.query("SELECT temp_id FROM before_after_members ORDER BY temp_id")
        self.assertEqual([row["temp_id"] for row in members], sorted([nadir_a, nadir_b]))
        links = journal.query("SELECT status FROM overlap_links")
        self.assertTrue(links)
        self.assertTrue(all(row["status"] == "insufficient" for row in links))
        oblique_row = journal.conn.execute("SELECT location_id FROM assets WHERE temp_id = ?", (oblique,)).fetchone()
        self.assertIsNotNone(oblique_row["location_id"])
        journal.close()


class ModelAndWatermarkTests(unittest.TestCase):
    def test_model_views_stay_individual_and_combined(self):
        root = Path("pipeline/tests/_work_model")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        (root / "Database").mkdir(parents=True)
        journal = Journal(journal_path(root))
        place = location_id_for(40.75, -73.99)
        nadir = build_temp_id("still", "BBBB", WHEN, "AA", 1)
        high = build_temp_id("still", "BBBC", WHEN, "AA", 2)
        for temp_id, shot in ((nadir, "nadir"), (high, "high_oblique")):
            journal.insert_asset(
                {
                    "temp_id": temp_id,
                    "media_kind": "still",
                    "original_name": f"{temp_id}.jpg",
                    "captured_at": WHEN.isoformat(),
                    "latitude": 40.75,
                    "longitude": -73.99,
                    "location_id": place,
                    "location_confidence": 1,
                    "shot_characteristic": shot,
                }
            )
            journal.add_pass(temp_id, "still", "describe", "skip", {"description": shot, "alt_text": None})
        journal.execute(
            "INSERT INTO locations(location_id, latitude, longitude, confidence, status) VALUES (?, ?, ?, 1, 'assigned')",
            (place, 40.75, -73.99),
        )
        journal.close()
        group.group_model(root)
        os.environ["AIR_VISION_BACKEND"] = "skip"
        result = ai.synthesize_model(root)
        self.assertEqual(result["model_sets"], 1)
        journal = Journal(journal_path(root))
        self.assertEqual(len(journal.passes_for(nadir, "still")), 1)
        self.assertEqual(len(journal.passes_for(high, "still")), 1)
        sets = journal.query("SELECT set_id FROM model_view_sets")
        combined = journal.passes_for(sets[0]["set_id"], "model_set")
        self.assertEqual(combined[0]["stage"], "synthesize")
        payload = json.loads(combined[0]["payload_json"])
        self.assertEqual(payload["part_count"], 2)
        journal.close()

    def test_rewatermark_keeps_ids_and_tags(self):
        from PIL import Image

        root = Path("pipeline/tests/_work_mark")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        (root / "originals").mkdir(parents=True)
        (root / "Database").mkdir(parents=True)
        image_path = root / "originals" / "sample.jpg"
        Image.new("RGB", (80, 60), (10, 10, 10)).save(image_path, "JPEG")
        journal = Journal(journal_path(root))
        prep.seed_presets(journal)
        temp_id = build_temp_id("still", "K4M2", WHEN, "AA", 1)
        compiled = build_compiled_id(WHEN, 3, "LH7KABC", "still", "A01", "K4M2")
        journal.insert_asset(
            {
                "temp_id": temp_id,
                "media_kind": "still",
                "original_name": "sample.jpg",
                "original_store": str(image_path),
                "sha256": "abc",
                "compiled_id": compiled,
                "location_id": "LH7KABC",
                "captured_at": WHEN.isoformat(),
                "workflow_state": "IDENTIFIED",
            }
        )
        journal.assign_tag(temp_id, "standing seam metal roof", "preset")
        journal.close()
        derive.derive(root, "OLD")
        first = Journal(journal_path(root))
        first_hash = first.query("SELECT sha256, watermark_label FROM asset_copies ORDER BY width")[0]
        first.close()
        derive.rewatermark(root, "NEW MARK", compiled)
        journal = Journal(journal_path(root))
        asset = journal.conn.execute("SELECT compiled_id, location_id FROM assets WHERE temp_id = ?", (temp_id,)).fetchone()
        self.assertEqual(asset["compiled_id"], compiled)
        self.assertEqual(asset["location_id"], "LH7KABC")
        self.assertEqual(journal.tags_for(temp_id), ["standing seam metal roof"])
        updated = journal.query("SELECT sha256, watermark_label FROM asset_copies ORDER BY width")
        self.assertTrue(all(row["watermark_label"] == "NEW MARK" for row in updated))
        self.assertNotEqual(updated[0]["sha256"], first_hash["sha256"])
        self.assertTrue((root / "originals" / "sample.jpg").exists())
        journal.close()


class ManifestAndSyncTests(unittest.TestCase):
    def test_manifest_omits_gps_and_keeps_detailed_tags(self):
        root = Path("pipeline/tests/_work_manifest")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        (root / "Database").mkdir(parents=True)
        (root / "exports").mkdir(parents=True)
        journal = Journal(journal_path(root))
        temp_id = build_temp_id("still", "CCCC", WHEN, "AA", 1)
        journal.insert_asset(
            {
                "temp_id": temp_id,
                "media_kind": "still",
                "original_name": "roof.jpg",
                "compiled_id": "21A26-001ABCDEF PHOA01-CCCC".replace(" ", ""),
                "location_id": "LABCDEF",
                "latitude": 40.1,
                "longitude": -74.2,
                "alt_text": "Standing seam metal roof on a low commercial building.",
                "workflow_state": "AUTO_ACCEPTED",
            }
        )
        journal.assign_tag(temp_id, "standing seam metal roof", "preset")
        journal.execute(
            """
            INSERT INTO asset_copies(copy_id, temp_id, compiled_id, width, path, object_key, sha256, watermark_label)
            VALUES ('2D4-CCCC-I10', ?, '21A26-001ABCDEF PHOA01-CCCC', 2560, 'out.webp', 'bam/web/stills/x/2D4.webp', 'ff', 'mark')
            """.replace("21A26-001ABCDEF PHOA01-CCCC", "21A26-001ABCDEFPHOA01-CCCC"),
            (temp_id,),
        )
        journal.close()
        # The insert above used a cleaned compiled id in the asset row. Point the copy at it.
        journal = Journal(journal_path(root))
        compiled = journal.assets()[0]["compiled_id"]
        journal.execute("UPDATE asset_copies SET compiled_id = ?", (compiled,))
        journal.close()
        result = manifest.build_manifest(root)
        document = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(document["assets"][0]["tags"], ["standing seam metal roof"])
        blob = json.dumps(document)
        self.assertNotIn("latitude", blob)
        self.assertNotIn("40.1", blob)
        self.assertNotIn("city", blob)

    def test_sync_skips_without_database_url(self):
        root = Path("pipeline/tests/_work_sync")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        (root / "Database").mkdir(parents=True)
        journal = Journal(journal_path(root))
        journal.insert_asset(
            {
                "temp_id": build_temp_id("model3d", "DDDD", WHEN, "AA", 1),
                "media_kind": "model3d",
                "original_name": "tower.obj",
            }
        )
        journal.add_keyframe(
            {
                "keyframe_id": "KDD01",
                "parent_temp_id": journal.assets()[0]["temp_id"],
                "label": "V5",
                "timestamp_s": 15,
                "path": None,
                "stem": "DD",
                "extract_status": "skipped",
            }
        )
        snapshot = sync.snapshot(journal)
        journal.close()
        self.assertIn("keyframe", {row["relation"] for row in snapshot["asset_relations"]})
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("NEON_DATABASE_URL", None)
        result = sync.sync_library(root, neon=True)
        self.assertEqual(result["status"], "skipped")

    def test_review_defines_a_tag_and_overrides_a_locked_one(self):
        root = Path("pipeline/tests/_work_review")
        if root.exists():
            import shutil

            shutil.rmtree(root)
        (root / "Database").mkdir(parents=True)
        journal = Journal(journal_path(root))
        prep.seed_presets(journal)
        temp_id = build_temp_id("still", "EEEE", WHEN, "AA", 1)
        journal.insert_asset({"temp_id": temp_id, "media_kind": "still", "original_name": "a.jpg"})
        journal.assign_tag(temp_id, "nadir", "exif")
        journal.close()
        saved = add_tag(root, "brick chimney flashing", {"visible_subject": "brick chimney flashing"})
        self.assertEqual(saved["name"], "brick chimney flashing")
        record = override_tag(root, temp_id, "nadir", "high_oblique", "gimbal was mounted off-axis")
        self.assertEqual(record["measured_value"], "nadir")
        self.assertEqual(record["display_value"], "high_oblique")


if __name__ == "__main__":
    unittest.main()
