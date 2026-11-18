"""ffprobe/ffmpeg helpers for sampling frames from videos."""
import json
import logging
import os
import re
import shutil
import subprocess

logger = logging.getLogger("bam.video_frames")

DEFAULT_MAX_EDGE = 1600


class VideoToolError(RuntimeError):
    """ffmpeg/ffprobe missing or failed."""


def max_edge():
    try:
        return max(32, int(os.getenv("BAM_VISION_MAX_EDGE", DEFAULT_MAX_EDGE)))
    except ValueError:
        return DEFAULT_MAX_EDGE


def frames_dir(asset_id, exports_dir=None):
    base = exports_dir or os.getenv("EXPORTS_DIR", "/storage/exports")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(asset_id))
    return os.path.join(base, "frames", safe)


def frame_basename(pass_name, index):
    return f"{pass_name}_{int(index):04d}.jpg"


def thumb_basename(pass_name, index):
    return f"{pass_name}_{int(index):04d}_thumb.jpg"


def scale_filter(edge):
    e = int(edge)
    return f"scale='if(gt(iw,ih),min({e},iw),-2)':'if(gt(iw,ih),-2,min({e},ih))'"


def build_probe_cmd(path):
    return ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]


def build_extract_cmd(path, time_s, out_path, edge):
    return ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(float(time_s), 0.0):.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", scale_filter(edge), "-q:v", "2", str(out_path)]


def _parse_rate(s):
    try:
        if "/" in str(s):
            n, d = str(s).split("/", 1)
            return float(n) / float(d) if float(d) else None
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_probe(data):
    data = data or {}
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise VideoToolError("no video stream found")
    s = streams[0]
    dur = None
    for v in (s.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            if v not in (None, "N/A"):
                dur = float(v)
                break
        except (TypeError, ValueError):
            continue
    return {"duration_s": dur or 0.0,
            "fps": _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate")),
            "width": s.get("width"), "height": s.get("height")}


def _run(cmd):
    if shutil.which(cmd[0]) is None:
        raise VideoToolError(f"{cmd[0]} is not installed")
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)
    except subprocess.CalledProcessError as e:
        raise VideoToolError(f"{cmd[0]} failed: {(e.stderr or '').strip()[:300]}") from e
    except subprocess.TimeoutExpired as e:
        raise VideoToolError(f"{cmd[0]} timed out") from e


def probe_video(path):
    """Return {duration_s, fps, width, height}; raises VideoToolError."""
    if not os.path.exists(path):
        raise VideoToolError(f"video not found: {path}")
    res = _run(build_probe_cmd(path))
    try:
        return parse_probe(json.loads(res.stdout or "{}"))
    except ValueError as e:
        raise VideoToolError("ffprobe returned invalid JSON") from e


def extract_frames(path, times, out_dir, thumb_edge, pass_name="base", start_index=0):
    """One frame per time -> [{time_s, file_path, thumb_path}]; failed frames are skipped."""
    os.makedirs(out_dir, exist_ok=True)
    edge = max_edge()
    out = []
    for i, t in enumerate(times):
        idx = start_index + i
        full = os.path.join(out_dir, frame_basename(pass_name, idx))
        thumb = os.path.join(out_dir, thumb_basename(pass_name, idx))
        try:
            _run(build_extract_cmd(path, t, full, edge))
            if not os.path.exists(full):
                raise VideoToolError("no frame produced (time past end?)")
            _run(build_extract_cmd(path, t, thumb, thumb_edge))
        except VideoToolError as e:
            logger.warning("Frame extraction failed; path=%s time_s=%s error=%s", path, t, e)
            continue
        out.append({"time_s": float(t), "file_path": full, "thumb_path": thumb if os.path.exists(thumb) else None})
    return out
