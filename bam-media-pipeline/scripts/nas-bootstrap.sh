#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo "Docker Engine required"; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 required"; exit 1; }
if [[ ! -f .env ]]; then
  cp env.example .env
  chmod 600 .env
  echo "Created .env template. Fill unique credentials and mount paths before rerunning."
  exit 2
fi
if grep -Eq '^(POSTGRES_PASSWORD|DATABASE_URL)=.*CHANGE_ME' .env; then
  echo "Replace all CHANGE_ME database credentials in .env" >&2
  exit 2
fi
set -a
# dotenv syntax should contain only literal KEY=VALUE pairs; do not source untrusted files.
storage=$(sed -n 's/^PIPELINE_STORAGE_PATH=//p' .env | tail -1)
onedrive=$(sed -n 's/^ONEDRIVE_SOURCE_PATH=//p' .env | tail -1)
[[ -n "$storage" && -n "$onedrive" && "$storage" = /* && "$onedrive" = /* ]] || { echo "Set absolute PIPELINE_STORAGE_PATH and ONEDRIVE_SOURCE_PATH"; exit 2; }
mkdir -p "$storage"/{active,originals,exports} "$onedrive"
docker compose --env-file .env -f docker-compose.nas.yml config --quiet
docker compose --env-file .env -f docker-compose.nas.yml up -d --build
echo "AIR API bound to VM loopback :3080. Access only through a trusted SSH tunnel."
