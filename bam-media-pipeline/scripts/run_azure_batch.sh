#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Copy env.azure.example to .env and fill Azure keys first." >&2
  exit 1
fi

LIMIT="${BAM_VISION_LIMIT:-180}"
echo "[BATCH] Azure vision slice limit=${LIMIT}"

docker compose -f docker-compose.yml -f docker-compose.azure.yml build api-server vision-worker
docker compose -f docker-compose.yml -f docker-compose.azure.yml up -d postgres redis api-server

# Wait for Postgres
for i in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-bam_admin}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

BAM_VISION_LIMIT="${LIMIT}" docker compose -f docker-compose.yml -f docker-compose.azure.yml run --rm --no-deps vision-worker

echo "[BATCH] slice finished. Remaining INGESTED rows stay queued for the next day."
docker compose -f docker-compose.yml -f docker-compose.azure.yml exec -T postgres \
  psql -U "${POSTGRES_USER:-bam_admin}" -d "${POSTGRES_DB:-bam_media_factory}" -c \
  "SELECT workflow_state, count(*) FROM assets GROUP BY 1 ORDER BY 1;"
