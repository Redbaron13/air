import copy
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collection_match as cm


def record(digest='a', latitude=40, longitude=-74, offset='-05:00'):
    fields = {name: {'value': value, 'status': 'recorded'} for name, value in {
        'latitude': latitude, 'longitude': longitude, 'captured_at': '2026:11:17 10:00:00',
        'capture_offset': offset}.items()}
    return {'schema_version': cm.VERSION, 'state': 'PENDING_HUMAN',
            'completed_local_rounds': 1, 'rounds': [{'a': 'full output', 'b': 'full output'}],
            'source_sha256': digest * 64, 'preview_sha256': 'c' * 64,
            'metadata': {'fields': fields}, 'final': {}}


class MatchingTests(unittest.TestCase):
    def test_no_match_before_review(self):
        data = record()
        data['state'] = 'INGESTED'
        with self.assertRaises(ValueError):
            cm.completed(data)

    def test_missing_offset_cannot_order_timeline(self):
        data = record(offset=None)
        self.assertIsNone(cm.evidence(data, record('b'))['b_minus_a_seconds'])

    def test_gps_proximity_is_not_flight_or_3d_membership(self):
        facts = cm.evidence(record(), record('b', latitude=40.0001))
        self.assertLess(facts['camera_distance_m'], 12)
        self.assertEqual(facts['flight_plan_membership'], 'unverified')
        self.assertEqual(facts['photogrammetry_membership'], 'unverified')

    def test_registry_conflict_preserves_original(self):
        with cm.connect(':memory:') as db:
            cm.register(db, record())
            changed = record()
            changed['final'] = {'changed': True}
            with self.assertRaises(ValueError):
                cm.register(db, changed)

    def test_candidates_are_completed_nearby_records_only(self):
        with cm.connect(':memory:') as db:
            for data in (record(), record('b', 40.0001), record('d', 45)):
                cm.register(db, data)
            found = cm.candidates(db, record())
            self.assertEqual([d['source_sha256'] for d in found], ['b' * 64])

    def test_model_gets_full_results_without_promoting_groups(self):
        a, b = record(), record('b')
        def call(model, prompt, schema):
            self.assertIn('full output', prompt)
            return dict(location='same_place_candidate', visual_changes='unknown',
                        subtle_differences='unknown', reason='nearby camera positions')
        proposal = cm.propose(a, b, call, 'local')
        self.assertFalse(proposal['auto_merge_allowed'])
        self.assertTrue(proposal['human_confirmation_required'])


if __name__ == '__main__':
    unittest.main()
