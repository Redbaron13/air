# Collection matching after independent review

User requirement: independently examine every photo first, including subtle visible details, then compare completed reviews across the collection to identify likely locations, timelines, capture sessions, flight-plan membership and photogrammetry inputs. Never let neighboring images contaminate the first review.

## Implemented foundation

`scripts/collection_match.py` imports completed `bam-local-review-1` records into a local SQLite registry. The full record is retained, keyed by original SHA256 with a digest of the review JSON. Duplicate imports are safe; a changed review is rejected until an explicit version migration is implemented. Inputs without completed independent review are rejected. A separate local model receives both complete review records and measured pair evidence. It returns proposed location alignment and visible/subtle differences. It cannot merge records, assert a flight association or modify the independent reviews.

Automatic candidate retrieval uses recorded camera GPS, a 150 m radius, at most 500 latitude candidates and 20 comparisons. This is a bounded prototype, not complete search of a large image database. Dense locations may omit valid candidates. Photos without GPS are not automatically matched. It supports a local registry of imported reviews, not live Neon synchronization yet. The text model has 32K context; oversized complete pairs fail instead of silently dropping evidence. Runtime memory must be benchmarked on the NAS before enabling this stage.

Camera GPS measures where the drone/camera was, not necessarily the building or ground patch depicted. Same coordinates can face opposite directions; nearby coordinates can view the same distant object. Capture timestamps without explicit UTC offset cannot establish a reliable cross-device timeline. Missing offsets remain unknown. Same-day GPS proximity is not sufficient evidence that photos belong to one 3D model.

Example after independent runs finish:

```sh
docker compose --env-file local-review.env -f compose.local-review.yml run --rm \
  --entrypoint python review /app/collection_match.py \
  --database /state/collection.sqlite3 \
  --import-result /state/runs/photo-001/result.json \
  --import-result /state/runs/photo-002/result.json

# Replace SOURCE_SHA256 with the original hash from a completed result.
docker compose --env-file local-review.env -f compose.local-review.yml run --rm \
  --entrypoint python review /app/collection_match.py \
  --database /state/collection.sqlite3 --target SOURCE_SHA256 \
  --output /state/relationship-proposals.json
```

## Required production integration

1. Version independent results in Neon by source hash, run ID, schema/prompt versions and model digests. Keep private metadata separate from website output. Import only successful final records with verified object hashes. Record errors separately.
2. Add indexed spatial/time lookup (PostGIS if available) and visual embeddings for candidate retrieval. Embeddings are search signals, not location proof. Query bounded candidates and load their complete prior reviews. Never feed the entire database into a model prompt.
3. Compare paired previews for geometric features and subtle changes after candidate retrieval; retain the full prior JSON as context. The current matching prototype compares JSON only, so it cannot find details omitted by the first reviewers. Add multi-image model input, feature-match/inlier evidence and viewpoint compatibility before calling this a visual alignment system.
4. Store relationship edges with type, both source IDs, evidence, uncertainty, model/prompt version, reviewer decision and timestamps. Distinguish same-location candidate, same-session candidate, temporal predecessor, overlapping view, explicit flight membership and explicit reconstruction membership. Do not collapse all into one group ID.
5. Import flight plans and actual flight logs with original hashes, timezone/UTC mapping, aircraft identity, camera triggers and coordinate reference system. Match photo timestamps to recorded triggers/track segments within stated tolerances. Planned paths are not proof of the route actually flown. Record unmatched photos and ambiguous candidates.
6. For 3D inputs, require a mission/reconstruction manifest or independently checked overlap, camera calibration and capture pattern. Preserve original ordering and image hashes. A model may suggest a set but cannot certify reconstruction coverage from prose.
7. Build map/timeline views from recorded GPS/time, not model-invented coordinates. Distinguish camera-track approximation from verified flight track. Before/after pairs need confirmed same subject and comparable viewpoint; season, lighting and perspective can mimic changes.
8. Human review confirms proposed groups/relationships; no automatic publication or source deletion. Replit needs an inspector with side-by-side images, full per-model JSON, telemetry, disagreement history, proposed relationships and evidence controls. These UI and Neon steps remain pending.

## Benchmark before rollout

The image worker instructs both visual reviewers to note counts, colors, positions and small visible details. `scripts/vision_benchmark.py` generates labeled controls: one red square on blue, two red squares on blue, and the original square plus a small yellow circle. Every fixture is processed independently through the complete local review path, with cloud disabled. The benchmark reports each reviewer's lexical misses/unexpected terms for human scoring. It never claims lexical matching proves scene understanding.

```sh
# Generate fixtures in writable state, then copy ONLY fixtures into inbox using NAS Files.
docker compose --env-file local-review.env -f compose.local-review.yml run --rm \
  --entrypoint python review /app/vision_benchmark.py --create /state/benchmark-fixtures
# After fixtures are in /input/benchmark, repeat for each fixture id:
docker compose --env-file local-review.env -f compose.local-review.yml run --rm review \
  /input/benchmark/one-square.png --output /state/runs/one-square
# Then score completed runs; unrun fixtures are explicitly NOT_RUN.
docker compose --env-file local-review.env -f compose.local-review.yml run --rm \
  --entrypoint python review /app/vision_benchmark.py \
  --manifest /state/benchmark-fixtures/manifest.json --runs /state/runs \
  --report /state/benchmark-report.json
```

Next add a human-labeled sample of real collection photos: broad scene/object recognition, subtle additions/removals, adjacent viewpoints, same location on different dates, different locations with similar roofs/roads, oblique versus nadir, low-light/blur and absent/bad GPS. Include known flight-plan/3D-manifest pairs and negative pairs. Label only visible facts, not structural diagnoses or inferred location. Keep evaluation photos separate from prompt-tuning examples. Record per-model errors, false agreement, dispute resolution accuracy, false-positive relationship rate, local/cloud request counts, latency and peak RAM. No real local-model benchmark has run yet; NAS sign-in is pending.

Six new unit tests cover review-before-matching, missing timezones, GPS not establishing mission membership, version conflicts, candidate limits/filtering and non-promoting model proposals. They use model stubs and do not establish model accuracy.
