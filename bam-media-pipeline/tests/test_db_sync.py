import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import db_sync  # noqa: E402

LOCAL = os.getenv("BAM_TEST_PG_DSN_LOCAL")
REMOTE = os.getenv("BAM_TEST_PG_DSN_REMOTE")


class PureHelperTests(unittest.TestCase):
    def test_coalesce_keeps_latest_per_pk(self):
        rows = [
            (3, "assets", "upsert", {"asset_id": "a"}, {"asset_id": "a", "notes": "v2"}),
            (1, "assets", "upsert", {"asset_id": "a"}, {"asset_id": "a", "notes": "v1"}),
            (2, "assets", "upsert", {"asset_id": "b"}, {"asset_id": "b"}),
        ]
        changes, ids = db_sync.coalesce_rows(rows)
        self.assertEqual(ids, [1, 2, 3])
        by_pk = {c["pk"]["asset_id"]: c for c in changes}
        self.assertEqual(by_pk["a"]["payload"]["notes"], "v2")
        self.assertEqual(len(changes), 2)

    def test_delete_after_upsert_wins(self):
        rows = [
            (1, "shoots", "upsert", {"shoot_id": "s"}, {"shoot_id": "s"}),
            (2, "shoots", "delete", {"shoot_id": "s"}, None),
        ]
        changes, _ = db_sync.coalesce_rows(rows)
        self.assertEqual([c["op"] for c in changes], ["delete"])

    def test_coalesce_accepts_json_strings(self):
        rows = [(1, "shoots", "upsert", json.dumps({"shoot_id": "s"}), json.dumps({"shoot_id": "s"}))]
        changes, _ = db_sync.coalesce_rows(rows)
        self.assertEqual(changes[0]["pk"], {"shoot_id": "s"})

    def test_fk_order(self):
        mk = lambda t, op: {"table": t, "op": op, "pk": {}, "payload": {}}
        ordered = db_sync.order_changes([
            mk("shoot_links", "upsert"), mk("assets", "upsert"), mk("shoots", "upsert"),
            mk("shoots", "delete"), mk("assets", "delete"), mk("unknown", "upsert"),
        ])
        self.assertEqual([(c["table"], c["op"]) for c in ordered], [
            ("shoots", "upsert"), ("assets", "upsert"), ("shoot_links", "upsert"),
            ("assets", "delete"), ("shoots", "delete"),
        ])

    def test_upsert_sql(self):
        sql = db_sync.build_upsert_sql("assets", ["asset_id", "notes"], ["asset_id"])
        self.assertIn("jsonb_populate_record(NULL::\"assets\"", sql)
        self.assertIn('ON CONFLICT ("asset_id") DO UPDATE SET "notes" = EXCLUDED."notes"', sql)
        sql = db_sync.build_upsert_sql("shoot_links", ["shoot_id", "related_shoot_id"],
                                       ["shoot_id", "related_shoot_id"])
        self.assertIn("DO NOTHING", sql)

    def test_delete_sql_composite(self):
        sql = db_sync.build_delete_sql("shoot_links", ["shoot_id", "related_shoot_id"])
        self.assertIn('"shoot_id"::text = %s AND "related_shoot_id"::text = %s', sql)


