import unittest
from datetime import datetime

from pipeline.air.identity import (
    build_compiled_id,
    build_copy_id,
    build_keyframe_id,
    build_temp_id,
    date_code,
    location_code,
    location_id_for,
    overlap_id_for,
    parse_compiled_id,
    parse_copy_id,
    parse_keyframe_id,
    parse_temp_id,
    shared_stem,
    unique4,
)


WHEN = datetime(2026, 1, 21, 13, 15, 21)


class IdentityTests(unittest.TestCase):
    def test_month_alphabet(self):
        self.assertEqual(date_code(WHEN), "21A26")
        self.assertEqual(date_code(datetime(2026, 6, 21, 8, 0, 0)), "21J26")
        self.assertEqual(date_code(datetime(2026, 11, 3, 0, 0, 0)), "03N26")

    def test_temp_id_round_trip(self):
        temp_id = build_temp_id("still", "7K2M", WHEN, "AA", 1)
        self.assertEqual(temp_id, "P7K2M_21A26131521-AA001")
        parsed = parse_temp_id(temp_id)
        self.assertEqual(parsed["kind"], "still")
        self.assertEqual(parsed["unique"], "7K2M")
        self.assertEqual(parsed["captured_at"], WHEN)
        self.assertEqual(parsed["subfolder"], "AA")
        self.assertEqual(parsed["sequence"], 1)

    def test_unique_avoids_collision(self):
        first = unique4("same-bytes")
        second = unique4("same-bytes", taken={first})
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 4)

    def test_keyframe_shares_video_stem(self):
        self.assertEqual(shared_stem("7K2M"), "7K")
        key = build_keyframe_id("7K2M", 5)
        self.assertEqual(key, "K7K05")
        self.assertEqual(parse_keyframe_id(key)["stem"], "7K")

    def test_compiled_id_carries_location_code(self):
        compiled = build_compiled_id(WHEN, 3, "LH7KABC", "still", "A01", "K4M2")
        self.assertEqual(compiled, "21A26-003H7KPHOA01-K4M2")
        parsed = parse_compiled_id(compiled)
        self.assertEqual(parsed["location_code"], "H7K")
        self.assertEqual(parsed["kind"], "still")
        self.assertEqual(parsed["unique"], "K4M2")
        self.assertEqual(location_code("LH7KABC"), "H7K")

    def test_location_id_is_stable_for_one_place(self):
        first = location_id_for(40.7484, -73.9857)
        second = location_id_for(40.74841, -73.98569)
        other = location_id_for(40.76, -73.99)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("L"))

    def test_overlap_id_differs_by_visit(self):
        place = location_id_for(40.7484, -73.9857)
        morning = overlap_id_for(place, "2026-01-21T13:00:00")
        later = overlap_id_for(place, "2026-06-21T13:00:00")
        self.assertNotEqual(morning, later)
        self.assertTrue(morning.startswith("O"))

    def test_copy_id(self):
        copy_id = build_copy_id(2560, "K4M2")
        self.assertEqual(copy_id, "2D4-K4M2-I10")
        self.assertEqual(parse_copy_id(copy_id)["width"], 2560)

    def test_video_and_model_letters(self):
        self.assertTrue(build_temp_id("video", "7K2M", WHEN, "AA", 2).startswith("M"))
        self.assertTrue(build_temp_id("model3d", "7K2M", WHEN, "AA", 3).startswith("D"))
        self.assertTrue(build_temp_id("other", "7K2M", WHEN, "AA", 4).startswith("X"))


if __name__ == "__main__":
    unittest.main()
