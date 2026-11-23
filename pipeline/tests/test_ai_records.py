"""Schema-shaped vision records. The skip backend writes them with no network."""

import json
import os
import shutil
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from pipeline.air import ai, group
from pipeline.air.backends import complete_json, sanitize_model_output
from pipeline.air.identity import build_temp_id
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path
from pipeline.air.tags import load_presets


WHEN = datetime(2026, 1, 21, 13, 15, 21)
SCHEMA_BLOCKS = (
    "schema_version",
    "media_kind",
    "capture",
    "classification",
    "content",
    "photogrammetry",
    "view",
    "quality",
)


def _payload(journal: Journal, target_id: str, scope: str, stage: str) -> dict:
    rows = [row for row in journal.passes_for(target_id, scope) if row["stage"] == stage]
    if len(rows) != 1:
        raise AssertionError(f"expected one {stage} pass for {target_id}, found {len(rows)}")
    return json.loads(rows[0]["payload_json"])


class AiRecordTests(unittest.TestCase):
    def setUp(self):
        self.previous_backend = os.environ.get("AIR_VISION_BACKEND")
        os.environ["AIR_VISION_BACKEND"] = "skip"
        self.root = Path("pipeline/tests/_work_ai_records")
        if self.root.exists():
            shutil.rmtree(self.root)
        (self.root / "Database").mkdir(parents=True)

    def tearDown(self):
        if self.previous_backend is None:
            os.environ.pop("AIR_VISION_BACKEND", None)
        else:
            os.environ["AIR_VISION_BACKEND"] = self.previous_backend

    def test_skip_still_record_has_schema_blocks_and_does_not_invent_gps(self):
        journal = Journal(journal_path(self.root))
        temp_id = build_temp_id("still", "STIL", WHEN, "AA", 1)
        journal.insert_asset(
            {
                "temp_id": temp_id,
                "media_kind": "still",
                "original_name": "roof.jpg",
                "staging_path": str(self.root / "staging" / "roof.jpg"),
                "gimbal_pitch": -90,
                "shot_characteristic": "nadir",
            }
        )
        journal.assign_tag(temp_id, "nadir", "exif")
        journal.close()

        described = ai.describe(self.root)
        self.assertEqual(described["backend"], "skip")
        reasoned = ai.reason(self.root)
        self.assertEqual(reasoned["backend"], "skip")
        ai.validate(self.root)

        journal = Journal(journal_path(self.root))
        record = _payload(journal, temp_id, "still", "describe")
        self.assertEqual(record["schema_version"], "1.1.0")
        self.assertEqual(record["media_kind"], "still")
        for block in SCHEMA_BLOCKS:
            self.assertIn(block, record)
        self.assertIn("description", record["content"])
        self.assertIn("alt_text", record["content"])
        self.assertIn("tags", record["content"])
        self.assertIsNone(record["content"]["description"])
        self.assertIsNone(record["content"]["alt_text"])
        self.assertEqual(record["content"]["tags"], ["nadir"])
        self.assertEqual(record["classification"]["drone_angle"], "nadir")
        self.assertEqual(record["classification"]["shot_characteristic"], "nadir")
        self.assertEqual(record["classification"]["shot_type_from_exif"], "aerial_nadir")
        self.assertIsNone(record["classification"]["shot_type"])
        self.assertEqual(record["capture"]["gimbal_pitch_deg"], -90)
        self.assertIsNone(record["capture"]["captured_at"])
        self._assert_gps_absent(record)
        self.assertIsNone(record["address"]["city"])
        self.assertIsNone(record["flight"]["dist_prev_m"])

        reason = _payload(journal, temp_id, "still", "reason")
        validate = _payload(journal, temp_id, "still", "validate")
        self.assertEqual(reason["content"]["tags"], ["nadir"])
        self.assertEqual(reason["proposed"], [])
        self.assertEqual(validate["review"]["agreement_score"], 0.0)
        self._assert_gps_absent(reason)
        self._assert_gps_absent(validate)
        source = journal.conn.execute(
            "SELECT source FROM tag_assignments WHERE temp_id = ? AND tag_name = 'nadir'",
            (temp_id,),
        ).fetchone()
        self.assertEqual(source["source"], "exif")
        self.assertEqual(journal.query("SELECT name FROM tag_proposals"), [])
        journal.close()

    def test_keyframe_record_stays_separate_from_video_synthesis(self):
        journal = Journal(journal_path(self.root))
        video_id = build_temp_id("video", "ABCD", WHEN, "AA", 1)
        frame_id = "KABCD01"
        journal.insert_asset(
            {
                "temp_id": video_id,
                "media_kind": "video",
                "original_name": "clip.mp4",
                "staging_path": str(self.root / "staging" / "clip.mp4"),
            }
        )
        journal.add_keyframe(
            {
                "keyframe_id": frame_id,
                "parent_temp_id": video_id,
                "label": "V5",
                "timestamp_s": 15,
                "path": str(self.root / "staging" / "KABCD01.jpg"),
                "stem": "AB",
                "extract_status": "extracted",
            }
        )
        journal.add_pass(
            frame_id,
            "keyframe",
            "exif",
            "exiftool",
            {
                "latitude": None,
                "longitude": None,
                "altitude_m": None,
                "gimbal_pitch": -60,
                "captured_at": None,
                "shot_characteristic": "high_oblique",
                "altitude_band": None,
            },
        )
        journal.assign_tag(frame_id, "high_oblique", "exif")
        journal.close()

        ai.describe(self.root)
        journal = Journal(journal_path(self.root))
        before = _payload(journal, frame_id, "frame", "describe")
        journal.close()
        ai.synthesize_video(self.root)

        journal = Journal(journal_path(self.root))
        frame = _payload(journal, frame_id, "frame", "describe")
        video = _payload(journal, video_id, "video", "synthesize")
        self.assertEqual(frame, before)
        self.assertEqual(frame["media_kind"], "video_frame")
        self.assertEqual(frame["classification"]["drone_angle"], "high_oblique")
        self.assertEqual(frame["classification"]["shot_characteristic"], "high_oblique")
        self.assertIn("changes_since_previous", frame)
        self.assertIn("description", frame["content"])
        self.assertIn("alt_text", frame["content"])
        self.assertEqual(frame["content"]["tags"], ["high_oblique"])
        self._assert_gps_absent(frame)
        self.assertEqual(video["media_kind"], "video")
        self.assertEqual(video["part_count"], 1)
        self.assertEqual(video["parts"], [{"keyframe_id": frame_id, "label": "V5", "scope": "frame", "stage": "describe"}])
        self.assertEqual(video["video"]["frames"][0]["keyframe_id"], frame_id)
        self.assertNotEqual(frame_id, video_id)
        self.assertEqual(journal.passes_for(frame_id, "video"), [])
        self.assertEqual(len([row for row in journal.passes_for(frame_id, "frame") if row["stage"] == "describe"]), 1)
        self._assert_gps_absent(video)
        journal.close()

    def test_model_view_synthesis_keeps_part_count_and_individual_passes(self):
        journal = Journal(journal_path(self.root))
        place = "LH7KABC"
        nadir = build_temp_id("still", "NAD1", WHEN, "AA", 1)
        oblique = build_temp_id("still", "OBL1", WHEN, "AA", 2)
        model_id = build_temp_id("model3d", "MOD1", WHEN, "AA", 3)
        for temp_id, shot in ((nadir, "nadir"), (oblique, "high_oblique")):
            journal.insert_asset(
                {
                    "temp_id": temp_id,
                    "media_kind": "still",
                    "original_name": f"{temp_id}.jpg",
                    "staging_path": str(self.root / "staging" / f"{temp_id}.jpg"),
                    "location_id": place,
                    "shot_characteristic": shot,
                    "gimbal_pitch": -90 if shot == "nadir" else -65,
                }
            )
            journal.assign_tag(temp_id, shot, "exif")
        journal.insert_asset(
            {
                "temp_id": model_id,
                "media_kind": "model3d",
                "original_name": "tower.obj",
                "staging_path": str(self.root / "staging" / "tower.obj"),
            }
        )
        journal.close()

        ai.describe(self.root)
        journal = Journal(journal_path(self.root))
        model_view = _payload(journal, model_id, "view", "describe")
        nadir_before = journal.passes_for(nadir, "still")[0]["payload_json"]
        oblique_before = journal.passes_for(oblique, "still")[0]["payload_json"]
        journal.close()
        self.assertEqual(model_view["media_kind"], "model3d")
        self.assertIn("model3d", model_view)
        self.assertIn("description", model_view["content"])
        self.assertIn("alt_text", model_view["content"])
        self.assertIn("tags", model_view["content"])
        self.assertIsNone(model_view["classification"]["drone_angle"])
        self._assert_gps_absent(model_view)
        for block in SCHEMA_BLOCKS:
            self.assertIn(block, model_view)

        grouped = group.group_model(self.root)
        self.assertEqual(grouped["sets"], 1)
        synthesized = ai.synthesize_model(self.root)
        self.assertEqual(synthesized["model_sets"], 1)

        journal = Journal(journal_path(self.root))
        self.assertEqual(journal.passes_for(nadir, "still")[0]["payload_json"], nadir_before)
        self.assertEqual(journal.passes_for(oblique, "still")[0]["payload_json"], oblique_before)
        self.assertEqual(len(journal.passes_for(model_id, "view")), 1)
        sets = journal.query("SELECT set_id FROM model_view_sets")
        combined = _payload(journal, sets[0]["set_id"], "model_set", "synthesize")
        self.assertEqual(combined["part_count"], 2)
        self.assertEqual(
            {(part["temp_id"], part["shot_characteristic"]) for part in combined["parts"]},
            {(nadir, "nadir"), (oblique, "high_oblique")},
        )
        self.assertEqual(journal.passes_for(nadir, "model_set"), [])
        self.assertEqual(journal.passes_for(oblique, "model_set"), [])
        nadir_record = json.loads(nadir_before)
        oblique_record = json.loads(oblique_before)
        self.assertEqual(nadir_record["classification"]["shot_characteristic"], "nadir")
        self.assertEqual(oblique_record["classification"]["shot_characteristic"], "high_oblique")
        self._assert_gps_absent(combined)
        self._assert_gps_absent(nadir_record)
        self._assert_gps_absent(oblique_record)
        journal.close()

    def test_skip_backend_and_model_sanitizer_do_not_invent_gps_or_rewrite_locked_tags(self):
        with patch("urllib.request.urlopen") as urlopen:
            skipped = complete_json("skip", "Describe the file", None)
            urlopen.assert_not_called()
        self.assertTrue(skipped["skipped"])
        self.assertIsNone(skipped["description"])
        self.assertNotIn("latitude", skipped)
        self.assertNotIn("gimbal_pitch_deg", skipped)
        self.assertNotIn("captured_at", skipped)

        poisoned = {
            "description": "A standing seam metal roof fills the frame.",
            "alt_text": "Standing seam metal roof.",
            "latitude": 40.7484,
            "longitude": -73.9857,
            "captured_at": "2026-01-21T13:15:21",
            "gimbal_pitch_deg": -12,
            "shot_characteristic": "horizon",
            "tags": ["horizon", "standing seam metal roof", "brick chimney flashing"],
            "proposed": ["brick chimney flashing"],
            "capture": {"latitude": 1.2, "longitude": 3.4, "gimbal_pitch_deg": -5},
            "classification": {"drone_angle": "horizon", "shot_type": "nadir", "media_genre": "infrastructure"},
            "content": {"description": "Roof", "latitude": 9.9},
        }
        cleaned = sanitize_model_output(poisoned)
        blob = json.dumps(cleaned)
        self.assertNotIn("latitude", cleaned)
        self.assertNotIn("capture", cleaned)
        self.assertNotIn("40.7484", blob)
        self.assertNotIn("gimbal_pitch_deg", blob)
        self.assertNotIn("drone_angle", cleaned["classification"])
        self.assertEqual(cleaned["classification"]["shot_type"], "nadir")
        self.assertEqual(cleaned["description"], "A standing seam metal roof fills the frame.")
        self.assertNotIn("latitude", cleaned["content"])

        assignable, proposals = ai.split_model_tags(
            load_presets(),
            ["horizon", "low altitude", "standing seam metal roof", "brick chimney flashing"],
            ["brick chimney flashing", "NADIR"],
        )
        self.assertEqual(assignable, ["standing seam metal roof"])
        self.assertEqual(proposals, ["brick chimney flashing"])
        self.assertNotIn("horizon", assignable)
        self.assertNotIn("nadir", proposals)
        self.assertNotIn("low altitude", assignable)

    def _assert_gps_absent(self, record: dict):
        capture = record["capture"]
        self.assertIsNone(capture["latitude"])
        self.assertIsNone(capture["longitude"])
        self.assertFalse(capture["gps_valid"])
        blob = json.dumps(record)
        self.assertNotIn("40.7484", blob)
        self.assertNotIn("-73.9857", blob)


if __name__ == "__main__":
    unittest.main()
