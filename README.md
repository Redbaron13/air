# BAM Media Pipeline

Local media-ingestion and review pipeline for Baron Aerial Media. It stages source media, extracts EXIF telemetry, runs a three-model vision review, collects a human approval decision, and writes a deployable asset manifest.

## Architecture

| Component | Responsibility |
| --- | --- |
| `api-server` | FastAPI dashboard, WebSocket log stream, ingestion and batch controls |
| PostgreSQL | System of record for assets, AI evaluations, variants, and approvals |
| Redis | Broadcasts pipeline events to dashboard clients |
| `vision_orchestrator.py` | EXIF enrichment and Moondream, LLaVA, and Llama review workflow |
| `ingest_worker.py` | Validates staged files, hashes them, extracts basic EXIF, and creates `INGESTED` assets |
| `emit_manifest.py` | Writes approved, valid assets to `assets.manifest.json` |
| `derivative_builder.js` | Contains the Sharp conversion routine for responsive WebP and AVIF variants |

## Prerequisites

- Docker Desktop with Docker Compose
- An Ollama instance reachable from the API container, with `moondream`, `llava`, and `llama3.1` installed
- A local directory for staged source files and generated exports

## Configure

Create `bam-media-pipeline/.env` with environment-specific values. Do not commit it.

```dotenv
POSTGRES_DB=bam_media_factory
POSTGRES_USER=bam_admin
POSTGRES_PASSWORD=replace-with-a-strong-password
DATABASE_URL=postgresql://bam_admin:replace-with-a-strong-password@postgres:5432/bam_media_factory
REDIS_URL=redis://redis:6379/0
OLLAMA_URL=http://host.docker.internal:11434
PIPELINE_STORAGE_PATH=/absolute/path/to/bam-storage
ONEDRIVE_SOURCE_PATH=/absolute/path/to/source-media
ALLOWED_ORIGINS=http://localhost:3080,http://127.0.0.1:3080
```

The storage directory must contain `active`, `originals`, and `exports` subdirectories. Files submitted through the dashboard must be regular files inside `active`; symlinks and paths outside this root are rejected.

## Start Locally

```sh
cd bam-media-pipeline
docker compose --env-file .env up --build
```

Open `http://localhost:3080`. The database schema is created on the first empty PostgreSQL volume. To reset local data, stop the stack and remove its named volumes before starting again.

## Workflow

1. Place source files under `$PIPELINE_STORAGE_PATH/active`.
2. Select files in the dashboard and choose **Ingest Selected**. Each file is SHA-256 hashed and recorded as `INGESTED`.
3. Choose **Run Batch**. The orchestrator enriches EXIF telemetry, obtains screener and verifier output, then records the judge result and moves successful assets to `PENDING_HUMAN`.
4. Review the AI output and approve an asset. Approval moves it to `APPROVED`.
5. Generate a manifest manually from the API container:

```sh
docker compose --env-file .env exec api-server python /app/scripts/emit_manifest.py
```

The manifest is written to `$PIPELINE_STORAGE_PATH/exports/assets.manifest.json`.

## APIs

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/storage/staged` | List supported staged media files |
| `POST` | `/api/pipeline/ingest` | Ingest 1-100 selected staged paths |
| `POST` | `/api/pipeline/run-orchestrator` | Run AI evaluation for `INGESTED` assets |
| `GET` | `/api/assets/pending` | Fetch the human-review queue |
| `POST` | `/api/assets/{asset_id}/approve` | Approve a queued asset |
| `POST` | `/api/system/vram/{load|unload}/{model}` | Manage supported Ollama models |
| `WS` | `/ws/logs` | Receive pipeline log events |

## Logging and Troubleshooting

Python services emit UTC timestamped log lines to container stdout. The API republishes worker stdout to Redis, which appears in the dashboard log panel. Inspect logs with:

```sh
docker compose --env-file .env logs --follow api-server derivative-worker postgres redis
```

- `ERROR` messages include exceptions and failed subprocess exit codes.
- Ingestion and orchestration have five-minute process limits; Ollama requests have a two-minute limit.
- An Ollama or validation failure leaves the affected batch in `INGESTED` rather than automatically marking it ready for review.
- Confirm `DATABASE_URL`, `REDIS_URL`, and storage mount values before debugging application code.

## Current Limitations

`derivative_builder.js` provides the variant-generation function but no queue consumer or command-line dispatcher invokes it yet. Its container therefore cannot generate copies automatically. Add a defined job source and invoke `buildVariants` before relying on `asset_copies` or manifest variants.

There is not yet an automated test suite. Exercise ingestion with a known staged sample, simulate an unavailable Ollama endpoint, and verify the resulting manifest before production deployment.