---
type: discovery
project_id: bam-media-platform
status: accepted
confidence: confirmed
updated: 2026-11-16
---
# Media review interface

Current remote AIR has `/review.html`, `static/js/review.js`, `server/review_api.py` and `server/review_logic.py`. This is more complete than the older local standalone review app. Extend this API and its domain model rather than creating a second database with competing approval truth.

The API exposes overview, health, events, asset list, asset detail, source media, live EXIF, groups, validation and decisions. It includes latest AI evaluation, copies, corrections, same-shoot relationships and nearby assets. The model grouping code exists, but the media route serves only browser image/video extensions, so a grouped model is not yet a working model preview.

| Area | Problem | Recommended implementation / acceptance |
|---|---|---|
| Main layout | Inputs, AI results, outputs and corrections are fragmented | Three panes: grouped asset list; large preview; inspector with Source / Telemetry / AI / Outputs / History tabs. Keep the current selection in the URL |
| Input lineage | Preview can be confused with an original | Show original filename/hash, parent identity, preview transform and source availability; immutable original badge |
| Output visibility | Copy rows do not prove files exist remotely | Show local/generated/uploaded/verified separately, dimensions, bytes, MIME, SHA and last verification; disable Open when missing |
| Grouping | Time/GPS proximity is only a suggestion | Separate exact duplicates, capture bursts, same shoot, model dependencies and tour scenes. Explain grouping evidence and allow reversible manual overrides |
| Metadata | Stored versus live values can disagree | Side-by-side comparison with source field, units, datum, timezone and unknowns; save revisions rather than overwriting evidence |
| Telemetry | EXIF alone misses DJI XMP/SRT and video timestamps | Parse ExifTool JSON plus sidecars; synchronize frames by capture time; distinguish AGL/MSL/relative altitude; never infer camera perspective solely from altitude |
| AI | JSON validity does not mean correctness | Show raw response and validated fields separately, model/config/prompt version, image/frame hash, latency and validation errors; never hide missing outputs behind demo data |
| Approval | No authenticated actor or concurrency guard | Reviewer login; expected revision; conflict message; atomic event log. Approval requires rights, valid public metadata and finished validation |
| Bulk actions | Wrong group can be approved accidentally | Preview exact count and identifiers; explicit selection; batch limits; per-item failure result; idempotency key |
| Video | Container file may not decode in browser | Browser-safe proxy, keyframe strip with timestamps, duration/codec/audio, transcript if scoped, parent-linked AI summaries |
| Models | Binary model and missing textures not renderable | GLB viewer, texture manifest, units/up-axis/bounds/CRS, camera presets and geometry validation; use deterministic preview renders for vision |
| Tours | No scene graph/hotspot model | Equirectangular/cubemap validation, scene inventory, hotspot target validation, initial view, keyboard navigation, mobile gyro opt-in |
| Reliability | API errors may look like empty library | Visible disconnected/degraded state, retry action and diagnostic correlation ID. Never call an empty result a successful ingest |
| Performance | Full scans and repeated grouping | Server-side filters and pagination; thumbnails; lazy details; memoized grouping keyed to revision; virtualized large lists |
| Accessibility | Dense tables and state colors | Keyboard selection, visible focus, text status labels, labeled JSON controls, live announcements, no forced animation |

## Replit contract

Build the review frontend as a client of the existing API. Preserve the endpoints and status semantics; do not fabricate a functioning pipeline. With no secured backend configured, display an explicit disconnected setup screen and accept local files for local preview only. Local previews do not imply saved records, AI evaluation or approval. Secrets belong server-side in Replit Secrets. Replit cannot reach a workstation's `localhost`; an authenticated reachable backend is required.

Never expose the current unauthenticated API through a public tunnel merely to make the demo connect. First implement authorization, scoped file serving and authenticated mutation endpoints. The R2 public domain serves only approved derivatives. Private originals must use controlled access, not public URLs.

Acceptance: inspect one real image, its original metadata, AI output, manual correction event and generated copies; reject/rework/approve with persisted state; reload and confirm; test two-reviewer conflict; show unavailable source/model/R2 as distinct blockers. Repeat for video frames, a model package and a tour package when adapters exist.
