"""Upload watermarked files to public R2 and originals to a private prefix."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pipeline.air.journal import Journal
from pipeline.air.paths import journal_path


def _credentials() -> dict | None:
    values = {
        "endpoint": os.environ.get("R2_ENDPOINT") or "",
        "bucket": os.environ.get("R2_BUCKET") or "",
        "access_key": os.environ.get("R2_ACCESS_KEY_ID") or "",
        "secret_key": os.environ.get("R2_SECRET_ACCESS_KEY") or "",
    }
    if not all(values.values()):
        return None
    return values


def publish(root: Path) -> dict:
    creds = _credentials()
    journal = Journal(journal_path(root))
    try:
        copies = journal.query("SELECT * FROM asset_copies")
        originals = [
            row for row in journal.assets() if row["original_store"] and row["compiled_id"]
        ]
        output = root / "Output"
        output.mkdir(parents=True, exist_ok=True)
        staged = 0
        for copy in copies:
            source = Path(copy["path"]) if copy["path"] else None
            if source is None or not source.exists():
                continue
            dest = output / copy["compiled_id"] / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != dest.resolve():
                shutil.copy2(source, dest)
            staged += 1
        if creds is None:
            return {
                "status": "skipped",
                "reason": "R2 credentials are not set",
                "watermarked_local": staged,
                "originals_local": len(originals),
            }
        uploaded = _upload(creds, copies, originals)
        for copy in copies:
            source = Path(copy["path"]) if copy["path"] else None
            if source is None or not source.exists() or not copy["sha256"]:
                continue
            import hashlib

            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != copy["sha256"] or source.stat().st_size <= 0:
                raise RuntimeError(f"derivative failed verification: {copy['copy_id']}")
        return {"status": "uploaded", **uploaded, "verified": True}
    finally:
        journal.close()


def _upload(creds: dict, copies, originals) -> dict:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=creds["endpoint"],
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
    )
    public = 0
    private = 0
    for copy in copies:
        source = Path(copy["path"]) if copy["path"] else None
        if source is None or not source.exists() or not copy["object_key"]:
            continue
        client.upload_file(str(source), creds["bucket"], copy["object_key"])
        public += 1
    for asset in originals:
        source = Path(asset["original_store"])
        if not source.exists():
            continue
        key = f"bam/originals/{asset['sha256']}{source.suffix.lower()}"
        client.upload_file(str(source), creds["bucket"], key)
        private += 1
    return {"public_objects": public, "private_originals": private}
