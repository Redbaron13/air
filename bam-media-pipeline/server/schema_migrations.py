"""Idempotent schema bootstrap/migration runner (local and Neon)."""
import logging
import os
from pathlib import Path

logger = logging.getLogger("bam.schema")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MEDIA_INTEL = "m002_media_intel.sql"
SYNC_OUTBOX = "m003_sync_outbox.sql"
DRONE_META = "m004_drone_meta.sql"
_LOCK_KEY = 7243901123


def _find_base_schema(base_schema_path=None):
    candidates = [base_schema_path, "/app/sql/init_schema.sql",
                  Path(__file__).resolve().parent.parent / "sql" / "init_schema.sql"]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def ensure_schema(dsn, *, local=True, base_schema_path=None):
    """Apply base schema (if needed) and migrations. Returns applied file names."""
    import psycopg2

    if not dsn:
        raise ValueError("no DSN provided for ensure_schema")
    applied = []
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
            try:
                cur.execute("SELECT to_regclass('public.assets') IS NOT NULL")
                if not cur.fetchone()[0]:
                    base = _find_base_schema(base_schema_path)
                    if base is None:
                        raise FileNotFoundError("init_schema.sql not found")
                    logger.info("Applying base schema %s", base)
                    cur.execute(base.read_text())
                    applied.append(base.name)
                files = [MEDIA_INTEL, DRONE_META]
                if local:
                    files += [SYNC_OUTBOX, "sync_outbox_snapshot.sql"]
                for name in files:
                    logger.info("Applying migration %s", name)
                    cur.execute((MIGRATIONS_DIR / name).read_text())
                    applied.append(name)
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    finally:
        conn.close()
    return applied
