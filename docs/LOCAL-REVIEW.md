# NAS local-first review

Status, 2026-11-17: implemented worker and deployment recipe; not yet installed or live-model tested. NAS web UI at http://10.0.0.37:9999 is reachable and awaiting user sign-in. Existing Replit/direct-provider routes still use their prior implementation; this standalone worker does not silently replace them. Do not run direct cloud review for this new workflow.

## Processing contract

1. Stage 1 reads EXIF/XMP using ExifTool and makes an oriented, metadata-stripped preview. JSON records original SHA256, dimensions, camera, capture time/offset, GPS, separate GPS/absolute/relative altitudes, focal lengths, orientation and separate gimbal/aircraft pitch/yaw/roll. Each telemetry value has its source tag, units and missing/invalid/recorded status. No model invents telemetry. Gimbal pitch is not a calibrated lens-axis measurement; relative altitude is not clearance above terrain. Metadata may be inaccurate or spoofed.
2. Stage 2 reviewer A (`gemma3:4b`) gets the image and stage-1 JSON. Reviewer B (`qwen2.5vl:3b`) gets only the stripped image and task prompt. All calls are stateless and process one image independently. Neither sees other collection images or filenames. Both return five standardized fields: scene, subjects, activities, visible_condition, image_quality.
3. A third model (`qwen3:4b`) compares the two outputs field by field for semantic agreement. Accepted values are drawn from the actual reviewers; the merger cannot invent a new value. Both original outputs and comparison reasons remain in the audit record.
4. Any disagreement triggers a second, fresh local A/B/merger round with a more conservative examination prompt. B remains blind. This is a second examination, not independent statistical evidence: repeated models can repeat the same errors.
5. Substantial disagreement means two or more disputed fields, or a dispute about scene or visible_condition. After the second round, a fourth image model (`llama3.2-vision:11b`) receives the image plus both second-round outputs. It selects A, B or unknown for each field. This deliberately preserves ambiguity rather than fabricating a composite answer.
6. Only substantial uncertainty still unresolved by that local arbitrator is eligible for one explicitly free OpenRouter call. It requires `--allow-free-cloud`. The existing provider adapter checks live free pricing, modality, model identity, JSON, daily budget (10 attempts), pacing and no paid fallback. Cloud receives the stripped preview and visual outputs, not the original EXIF or GPS fields. Model failure, invalid JSON, timeout, missing model or insufficient RAM never triggers cloud fallback. They stop the job and preserve evidence.
7. Results always require human review. Agreement is not truth; no route grants rights, approves publication, certifies an inspection or deletes an original.
8. Collection matching happens only after this independent review finishes. It must preserve per-image results and record proposed relationships separately. Location, timeline, capture-session, flight membership and photogrammetry membership are different relationships. Similar appearances alone do not establish identity or 3D-set membership.

## Deployment on ext4

Use the AIR checkout's `bam-media-pipeline/deploy/vision` directory. Do not move the entire Docker data root without a separate migration plan. Model weights, staged inputs, outputs, budgets and secrets are bind-mounted to the selected ext4 directory. Container layers/logs remain wherever the NAS Docker engine is configured; `check` prints that location.

```sh
cd bam-media-pipeline/deploy/vision
# Replace this with the verified NAS ext4 directory, not the Mac path.
export BAM_EXT4_ROOT=/actual/ext4/path/bam-local-review
mkdir -p "$BAM_EXT4_ROOT"
cp local-review.env.example local-review.env
chmod 600 local-review.env
# Edit settings; exported BAM_EXT4_ROOT takes precedence over the template.
sh setup-local-review.sh check
sh setup-local-review.sh install
```

The installer checks ext4, at least 16 GB RAM and 30 GiB free. This is a conservative starting gate, not a performance guarantee. Prefer 24+ GB system RAM for the 11B arbitrator. CPU-only runs may take minutes per call. One model is loaded at a time and unloaded after each response. No GPU privileges are assumed. Pin image/model versions after a successful hardware benchmark. Model weights total approximately 17 GB before Docker layers, runtime memory and cache; verify actual downloads. Ollama binds only NAS loopback port 11435, avoiding the earlier recipe's port 11434. Internal workers use the Docker service name. No public tunnel or OpenClaw installation is required.

For one staged test JPEG, run as the same NAS user used during setup:

```sh
export BAM_UID="$(id -u)" BAM_GID="$(id -g)"
docker compose --env-file local-review.env -f compose.local-review.yml run --rm review \
  /input/test.jpg --output /state/runs/test-001
```

Use a new run directory; existing outputs are never overwritten. Worker requires 10 GiB remaining free. Its input mount is read-only. Start with one photo; upstream bounded ingestion still limits batches to 100 photos/2 GiB. This worker does not download or delete synced OneDrive files. Preserve checkpoints on failure. Cleanup requires separately verified cloud persistence, not successful inference alone.

No credentials are required locally. For optional cloud arbitration, securely place the already-created OpenRouter key at `$BAM_EXT4_ROOT/state/secrets/openrouter.key`, mode 0600 and owned by the worker UID, then append `--allow-free-cloud`. Do not paste keys into commands, Git, notes or chat. Existing Cloudflare/Replit credential copies are unchanged; no NAS key transfer has yet been verified.

## Output and integration

`stage-1.json`, model manifest with digests, each round's informed/blind/comparison JSON, progress, arbitration if needed, and `result.json` are stored alongside the stripped preview. `failure.json` marks an interrupted/failed job. Only `result.json` after a successful exit is final. `review-output.json` is an intermediate checkpoint. The final record includes immutable source/preview hashes and `schema_version: bam-local-review-1`. Model schemas are under `deploy/vision/schemas`. Unknown values remain null in final fields; disagreement history is retained even if later resolved. Four distinct locally installed model digests are required; cloud-backed Ollama models are rejected.

The worker is a standalone implementation. Neon import, Replit rendering of this new schema, queue claims/retries, storage acknowledgement and collection matching promotion still need integration tests. Do not mark those complete based on unit tests. Existing legacy orchestrator/direct cloud routes must be disabled or replaced when switching production routing. Current R2 and Neon adapters are not invoked by this CLI.

## Verification

61 AIR unit tests pass, including 12 new review tests and 6 matching tests for blind reviewer isolation, repeated local rounds, arbitration/cloud gating, missing metadata, altitude separation, strict output fields and permanent human-review requirements. Compose validates and installer shell syntax passes. No live local-model accuracy, latency or RAM benchmark has run. Local Python lacks Pillow and ExifTool; Docker build installs both, but container runtime remains untested until NAS sign-in/deployment.

Before enabling batch processing: run known-color/shape/count fixture pairs and real photos with human-authored labels; test subtle additions/removals, crop/viewpoint changes and different scenes with similar appearance. Record expected/actual findings for each reviewer separately, metadata preservation, latency, peak RAM, false agreements and unresolved rates. Do not use model-generated descriptions as ground truth. Test a missing model, truncated output, timeout, disk limit, interrupted run and disabled cloud credentials. Confirm no online request occurs for ordinary local agreement.

Official references checked: https://docs.ollama.com/docker ; https://docs.ollama.com/capabilities/structured-outputs ; https://ollama.com/library/gemma3 ; https://ollama.com/library/qwen2.5vl ; https://ollama.com/library/qwen3:4b ; https://ollama.com/library/llama3.2-vision . Selected models are a testable baseline, not a measured best-performing ensemble.
