# AIR pipeline

This package processes Baron Aerial Media files one stage at a time. Prep runs on the local library with no network. Vision, Postgres, Neon, and R2 run only when that stage is invoked and its setting is present.

The review console in the repository root is separate. It still serves the in-memory sample with `bun run dev` on port 3000. It does not read `tempID.db` yet.

## Identifier jobs

These ids are stored on the same asset and are not substitutes for each other.

- Temp id, assigned by `prep scan`. Example: `P7K2M_21A26131521-AA001`. The first letter is `P` still, `M` video, `D` 3D model, or `X` other media. The next four characters are unique in the journal. The stamp is `DDMYY` plus `HHMMSS`. The month letter is shared with the compiled id: Jan A, Feb F, Mar R, Apr P, May M, Jun J, Jul Y, Aug G, Sep S, Oct O, Nov N, Dec D. 21 January 2026 is `21A26`. 21 June 2026 is `21J26`.
- Keyframe id, assigned for a video still. Example: `K7K2M01` for video unique `7K2M`. It includes the whole unique token, so two videos that share the first two characters do not reuse a frame id or a staging file. Those first two characters are still the shared stem. The keyframes table stores the parent video temp id, the timestamp, and the label (`V5` 15s, `V3` 30s, `V4` 45s, `V1` 60s when the clip is longer than one minute, `V0` ten seconds before the end).
- Location id, assigned by `group location`. One place across dates. Every photo of that place stays on it, including photos that are not in a before-and-after pair.
- Overlap link id, assigned by `group overlap`. One visit at that place. Three or more members whose mean spacing is 30 meters or less are marked `photogrammetry`. A smaller or wider visit is stored as `insufficient`.
- Compiled id, assigned by `id` after the location confidence is at least 0.7. Example: `21A26-003H7KPHOA01-K4M2`. The location code sits in the middle. Tags and capture facts stay on the row so a website search can find the file.
- Copy id, assigned by `derive`. Example: `2D4-K4M2-I10` for the 2560 pixel WebP. The other widths are 1600 (`1T6`), 800 (`6M7`), and 320 (`3M2`).

## Shot characteristics

`ai exif` reads gimbal pitch with ExifTool and writes a locked tag using the same bands as `droneAngleOf` in `src/logic.ts`.

- `nadir`: within 5 degrees of straight down (about 90 degrees, including a DJI pitch of -90)
- `high_oblique`: 60 degrees or more below the horizon
- `medium_oblique`: 30 degrees or more
- `low_oblique`: 10 degrees or more
- `horizon`: shallower than 10 degrees

Missing pitch stays unset. Missing GPS stays null. A reading of `0,0` is stored as null.

`group shots` gathers files that share one of those characteristics. `group before-after` proposes pairs from the same location and the same characteristic on different calendar dates. The other photos of that location remain on the location id. `group model` builds a set when a location has at least two characteristics, such as nadir plus a high oblique.

## Tags

The starting catalog is `pipeline/tags/presets.json`. Angle and altitude tags are locked. Content tags keep their words, for example `standing seam metal roof`.

`ai reason` may select names that are already in the saved catalog. A name that is not saved is stored as a proposal and is not applied. `review tag` saves a new tag only when characteristics are supplied. `review override` changes the displayed value of a locked tag and keeps the measured value and the reason. Running `ai exif` again refreshes the measurement and leaves the override text in place.

## Library folders

`setup.sh` and `prep init` create these under `library/` (or `AIR_LIBRARY`):

- `input` files to scan
- `staging` numbered copies used by later stages
- `resize` smaller JPEG copies of stills when Pillow is installed
- `duplicate` second copies of a SHA-256 already in the journal
- `originals` untouched bytes named by SHA-256
- `Renamed` a copy named with the compiled id after `id`
- `Output` watermarked derivatives
- `exports` `website.manifest.json`
- `Database/tempID.db` the offline journal

`library/` is gitignored. `setup.sh` does not write `.env` or `.env.example`, and it does not write a password. Copy `.env.example` to `.env` on the machine that will run a networked stage.

## Commands

Run from the repository root:

```sh
python3 -m pipeline.air --library library <command>
```

