"""Crop still frame extract: limited-range iPhone → full-range JPEG identity."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_VIDEO = ROOT / "voice" / "src" / "routes" / "video.py"


def _load_frame_helpers():
    """Load frame_still_* helpers without importing the full FastAPI voice app."""
    text = VOICE_VIDEO.read_text()
    start = text.index("def frame_still_vf()")
    end = text.index('@router.get("/api/video/frame")')
    ns = {}
    exec(text[start:end], ns)
    return ns["frame_still_vf"], ns["frame_still_ffmpeg_cmd"]


def test_frame_still_vf_expands_limited_to_full_range():
    vf, _ = _load_frame_helpers()
    s = vf()
    assert "in_range=auto" in s
    assert "out_range=pc" in s
    assert "yuvj420p" in s
    assert "lanczos" in s
    # No look grade on the still path — CSS owns Original/Natural/etc.
    assert "eq=" not in s
    assert "curves=" not in s
    assert "colortemperature" not in s


def test_frame_still_ffmpeg_cmd_is_identity_jpeg():
    _, cmd_fn = _load_frame_helpers()
    cmd = cmd_fn("/tmp/clip.mov", 12.5)
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and "12.5" in cmd
    assert "-i" in cmd and "/tmp/clip.mov" in cmd
    assert "-vframes" in cmd and "1" in cmd
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "out_range=pc" in vf
    assert "-q:v" in cmd and "2" in cmd
    assert "-color_range" in cmd and "pc" in cmd
    assert "-colorspace" in cmd and "bt709" in cmd
    assert "-f" in cmd and "image2" in cmd
    assert cmd[-1] == "pipe:1"
    # Must not be the old bare extract (no range expand).
    joined = " ".join(cmd)
    assert "in_range=auto" in joined
    assert "eq=" not in joined


def test_extract_frame_route_uses_helper():
    text = VOICE_VIDEO.read_text()
    assert "frame_still_ffmpeg_cmd(video_path, t)" in text
    # Old bare argv must be gone.
    assert (
        '["ffmpeg", "-ss", str(t), "-i", video_path,\n'
        '             "-vframes", "1", "-q:v", "2", "-f", "image2", "pipe:1"]'
        not in text
    )
