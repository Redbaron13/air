"""Vision backends. Local Ollama is the default when OLLAMA_URL is set. Azure is optional."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


class BackendError(RuntimeError):
    pass


def resolve_backend() -> str:
    choice = os.environ.get("AIR_VISION_BACKEND", "").strip().lower()
    if choice == "azure":
        if not os.environ.get("AZURE_OPENAI_API_KEY"):
            raise BackendError("AIR_VISION_BACKEND=azure requires AZURE_OPENAI_API_KEY")
        return "azure"
    if choice in {"ollama", "skip"}:
        return choice
    if os.environ.get("OLLAMA_URL"):
        return "ollama"
    return "skip"


# Capture and shot fields belong to EXIF. A vision model is not allowed to set them.
_TELEMETRY_KEYS = {
    "latitude",
    "longitude",
    "altitude_m",
    "altitude",
    "altitude_meters",
    "gps",
    "gps_valid",
    "captured_at",
    "captured_at_local",
    "utc_offset",
    "gimbal_pitch",
    "gimbal_pitch_deg",
    "gimbal_yaw",
    "gimbal_yaw_deg",
    "flight_yaw_deg",
    "flight_roll_deg",
    "rel_altitude_m",
    "heading_deg",
    "sensor_model",
    "focal_length_mm",
    "focal_length",
    "orientation",
    "camera_make",
    "lens_model",
    "iso",
    "shutter_speed",
    "aperture",
    "camera_serial",
    "shot_characteristic",
    "drone_angle",
    "altitude_band",
    "shot_type_from_exif",
    "exif_basis",
    "camera_perspective",
    "shot_type_matches_exif",
    "facing_compass",
    "facing_relative",
    "facing_side",
    "facing_reference",
    "viewed_from",
    "mapping_role",
}
_DROP_BLOCKS = {"capture", "address", "flight"}


def sanitize_model_output(raw) -> dict:
    """Keep model content. Drop GPS, time, gimbal, and other EXIF fields."""
    if not isinstance(raw, dict):
        return {}
    return _strip_telemetry(raw)


def _strip_telemetry(value):
    if isinstance(value, list):
        return [_strip_telemetry(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = {}
    for key, item in value.items():
        if key in _DROP_BLOCKS or key in _TELEMETRY_KEYS:
            continue
        cleaned[key] = _strip_telemetry(item) if isinstance(item, (dict, list)) else item
    return cleaned


def complete_json(backend: str, prompt: str, image_path: str | None = None) -> dict:
    if backend == "skip":
        return {
            "skipped": True,
            "note": "No vision backend was called.",
            "description": None,
            "alt_text": None,
            "tags": [],
            "proposed": [],
        }
    if backend == "ollama":
        return sanitize_model_output(_ollama(prompt, image_path))
    if backend == "azure":
        return sanitize_model_output(_azure(prompt, image_path))
    raise BackendError(f"unknown vision backend: {backend}")


def _ollama(prompt: str, image_path: str | None) -> dict:
    base = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5-vl:3b"),
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    if image_path:
        import base64

        payload["images"] = [base64.b64encode(Path(image_path).read_bytes()).decode("ascii")]
    return _post_json(f"{base}/api/generate", payload, headers={"Content-Type": "application/json"})


def _azure(prompt: str, image_path: str | None) -> dict:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://bam-pipeline.openai.azure.com").rstrip("/")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21"
    content = [{"type": "text", "text": prompt}]
    if image_path:
        import base64

        encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    payload = {
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    raw = _post_json(
        url,
        payload,
        headers={
            "Content-Type": "application/json",
            "api-key": os.environ["AZURE_OPENAI_API_KEY"],
        },
    )
    message = raw.get("choices", [{}])[0].get("message", {}).get("content", "{}")
    return _load_json(message)


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface backend failure without secrets
        raise BackendError(f"vision backend request failed: {exc.__class__.__name__}") from exc
    if "response" in body:
        return _load_json(body.get("response") or "{}")
    return body


def _load_json(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"unparsed": str(value)[:500]}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
