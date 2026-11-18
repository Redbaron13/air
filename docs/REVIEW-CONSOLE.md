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
- Location clusters: single-linkage within `REVIEW_GROUP_RADIUS_M` (default 150 m).

## Configuration
`REVIEW_GROUP_RADIUS_M`, `REVIEW_STUCK_MINUTES` (default 60), `ORIGINALS_DIR`, `EXPORTS_DIR`. Media is only served from the staging and originals directories; browsers cannot preview DNG/HEIC/3D files, which show a notice instead.
