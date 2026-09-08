import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

def build_manifest_entry(asset: dict, copies: list[dict]) -> dict:
    variants = sorted({c["width"] for c in copies if c.get("width") is not None})
    formats = sorted({c["format"] for c in copies if c.get("format")})
    captured_at = asset.get("captured_at")
    captured_date = captured_at.strftime("%Y-%m-%d") if captured_at else "unknown"

    geo = {
        "site": asset.get("project_name") or "North Jersey Site",
        "city": asset.get("city") or "East Orange",
        "state": asset.get("state") or "NJ"
    }
    if asset.get("centroid_lat") is not None and asset.get("centroid_lon") is not None:
        geo["centroid"] = {
            "lat": asset["centroid_lat"],
            "lon": asset["centroid_lon"]
        }

    return {
        "key": f"captures/{captured_date}-{asset['shoot_id'] or 'general'}/{asset['unique_stem']}",
        "job": asset["shoot_id"] or "BAM-DIRECT",
        "captured": captured_date,
        "sensor": asset["sensor_model"],
        "geo": geo,
        "serviceTags": asset["service_tags"],
        "describes": asset["describes"],
        "alt": asset["alt"],
        "credit": "Baron Aerial Media",
        "variants": variants,
        "formats": formats,
        "lqip": asset.get("lqip", ""),
        "sha256": asset["original_sha256"]
    }

DB_DSN = os.getenv("DATABASE_URL", "postgresql://bam_admin:bam_secure_super_password_2026@postgres:5432/bam_media_factory")

def compile_manifest(output_path="/storage/exports/assets.manifest.json"):
    conn = psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # Retrieve all human-approved assets
    cur.execute("""
        SELECT a.*, s.project_name, s.centroid_lat, s.centroid_lon
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

        manifest[aid] = build_manifest_entry(a, copies)

    # Ensure the export directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[MANIFEST COMPILED] Wrote {len(manifest)} validated assets to {output_path}")
    conn.close()

if __name__ == "__main__":
    compile_manifest()
