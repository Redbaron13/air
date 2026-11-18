#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
command -v docker >/dev/null || { echo 'Enable Docker on the NAS first.'; exit 1; }
docker compose -f compose.ollama.yml config --quiet
case "${1:-check}" in
 check) docker info --format '{{.Architecture}} CPUs={{.NCPU}} RAM-bytes={{.MemTotal}}'; docker compose -f compose.ollama.yml ps ;;
 install)
  docker compose -f compose.ollama.yml up -d
  docker compose -f compose.ollama.yml exec -T ollama ollama pull gemma3:4b
  docker compose -f compose.ollama.yml exec -T ollama ollama show gemma3:4b
  ;;
 stop) docker compose -f compose.ollama.yml stop ;;
 *) echo 'Usage: sh setup-ollama.sh check|install|stop'; exit 2 ;;
esac
