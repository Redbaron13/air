# NAS deployment status — 2026-11-17

BAMNAS is signed in at http://10.0.0.37:9999. Volume 1 (Hot Storage) is ext4, 2.8 TB, initially 13.5 GB used. Docker share is `/volume1/docker`. Volume 2 is Btrfs and was not used.

Created and verified running Docker project `bam-local-review`, container `bam-local-review-ollama-1`. Models persist at `/volume1/docker/bam-local-review/models`. Ollama version 0.40.16, 5 GB memory limit, 2 CPUs, one parallel request and one loaded model, default context 4096, keep-alive zero. Host API mapping is loopback `127.0.0.1:11435:11434`. Existing Tailscale container left unchanged. No public API exposure, credentials or source collection mounts added.

Hardware correction: NAS has 7.50 GB usable RAM. Earlier 16+ GB four-model profile is not suitable as written. Do not run its installer unchanged. Use the saved `compose.nas-8gb.yml` bootstrap while benchmarking smaller models. Qwen2.5-VL 3B downloaded successfully and reports vision capability; first inference reached approximately 4.5 GB within a 5 GB container cap. This is already close to the limit. Full review worker is not deployed and model accuracy is not verified yet.

Synthetic control (96x96, one red square on blue) and test output/log files are on the NAS under `models/bam-tests`. Initial CLI inference did not return promptly; inspect JSON/log and server process before claiming success. No collection photo was uploaded to the NAS. Next: bounded API benchmark, select model/context sizes based on measured results, build worker, run known positive/negative fixtures and real photo, then validate full local disagreement gates. Existing local worker uses 8192 context and collection matcher 32768; neither is approved for this low-RAM NAS until tested. No cloud fallback should conceal model failure.

## Latest checkpoint

Both `qwen2.5vl:3b` and `qwen3-vl:2b` downloaded successfully. Qwen2.5VL was stopped without a response. Qwen3VL alias `bam-vision-test` (2048 context, 128 output tokens, temperature zero) produced truncated reasoning identifying the blue background and red square, but no valid final JSON. This is NOT a passed benchmark. Retried with `--think=false`, a 180-second timeout and JSON format; final result could not be observed because browser control repeatedly reported concurrent user changes and timed out. Inspect `qwen3-control-nothink.json` and `.log` in `/root/.ollama/bam-tests` before rerunning. The alias shares base weights and is NOT an independent reviewer.

Uploaded credential-free `nas-review-worker.tar.gz` to the models directory; file presence verified in NAS Files. Extraction to `/root/.ollama/bam-worker` was submitted but not verified. The worker container has NOT been built or started. Do not deploy its original 12 GB Compose profile to this NAS. No collection photos were uploaded to NAS; only the synthetic control.

Worker context is now configurable with `LOCAL_MODEL_CONTEXT`, including Compose propagation, constrained to 2048–32768. All 62 AIR unit tests pass locally; model behavior remains unverified. These are mock/boundary tests, not end-to-end NAS validation.

## Resume without reinstalling

1. Open existing Docker project `bam-local-review`, container Terminal `/bin/sh`. Inspect the bounded test result and logs; stop any leftover inference before new work. Do not print NAS authentication tokens or Ollama private keys.
2. Confirm archive extraction. Build a separate review-worker service from `models/bam-worker`, Dockerfile `deploy/vision/Dockerfile.local-review`. Allocate at most 512 MiB initially, use project-only inbox/state mounts, and keep cloud fallback disabled during tests. Do not mount Docker socket or source collection.
3. Benchmark strict JSON via Ollama API with `think:false`, measured duration and small context. Obtain valid known-control output before choosing the complete four-distinct-weight model ensemble. Small same-family models can share errors; agreement is not proof.
4. Run labeled subtle-difference controls and one resident real-photo preview. Preserve provenance, unknowns, independent reviewer context, complete disagreement records and human approval.
5. Only then connect collection matching, Neon/R2 and review UI. Validate authenticated ingest, persistence, approval and publication round trip; never delete synced OneDrive originals.
