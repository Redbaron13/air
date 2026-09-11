#!/usr/bin/env bash
# Copy preprocessed preview/ + manifest to the Tailscale host.
set -euo pipefail
STAGING="${1:?usage: rsync_to_azure.sh <staging-dir> [tailscale-host] [remote-dir]}"
HOST="${2:-bam-azure}"
REMOTE="${3:-~/bam-storage/active}"

if [[ ! -d "$STAGING/preview" ]]; then
  echo "Run preprocess_for_ingest.py first. Missing $STAGING/preview" >&2
  exit 1
fi

echo "[TS] $HOST"
tailscale ping -c 1 "$HOST" >/dev/null

rsync -avP --partial "$STAGING/preview/" "$HOST:$REMOTE/preview/"
if [[ -f "$STAGING/manifest.jsonl" ]]; then
  rsync -avP "$STAGING/manifest.jsonl" "$HOST:$REMOTE/manifest.jsonl"
fi
echo "[DONE] ssh $HOST  then ingest /storage/active/preview"
