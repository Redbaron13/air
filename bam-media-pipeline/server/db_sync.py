"""Local-first Postgres -> Neon sync driven by the local sync_outbox table."""
import json
import logging
import os

logger = logging.getLogger("bam.db_sync")

DEFAULT_BATCH = 5 * 5 * 5 * 4
CONNECT_TIMEOUT = 5

# FK-safe order for upserts; deletes run in reverse.
TABLE_ORDER = ["shoots", "assets", "ai_evaluations", "asset_copies",
               "human_corrections", "video_frames", "training_examples", "shoot_links"]
PK_COLUMNS = {
    "shoots": ["shoot_id"],
    "assets": ["asset_id"],
    "ai_evaluations": ["eval_id"],
    "asset_copies": ["copy_id"],
    "human_corrections": ["correction_id"],
    "training_examples": ["example_id"],
    "video_frames": ["frame_id"],
    "shoot_links": ["shoot_id", "related_shoot_id"],
}


def _local_dsn():
    return os.getenv("DATABASE_URL") or None


def _remote_dsn():
    return os.getenv("NEON_DATABASE_URL") or None


def _connect(dsn, timeout=None):
    import psycopg2
    kwargs = {"connect_timeout": timeout} if timeout else {}
    return psycopg2.connect(dsn, **kwargs)


def _qi(name):
    return '"' + name.replace('"', '""') + '"'


def coalesce_rows(rows):
    """rows: iterable of (id, table_name, op, pk(dict), payload). Keep the latest per (table, pk).

    Returns (changes, ids): changes is a list of dicts {table, op, pk, payload}; ids covers all rows.
    """
    latest = {}
    ids = []
    for rid, table, op, pk, payload in sorted(rows, key=lambda r: r[0]):
        ids.append(rid)
        if isinstance(pk, str):
            pk = json.loads(pk)
        if isinstance(payload, str):
            payload = json.loads(payload)
        key = (table, json.dumps(pk, sort_keys=True))
        latest[key] = {"table": table, "op": op, "pk": pk, "payload": payload, "id": rid}
    return list(latest.values()), ids


def order_changes(changes):
    """Upserts in FK order first, then deletes in reverse order. Unknown tables are dropped."""
    pos = {t: i for i, t in enumerate(TABLE_ORDER)}
    ups = [c for c in changes if c["op"] != "delete" and c["table"] in pos]
    dels = [c for c in changes if c["op"] == "delete" and c["table"] in pos]
    ups.sort(key=lambda c: pos[c["table"]])
    dels.sort(key=lambda c: -pos[c["table"]])
    return ups + dels


def build_upsert_sql(table, columns, pk_cols):
    """columns: remote column names (already filtered). Payload is passed as a jsonb parameter."""
    non_pk = [c for c in columns if c not in pk_cols]
    conflict = ", ".join(_qi(c) for c in pk_cols)
    if non_pk:
        action = "DO UPDATE SET " + ", ".join(f"{_qi(c)} = EXCLUDED.{_qi(c)}" for c in non_pk)
    else:
        action = "DO NOTHING"
    cols = ", ".join(_qi(c) for c in columns)
    return (f"INSERT INTO {_qi(table)} ({cols}) "
            f"SELECT {cols} FROM jsonb_populate_record(NULL::{_qi(table)}, %s::jsonb) "
            f"ON CONFLICT ({conflict}) {action}")


def build_delete_sql(table, pk_cols):
    where = " AND ".join(f"{_qi(c)}::text = %s" for c in pk_cols)
    return f"DELETE FROM {_qi(table)} WHERE {where}"


def _remote_columns(cur, table):
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (table,))
    return [r[0] for r in cur.fetchall()]


def _apply_changes(rcur, changes):
    colcache = {}
    for ch in order_changes(changes):
        table = ch["table"]
        pk_cols = PK_COLUMNS[table]
        if ch["op"] == "delete":
            rcur.execute(build_delete_sql(table, pk_cols), [str(ch["pk"][c]) for c in pk_cols])
            continue
        if table not in colcache:
            colcache[table] = _remote_columns(rcur, table)
        remote_cols = colcache[table]
        payload = {k: v for k, v in (ch["payload"] or {}).items() if k in remote_cols}
        cols = [c for c in remote_cols if c in payload]
        rcur.execute(build_upsert_sql(table, cols, pk_cols), (json.dumps(payload),))


