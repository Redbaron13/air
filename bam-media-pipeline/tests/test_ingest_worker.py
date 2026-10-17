import importlib.util
import runpy
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


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
    def test_cli_accepts_multiple_paths_and_skips_unsupported_files(self):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "photo.jpg").write_bytes(b"photo")
            folder = root / "folder"
            folder.mkdir()
            (folder / "clip.mov").write_bytes(b"video")
            (folder / "notes.txt").write_text("skip", encoding="utf-8")
            with patch.object(sys.modules["psycopg2"], "connect", return_value=connection, create=True), patch(
                "subprocess.run", return_value=types.SimpleNamespace(stdout="[]")
            ), patch.object(sys, "argv", [str(MODULE_PATH), str(root / "photo.jpg"), str(folder), str(root / "missing")]):
                runpy.run_path(str(MODULE_PATH), run_name="__main__")
        inserts = [call.args[1] for call in cursor.execute.call_args_list if "INSERT INTO assets" in call.args[0]]
        self.assertEqual([values[2] for values in inserts], ["photo.jpg", "clip.mov"])
        self.assertEqual([values[5] for values in inserts], ["still", "video"])

    def test_ingest_preserves_long_stem_and_media_kind(self):
        for filename, expected_kind in (("clip.mkv", "video"), ("model.glb", "3d_model")):
            with self.subTest(filename=filename):
                connection = MagicMock()
                cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = None
                with patch.object(ingest_worker, "get_db", return_value=connection), patch.object(
                    ingest_worker, "compute_sha256", return_value="abcdef1234567890"
                ), patch.object(ingest_worker, "extract_exif", return_value={}):
                    asset_id = ingest_worker.ingest_file(filename)
                values = cursor.execute.call_args.args[1]
                self.assertTrue(asset_id.endswith("-ABCDEF12"))
                self.assertEqual(values[1], "ABCDEF12")
                self.assertEqual(values[5], expected_kind)

    def test_target_filter_preserves_formats_from_both_branches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in ("photo.avif", "scan.tiff", "clip.mkv", "notes.txt"):
                (root / name).write_bytes(b"sample")
            self.assertEqual(
                {Path(path).name for path in ingest_worker.iter_ingest_targets(tmpdir)},
                {"photo.avif", "scan.tiff", "clip.mkv"},
            )
            self.assertEqual(ingest_worker.iter_ingest_targets(str(root / "notes.txt")), [])
            self.assertEqual(ingest_worker.iter_ingest_targets(str(root / "missing.jpg")), [])

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
