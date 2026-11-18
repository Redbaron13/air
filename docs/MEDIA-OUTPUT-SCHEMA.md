# Media output schema (v1.0.0)

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
