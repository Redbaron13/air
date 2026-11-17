#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
: "${BAM_EXT4_ROOT:?Export the absolute existing NAS ext4 directory as BAM_EXT4_ROOT}"
case "$BAM_EXT4_ROOT" in /*) ;; *) echo 'Use an absolute NAS path'; exit 1;; esac
test -d "$BAM_EXT4_ROOT" || { echo 'Create the chosen directory on the ext4 volume first'; exit 1; }
test "$(findmnt -n -o FSTYPE -T "$BAM_EXT4_ROOT")" = ext4 || { echo 'Refusing: selected directory is not ext4'; exit 1; }
export BAM_UID="$(id -u)" BAM_GID="$(id -g)"
dc() { docker compose --env-file local-review.env -f compose.local-review.yml "$@"; }
test -f local-review.env || cp local-review.env.example local-review.env
chmod 600 local-review.env
dc config --quiet
case "${1:-check}" in
 check)
  docker info --format 'Architecture={{.Architecture}} CPUs={{.NCPU}} RAM={{.MemTotal}} DockerRoot={{.DockerRootDir}}'
  findmnt -T "$BAM_EXT4_ROOT"
  df -h "$BAM_EXT4_ROOT"
  dc ps
  ;;
 install)
  ram="$(docker info --format '{{.MemTotal}}')"
  test "$ram" -ge 16000000000 || { echo 'This four-model profile needs at least 16 GB NAS RAM; 24+ GB preferred. Tune only after a hardware test.'; exit 1; }
  free_kb="$(df -Pk "$BAM_EXT4_ROOT" | awk 'NR==2 {print $4}')"
  test "$free_kb" -ge 31457280 || { echo 'Keep at least 30 GiB free for models and review state'; exit 1; }
  umask 077
  mkdir -p "$BAM_EXT4_ROOT/models" "$BAM_EXT4_ROOT/inbox" "$BAM_EXT4_ROOT/state/secrets" "$BAM_EXT4_ROOT/state/runs"
  dc up -d ollama
  dc build review
  # Sequential downloads and one model resident at a time limit RAM/disk pressure.
  for variable in LOCAL_INFORMED_MODEL LOCAL_BLIND_MODEL LOCAL_MERGER_MODEL LOCAL_ARBITER_MODEL; do
   model="$(dc run --rm --no-deps --entrypoint printenv review "$variable")"
   dc exec -T ollama ollama pull "$model"
  done
  ;;
 stop) dc stop ;;
 *) echo 'Usage: sh setup-local-review.sh check|install|stop'; exit 2 ;;
esac
