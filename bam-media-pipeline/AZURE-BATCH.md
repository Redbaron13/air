# Azure 3-day vision batch

Local Ollama is off. Containers stay on your machine so originals never leave the NAS.
Azure is used only for the vision API.

## One-time setup

1. In Azure Portal create an Azure OpenAI (or Foundry) resource.
2. Deploy a vision model named `gpt-4o` (or change `AZURE_OPENAI_DEPLOYMENT`).
3. Copy keys into `.env`:

```bash
cd bam-media-pipeline
cp env.azure.example .env
```

4. Set `ONEDRIVE_SOURCE_PATH` and `PIPELINE_STORAGE_PATH` to real folders.
5. Rebuild:

```bash
docker compose -f docker-compose.yml -f docker-compose.azure.yml up -d --build postgres redis api-server
```

6. Ingest the 400-500 photos (UI at http://localhost:3080 or):

```bash
docker compose exec api-server python -u /app/scripts/ingest_worker.py /storage/active
```

`ingest_worker.py` now walks folders.

## Daily run (repeat for 3 days)

Default slice is 180 assets so a 500-photo + 50-video set finishes in three passes.

```bash
chmod +x scripts/run_azure_batch.sh
./scripts/run_azure_batch.sh
```

Or:

```bash
BAM_VISION_LIMIT=180 docker compose -f docker-compose.yml -f docker-compose.azure.yml run --rm vision-worker
```

After each slice, review `PENDING_HUMAN` in the GUI and approve site keepers.

Day 1: photos 1-180  
Day 2: photos 181-360  
Day 3: remainder + videos  

The worker only selects `workflow_state = 'INGESTED'`, so reruns are safe.

## Azure DevOps pipeline

`azure-pipelines.yml` at the repo root is meant for a **self-hosted** agent that can see your photo volumes. Hosted Microsoft agents cannot mount your NAS.

Install a self-hosted agent on the same machine as Docker, then queue the pipeline each day (or keep the 08:00 schedule).

## Stop spend when done

```bash
docker compose -f docker-compose.yml -f docker-compose.azure.yml stop api-server vision-worker
```

Leave Postgres up if you still need the review UI. Delete the Azure OpenAI resource if the trial is ending and you have exported a `pg_dump`.
