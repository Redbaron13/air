import importlib.util
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import types
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ingest_worker.py"
fake_psycopg2 = types.ModuleType("psycopg2")
fake_extras = types.ModuleType("psycopg2.extras")
fake_extras.RealDictCursor = object
fake_psycopg2.extras = fake_extras
sys.modules.setdefault("psycopg2", fake_psycopg2)
sys.modules.setdefault("psycopg2.extras", fake_extras)
SPEC = importlib.util.spec_from_file_location("ingest_worker", MODULE_PATH)
ingest_worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ingest_worker)


class IngestWorkerHelperTests(unittest.TestCase):
    def test_build_asset_identifiers_uses_eight_character_stem(self):
        unique_stem, asset_id = ingest_worker.build_asset_identifiers(
            "abcdef1234567890",
            batch_id="A01",
            now=datetime(2026, 9, 8, 12, 0, 0),
        )

        self.assertEqual(unique_stem, "ABCDEF12")
        self.assertEqual(asset_id, "080926-001BAMPHOA01-ABCDEF12")

    def test_iter_ingest_targets_expands_supported_files_in_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "image.JPG").write_bytes(b"image")
            (root / "notes.txt").write_text("ignore me", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "model.glb").write_bytes(b"3d")

            targets = ingest_worker.iter_ingest_targets(tmpdir)

        self.assertEqual(
            targets,
            [str(root / "image.JPG"), str(nested / "model.glb")],
        )


if __name__ == "__main__":
    unittest.main()
