#!/bin/bash
# Create the local library folders used by the AIR pipeline.
# This script does not write .env, does not write .env.example, and does not write a password.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LIB="${AIR_LIBRARY:-$ROOT/library}"

mkdir -p \
  "$LIB/input" \
  "$LIB/staging" \
  "$LIB/duplicate" \
  "$LIB/resize" \
  "$LIB/Renamed" \
  "$LIB/Output" \
  "$LIB/Database" \
  "$LIB/originals" \
  "$LIB/exports"

EXAMPLE="$ROOT/.env.example"
if [[ ! -f "$EXAMPLE" ]]; then
  echo "Missing $EXAMPLE. Restore it from the repository before writing a local .env." >&2
  exit 1
fi

echo "Library folders are ready at $LIB"
echo "Example environment file: $EXAMPLE"
echo "Prep does not use the network. Start with: python3 -m pipeline.air prep init"
