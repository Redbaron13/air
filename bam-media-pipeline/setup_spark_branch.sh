#!/bin/bash
set -e

BRANCH_NAME="feature/gemini-spark-orchestration"
echo "Creating and switching to branch $BRANCH_NAME..."
git checkout -B "$BRANCH_NAME"

echo "Ensuring directory structure..."
mkdir -p scripts sql storage/exports storage/active

# 1. Append Neon config to .env if not present
if ! grep -q "NEON_DATABASE_URL" .env 2>/dev/null; then
    echo "" >> .env
    echo "# Neon Serverless PostgreSQL" >> .env
    echo "NEON_DATABASE_URL=postgresql://bam_admin:your_secret_password@ep-cool-project-123456.us-east-2.aws.neon.tech/bam_media_factory?sslmode=require" >> .env
    echo "Appended NEON_DATABASE_URL placeholder to .env. Remember to update it with your actual connection string!"
fi

# 2. Write the Spark Worker Script
cat << 'EOF' > scripts/spark_worker.py
import os
import sys
import json
import hashlib
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

NEON_DSN = os.getenv("NEON_DATABASE_URL")
LOCAL_MANIFEST = os.path.abspath(os.path.join(os.path.dirname(__file__), "../storage/exports/assets.manifest.json"))

def get_db():
    if not NEON_DSN:
        raise ValueError("NEON_DATABASE_URL environment variable is missing or empty.")
    return psycopg2.connect(NEON_DSN, cursor_factory=RealDictCursor)

def log_event(stage, level, message, asset_id=None, details=None):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pipeline_logs (stage, level, asset_id, message, details)
                    VALUES (%s, %s, %s, %s, %s)
                """, (stage, level, asset_id, message, json.dumps(details) if details else None))
                conn.commit()
    except Exception as err:
        print(f"[LOG ERROR] Could not commit pipeline log: {err}", file=sys.stderr)

def sync_asset(asset_record, ai_eval_record, copies_list):
    aid = asset_record["asset_id"]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO assets (
                    asset_id, unique_stem, original_filename, source_path,
                    original_sha256, media_kind, captured_at, sensor_model,
                    focal_length, gimbal_pitch, latitude, longitude, altitude_meters,
                    shoot_id, service_tags, subject_tags, describes, alt, workflow_state
                ) VALUES (
                    %(asset_id)s, %(unique_stem)s, %(original_filename)s, %(source_path)s,
                    %(original_sha256)s, %(media_kind)s, %(captured_at)s, %(sensor_model)s,
                    %(focal_length)s, %(gimbal_pitch)s, %(latitude)s, %(longitude)s, %(altitude_meters)s,
                    %(shoot_id)s, %(service_tags)s, %(subject_tags)s, %(describes)s, %(alt)s, 'APPROVED'
                ) ON CONFLICT (asset_id) DO UPDATE SET
                    describes = EXCLUDED.describes,
                    alt = EXCLUDED.alt,
                    service_tags = EXCLUDED.service_tags,
                    workflow_state = 'APPROVED';
            """, asset_record)

            if ai_eval_record:
                cur.execute("""
                    INSERT INTO ai_evaluations (
                        asset_id, screener_model, screener_output, verifier_model,
                        verifier_output, judge_model, judge_output, agreement_score
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    aid,
                    ai_eval_record.get("screener_model", "gemini-spark"),
                    json.dumps(ai_eval_record.get("screener_output", {})),
                    ai_eval_record.get("verifier_model", "gemini-spark"),
                    json.dumps(ai_eval_record.get("verifier_output", {})),
                    ai_eval_record.get("judge_model", "gemini-spark"),
                    json.dumps(ai_eval_record.get("judge_output", {})),
                    ai_eval_record.get("agreement_score", 1.0)
                ))

            for c in copies_list:
                cur.execute("""
                    INSERT INTO asset_copies (
                        copy_id, parent_asset_id, size_tier, version_code,
                        format, width, height, byte_size, sha256, r2_storage_key
                    ) VALUES (
                        %(copy_id)s, %(parent_asset_id)s, %(size_tier)s, %(version_code)s,
                        %(format)s, %(width)s, %(height)s, %(byte_size)s, %(sha256)s, %(r2_storage_key)s
                    ) ON CONFLICT (copy_id) DO NOTHING;
                """, c)

            conn.commit()

    # Update the local manifest JSON copy
    os.makedirs(os.path.dirname(LOCAL_MANIFEST), exist_ok=True)
    manifest = {}
    if os.path.exists(LOCAL_MANIFEST):
        try:
            with open(LOCAL_MANIFEST, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}

    manifest[aid] = {
        "key": f"captures/{asset_record.get('captured_at', 'unknown')[:10]}-{asset_record.get('shoot_id') or 'general'}/{asset_record['unique_stem']}",
        "job": asset_record.get("shoot_id") or "BAM-DIRECT",
        "captured": str(asset_record.get("captured_at"))[:10],
        "sensor": asset_record.get("sensor_model"),
        "geo": {
            "site": asset_record.get("shoot_id", "North Jersey Site"),
            "lat": asset_record.get("latitude"),
            "lon": asset_record.get("longitude")
        },
        "serviceTags": json.loads(asset_record["service_tags"]) if isinstance(asset_record["service_tags"], str) else asset_record["service_tags"],
        "describes": asset_record.get("describes"),
        "alt": asset_record.get("alt"),
        "credit": "Baron Aerial Media",
        "variants": [c["width"] for c in copies_list],
        "formats": sorted(list(set(c["format"] for c in copies_list))),
        "sha256": asset_record["original_sha256"]
    }

    with open(LOCAL_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    log_event("SYNC", "INFO", f"Asset {aid} successfully synced to Neon and local manifest.", aid)
    print(f"[SUCCESS] {aid} registered to Neon and local manifest.")

if __name__ == "__main__":
    print("Testing connection to Neon...")
    try:
        with get_db() as c:
            with c.cursor() as cursor:
                cursor.execute("SELECT current_database(), version();")
                db_info = cursor.fetchone()
                print(f"Connected successfully to Neon database: {db_info['current_database']}")
    except Exception as e:
        print(f"Connection failed: {e}")
EOF

chmod +x scripts/spark_worker.py

echo "Updating requirements.txt..."
if ! grep -q "psycopg2-binary" server/requirements.txt 2>/dev/null; then
    echo "psycopg2-binary==2.9.9" >> server/requirements.txt
fi

echo "Staging files to Git..."
git add .
git status

echo "Branch $BRANCH_NAME ready! Next steps: update .env with your real Neon credentials, test with python scripts/spark_worker.py, and push with: git push -u origin $BRANCH_NAME"