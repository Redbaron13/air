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

class FreeModelGuards(unittest.TestCase):
    def entry(self):
        return {'id':'test/vision:free','pricing':{'prompt':'0','completion':'0'},'supported_parameters':['response_format'],'architecture':{'input_modalities':['image']}}
    def test_free_vision_json(self):
        from vision_client import validate_catalog_entry
        validate_catalog_entry(self.entry(),True)
    def test_extra_charge_and_bad_prices_rejected(self):
        from vision_client import validate_catalog_entry
        for value in ['0.01','NaN','-1',None]:
            e=self.entry();e['pricing']['image']=value
            with self.assertRaises(ValueError):validate_catalog_entry(e,True)
    def test_json_and_vision_required(self):
        from vision_client import validate_catalog_entry
        for field in ['supported_parameters','architecture']:
            e=self.entry();del e[field]
            with self.assertRaises(ValueError):validate_catalog_entry(e,True)
    def test_budget_persists_paces_and_resets_day(self):
        from vision_client import reserve_request
        from unittest.mock import patch
        import tempfile
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'OPENROUTER_MAX_REQUESTS_PER_DAY':'2','OPENROUTER_MIN_INTERVAL_SECONDS':'6'}):
            p=Path(d)/'budget.db'
            reserve_request(p,100)
            with self.assertRaises(ValueError):reserve_request(p,101)
            reserve_request(p,106)
            with self.assertRaises(ValueError):reserve_request(p,120)
            reserve_request(p,86500)
