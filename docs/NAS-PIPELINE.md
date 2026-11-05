# NAS Media Pipeline development
Use Redbaron13/air as a separate local Docker Compose project from the website.

1. On the Debian Docker VM, clone into /srv/bam/air.
2. `cd /srv/bam/air/bam-media-pipeline`; `cp env.example .env`; replace all CHANGE_ME values, set readable mount paths, and chmod 600 .env.
3. Create `PIPELINE_STORAGE_PATH/{active,originals,exports}` and `ONEDRIVE_SOURCE_PATH` on the host. Keep originals read-only, and back up exports and PostgreSQL.
4. Run `docker compose -f docker-compose.nas.yml config --quiet` then `docker compose -f docker-compose.nas.yml up -d --build`.
5. Preview with `ssh -L 3080:127.0.0.1:3080 user@nas-vm` and http://localhost:3080.
6. Enable Azure/OpenAI/Anthropic only for reviewed jobs using separate environment-scoped credentials, budgets and concurrency limits.
7. Never publish AI-produced assets without human approval and license/privacy checks.

## Architecture review findings
- Existing docker-compose.yml exposes postgres, Redis and API ports broadly; use docker-compose.nas.yml for local testing instead.
- server/app.py has permissive CORS and an unauthenticated ingestion endpoint. **Loopback-only access is not authentication.** Restrict access via tunnel and add authentication/authorization before LAN or internet exposure.
- Ingestion worker previously contained a hard-coded PostgreSQL connection string fallback. This branch removes it; rotate that credential if used anywhere.
- Existing setup.sh touches configuration and source paths; do not run it over an initialized checkout.
- The pipeline lacks comprehensive tests. CI initially checks syntax, Compose resolution, and Docker build only. Add database-backed integration tests and safe fixture media next.
- Keep source originals, rendered derivatives, reviewed manifests, and published exports distinct. Add content hashes and provenance to future artifact manifests.
- Share approved export manifests with the website through a versioned JSON contract, never by giving the public site raw archive access.
