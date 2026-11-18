# Review Console

Local GUI at `http://<host>:3080/review.html` (linked from Mission Control) for reviewing media and verifying the pipeline works.

| Tab | What it shows |
|-----|---------------|
| Pipeline | Asset counts per stage (ingested → telemetry → AI screener/verifier/judge → human → derivatives), state/type/local-vs-cloud breakdown, agreement-score histogram, system health (Postgres, Redis, Ollama + loaded models, cloud credentials, storage dirs, manifest), live event stream with history |
| Review | Filterable asset list; preview, stored EXIF plus live `exiftool` cross-check, GPS, per-asset stage timeline, the three AI passes side by side (plus cloud deep pass), validation findings, related media (same shoot, nearby GPS, other parts of the same model/map). Approve (with edits), Reject or Rework (re-queue for AI). Keys: J/K, A, R, W. Decisions with edits/notes are stored in `human_corrections`. |
| Groups | Model/map parts by shared identifier, shoots, GPS location clusters, and a plot of geotagged assets |
| Validation | Every finding across all assets, click to jump to the asset |

## Validation checks
`no_ai_eval`, `eval_incomplete`, `score_range`, `score_mismatch` (stored score vs judge's own), `low_agreement`, `eval_not_advanced`, `stuck_ingested`, `no_gps`, `no_sensor`, `no_shoot`, `outside_shoot`, `short_alt`, `no_describes`, `no_derivatives`, `file_missing`. Logic lives in `server/review_logic.py`.

## Grouping
- Model/map parts share an identifier: an AI/human supplied `group_id` (in the judge/deep output) if present, otherwise the filename with part/tile/chunk/LOD/`1of4`/`(2)` suffixes removed (`Bridge_part1.glb`, `Bridge_part2.glb` → `bridge`). Groups with only one part are flagged.
- Location clusters: single-linkage within the grouping radius (default 150 m, from `REVIEW_GROUP_RADIUS_M`).

## Configuration
`REVIEW_GROUP_RADIUS_M`, `REVIEW_STUCK_MINUTES` (default 60), `ORIGINALS_DIR`, `EXPORTS_DIR`. Media is only served from the staging and originals directories; browsers cannot preview DNG/HEIC/3D files, which show a notice instead.

## Settings tab

Grouping radius, shoot-boundary override (flags assets farther than this from their shoot centroid; blank uses each shoot's own radius), stuck-in-ingest minutes, low-agreement threshold and minimum alt-text length are editable in the Settings tab. Values are saved to `review_settings.json` in `EXPORTS_DIR`; the environment variables above only provide the initial defaults.

## Search, grouping, training and sync API

All endpoints live under `/api/review`. Searching and grouping use only stored database data; models are never re-run.

- `GET /settings` – current settings, defaults and the `specs` that drive the settings form.
- `PUT /settings` – validate (422 on bad values) and save settings; `DELETE /settings` resets them.
- `POST /assets/{id}/decision` – approve/reject/rework; accepts `final_output` (human-edited record, identifier fields stay locked), `notes`, `feedback`, `training_candidate` and `reprocess_feedback`; edits and flags create training examples.
- `GET /assets/{id}` – asset detail incl. final output, identifier, indicators, notes, feedback, frame count, model-candidate and progression snippets.
- `GET /assets/{id}/frames` – extracted video frames with thumb/image URLs and per-frame output.
- `GET /assets/{id}/frames/{frame_index}/thumb` and `/image` – frame files, served only from inside `EXPORTS_DIR/frames`.
- `GET /assets/{id}/media` – source media with HTTP range support for video playback.
- `GET /intel/search` – ILIKE search over final output, description, alt, notes, identifier and filename (`q`, `kind`, `shoot`, `state`, `shot_type`, `genre`, `limit`).
- `GET /intel/model-candidates` – photo sets suitable for 3D models (`shoot_id`, `asset_id`, `lat`, `lon`, `radius_m`).
- `GET /intel/progression` – locations visited repeatedly, with before/after indicators (same filters).
- `GET /intel/view-groups` – groups of captures showing the same view.
- `POST /intel/refresh` – recompute stored indicators for all assets.
- `POST /intel/shoots/split` – split a shoot into two groups of assets (`shoot_id`, `group_a`, `group_b`, `project_name_b`).
- `POST /intel/shoots/reassign` – re-run shoot assignment for a shoot (`shoot_id`, `radius_m`).
- `POST /intel/batch/finalize` – finalize the given (default: all approved) assets and refresh indicators.
- `GET /intel/shoots` – shoots with asset counts, first/last capture and related shoots.
- `GET /intel/training/stats` – training example counts by source, flagged assets and total.
- `GET /intel/training/export` – NDJSON training examples (`only=all|flagged|edited`, `limit`).
- `GET /intel/sync/status` – pending/failed outbox state for the cloud database sync.
- `POST /intel/sync/run` – run a sync now; returns `remote: "unreachable"` instead of failing when offline.
