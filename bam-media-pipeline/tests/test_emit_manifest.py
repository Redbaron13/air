import importlib.util
from datetime import datetime
from pathlib import Path
import sys
import types
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "emit_manifest.py"
fake_psycopg2 = types.ModuleType("psycopg2")
fake_extras = types.ModuleType("psycopg2.extras")
fake_extras.RealDictCursor = object
fake_psycopg2.extras = fake_extras
sys.modules.setdefault("psycopg2", fake_psycopg2)
sys.modules.setdefault("psycopg2.extras", fake_extras)
SPEC = importlib.util.spec_from_file_location("emit_manifest", MODULE_PATH)
emit_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(emit_manifest)


class BuildManifestEntryTests(unittest.TestCase):
    def test_includes_centroid_when_available(self):
        entry = emit_manifest.build_manifest_entry(
            {
                "shoot_id": "SHOT-001",
                "unique_stem": "ABCDEF12",
                "captured_at": datetime(2026, 9, 8, 12, 0, 0),
                "sensor_model": "DJI",
                "project_name": "Bridge Survey",
                "centroid_lat": 40.742,
                "centroid_lon": -74.172,
                "service_tags": ["bridge"],
                "describes": "Bridge deck repair",
                "alt": "Bridge deck from above",
                "original_sha256": "deadbeef",
            },
            [{"width": 800, "format": "webp"}, {"width": 1400, "format": "avif"}],
        )

        self.assertEqual(entry["key"], "captures/2026-09-08-SHOT-001/ABCDEF12")
        self.assertEqual(entry["variants"], [800, 1400])
        self.assertEqual(entry["formats"], ["avif", "webp"])
        self.assertEqual(entry["geo"]["site"], "Bridge Survey")
        self.assertEqual(entry["geo"]["centroid"], {"lat": 40.742, "lon": -74.172})


if __name__ == "__main__":
    unittest.main()
