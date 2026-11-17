# NAS deployment status

Updated November 17, 2026. This replaces earlier bootstrap-only checkpoints.

## Verified deployment

BAMNAS at http://10.0.0.37:9999 has an Intel N100 CPU, 7.50 GB usable RAM and no GPU visible to Ollama. Volume 1 (Hot Storage) is ext4, 2.8 TB. Project root is `/volume1/docker/bam-local-review`. Volume 2 Btrfs and the existing Tailscale container were not changed.

Docker project `bam-local-review` has two running containers:

- `ollama`: version 0.40.16, 5g memory, 2 CPUs, one loaded model and one concurrent request, keep-alive zero. Host port is loopback-only `127.0.0.1:11435:11434`; models persist under `models`.
- `review`: successfully built from the extracted AIR worker source in `models/bam-worker`. Python 3.12, Pillow and ExifTool; 512 MiB, one CPU, read-only root, dropped capabilities, no-new-privileges, bounded temporary filesystem. It idles between manual tests. It is NOT an automatic ingest/queue consumer.

Worker mounts only synthetic `/input` (read-only) and project `/state` (read-write). No original collection mount, Docker socket, cloud secret or public NAS endpoint was added. The saved `compose.nas-8gb.yml` describes this observed test deployment; its relative build context assumes the NAS project layout, not an arbitrary checkout directory. Do not run the separate 16GB installer unchanged on this NAS.

## Model evidence

`qwen2.5vl:3b` downloaded but was stopped without a completed result. Generic `qwen3-vl:2b` produced reasoning and timed out after 180 seconds even with thinking disabled; it did not pass JSON validation.

Explicit `qwen3-vl:2b-instruct` downloaded successfully, digest `afc393e3572c07db0bc84aa460d928a16c75f3fbb9ca9e0dccc4fbd2dafb573e`, Q4_K_M. A bounded API request on a 96x96 control returned valid JSON identifying blue background, one red square, and count one in 188.32 seconds. Of that, image/prompt evaluation took 179.77 seconds, loading 4.07 seconds and generation 4.41 seconds. This establishes basic functionality, not production accuracy or throughput.

The independent three-control benchmark uses 2048 context, 128 output tokens, two threads, zero temperature and a 240-second request timeout. One-square and two-square controls passed exactly in 201.29 and 202.37 seconds. Inspect `/state/controls-thread2/report.json` for the small-yellow-circle result before claiming all controls pass. Full raw outputs remain beside the report. No cloud fallback was used.

A single existing 400x267, 10,886-byte DJI WebP preview was uploaded to `/state/76c9684c913c86ff06998faff748c3cf6b9e7dd7e33364d8c123b692520d5e16.webp`. Its local SHA256 is the filename stem. No original was uploaded, removed or changed. Real-photo local inference remains to be verified.

## CI and remaining integration

PR #8 Docker failure was caused by absent `PIPELINE_OPERATOR_TOKEN` during Compose interpolation. CI now generates a disposable random token, preserves the production required-token guard, validates all profiles and builds API, derivative and review images. Fix commit `22ba783e2cd17aa8640cfa70d4e08737894b1c31`; successful run 44152588710. Benchmark commit `c950f4200913b0356c846929d52fac38d2eb63dd` also passed all 62 tests and builds in run 44153599186.

Four independent local reviewers have NOT been qualified. Aliases or different prompts over the same weights do not constitute independent models. The complete two-round/arbitration pipeline, Neon/R2 result persistence, collection matching and publication round trip are unverified. Replit is implementing outbound worker leases and receipts; dispatch remains disabled pending a dedicated worker credential and a verified round trip. Existing mocked tests do not establish live connectivity.

Next: finish control scoring, run one bounded real-photo observation, qualify distinct small model weights sequentially, then integrate authenticated outbound jobs with long renewable leases and idempotent evidence receipts. Preserve model disagreements and human approval. Never bypass local rounds because inference is slow. OneDrive staging remains bounded to 100 files and 2 GiB; delete only pipeline-owned scratch after cloud verification, never synced originals.