class OfflineBehaviourTests(unittest.TestCase):
    def test_unconfigured(self):
        with mock.patch.dict(os.environ, {"NEON_DATABASE_URL": "", "DATABASE_URL": ""}):
            r = db_sync.run_sync()
            self.assertEqual(r["remote"], "unconfigured")
            self.assertEqual(r["pushed"], 0)
            self.assertFalse(db_sync.sync_status()["configured"])

    def test_unreachable_local_does_not_raise(self):
        env = {"NEON_DATABASE_URL": "postgresql://x@127.0.0.1:1/n", "DATABASE_URL": "postgresql://x@127.0.0.1:1/l"}
        with mock.patch.dict(os.environ, env):
            r = db_sync.run_sync()
            self.assertEqual(r["remote"], "unreachable")
            self.assertTrue(r["error"])
            self.assertTrue(db_sync.sync_status()["configured"])

    def test_remote_failure_marks_batch_and_returns_unreachable(self):
        lcur = mock.MagicMock()
        lcur.__enter__.return_value = lcur
        lcur.fetchone.return_value = (2,)
        lcur.fetchall.return_value = [(1, "shoots", "upsert", {"shoot_id": "s"}, {"shoot_id": "s"})]
        lconn = mock.MagicMock()
        lconn.cursor.return_value = lcur
        env = {"NEON_DATABASE_URL": "remote", "DATABASE_URL": "local"}

        def fake_connect(dsn, timeout=None):
            return lconn if dsn == "local" else mock.MagicMock()

        with mock.patch.dict(os.environ, env), \
                mock.patch.object(db_sync, "_connect", side_effect=fake_connect), \
                mock.patch.object(db_sync, "_apply_changes", side_effect=RuntimeError("boom")), \
                mock.patch("server.schema_migrations.ensure_schema", return_value=[]):
            r = db_sync.run_sync()
        self.assertEqual(r["remote"], "unreachable")
        self.assertEqual(r["error"], "boom")
        updates = [c.args for c in lcur.execute.call_args_list if "attempts = attempts + 1" in c.args[0]]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1][1], [1])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(LOCAL and REMOTE, "BAM_TEST_PG_DSN_LOCAL / BAM_TEST_PG_DSN_REMOTE not set")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        from server.schema_migrations import ensure_schema
        cls.psycopg2 = psycopg2
        for dsn in (LOCAL, REMOTE):
            c = psycopg2.connect(dsn)
            c.autocommit = True
            c.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            c.close()
        cls.applied = ensure_schema(LOCAL, local=True)
        assert ensure_schema(LOCAL, local=True)  # idempotent

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"DATABASE_URL": LOCAL, "NEON_DATABASE_URL": REMOTE})
        self._env.start()
        self.run_sql(LOCAL, "TRUNCATE sync_outbox, shoots, assets, training_examples, shoot_links CASCADE")
        if self.run_sql(REMOTE, "SELECT to_regclass('public.shoots') IS NOT NULL", fetch=True)[0][0]:
            self.run_sql(REMOTE, "TRUNCATE shoots, assets, training_examples, shoot_links CASCADE")

    def tearDown(self):
        self._env.stop()

    def run_sql(self, dsn, sql, args=None, fetch=False):
        conn = self.psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall() if fetch else None
            conn.commit()
            return rows
        finally:
            conn.close()

    def seed(self):
        self.run_sql(LOCAL, "INSERT INTO shoots (shoot_id, project_name, centroid_lat, centroid_lon) "
                            "VALUES ('s1', 'P', 1.5, 2.5)")
        self.run_sql(LOCAL, "INSERT INTO assets (asset_id, unique_stem, original_filename, source_path, "
                            "original_sha256, media_kind, shoot_id, indicators) "
                            "VALUES ('a1', 'ab12', 'f.jpg', '/x/f.jpg', %s, 'image', 's1', '{\"k\": 1}')",
                     ("a" * 64,))
        self.run_sql(LOCAL, "INSERT INTO training_examples (asset_id, source, after_output, original_ref) "
                            "VALUES ('a1', 'edit', '{\"d\": \"x\"}', '{\"nas\": \"k\"}')")

    def test_migrations_and_outbox(self):
        self.assertIn("m002_media_intel.sql", self.applied)
        self.seed()
        rows = self.run_sql(LOCAL, "SELECT table_name, op FROM sync_outbox ORDER BY id", fetch=True)
        self.assertEqual(rows, [("shoots", "upsert"), ("assets", "upsert"), ("training_examples", "upsert")])
        self.assertEqual(db_sync.sync_status()["pending"], 3)

    def test_sync_update_delete_and_offline_recovery(self):
        self.seed()
        r = db_sync.run_sync()
        self.assertEqual((r["remote"], r["pushed"], r["pending"]), ("ok", 3, 0))
        self.assertEqual(self.run_sql(REMOTE, "SELECT indicators->>'k' FROM assets", fetch=True), [("1",)])
        self.assertEqual(self.run_sql(REMOTE, "SELECT count(*) FROM training_examples", fetch=True), [(1,)])

        self.run_sql(LOCAL, "UPDATE assets SET notes = 'hello' WHERE asset_id = 'a1'")
        r = db_sync.run_sync()
        self.assertEqual((r["remote"], r["pushed"]), ("ok", 1))
        self.assertEqual(self.run_sql(REMOTE, "SELECT notes FROM assets", fetch=True), [("hello",)])

        # offline: local writes keep working, backlog grows, nothing raises
        bad = REMOTE.rsplit("@", 1)[0] + "@127.0.0.1:%d/neon" % _free_port() if "@" in REMOTE else None
        if bad is None:
            bad = "postgresql://127.0.0.1:%d/neon" % _free_port()
        with mock.patch.dict(os.environ, {"NEON_DATABASE_URL": bad}):
            self.run_sql(LOCAL, "UPDATE assets SET notes = 'offline' WHERE asset_id = 'a1'")
            self.run_sql(LOCAL, "INSERT INTO shoot_links (shoot_id, related_shoot_id, relation) "
                                "VALUES ('s1', 's2', 'near')")
            r = db_sync.run_sync()
            self.assertEqual(r["remote"], "unreachable")
            self.assertEqual(r["pending"], 2)
            self.run_sql(LOCAL, "UPDATE assets SET notes = 'offline2' WHERE asset_id = 'a1'")
            self.assertEqual(db_sync.sync_status()["pending"], 3)
            self.assertEqual(db_sync.sync_status()["failed"], 0)

        r = db_sync.run_sync()
        self.assertEqual((r["remote"], r["pending"]), ("ok", 0))
        self.assertEqual(self.run_sql(REMOTE, "SELECT notes FROM assets", fetch=True), [("offline2",)])
        self.assertEqual(self.run_sql(REMOTE, "SELECT relation FROM shoot_links", fetch=True), [("near",)])

        self.run_sql(LOCAL, "DELETE FROM shoot_links")
        self.run_sql(LOCAL, "DELETE FROM assets WHERE asset_id = 'a1'")
        r = db_sync.run_sync()
        self.assertEqual(r["remote"], "ok")
        self.assertEqual(self.run_sql(REMOTE, "SELECT count(*) FROM assets", fetch=True), [(0,)])
        self.assertEqual(self.run_sql(REMOTE, "SELECT count(*) FROM shoot_links", fetch=True), [(0,)])

    def test_no_outbox_on_remote_and_prune(self):
        self.seed()
        db_sync.run_sync()
        self.assertEqual(self.run_sql(REMOTE, "SELECT to_regclass('public.sync_outbox') IS NULL",
                                      fetch=True), [(True,)])
        self.run_sql(LOCAL, "UPDATE sync_outbox SET synced_at = now() - interval '9 days'")
        self.assertEqual(db_sync.prune_outbox(7), 3)


if __name__ == "__main__":
    unittest.main()
