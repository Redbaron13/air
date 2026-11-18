import os
import json
import asyncio
import logging
from pathlib import Path
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import redis.asyncio as aioredis

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bam.api")

app = FastAPI()
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:3080,http://127.0.0.1:3080"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_DSN = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
ACTIVE_DIR = Path(os.getenv("ACTIVE_DIR", "/storage/active")).resolve()
INGEST_TIMEOUT_SECONDS = 300
ALLOWED_MODELS = {"moondream", "llava", "llama3.1"}

manager = []

class IngestRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)

def validated_staged_file(raw_path: str) -> str:
    requested_path = Path(raw_path)
    if requested_path.is_symlink():
        raise HTTPException(status_code=400, detail="Symbolic links cannot be ingested")
    try:
        resolved_path = requested_path.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Selected file does not exist") from exc
    if not resolved_path.is_file() or not resolved_path.is_relative_to(ACTIVE_DIR):
        raise HTTPException(status_code=400, detail="Files must be regular files inside the staging directory")
    return str(resolved_path)

async def log_to_gui(message: str):
    """Publishes log messages to Redis so they stream to the GUI terminal via WebSockets."""
    try:
        r = aioredis.from_url(REDIS_URL)
        await r.publish("pipeline_events", message)
    except Exception as e:
        logger.warning("Unable to publish dashboard event: %s", e)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting API server; active_dir=%s allowed_origins=%s", ACTIVE_DIR, ALLOWED_ORIGINS)
    asyncio.create_task(redis_log_subscriber())

async def redis_log_subscriber():
    """Listens to Redis pub/sub and broadcasts logs to connected WebSockets."""
    while True:
        try:
            logger.info("Connecting Redis log subscriber")
            r = aioredis.from_url(REDIS_URL)
            pubsub = r.pubsub()
            await pubsub.subscribe("pipeline_events")
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg:
                    for ws in manager:
                        try:
                            await ws.send_text(msg["data"].decode("utf-8"))
                        except Exception as exc:
                            logger.info("Dropping unavailable WebSocket client: %s", exc)
                            if ws in manager:
                                manager.remove(ws)
        except Exception as exc:
            logger.exception("Redis log subscriber failed; retrying in two seconds: %s", exc)
            await asyncio.sleep(2.0)

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    manager.append(websocket)
    logger.info("WebSocket log client connected; clients=%d", len(manager))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in manager:
            manager.remove(websocket)
        logger.info("WebSocket log client disconnected; clients=%d", len(manager))

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
    logger.info("Listed %d staged media files under %s", len(staged), active_dir)
    return staged

@app.post("/api/pipeline/ingest")
async def run_ingestion(req: IngestRequest):
    """Triggers the ingest worker for specifically selected files or folders."""
    await log_to_gui(f"[SYSTEM] Starting targeted ingestion for {len(req.paths)} files...")
    logger.info("Ingestion requested for %d paths", len(req.paths))
    failed_paths = []
    for raw_path in req.paths:
        path = validated_staged_file(raw_path)
        try:
            logger.info("Starting ingest worker for %s", path)
            process = await asyncio.create_subprocess_exec(
                "python", "-u", "/app/scripts/ingest_worker.py", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            try:
                async with asyncio.timeout(INGEST_TIMEOUT_SECONDS):
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        text = line.decode(errors="replace").strip()
                        if text:
                            await log_to_gui(text)
                    return_code = await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError(f"ingestion timed out after {INGEST_TIMEOUT_SECONDS} seconds")
            if return_code != 0:
                raise RuntimeError(f"ingestion exited with code {return_code}")
            logger.info("Ingest worker completed successfully for %s", path)
        except Exception as e:
            failed_paths.append(path)
            logger.exception("Ingest worker failed for %s", path)
            await log_to_gui(f"[ERROR] Failed to ingest {path}: {str(e)}")
    if failed_paths:
        raise HTTPException(status_code=502, detail={"failed_paths": failed_paths})
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
        assets = [dict(row) for row in cur.fetchall()]
        logger.info("Fetched %d assets awaiting human review", len(assets))
        return assets

@app.post("/api/assets/{asset_id}/approve")
def approve_asset(asset_id: str):
    """Approves an asset, moving it forward in the pipeline."""
    with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE assets SET workflow_state = 'APPROVED' WHERE asset_id = %s", (asset_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Asset not found")
        conn.commit()
    logger.info("Asset approved by dashboard; asset_id=%s", asset_id)
    return {"status": "success"}

@app.post("/api/system/vram/{action}/{model_name}")
async def manage_vram(action: str, model_name: str):
    """Handles user-driven VRAM pre-warming and flushing."""
    if action not in {"load", "unload"} or model_name not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail="Unsupported model action")
    payload = {"model": model_name, "keep_alive": 0 if action == "unload" else -1}
    logger.info("VRAM action requested; action=%s model=%s", action, model_name)
    await log_to_gui(f"[VRAM] {'Flushing' if action == 'unload' else 'Pre-warming'} {model_name}...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=60.0)
            response.raise_for_status()
            await log_to_gui(f"[SYSTEM] {model_name} {action} complete.")
            return {"status": "success"}
        except Exception as e:
            await log_to_gui(f"[ERROR] {str(e)}")
            return {"error": str(e)}

@app.post("/api/pipeline/run-orchestrator")
async def run_orchestrator():
    """Triggers the multi-phase AI evaluation pipeline."""
    await log_to_gui("[SYSTEM] Starting Vision Orchestrator batch...")
    logger.info("Vision orchestrator requested")
    try:
        process = await asyncio.create_subprocess_exec(
            "python", "-u", "/app/scripts/vision_orchestrator.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        try:
            async with asyncio.timeout(INGEST_TIMEOUT_SECONDS):
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").strip()
                    if text:
                        await log_to_gui(text)
                return_code = await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"orchestrator timed out after {INGEST_TIMEOUT_SECONDS} seconds")
        if return_code != 0:
            raise RuntimeError(f"orchestrator exited with code {return_code}")
        logger.info("Vision orchestrator completed successfully")
        await log_to_gui("[SYSTEM] Orchestrator batch complete.")
        return {"status": "success"}
    except Exception as e:
        logger.exception("Vision orchestrator failed")
        await log_to_gui(f"[ERROR] Orchestrator failed: {str(e)}")
        return {"error": str(e)}

app.mount("/", StaticFiles(directory="static", html=True), name="static")