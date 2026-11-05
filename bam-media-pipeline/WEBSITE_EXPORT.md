# Export reviewed media to the BAM website

The new `scripts/export_website_manifest.py` is a separate, read-only export path. It does not replace `emit_manifest.py`, restart workers, alter the review dashboard, write to the database, or upload media. This lets the current ingestion and AI work continue independently.

## What it fixes

The existing emitter builds guessed object paths, exports only width/format lists, assumes a location when one is missing, and joins `shoots.city` / `shoots.state`, which are absent from the checked-in schema. The website needs actual derivative URLs, dimensions and image descriptions. This exporter reads only existing `assets` and `asset_copies` fields and uses each derivative's recorded `r2_storage_key`.

Only records marked `APPROVED` are considered. Every still needs a reviewed alt description, website service tags and at least one registered WebP derivative. AI evaluation scores do not grant approval. Unsupported video/model records are reported as held. Any invalid still stops the complete export and preserves the last output, so an empty or partial export cannot silently replace a usable one.

Service tags must use the website identifiers:

`real-estate`, `construction`, `inspections`, `damage`, `roof-solar`, `mapping`, `events`

The output includes only the public display fields and hashes. It omits local source paths, EXIF coordinates, sensor data, project addresses and guessed geography. Review alt text itself for intended public disclosure before approving it.

## First run: local validation

In a shell with the pipeline's existing `DATABASE_URL` set, run from `bam-media-pipeline`:

```sh
python3 scripts/export_website_manifest.py \
  --origin https://media.your-domain.com \
  --output /absolute/path/storage/exports/website.manifest.json
```

Replace the origin and output path with your actual values. The database mode uses the pipeline's existing `psycopg2` dependency. It uses a read-only, consistent transaction and never reads the `.env` file itself. Keep credentials in the existing runtime environment.

The result is marked **staged**. A staged export can be checked by the website's `media:import` command but cannot be applied. Database records alone are not evidence that the files are publicly available.

## After approved derivatives have been uploaded

Upload orchestration is not included. Once approved derivatives are at their exact recorded object keys, rerun with `--verify-remote`:

```sh
python3 scripts/export_website_manifest.py \
  --origin https://media.your-domain.com \
  --output /absolute/path/storage/exports/website.manifest.json \
  --verify-remote
```

Verification downloads only those public derivatives, checks their byte count and SHA-256 against the database, requires `image/webp`, and checks CORS for `https://baronaerial.com`. Redirects fail. Each image is limited to 50 MB. No API secrets are sent to the media origin. All objects must pass before the file is marked **verified** and replaced atomically.

Then, in the website repository:

```sh
npm run media:import -- /absolute/path/storage/exports/website.manifest.json https://media.your-domain.com
npm run media:import -- /absolute/path/storage/exports/website.manifest.json https://media.your-domain.com --apply
npm run build
```

The first command checks only. The second updates the local manifest. Neither publishes the site. Review the resulting Git diff before choosing new asset IDs for pages. Duplicate original hashes assigned to different asset IDs require explicit reconciliation.

## Tests and offline use

```sh
python3 -m unittest discover -s bam-media-pipeline/tests -p 'test_website_manifest.py'
```

Run that command from the repository root. Tests use synthetic metadata and temporary folders and require no database or network access. For offline exports, `--snapshot /absolute/path/snapshot.json` accepts an object with `assets` and `copies` arrays containing the same selected database fields. Never commit snapshots of the real archive.

The live database, R2 objects and approval dashboard have not been exercised by these offline tests. The active derivative builder still needs its ingestion/queue integration; this exporter will say which required metadata or derivative is missing instead of inventing it.
