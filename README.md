# BAM Media Pipeline

This pipeline ingests aerial media, enriches it with EXIF and local AI analysis, presents it for human approval, creates responsive derivatives, and exports an approved-asset manifest.

## Prerequisites

- Docker Desktop and Docker Compose
- Ollama running on the Mac host, with `moondream`, `llava`, and `llama3.1` available
- Python 3.11+ for direct worker commands
- Source media staged under the configured storage directory

## First-Time Setup

1. Create the local Python environment:

	```sh
	python3 -m venv .venv
	.venv/bin/python -m pip install -r server/requirements.txt
	```

2. Create `.env` with the required values. It is gitignored. Quote database URLs because they contain `&`:

	```dotenv
	POSTGRES_DB=bam_media_factory
	POSTGRES_USER=bam_admin
	POSTGRES_PASSWORD=replace-me
	DATABASE_URL='postgresql://bam_admin:replace-me@postgres:5432/bam_media_factory'
	NEON_DATABASE_URL='postgresql://user:password@host/database?sslmode=require&channel_binding=require'
	REDIS_URL=redis://redis:6379
	OLLAMA_URL=http://host.docker.internal:11434
	ONEDRIVE_SOURCE_PATH=/absolute/path/to/source-media
	PIPELINE_STORAGE_PATH=/absolute/path/to/air/storage
	PIPELINE_VERBOSE=0
	```

	For a Keychain-backed `.env` workflow, use the **Secret Management** section instead of manually entering credentials.

3. Pull the local AI models:

	```sh
	ollama pull moondream
	ollama pull llava
	ollama pull llama3.1
	```

## Run The Pipeline

1. Start PostgreSQL, Redis, the API server, and derivative worker:

	```sh
	docker compose up --build
	```

2. Open `http://localhost:3080`. Stage media in `${PIPELINE_STORAGE_PATH}/active`; it will appear in the web interface.

3. In the interface, ingest selected files, then run the Vision Orchestrator batch:

	- Ingest hashes media, extracts basic EXIF, and stores `INGESTED` assets.
	- Vision extracts telemetry, runs Moondream, LLaVA, and Llama 3.1, then moves assets to `PENDING_HUMAN`.
	- Review approves assets and moves them to `APPROVED`.
	- Derivatives create responsive copies after their queue integration is configured.

4. Compile a manifest from inside the API container:

	```sh
	docker compose exec api-server python /app/scripts/emit_manifest.py
	```

	The output is `${PIPELINE_STORAGE_PATH}/exports/assets.manifest.json`.

## Direct Worker Commands

For local scripts, load `.env` first. Local PostgreSQL URLs must use `localhost`, not Docker's `postgres` hostname.

```sh
set -a
source .env
set +a
.venv/bin/python scripts/spark_worker.py
```

`spark_worker.py` uses `NEON_DATABASE_URL`; the API pipeline uses `DATABASE_URL`.

## Verbose Logging

Set `PIPELINE_VERBOSE=1` before starting the pipeline to include database connections, file hashes, EXIF failures, model calls, derivative dimensions, and manifest copy counts.

```sh
PIPELINE_VERBOSE=1 docker compose up --build
```

Normal progress, warnings, and errors remain visible without verbose logging. API-triggered worker output is also published to the UI log stream through Redis.

## Secret Management

`secrets-manager/keychain_env.py` keeps credentials in the macOS Keychain and renders `.env` from `.env.example`. Templates can be committed; generated `.env` files remain ignored and are written with owner-only permissions.

Store each secret through a hidden prompt. Do not put a credential after `set` because shell history would retain it:

```sh
python3 secrets-manager/keychain_env.py set NEON_DATABASE_URL
python3 secrets-manager/keychain_env.py set DATABASE_URL
python3 secrets-manager/keychain_env.py set POSTGRES_PASSWORD
```

Repeat for every `{{keychain:NAME}}` reference in `.env.example`. Render and validate the project file:

```sh
python3 secrets-manager/keychain_env.py check .env.example
python3 secrets-manager/keychain_env.py render .env.example .env --force
```

Use another vault namespace to keep environments separate, for example `--vault production`. The same vault and key name can be referenced from templates in other local projects:

```sh
python3 /absolute/path/to/air/secrets-manager/keychain_env.py --vault production set R2_SECRET_ACCESS_KEY
python3 /absolute/path/to/air/secrets-manager/keychain_env.py --vault production render .env.example .env --force
```

For shared team secrets, CI/CD, access controls, or audit logs, use a managed secret provider such as 1Password Secrets Automation, Doppler, or a cloud secret manager instead of sharing a macOS Keychain.

## Troubleshooting

- `ModuleNotFoundError: psycopg2`: use `.venv/bin/python` and install `server/requirements.txt`.
- Missing `NEON_DATABASE_URL`: load `.env` before running `spark_worker.py`.
- Ollama request failures: confirm Ollama is running and the three models are installed.
- No assets to process: ingest staged media before starting the Vision Orchestrator.
- Local database connection errors: use a `localhost` URL for local scripts; containers use the `postgres` hostname.