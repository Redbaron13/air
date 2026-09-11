#!/usr/bin/env python3
"""One-pass cloud vision worker for BAM assets already in Postgres.

Replaces the local Moondream + LLaVA + Llama 3.1 loop with a single
structured call to Azure OpenAI, OpenAI, or Anthropic.

Reads assets WHERE workflow_state = 'INGESTED'.
Writes ai_evaluations + assets.alt / describes / service_tags.
Sets workflow_state = 'PENDING_HUMAN' so the existing review UI works.

Env (first match wins unless BAM_VISION_BACKEND is set):
  BAM_VISION_BACKEND=azure|openai|anthropic|auto

  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_DEPLOYMENT   (default gpt-4o)
  AZURE_OPENAI_API_VERSION  (default 2024-08-01-preview)

  OPENAI_API_KEY
  OPENAI_MODEL              (default gpt-4o)

  ANTHROPIC_API_KEY
  ANTHROPIC_MODEL           (default claude-sonnet-4-5)

  DATABASE_URL
  BAM_VISION_MAX_EDGE       (default 1600)
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import Json, RealDictCursor

DB_DSN = os.getenv("DATABASE_URL")
MAX_EDGE = int(os.getenv("BAM_VISION_MAX_EDGE", "1600"))
STILL_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".dng",
}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MODEL_EXT = {".glb", ".gltf", ".obj", ".usdz"}

SYSTEM_PROMPT = """You are the Baron Aerial Media cataloger.
Analyze the image (drone still, keyframe, or 3D preview) for a public photography site.
Use EXIF telemetry when provided. If altitude > 0 treat the shot as aerial:
camera_perspective must be nadir or oblique, never eye-level.
Return ONLY valid JSON matching the schema. No markdown."""

USER_SCHEMA = """Return JSON with exactly these keys:
{
  "camera_perspective": "nadir | oblique | eye-level | unknown",
  "perspective_reasoning": "one sentence",
  "media_genre": "real-estate | roof | solar | construction | infrastructure | landscape | other",
  "shot_type": "hero | detail | context | inspection | orbit-frame | grid-frame | other",
  "primary_subjects": ["short tags"],
  "structural_and_design_elements": ["tags or none"],
  "materials_and_textures": ["tags or none"],
  "objects_spotted": ["tags or none"],
  "service_tags": ["roof", "solar", "construction", "real-estate"],
  "dominant_colors": ["colors"],
  "visual_mood": ["professional"],
  "website_suitable": true,
  "deliverable_suitable": true,
  "photogrammetry_value": "none | candidate | ready",
  "photogrammetry_notes": "overlap/orbit/grid hint or none",
  "describes": "two professional sentences for the site manifest",
  "alt": "accessible alt text, at least 12 words",
  "comprehensive_summary": "two sentences"
}"""


def log(msg: str) -> None:
    print(msg, flush=True)


def detect_backend() -> str:
    forced = os.getenv("BAM_VISION_BACKEND", "auto").strip().lower()
    if forced in {"azure", "openai", "anthropic"}:
        return forced
    if os.getenv("AZURE_OPENAI_ENDPOINT") and (
        os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY")
    ):
        return "azure"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise SystemExit(
        "No cloud vision backend configured. Set AZURE_OPENAI_ENDPOINT + "
        "AZURE_OPENAI_API_KEY, or OPENAI_API_KEY, or ANTHROPIC_API_KEY."
    )


def which_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def resize_still(src: Path, dest: Path, max_edge: int) -> Path:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        return src
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale='min({max_edge},iw)':-2"
    cmd = [
        ffmpeg, "-y", "-i", str(src),
        "-vf", vf, "-q:v", "3", str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.is_file():
        log(f"[WARN] resize failed for {src.name}: {result.stderr[-300:]}")
        return src
    return dest


def extract_video_keyframes(src: Path, work: Path) -> List[Path]:
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        log(f"[WARN] ffmpeg missing; cannot keyframe {src.name}")
        return []
    frames: List[Path] = []
    for idx, stamp in enumerate(("10%", "50%", "90%")):
        out = work / f"{src.stem}_k{idx}.jpg"
        cmd = [
            ffmpeg, "-y", "-i", str(src),
            "-ss", stamp if stamp[0].isdigit() else "00:00:01",
            "-vframes", "1",
            "-vf", f"scale='min({MAX_EDGE},iw)':-2",
            str(out),
        ]
        # Percent seeks are unreliable; use duration fractions instead.
        if stamp.endswith("%"):
            probe = subprocess.run(
                [
                    shutil.which("ffprobe") or ffmpeg,
                    "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(src),
                ],
                capture_output=True,
                text=True,
            )
            try:
                dur = float(probe.stdout.strip() or "0")
            except ValueError:
                dur = 0.0
            frac = int(stamp[:-1]) / 100.0
            t = max(0.1, dur * frac) if dur > 0 else 1.0 + idx
            cmd = [
                ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", str(src),
                "-vframes", "1",
                "-vf", f"scale='min({MAX_EDGE},iw)':-2",
                str(out),
            ]
        subprocess.run(cmd, capture_output=True, text=True)
        if out.is_file() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def file_to_data_url(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/jpeg")
    b64 = base64.b64encode(raw).decode("ascii")
    return mime, b64


def parse_json_text(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(cleaned[start:end + 1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def telemetry_block(asset: Dict[str, Any]) -> str:
    return (
        "[TELEMETRY]\n"
        f"- sensor: {asset.get('sensor_model')}\n"
        f"- altitude_m: {asset.get('altitude_meters')}\n"
        f"- gimbal_pitch: {asset.get('gimbal_pitch')}\n"
        f"- lat: {asset.get('latitude')} lon: {asset.get('longitude')}\n"
        f"- media_kind: {asset.get('media_kind')}\n"
        f"- filename: {asset.get('original_filename')}\n"
    )


def call_openai_compatible(
    images: List[Path],
    prompt: str,
    backend: str,
) -> Dict[str, Any]:
    from openai import AzureOpenAI, OpenAI

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in images:
        mime, b64 = file_to_data_url(img)
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                    "detail": "low",
                },
            }
        )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    if backend == "azure":
        client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        )
        model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    else:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        response_format={"type": "json_object"},
        max_tokens=800,
    )
    return parse_json_text(resp.choices[0].message.content or "")


def call_anthropic(images: List[Path], prompt: str) -> Dict[str, Any]:
    import anthropic

    blocks: List[Dict[str, Any]] = []
    for img in images:
        mime, b64 = file_to_data_url(img)
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            }
        )
    blocks.append({"type": "text", "text": prompt})
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    resp = client.messages.create(
        model=model,
        max_tokens=800,
        temperature=0.1,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": blocks}],
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    return parse_json_text(text)


def analyze_images(images: List[Path], asset: Dict[str, Any], backend: str) -> Dict[str, Any]:
    prompt = telemetry_block(asset) + "\n" + USER_SCHEMA
    if backend in {"azure", "openai"}:
        return call_openai_compatible(images, prompt, backend)
    return call_anthropic(images, prompt)


def merge_frame_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not results:
        return {}
    if len(results) == 1:
        return results[0]
    merged = dict(results[0])
    tags: List[str] = []
    subjects: List[str] = []
    for row in results:
        tags.extend(row.get("service_tags") or [])
        subjects.extend(row.get("primary_subjects") or [])
    merged["service_tags"] = sorted(set(str(t) for t in tags))
    merged["primary_subjects"] = sorted(set(str(t) for t in subjects))
    if any(r.get("photogrammetry_value") == "ready" for r in results):
        merged["photogrammetry_value"] = "ready"
    elif any(r.get("photogrammetry_value") == "candidate" for r in results):
        merged["photogrammetry_value"] = "candidate"
    summaries = [r.get("comprehensive_summary") for r in results if r.get("comprehensive_summary")]
    if summaries:
        merged["comprehensive_summary"] = " ".join(summaries[:2])
    return merged


def prepare_inputs(asset: Dict[str, Any], work: Path) -> List[Path]:
    src = Path(asset["source_path"])
    if not src.is_file():
        log(f"[ERROR] missing file {src}")
        return []
    ext = src.suffix.lower()
    if ext in VIDEO_EXT:
        return extract_video_keyframes(src, work)
    if ext in MODEL_EXT:
        log(f"[SKIP-VISION-FILE] 3D source {src.name} needs a preview still")
        return []
    dest = work / f"{src.stem}_web.jpg"
    return [resize_still(src, dest, MAX_EDGE)]


def split_for_ui(deep: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    screener = {
        "media_genre": deep.get("media_genre"),
        "camera_perspective": deep.get("camera_perspective"),
        "perspective_reasoning": deep.get("perspective_reasoning"),
        "primary_subjects": deep.get("primary_subjects") or [],
        "contains_infrastructure_or_architecture": deep.get("media_genre")
        in {"roof", "solar", "construction", "infrastructure", "real-estate"},
    }
    verifier = {
        "primary_subjects": deep.get("primary_subjects") or [],
        "structural_and_design_elements": deep.get("structural_and_design_elements") or [],
        "materials_and_textures": deep.get("materials_and_textures") or [],
        "objects_spotted": deep.get("objects_spotted") or [],
        "dominant_colors": deep.get("dominant_colors") or [],
        "visual_mood": deep.get("visual_mood") or [],
        "comprehensive_summary": deep.get("comprehensive_summary") or deep.get("describes"),
        "website_suitable": deep.get("website_suitable"),
        "deliverable_suitable": deep.get("deliverable_suitable"),
        "photogrammetry_value": deep.get("photogrammetry_value"),
    }
    judge = {
        "reasoning": "Single cloud vision pass; no local model disagreement.",
        "agreementScore": 1.0 if deep else 0.0,
        "backend": detect_backend(),
    }
    return screener, verifier, judge


def run_batch() -> int:
    if not DB_DSN:
        raise SystemExit("DATABASE_URL is required")
    backend = detect_backend()
    log(f"[CLOUD] backend={backend} max_edge={MAX_EDGE}")

    conn = psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SELECT * FROM assets WHERE workflow_state = 'INGESTED' ORDER BY created_at ASC")
    assets = cur.fetchall()
    if not assets:
        log("No INGESTED assets.")
        conn.close()
        return 0

    ok = 0
    failed = 0
    work_root = Path(tempfile.mkdtemp(prefix="bam-cloud-"))
    try:
        for asset in assets:
            aid = asset["asset_id"]
            work = work_root / aid
            work.mkdir(parents=True, exist_ok=True)
            log(f"[CLOUD] {aid} {asset.get('original_filename')}")
            images = prepare_inputs(asset, work)
            if not images:
                failed += 1
                log(f"[HOLD] {aid} no analyzable image")
                continue
            try:
                deep = analyze_images(images, asset, backend)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"[ERROR] {aid} {exc}")
                continue
            if not deep:
                failed += 1
                log(f"[ERROR] {aid} empty model JSON")
                continue

            screener, verifier, judge = split_for_ui(deep)
            alt = (deep.get("alt") or deep.get("comprehensive_summary") or "").strip()
            describes = (deep.get("describes") or deep.get("comprehensive_summary") or "").strip()
            tags = deep.get("service_tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]

            cur.execute(
                """
                INSERT INTO ai_evaluations (
                    asset_id, screener_output, verifier_output, judge_output,
                    deep_output, agreement_score
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    aid,
                    Json(screener),
                    Json(verifier),
                    Json(judge),
                    Json(deep),
                    float(judge.get("agreementScore") or 1.0),
                ),
            )
            cur.execute(
                """
                UPDATE assets
                   SET workflow_state = 'PENDING_HUMAN',
                       alt = %s,
                       describes = %s,
                       service_tags = %s
                 WHERE asset_id = %s
                """,
                (alt, describes, Json(tags), aid),
            )
            conn.commit()
            ok += 1
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
        conn.close()

    log(f"[CLOUD] done ok={ok} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(run_batch())
