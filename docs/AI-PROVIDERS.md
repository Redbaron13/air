# Media AI: free OpenRouter and private NAS Ollama

Updated 2026-11-17. OpenRouter is the primary hosted test path. Ollama is an optional local alternative. No automatic provider switching or paid fallback is enabled.

## OpenRouter

Selected explicit model: `nvidia/nemotron-3.5-super-vl:free`. Replit queried the live catalog and confirmed image input, JSON response-format support, and zero advertised pricing. This is a catalog check, not a quality benchmark. Model availability can change; each request revalidates it.

The owner created the key in OpenRouter. Saved as `BAM_DEV_OPENROUTER_API_KEY` in Cloudflare Secrets Store and `OPENROUTER_API_KEY` in Replit Secrets. Replit authenticated successfully using `GET /api/v1/key`. Never print the key or include it in frontend variables. Store bindings do not automatically inject secrets into other runtimes.

The local TextEdit save timed out; local `.env` still has no OpenRouter key. Cloudflare/Replit saves succeeded independently. To configure the local worker from your Terminal, use the hidden prompt:

```sh
cd /Users/kevinbaron/WorkDir/BAMWebsite/integration
python3 tools/configure_vision.py --save-openrouter-key --check openrouter
```

This switches the local backend only after saving the supplied key. Local shell DNS is still blocked in this execution environment. Do not mistake a local connection failure for a failed Replit credential.

Replit configuration UI contains OPENROUTER_MODEL, but its running development process did not receive this configuration entry during verification. The server's explicit default selects the same model. Key presence/authentication were verified after restart. Resolve Replit task/main configuration propagation before production deployment.

Safeguards: zero-price routing, no provider fallback, exact model identity check, JSON-only output, reject incomplete output, 10 MiB preview cap. Replit uses a persistent 10-attempt UTC-day budget, concurrency/pacing, bounded 429 cooldown, strict output schema and explicit external-send consent. Failed attempts count. Authenticated reviewers must approve rights/metadata; AI can only move INGESTED to PENDING_HUMAN. Replit tests reported 165 passing before live inference testing.

The local AIR adapter has a persistent SQLite daily budget (10 by default, configurable 1–50), minimum six-second pacing and catalog/JSON guards. Budget is stored in persistent active storage, outside temporary cloud-batch cleanup. Pacing exhaustion stops the call for later retry; no uncontrolled retry loop. Its legacy three-stage output schema remains less complete than Replit's strict review schema. Three calls per photo means a ten-call budget does not process ten photos through all stages. Do not treat repeated calls to the same model as independent expert agreement.

Free quota is account-dependent and shared across keys. Do not create extra keys to evade limits. See https://openrouter.ai/docs/api/reference/limits and the current model catalog https://openrouter.ai/api/v1/models .

## NAS Ollama option

Repository files: `bam-media-pipeline/deploy/vision/compose.ollama.yml`, `setup-ollama.sh`, `compose.air-ollama.yml`. A copy also lives in the local integration workbench under nas/. These have not been installed on the NAS; its Docker endpoint, available RAM/CPU/GPU and installed models are not verified.

The CPU-compatible baseline uses official `ollama/ollama`, a persistent model volume, one loaded model, one parallel request, a 6 GiB container cap and a loopback-only host listener. No source folder, Docker socket or cloud secrets are mounted. The image defaults to latest for initial setup; record/pin a tested image digest before unattended deployment.

`gemma3:4b` accepts images; its 1b variant does not. Model download is about 3.4 GB according to the vendor catalog; running memory needs exceed model-file size. Check actual NAS capacity before installation. CPU inference may be slow. No GPU support is assumed.

Copy bam-media-pipeline/deploy/vision to a NAS development directory, then run from its terminal:

```sh
sh setup-ollama.sh check
sh setup-ollama.sh install
```

Check only inventories Docker; install starts Ollama and downloads the vision model. Existing workloads are not modified. Stop with `sh setup-ollama.sh stop`; the model volume remains.

For AIR and Ollama in one Docker project, combine AIR's `docker-compose.nas.yml`, `compose.ollama.yml`, and `compose.air-ollama.yml` in that order. The overlay makes AIR use `http://ollama:11434` on their shared container network. A separate container cannot use its own localhost to reach NAS-host Ollama. Keep the API/operator token configured and private. For a local NAS-host runner use `http://127.0.0.1:11434`; a remote workstation needs an authenticated SSH/Tailscale route. Never expose unauthenticated port 11434 publicly. Hosted Replit cannot reach NAS localhost without a separately configured secure route.

Ollama itself needs no API credential on this private local path. OpenClaw is not required to run inference for this pipeline; adding another agent layer would add configuration without resolving media ingestion. Existing OpenClaw credentials/configuration were left unchanged.

Vendor references: https://docs.ollama.com/docker and https://ollama.com/library/gemma3 .

## Live synthetic inference evidence

Replit executed one actual OpenRouter image request through the production adapter: HTTP 200, exact model identity, stop finish reason, strict bam-vision-observation-1 JSON passed, correct red/blue/square recognition, reported cost $0, 775 tokens, 6,322 ms. Neon persisted one request-budget attempt and released the lease. This proves provider image inference/JSON, not collection ingest or public display. Evidence: integration/evidence/openrouter-synthetic-test.json.
