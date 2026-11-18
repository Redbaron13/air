# Media output schema (v1.1.0)

Small local vision models (moondream screener, llava verifier, llama3.1 judge) stay focused by being shown a strict,
realistic JSON example per media kind. The pipeline then assembles ONE standardized record per media file.
Code: `bam-media-pipeline/server/output_schema.py`; examples + field spec: `bam-media-pipeline/server/schemas/`.

## Standard record

| Block | Fields | Filled by |
|---|---|---|
| `schema_version`, `media_kind` | version, `still`/`video`/`video_frame`/`model3d` | pipeline |
| `identifier`, `identifier_components` | null until finalized | pipeline |
| `capture` | captured_at, latitude, longitude, altitude_m, gps_valid, sensor_model, focal_length_mm, gimbal_pitch_deg, orientation, heading_deg | exif (never the model) |
| `classification` | media_genre, shot_type, scene_type | model |
| | camera_perspective, shot_type_from_exif, exif_basis | exif |
| | shot_type_matches_exif | pipeline |
| | drone_angle, facing_compass, facing_relative, facing_side, facing_reference, viewed_from, altitude_band, mapping_role | exif + flight (see taxonomy) |
| `capture` (added in 1.1.0) | captured_at_local, utc_offset, gimbal_yaw_deg, flight_yaw_deg, flight_roll_deg, rel_altitude_m, camera_make, lens_model, iso, shutter_speed, aperture, camera_serial | exif |
| `address` (1.1.0) | display_name, road, city, county, state, postcode, country (all null when unknown) | geocoder, at finalize |
| `flight` (1.1.0) | flight_index, seq, total, elapsed_s, dt_prev_s, dist_prev_m, speed_mps, heading_deg | pipeline, at finalize |
| `content` | primary_subjects, objects, structures, materials, dominant_colors, visual_mood, description (2 sentences), alt_text (<=125), tags | model |
| `photogrammetry` | suitable_for_model, subject_coverage, motion_blur, texture_quality, notes | model |
| `view` | label, facing, key_objects | model |
| `quality` | sharpness, exposure, usable | model |
| `indicators` | model_candidate, progression, view_group | pipeline |
| `shoot` | shoot_id, related_shoots | pipeline |
| `human` | notes, feedback, training_candidate, edited | human |
| `review` | agreement_score, backend, reasoning | pipeline (judge) |
| `video` (video only) | duration_s, frames, timeline, summary, activity, scene_changes, camera_motion, subjects | model + pipeline |
| `model3d` (model3d only) | is_correctly_oriented, suggested_axis_rotations, orientation_notes, structural_and_design_elements | model |
| `confidence` | float 0 to 1 | model |

Enums: see `standard_fields.json`. Invalid model enums fall back to `other` (media_genre) or `unknown`.

## Per-kind model examples (`server/schemas/`)

- `still.example.json`: classification, content, photogrammetry, view, quality, confidence.
- `video_frame.example.json`: as still, lighter, plus `changes_since_previous`.
- `video.example.json`: synthesis output: summary, activity, scene_changes, camera_motion, subjects, timeline + shared blocks.
- `model3d.example.json`: orientation fields, structural elements, classification subset, content.

## Prompt stages

`build_prompt(kind, telemetry, stage=..., context=None, feedback=None)`:

- `screener`: narrow subset (classification, primary_subjects, view).
- `verifier`: full schema for the kind.
- `judge`: gets stage outputs as context; returns `{reasoning, agreementScore, corrected:{...}}`.
- `frame`, `video_base`, `video_detail`: per-frame example (`video_frame`).
- `video_synthesis`: context `{base_frames, detail_frames, duration_s}`; merges them into the `video` example shape.

Telemetry is passed as reference data, never instructions. Human `feedback` is appended as
"Human reviewer feedback to apply".

## Pipeline functions

- `normalize_model_output(kind, raw)`: coerces junk into schema shape; never raises.
- `assemble_record(kind, asset, telemetry, screener, verifier, judge, extra=None)`: verifier values win over
  screener, judge `corrected` fixes are applied, EXIF fields come only from telemetry.
- `validate_record(record)`: returns a list of problems (empty = valid).

## Drone shot taxonomy

Derived from stored telemetry by `media_intel.classify_shot_from_exif(telemetry, settings, context)`; every decision is explained in `classification.exif_basis`. Gimbal pitch uses the DJI convention (-90 = straight down, 0 = horizon). Thresholds are settings (defaults shown) and are measured as degrees below the horizon.

| `drone_angle` | Gimbal pitch (defaults) | Legacy `shot_type` |
|---|---|---|
| `nadir` | within 5 deg of vertical (<= -85) | `aerial_nadir` |
| `high_oblique` | 60 to 85 below horizon | `aerial_oblique` |
| `medium_oblique` | 30 to 60 below horizon | `aerial_oblique` |
| `low_oblique` | 10 to 30 below horizon | `aerial_low_oblique` |
| `horizon` | shallower than 10 | `aerial_low_oblique` above 15 m, otherwise `ground_level` |
| (no pitch) | altitude > 15 m: `unknown`; <= 3 m: `ground_level` | |

Oblique shots below 15 m altitude keep the legacy `aerial_low_oblique` `shot_type`. The model-facing `shot_type` enum is unchanged; aliases such as `aerial`, `nadir`, `high_oblique` or `horizon` normalize to it.

- `facing_compass`: N/NE/E/SE/S/SW/W/NW from gimbal yaw (falls back to `heading_deg`).
- `facing_relative` (`front` | `rear` | `side`, plus `facing_side` `left`/`right`): camera yaw versus `facing_reference`: the flight track heading (bearing between consecutive GPS points of the reconstructed flight; front within 45 deg, rear beyond 135 deg) or, when no track exists, the drone nose heading (`flight_yaw`).
- `viewed_from`: compass side of a known subject centre the camera is on.
- `altitude_band` (`low` <= 30 m, `medium`, `high` >= 90 m): relative altitude (AGL) when present, else GPS altitude for aerial shots.
- `mapping_role`: `nadir_grid` (nadir stills), `orbit_poi` (oblique, yaw within 25 deg of the bearing to the subject centre), `corridor` (oblique on a straight flight), `oblique_ring` (other obliques), `panorama` (shot hint), `video_nadir`/`video_oblique`/`video_horizon`, `ground`. Null when unknown.

Flights are rebuilt from capture time and GPS (`media_intel.build_flight_tracks`): a gap above `flight_gap_s` (600 s) starts a new flight; per photo we keep sequence N of M, elapsed seconds, delta to the previous shot, distance, ground speed and travel heading.

## Drone metadata capture

`server/exif_meta.py` reads exiftool output (when installed) and the embedded XMP packet: DJI `GimbalPitchDegree`, `GimbalYawDegree`, `FlightYawDegree`, `FlightRollDegree`, `RelativeAltitude`, `AbsoluteAltitude`, camera serial, make/model, lens, focal length, ISO, shutter, aperture, `DateTimeOriginal` + `OffsetTime`, GPS. `assets.captured_at` stays a naive UTC timestamp (converted with the EXIF offset when known); `capture_utc_offset` and `captured_at_local` keep the camera's local view. The full JSON-safe dump is stored in `assets.raw_exif` (migration `m004_drone_meta.sql`; the outbox triggers serialise whole rows, so new columns sync to Neon automatically once the migration has run there, which `ensure_schema` does).
