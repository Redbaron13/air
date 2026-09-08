import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

DB_DSN = os.getenv("DATABASE_URL", "postgresql://bam_admin:bam_secure_super_password_2026@postgres:5432/bam_media_factory")

def compile_manifest(output_path="/storage/exports/assets.manifest.json"):
    conn = psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # Retrieve all human-approved assets
    cur.execute("""
        SELECT a.*, s.project_name, s.city, s.state
        FROM assets a
        LEFT JOIN shoots s ON a.shoot_id = s.shoot_id
        WHERE a.workflow_state = 'APPROVED'
    """)
    assets = cur.fetchall()

    manifest = {}
    seen_hashes = set()

    for a in assets:
        aid = a["asset_id"]
        sha = a["original_sha256"]

        # Build Guardrail 1: Deduplication integrity
        if sha in seen_hashes:
            print(f"[FATAL BUILD ERROR] Duplicate SHA256 collision detected for: {aid}. Skipping.")
            continue
        seen_hashes.add(sha)

        # Build Guardrail 2: Alt text presence
        if not a["alt"] or len(a["alt"].strip()) < 10:
            print(f"[WARNING] Asset {aid} has empty or insufficient alt text. Skipping manifest entry.")
            continue

        # Query all confirmed responsive child variants
        cur.execute("SELECT width, format FROM asset_copies WHERE parent_asset_id = %s", (aid,))
        copies = cur.fetchall()

        # Extract unique widths and formats
        variants = sorted(list(set([c["width"] for c in copies])))
        formats = sorted(list(set([c["format"] for c in copies])))

        # Format timestamps safely
        captured_date = a["captured_at"].strftime("%Y-%m-%d") if a["captured_at"] else "unknown"

        manifest[aid] = {
            "key": f"captures/{captured_date}-{a['shoot_id'] or 'general'}/{a['unique_stem']}",
            "job": a["shoot_id"] or "BAM-DIRECT",
            "captured": captured_date,
            "sensor": a["sensor_model"],
            "geo": {
                "site": a.get("project_name") or "North Jersey Site",
                "city": a.get("city") or "East Orange",
                "state": a.get("state") or "NJ"
            },
            "serviceTags": a["service_tags"],
            "describes": a["describes"],
            "alt": a["alt"],
            "credit": "Baron Aerial Media",
            "variants": variants,
            "formats": formats,
            "lqip": a.get("lqip", ""),
            "sha256": sha
        }

    # Ensure the export directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[MANIFEST COMPILED] Wrote {len(manifest)} validated assets to {output_path}")
    conn.close()

if __name__ == "__main__":
    compile_manifest()
