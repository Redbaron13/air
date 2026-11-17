import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from vision_client import validate_output
class VisionValidation(unittest.TestCase):
    def test_bad_outputs(self):
        for value in [None,[],{}, {'agreementScore':float('nan')},{'agreementScore':True},{'agreementScore':1.1}]:
            with self.assertRaises(ValueError): validate_output(value)
    def test_valid_score(self):
        self.assertEqual(validate_output({'agreementScore':0.8})['agreementScore'],0.8)
