import unittest

from pipeline.air.shots import drone_angle
from pipeline.air.tags import (
    define_tag,
    display_tag,
    load_presets,
    override_locked_tag,
    propose_tag,
    select_tags,
)


class ShotTests(unittest.TestCase):
    def test_gimbal_bands(self):
        self.assertEqual(drone_angle(-90), "nadir")
        self.assertEqual(drone_angle(90), "nadir")
        self.assertEqual(drone_angle(-60), "high_oblique")
        self.assertEqual(drone_angle(-30), "medium_oblique")
        self.assertEqual(drone_angle(-10), "low_oblique")
        self.assertEqual(drone_angle(-5), "horizon")
        self.assertIsNone(drone_angle(None))


class TagTests(unittest.TestCase):
    def test_multiword_preset_is_kept(self):
        catalog = load_presets()
        accepted, rejected = select_tags(catalog, ["standing seam metal roof", "roof-solar"])
        self.assertEqual(accepted, ["standing seam metal roof"])
        self.assertEqual(rejected, ["roof-solar"])

    def test_undefined_tag_is_blocked_until_defined(self):
        catalog = load_presets()
        accepted, rejected = select_tags(catalog, ["brick chimney flashing"])
        self.assertEqual(accepted, [])
        self.assertEqual(rejected, ["brick chimney flashing"])
        proposal = propose_tag("brick chimney flashing", {"visible_subject": "brick chimney flashing"})
        self.assertFalse(proposal["saved"])
        saved = define_tag("brick chimney flashing", {"visible_subject": "brick chimney flashing"})
        catalog.append(saved)
        accepted, rejected = select_tags(catalog, ["brick chimney flashing"])
        self.assertEqual(accepted, ["brick chimney flashing"])
        self.assertEqual(rejected, [])

    def test_exif_override_keeps_measured_value(self):
        record = override_locked_tag("nadir", "high_oblique", "gimbal was mounted off-axis")
        self.assertEqual(record["measured_value"], "nadir")
        self.assertEqual(display_tag("nadir", record), "high_oblique")
        self.assertEqual(display_tag("nadir", None), "nadir")


if __name__ == "__main__":
    unittest.main()
