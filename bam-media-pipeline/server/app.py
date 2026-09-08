import os
import json
import asyncio
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import redis.asyncio as aioredis

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_DSN = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")

manager = []

class IngestRequest(BaseModel):
    paths: list[str]

async def log_to_gui(message: str):
    """Publishes log messages to Redis so they stream to the GUI terminal via WebSockets."""
    try:
        r = aioredis.from_url(REDIS_URL)
        await r.publish("pipeline_events", message)
    except Exception as e:
        print(f"Redis log error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(redis_log_subscriber())

async def redis_log_subscriber():
    """Listens to Redis pub/sub and broadcasts logs to connected WebSockets."""
    while True:
        try:
            r = aioredis.from_url(REDIS_URL)
            pubsub = r.pubsub()
            await pubsub.subscribe("pipeline_events")
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg:
                    for ws in manager:
                        try:
                            await ws.send_text(msg["data"].decode("utf-8"))
                        except:
                            pass
        except Exception:
            await asyncio.sleep(2.0)

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    manager.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.remove(websocket)

@app.get("/api/storage/staged")
def get_staged_files():
    """Scans the active directory for staged images, videos, and 3D assets."""
    active_dir = "/storage/active"
    staged = []
    if os.path.exists(active_dir):
        for root, dirs, files in os.walk(active_dir):
            for f in files:
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.avif', '.heic', '.dng', '.mp4', '.mov', '.glb', '.gltf', '.obj', '.usdz')):
                    full_path = os.path.join(root, f)
                    staged.append({
                        "path": full_path,
                        "folder": os.path.relpath(root, active_dir),
                        "name": f
                    })
    return staged

@app.post("/api/pipeline/ingest")
async def run_ingestion(req: IngestRequest):
    """Triggers the ingest worker for specifically selected files or folders."""
    await log_to_gui(f"[SYSTEM] Starting targeted ingestion for {len(req.paths)} files...")
    for path in req.paths:
        try:
            process = await asyncio.create_subprocess_exec(
                "python", "-u", "/app/scripts/ingest_worker.py", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            while True:
                line = await process.stdout.readline()
                if not line: break
                text = line.decode().strip()
                if text: await log_to_gui(text)
            await process.wait()
        except Exception as e:
            await log_to_gui(f"[ERROR] Failed to ingest {path}: {str(e)}")
    
    await log_to_gui("[SYSTEM] Target ingestion complete. Ready for Batch Run.")
    return {"status": "success"}

@app.get("/api/assets/pending")
def get_pending():
    """Fetches assets waiting for human review in the review board."""
    with psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT a.*, e.screener_output, e.verifier_output, e.judge_output, e.agreement_score 
            FROM assets a LEFT JOIN ai_evaluations e ON a.asset_id = e.asset_id 
            WHERE a.workflow_state = 'PENDING_HUMAN' ORDER BY a.created_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]

@app.post("/api/assets/{asset_id}/approve")
def approve_asset(asset_id: str):
    """Approves an asset, moving it forward in the pipeline."""
    with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE assets SET workflow_state = 'APPROVED' WHERE asset_id = %s", (asset_id,))
        conn.commit()
    return {"status": "success"}

@app.post("/api/system/vram/{action}/{model_name}")
async def manage_vram(action: str, model_name: str):
    """Handles user-driven VRAM pre-warming and flushing."""
    payload = {"model": model_name, "keep_alive": 0 if action == "unload" else -1}
    await log_to_gui(f"[VRAM] {'Flushing' if action == 'unload' else 'Pre-warming'} {model_name}...")
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=None)
            await log_to_gui(f"[SYSTEM] {model_name} {action} complete.")
            return {"status": "success"}
        except Exception as e:
            await log_to_gui(f"[ERROR] {str(e)}")
            return {"error": str(e)}

@app.post("/api/pipeline/run-orchestrator")
async def run_orchestrator():
    """Triggers the multi-phase AI evaluation pipeline."""
    await log_to_gui("[SYSTEM] Starting Vision Orchestrator batch...")
    try:
        process = await asyncio.create_subprocess_exec(
            "python", "-u", "/app/scripts/vision_orchestrator.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        while True:
            line = await process.stdout.readline()
            if not line: break
            text = line.decode().strip()
            if text: await log_to_gui(text)
        await process.wait()
        await log_to_gui("[SYSTEM] Orchestrator batch complete.")
        return {"status": "success"}
    except Exception as e:
        await log_to_gui(f"[ERROR] Orchestrator failed: {str(e)}")
        return {"error": str(e)}

app.mount("/", StaticFiles(directory="static", html=True), name="static")