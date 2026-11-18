import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('preprocess',Path(__file__).parents[1]/'scripts/preprocess_for_ingest.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class PreprocessTests(unittest.TestCase):
    def test_rejects_output_inside_source(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit): p.main(['--source',d,'--out',str(Path(d)/'output')])
    def test_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit): p.main(['--source',str(Path(d)/'missing'),'--out',str(Path(d)/'out')])