def sync_status():
    status = {"configured": bool(_remote_dsn()), "pending": 0, "oldest_pending_at": None,
              "last_error": None, "failed": 0}
    dsn = _local_dsn()
    if not dsn:
        return status
    try:
        conn = _connect(dsn, CONNECT_TIMEOUT)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*), min(created_at), count(*) FILTER (WHERE attempts > 0) "
                            "FROM sync_outbox WHERE synced_at IS NULL")
                n, oldest, failed = cur.fetchone()
                status.update(pending=n, failed=failed,
                              oldest_pending_at=oldest.isoformat() if oldest else None)
                cur.execute("SELECT last_error FROM sync_outbox WHERE synced_at IS NULL "
                            "AND last_error IS NOT NULL ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                status["last_error"] = row[0] if row else None
        finally:
            conn.close()
    except Exception as e:
        status["last_error"] = f"local db unavailable: {e}"
    return status


def _pending_count(lconn):
    with lconn.cursor() as cur:
        cur.execute("SELECT count(*) FROM sync_outbox WHERE synced_at IS NULL")
        return cur.fetchone()[0]


def _record_failure(lconn, ids, err):
    if not ids:
        return
    try:
        with lconn.cursor() as cur:
            cur.execute("UPDATE sync_outbox SET attempts = attempts + 1, last_error = %s WHERE id = ANY(%s)",
                        (str(err)[:5*5*5*4], ids))
        lconn.commit()
    except Exception:
        lconn.rollback()


def run_sync(batch_size=DEFAULT_BATCH, max_batches=None):
    result = {"pushed": 0, "pending": 0, "remote": "unconfigured", "error": None}
    remote_dsn, local_dsn = _remote_dsn(), _local_dsn()
    if not remote_dsn or not local_dsn:
        if local_dsn:
            try:
                lc = _connect(local_dsn, CONNECT_TIMEOUT)
                try:
                    result["pending"] = _pending_count(lc)
                finally:
                    lc.close()
            except Exception:
                pass
        return result

    lconn = None
    rconn = None
    ids = []
    try:
        lconn = _connect(local_dsn, CONNECT_TIMEOUT)
        result["pending"] = _pending_count(lconn)
        lconn.commit()
    except Exception as e:
        result.update(remote="unreachable", error=f"local db unavailable: {e}")
        return result

    try:
        from server.schema_migrations import ensure_schema
        rconn = _connect(remote_dsn, CONNECT_TIMEOUT)
        ensure_schema(remote_dsn, local=False)
        batches = 0
        while max_batches is None or batches < max_batches:
            with lconn.cursor() as cur:
                cur.execute("SELECT id, table_name, op, pk, payload FROM sync_outbox "
                            "WHERE synced_at IS NULL ORDER BY id LIMIT %s", (batch_size,))
                rows = cur.fetchall()
            lconn.commit()
            if not rows:
                break
            ids = [r[0] for r in rows]
            changes, _ = coalesce_rows(rows)
            try:
                with rconn.cursor() as rcur:
                    _apply_changes(rcur, changes)
                rconn.commit()
            except Exception:
                rconn.rollback()
                raise
            with lconn.cursor() as cur:
                cur.execute("UPDATE sync_outbox SET synced_at = now(), last_error = NULL WHERE id = ANY(%s)",
                            (ids,))
            lconn.commit()
            result["pushed"] += len(changes)
            ids = []
            batches += 1
        result["pending"] = _pending_count(lconn)
        lconn.commit()
        result["remote"] = "ok"
    except Exception as e:
        logger.warning("Neon sync failed: %s", e)
        result.update(remote="unreachable", error=str(e))
        try:
            if lconn is not None:
                lconn.rollback()
                _record_failure(lconn, ids, e)
                result["pending"] = _pending_count(lconn)
        except Exception:
            pass
    finally:
        for c in (rconn, lconn):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
    return result


def prune_outbox(days=7):
    dsn = _local_dsn()
    if not dsn:
        return 0
    conn = _connect(dsn, CONNECT_TIMEOUT)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sync_outbox WHERE synced_at IS NOT NULL "
                        "AND synced_at < now() - make_interval(days => %s)", (int(days),))
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()
