import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("video_frames", ROOT / "scripts" / "video_frames.py")
vf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vf)


def test_frames_dir_and_names(monkeypatch):
    monkeypatch.setenv("EXPORTS_DIR", "/x/exp")
    assert vf.frames_dir("a/b") == "/x/exp/frames/a_b"
    assert vf.frames_dir("A1", "/y") == "/y/frames/A1"
    assert vf.frame_basename("base", 3) == "base_0003.jpg"
    assert vf.thumb_basename("detail", 12) == "detail_0012_thumb.jpg"


def test_extract_cmd_seeks_before_input():
    cmd = vf.build_extract_cmd("/v.mp4", 1.5, "/o.jpg", 800)
    assert cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "800" in cmd[cmd.index("-vf") + 1]
    assert cmd[-1] == "/o.jpg"


def test_max_edge_env(monkeypatch):
    monkeypatch.delenv("BAM_VISION_MAX_EDGE", raising=False)
    assert vf.max_edge() == 1600
    monkeypatch.setenv("BAM_VISION_MAX_EDGE", "800")
    assert vf.max_edge() == 800
    monkeypatch.setenv("BAM_VISION_MAX_EDGE", "junk")
    assert vf.max_edge() == 1600


def test_parse_probe():
    data = {"streams": [{"codec_type": "audio"},
                        {"codec_type": "video", "width": 640, "height": 480,
                         "avg_frame_rate": "30000/1001", "duration": "N/A"}],
            "format": {"duration": "4.5"}}
    r = vf.parse_probe(data)
    assert r["duration_s"] == 4.5 and r["width"] == 640 and abs(r["fps"] - 29.97) < 0.01
    with pytest.raises(vf.VideoToolError):
        vf.parse_probe({"streams": []})


def test_missing_binaries(monkeypatch, tmp_path):
    monkeypatch.setattr(vf.shutil, "which", lambda b: None)
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")
    with pytest.raises(vf.VideoToolError, match="not installed"):
        vf.probe_video(str(p))
    assert vf.extract_frames(str(p), [1.0], str(tmp_path / "o"), 64) == []
    with pytest.raises(vf.VideoToolError):
        vf.probe_video(str(tmp_path / "missing.mp4"))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg missing")
def test_real_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("BAM_VISION_MAX_EDGE", "200")
    video = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=3",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    info = vf.probe_video(str(video))
    assert abs(info["duration_s"] - 3) < 0.5 and info["width"] == 640
    frames = vf.extract_frames(str(video), [0.5, 1.5], str(tmp_path / "fr"), 64)
    assert len(frames) == 2 and all(os.path.exists(f["file_path"]) and os.path.exists(f["thumb_path"]) for f in frames)