| Command | Writes | Needs |
| --- | --- | --- |
| `prep init` | folders and `tempID.db` | local disk |
| `prep scan` | temp ids, duplicates, keyframes, originals | local disk, `ffmpeg` for video frames |
| `sync` | Postgres when `DATABASE_URL` is set | nothing when the URL is unset |
| `sync --neon` | Neon when `NEON_DATABASE_URL` is set | nothing when the URL is unset |
| `ai exif` | pitch, GPS, locked shot tags | `exiftool` for values; otherwise records an empty read |
| `ai describe` | one description per still, keyframe, or 3D file | vision backend below |
| `ai reason` | catalog tags and unsaved proposals | vision backend below |
| `ai validate` | agreement score for that one file | vision backend below |
| `ai synthesize video` | one video record after every keyframe has a description | vision backend below |
| `ai synthesize model` | one set record after every view has a description | `group model` first |
| `review tag --name NAME --characteristic key=value` | a saved user tag | local journal |
| `review override --temp-id ID --tag nadir --value high_oblique --reason TEXT` | measured value plus display value | local journal |
| `group location` | location id for GPS clusters within 150 meters | local journal |
| `group shots` | similar-characteristic groups | shot characteristic |
| `group before-after` | cross-date candidates | location and shot characteristic |
| `group overlap` | visit overlap links | location and capture time |
| `group model` | multi-view sets | location and at least two shot characteristics |
| `id` | compiled id, then a renamed copy | location confidence of at least 0.7 |
| `derive` | four watermarked WebP files from the original | Pillow, compiled id, still image |
| `rewatermark --watermark TEXT` | new WebP files from the same originals | Pillow |
| `manifest` | `exports/website.manifest.json` | `APPROVED` or `AUTO_ACCEPTED`, alt text, WebP for stills |
| `publish` | local `Output/` always; R2 only with credentials | R2 settings below |

`run --from prep --to manifest` runs the chain from the first named stage through the second. The stage names are `prep`, `sync`, `exif`, `describe`, `reason`, `validate`, `synthesize-video`, `group`, `synthesize-model`, `id`, `derive`, `manifest`, `publish`. Video synthesis runs after each keyframe has its own description. Model synthesis runs after `group model` has written the view set.

Video and 3D files are ingested and tagged. `derive` does not invent a still for them. Their manifest entries use `media_kind` of `video` or `model3d`.

## Vision backend

`AIR_VISION_BACKEND` selects the backend.

- `skip` writes no visual description and does not open a network connection. This is the default when `OLLAMA_URL` is unset.
- `ollama` calls `OLLAMA_URL` (default `http://127.0.0.1:11434`) with `OLLAMA_VISION_MODEL` (default `qwen2.5-vl:3b`). This is the default when `OLLAMA_URL` is set and `AIR_VISION_BACKEND` is empty.
- `azure` calls `AZURE_OPENAI_ENDPOINT` (default `https://bam-pipeline.openai.azure.com`) and `AZURE_OPENAI_DEPLOYMENT` (default `gpt-4o`). It exits unless `AZURE_OPENAI_API_KEY` is set. The key is read from the environment and is not written by these commands.

Describe, reason, and validate each receive that one still, keyframe, or model view. Reason and validate also receive the description already stored for that file. Synthesize reads those finished rows and writes the combined video or model-set record. It does not delete the individual rows. A video is not auto-accepted from a single keyframe. A model set is not auto-accepted from a single view. Auto-accept also requires an agreement score of at least 0.85, alt text, at least 10 content tags from the catalog, and `rights_status` of `cleared`. Anything short of that stays `PENDING_HUMAN` on `approval_state`. `id` and `derive` update `workflow_state` and leave `approval_state` in place, so a later manifest still includes an approved still.

## Watermark and originals

`derive` and `rewatermark` read `originals/` (the path on `original_store`). They do not paint over that file. Public WebP files land in `Output/<compiled id>/`. `rewatermark` can take one `--compiled-id` or, when that flag is omitted, every compiled still. Temp id, compiled id, location id, and tags stay as they are.

`publish` copies those WebP files under `Output/`. Without `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` it stops there. With all four set, it uploads watermarked objects to `bam/web/stills/...` and originals to `bam/originals/<sha256>`. That upload path is not covered by the offline tests.

## Manifest

`manifest` includes an asset only when `approval_state` is `APPROVED` or `AUTO_ACCEPTED` and alt text is present. A still also needs a `.webp` copy. The file includes the detailed tags, the alt text, the media kind, and the copy keys. It does not include latitude, longitude, altitude, gimbal pitch, or a city.

## Database

`Database/tempID.db` is the working journal for every command above. `pipeline/sql/init_schema.sql` is the Postgres shape, including filename history, duplicates, keyframes, tag definitions, overrides, locations, before-and-after candidates, similar-shot groups, overlap links, model-view sets, relations, case studies, and copies. `sync` applies that file when a database URL is set. The offline tests do not open Postgres or Neon.

## What these tests run

From the repository root:

```sh
python3 -m unittest discover -s pipeline/tests
```

That run uses local files, ffmpeg, and Pillow. It does not call Ollama, Azure, Postgres, Neon, or R2. `sync --neon` is checked only for the skip path.
