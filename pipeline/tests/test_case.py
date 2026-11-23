import json
import shutil
import sqlite3
import subprocess
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest import mock

from pipeline.air import group
from pipeline.air.case import build_cases, case_id_for
from pipeline.air.cli import CHAIN
from pipeline.air.identity import build_temp_id, location_id_for
from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path


WHEN = datetime(2026, 1, 21, 13, 15, 21)
LATER = datetime(2026, 6, 21, 13, 15, 21)
PLACE = (40.7484, -73.9857)
OTHER = (41.5, -72.5)


def _insert(journal: Journal, temp_id: str, when: datetime, shot: str, lat: float, lon: float) -> None:
    journal.insert_asset(
        {
            "temp_id": temp_id,
            "media_kind": "still",
            "original_name": f"{temp_id}.jpg",
            "captured_at": when.isoformat(),
            "latitude": lat,
            "longitude": lon,
            "gimbal_pitch": -90 if shot == "nadir" else -60,
            "shot_characteristic": shot,
        }
    )


class CaseBuildTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("pipeline/tests/_work_case")
        if self.root.exists():
            shutil.rmtree(self.root)
        (self.root / "Database").mkdir(parents=True)
        self.nadir_a = build_temp_id("still", "CAAA", WHEN, "AA", 1)
        self.nadir_b = build_temp_id("still", "CAAB", LATER, "AA", 2)
        self.oblique = build_temp_id("still", "CAAC", WHEN, "AA", 3)
        self.other = build_temp_id("still", "CAAD", WHEN, "AA", 4)
        journal = Journal(journal_path(self.root))
        _insert(journal, self.nadir_a, WHEN, "nadir", *PLACE)
        _insert(journal, self.nadir_b, LATER, "nadir", *PLACE)
        _insert(journal, self.oblique, WHEN, "high_oblique", *PLACE)
        _insert(journal, self.other, WHEN, "nadir", *OTHER)
        journal.close()
        group.group_location(self.root)
        group.group_before_after(self.root)
        group.group_model(self.root)

    def tearDown(self):
        journal = Journal(journal_path(self.root))
        try:
            self.location_id = location_id_for(*PLACE)
            self.other_location_id = location_id_for(*OTHER)
            assets = {row["temp_id"]: row["location_id"] for row in journal.assets()}
        finally:
            journal.close()
        self.assertEqual(assets[self.oblique], self.location_id)
        self.assertEqual(assets[self.nadir_a], self.location_id)
        self.assertEqual(assets[self.nadir_b], self.location_id)
        self.assertEqual(assets[self.other], self.other_location_id)

    def test_grouped_place_writes_one_case_and_rerun_updates_it(self):
        journal = Journal(journal_path(self.root))
        candidate_ids = [row["candidate_id"] for row in journal.query("SELECT candidate_id FROM before_after_candidates")]
        set_ids = [row["set_id"] for row in journal.query("SELECT set_id FROM model_view_sets")]
        location_ids = {row["location_id"] for row in journal.query("SELECT location_id FROM locations")}
        journal.close()
        self.assertEqual(location_ids, {location_id_for(*PLACE), location_id_for(*OTHER)})
        self.assertEqual(len(candidate_ids), 1)
        self.assertEqual(len(set_ids), 1)

        first = _cli_case_build(self.root)
        self.assertEqual(first["cases"], 1)
        case_id = case_id_for(location_id_for(*PLACE))
        row = _only_case(self.root)
        self.assertEqual(row["case_id"], case_id)
        self.assertEqual(row["location_id"], location_id_for(*PLACE))
        self.assertEqual(row["title"], f"Location {row['location_id']}")
        self.assertNotIn("York", row["title"])
        self.assertIn(candidate_ids[0], row["notes"])
        self.assertIn(set_ids[0], row["notes"])
        self.assertNotEqual(row["case_id"], row["location_id"])

        second = build_cases(self.root)
        self.assertEqual(second["cases"], 1)
        again = _only_case(self.root)
        self.assertEqual(again["case_id"], case_id)
        self.assertEqual(again["notes"], row["notes"])

        journal = Journal(journal_path(self.root))
        journal.execute(
            """
            INSERT INTO model_view_sets(set_id, location_id, status)
            VALUES ('MVEXTRA', ?, 'various_views')
            """,
            (location_id_for(*PLACE),),
        )
        journal.execute(
            """
            INSERT INTO model_view_members(set_id, temp_id, shot_characteristic)
            VALUES ('MVEXTRA', ?, 'high_oblique')
            """,
            (self.oblique,),
        )
        journal.close()
        third = _cli_case_build(self.root)
        self.assertEqual(third["cases"], 1)
        updated = _only_case(self.root)
        self.assertEqual(updated["case_id"], case_id)
        self.assertIn("MVEXTRA", updated["notes"])
        self.assertIn(candidate_ids[0], updated["notes"])
        self.assertIn(set_ids[0], updated["notes"])

    def test_two_locations_commit_together_and_a_lost_candidate_drops_the_case(self):
        place_id = location_id_for(*PLACE)
        other_id = location_id_for(*OTHER)
        _add_candidate(self.root, "BAOTHER", other_id, self.other)

        built = build_cases(self.root)
        self.assertEqual(built["cases"], 2)
        rows = _cases(self.root)
        self.assertEqual(
            {row["case_id"] for row in rows},
            {case_id_for(place_id), case_id_for(other_id)},
        )
        self.assertEqual({row["location_id"] for row in rows}, {place_id, other_id})

        journal = Journal(journal_path(self.root))
        journal.execute("DELETE FROM before_after_members WHERE candidate_id = 'BAOTHER'")
        journal.execute("DELETE FROM before_after_candidates WHERE candidate_id = 'BAOTHER'")
        journal.execute(
            """
            INSERT INTO case_studies(case_id, location_id, title, notes)
            VALUES ('CS-GONE', 'LGONE', 'Gone', 'stale location')
            """
        )
        journal.close()
        again = build_cases(self.root)
        self.assertEqual(again["cases"], 1)
        remaining = _cases(self.root)
        self.assertEqual([row["case_id"] for row in remaining], [case_id_for(place_id)])
        self.assertEqual(remaining[0]["location_id"], place_id)

    def test_case_build_rolls_back_when_the_second_location_fails(self):
        other_id = location_id_for(*OTHER)
        _add_candidate(self.root, "BAOTHER", other_id, self.other)
        journal = Journal(journal_path(self.root))
        journal.execute(
            """
            INSERT INTO case_studies(case_id, location_id, title, notes)
            VALUES ('CS-GONE', 'LGONE', 'Gone', 'stale location')
            """
        )
        journal.execute(
            """
            CREATE TRIGGER case_studies_fail_second
            BEFORE INSERT ON case_studies
            WHEN (SELECT COUNT(*) FROM case_studies) >= 2
            BEGIN
                SELECT RAISE(ABORT, 'second case');
            END
            """
        )
        journal.close()
        with self.assertRaises(sqlite3.IntegrityError):
            build_cases(self.root)
        self.assertEqual([row["case_id"] for row in _cases(self.root)], ["CS-GONE"])

    def test_chain_places_case_after_model_synthesis(self):
        self.assertLess(CHAIN.index("synthesize-video"), CHAIN.index("group"))
        self.assertLess(CHAIN.index("group"), CHAIN.index("synthesize-model"))
        self.assertLess(CHAIN.index("synthesize-model"), CHAIN.index("case"))
        self.assertLess(CHAIN.index("case"), CHAIN.index("id"))
        result = _cli(self.root, "run", "--from", "case", "--to", "case")
        self.assertEqual(result["case"]["cases"], 1)
        self.assertEqual(result["sync_after_case"]["status"], "skipped")
        self.assertEqual(result["sync_after_case"]["tables"]["case_studies"], 1)
        self.assertNotIn("sync", result)
        self.assertEqual(_only_case(self.root)["case_id"], case_id_for(location_id_for(*PLACE)))

    def test_run_keeps_the_early_sync_and_syncs_again_after_case(self):
        import pipeline.air.cli as cli

        calls = []

        def record(label, payload=None):
            def inner(*_args, **_kwargs):
                calls.append(label)
                return {} if payload is None else payload

            return inner

        patches = [
            mock.patch("pipeline.air.cli.prep.scan", record("prep")),
            mock.patch("pipeline.air.cli.sync.sync_library", record("sync", {"status": "skipped", "tables": {}})),
            mock.patch("pipeline.air.cli.ai.exif", record("exif")),
            mock.patch("pipeline.air.cli.ai.describe", record("describe")),
            mock.patch("pipeline.air.cli.ai.reason", record("reason")),
            mock.patch("pipeline.air.cli.ai.validate", record("validate")),
            mock.patch("pipeline.air.cli.ai.synthesize_video", record("synthesize-video")),
            mock.patch("pipeline.air.cli.group.group_location", record("group-location")),
            mock.patch("pipeline.air.cli.group.group_shots", record("group-shots")),
            mock.patch("pipeline.air.cli.group.group_before_after", record("group-before-after")),
            mock.patch("pipeline.air.cli.group.group_overlap", record("group-overlap")),
            mock.patch("pipeline.air.cli.group.group_model", record("group-model")),
            mock.patch("pipeline.air.cli.ai.synthesize_model", record("synthesize-model")),
            mock.patch("pipeline.air.cli.case.build_cases", record("case", {"cases": 0})),
            mock.patch("pipeline.air.cli.group.assign_ids", record("id")),
            mock.patch("pipeline.air.cli.derive.derive", record("derive")),
            mock.patch("pipeline.air.cli.manifest.build_manifest", record("manifest")),
            mock.patch("pipeline.air.cli.publish.publish", record("publish")),
        ]
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            full = cli._run(self.root, "prep", "publish")
            self.assertEqual(
                calls,
                [
                    "prep",
                    "sync",
                    "exif",
                    "describe",
                    "reason",
                    "validate",
                    "synthesize-video",
                    "group-location",
                    "group-shots",
                    "group-before-after",
                    "group-overlap",
                    "group-model",
                    "synthesize-model",
                    "case",
                    "sync",
                    "id",
                    "derive",
                    "manifest",
                    "publish",
                ],
            )
            self.assertIn("sync_after_case", full)
            calls.clear()
            early = cli._run(self.root, "prep", "sync")
            self.assertEqual(calls, ["prep", "sync"])
            self.assertNotIn("sync_after_case", early)
            calls.clear()
            identified = cli._run(self.root, "id", "id")
            self.assertEqual(calls, ["id"])
            self.assertNotIn("sync_after_case", identified)


def _add_candidate(root: Path, candidate_id: str, location_id: str, temp_id: str) -> None:
    journal = Journal(journal_path(root))
    journal.execute(
        """
        INSERT INTO before_after_candidates(candidate_id, location_id, shot_characteristic, status)
        VALUES (?, ?, 'nadir', 'candidate')
        """,
        (candidate_id, location_id),
    )
    journal.execute(
        """
        INSERT INTO before_after_members(candidate_id, temp_id, captured_at)
        VALUES (?, ?, ?)
        """,
        (candidate_id, temp_id, WHEN.isoformat()),
    )
    journal.close()


def _cli_case_build(root: Path) -> dict:
    return _cli(root, "case", "build")


def _cli(root: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "pipeline.air", "--library", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _cases(root: Path):
    journal = Journal(journal_path(root))
    try:
        return journal.query("SELECT * FROM case_studies ORDER BY case_id")
    finally:
        journal.close()


def _only_case(root: Path):
    rows = _cases(root)
    if len(rows) != 1:
        raise AssertionError(f"expected one case study, found {len(rows)}")
    return rows[0]


if __name__ == "__main__":
    unittest.main()
