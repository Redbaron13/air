import os
import json
import psycopg2
from datetime import datetime

# Replace with your actual Neon connection string in your .env
NEON_DSN = os.getenv("NEON_DATABASE_URL")
LOCAL_MANIFEST_PATH = "../storage/exports/manifest.json"

def get_db_connection():
    return psycopg2.connect(NEON_DSN)

def setup_database():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            asset_id VARCHAR(64) PRIMARY KEY,
            sha256 VARCHAR(64) UNIQUE,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS pipeline_logs (
            log_id SERIAL PRIMARY KEY,
            asset_id VARCHAR(64),
            status VARCHAR(50),
            message TEXT,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def log_progress(asset_id, status, message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pipeline_logs (asset_id, status, message) VALUES (%s, %s, %s)",
        (asset_id, status, message)
    )
    conn.commit()
    conn.close()

def sync_manifest(asset_id, file_hash, metadata):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO assets (asset_id, sha256, metadata) VALUES (%s, %s, %s) ON CONFLICT (asset_id) DO NOTHING",
        (asset_id, file_hash, json.dumps(metadata))
    )
    conn.commit()
    conn.close()
    
    local_data = {}
    if os.path.exists(LOCAL_MANIFEST_PATH):
        with open(LOCAL_MANIFEST_PATH, "r") as f:
            local_data = json.load(f)
            
    local_data[asset_id] = {
        "sha256": file_hash,
        "metadata": metadata,
        "synced_at": datetime.now().isoformat()
    }
    
    os.makedirs(os.path.dirname(LOCAL_MANIFEST_PATH), exist_ok=True)
    with open(LOCAL_MANIFEST_PATH, "w") as f:
        json.dump(local_data, f, indent=4)
        
    log_progress(asset_id, "COMPLETED", "Manifest synced to Neon cloud and local storage.")

if __name__ == "__main__":
    setup_database()
    print("Spark Worker ready. Waiting for background task execution.")