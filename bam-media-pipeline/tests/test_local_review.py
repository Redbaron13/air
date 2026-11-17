import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import local_review as lr

MODELS = dict(informed='gemma3:4b', blind='qwen2.5vl:3b', merger='qwen3:4b', arbiter='llama3.2-vision:11b')


class Fake:
    def __init__(self, conflict_rounds=0, unresolved=False):
        self.calls = []
        self.conflict_rounds = conflict_rounds
        self.unresolved = unresolved
        self.round = 0

    def __call__(self, model, prompt, schema, image=None):
        self.calls.append((model, prompt, image))
        if model == MODELS['informed']:
            self.round += 1
        conflict = self.round <= self.conflict_rounds
        if schema == lr.OBSERVATION:
            return {f: ('different' if conflict and model == MODELS['blind'] else 'visible') for f in lr.FIELDS}
        if schema == lr.COMPARISON:
            return {f: {'status': 'disagree' if conflict else 'agree', 'reason': 'comparison'} for f in lr.FIELDS}
        return {f: {'choice': 'unknown' if self.unresolved else 'a', 'reason': 'reviewed'} for f in lr.FIELDS}


class LocalReviewTests(unittest.TestCase):
    def run_review(self, fake, cloud=None):
        return lr.review({'private_gps_sentinel': 123}, 'preview.jpg', MODELS, fake, lambda *args: None, cloud)

    def test_agreement_never_calls_cloud_or_second_round(self):
        fake = Fake()
        result = self.run_review(fake, lambda *args: self.fail('cloud called'))
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(result['completed_local_rounds'], 1)
        self.assertFalse(result['publication_allowed'])

    def test_blind_reviewer_never_receives_metadata(self):
        fake = Fake(2)
        self.run_review(fake)
        for model, prompt, image in fake.calls:
            if model == MODELS['blind']:
                self.assertNotIn('private_gps_sentinel', prompt)
                self.assertEqual(image, 'preview.jpg')

    def test_second_round_resolves_without_arbitration(self):
        fake = Fake(1)
        result = self.run_review(fake, lambda *args: self.fail('cloud called'))
        self.assertEqual(result['completed_local_rounds'], 2)
        self.assertEqual(len(fake.calls), 6)
        self.assertIsNone(result['local_arbitration'])
        self.assertTrue(result['rounds'][0]['disputed_fields'])

    def test_persistent_conflicts_use_fourth_model(self):
        fake = Fake(2)
        result = self.run_review(fake, lambda *args: self.fail('cloud called'))
        self.assertEqual(fake.calls[-1][0], MODELS['arbiter'])
        self.assertEqual(len(fake.calls), 7)
        self.assertEqual(result['unresolved_fields'], [])

    def test_cloud_only_after_two_rounds_and_local_arbitration(self):
        fake = Fake(2, True)
        def cloud(prompt, image):
            self.assertEqual(len(fake.calls), 7)
            self.assertNotIn('private_gps_sentinel', prompt)
            return {f: {'choice': 'b', 'reason': 'image supports b'} for f in lr.FIELDS}
        result = self.run_review(fake, cloud)
        self.assertIsNotNone(result['cloud_arbitration'])
        self.assertTrue(result['human_review_required'])
        self.assertEqual(result['final']['scene']['value'], 'different')

    def test_cloud_disabled_keeps_unresolved(self):
        result = self.run_review(Fake(2, True))
        self.assertEqual(result['unresolved_fields'], list(lr.FIELDS))
        self.assertIsNone(result['final']['scene']['value'])

    def test_local_failure_does_not_call_cloud(self):
        def broken(*args):
            raise ValueError('model unavailable')
        with self.assertRaises(ValueError):
            lr.review({}, 'preview', MODELS, broken, lambda *args: None,
                      lambda *args: self.fail('cloud called'))

    def test_closed_schema_rejects_invented_fields(self):
        value = {f: 'visible' for f in lr.FIELDS}
        value['gps'] = 'invented'
        with self.assertRaises(ValueError):
            lr.validate(value, lr.OBSERVATION)

    def test_missing_and_invalid_telemetry_not_inferred(self):
        fields = lr.normalize_metadata({'XMP:FlightPitchDegree': 20,
                                       'Composite:GPSLatitude': float('nan')})
        self.assertIsNone(fields['gimbal_pitch']['value'])
        self.assertEqual(fields['aircraft_pitch']['value'], 20)
        self.assertEqual(fields['latitude']['status'], 'invalid')
        self.assertIsNone(fields['relative_altitude']['value'])

    def test_negative_altitude_and_separate_references(self):
        fields = lr.normalize_metadata({'Composite:GPSAltitude': -7,
                                        'XMP:RelativeAltitude': 25, 'XMP:AbsoluteAltitude': 40})
        self.assertEqual(fields['gps_altitude']['value'], -7)
        self.assertEqual(fields['relative_altitude']['value'], 25)
        self.assertEqual(fields['absolute_altitude']['value'], 40)

    def test_unknown_agreement_is_not_evidence(self):
        a = {f: 'unknown' for f in lr.FIELDS}
        comp = {f: {'status': 'agree', 'reason': 'same'} for f in lr.FIELDS}
        self.assertEqual(lr.discrepancies(a, a, comp), list(lr.FIELDS))

    def test_context_budget_from_environment(self):
        with patch.dict(lr.os.environ, {"LOCAL_MODEL_CONTEXT": "4096"}):
            self.assertEqual(lr.Ollama().context, 4096)
        with self.assertRaises(ValueError):
            lr.Ollama(context=1024)
        with self.assertRaises(ValueError):
            lr.Ollama(context=65536)

    def test_substantial_policy(self):
        self.assertFalse(lr.substantial(['image_quality']))
        self.assertTrue(lr.substantial(['scene']))
        self.assertTrue(lr.substantial(['subjects', 'activities']))


if __name__ == '__main__':
    unittest.main()
