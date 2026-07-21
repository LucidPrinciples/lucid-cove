"""Crop still + publish color prep: HDR HLG tonemap and SDR limited→full still."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_VIDEO = ROOT / "voice" / "src" / "routes" / "video.py"


def _load_color_helpers():
    """Load color/still helpers without importing the full FastAPI voice app."""
    text = VOICE_VIDEO.read_text()
    start = text.index("# HLG (iPhone HDR)")
    end = text.index('@router.get("/api/video/frame")')
    ns = {
        "subprocess": __import__("subprocess"),
        "logger": __import__("logging").getLogger("test_frame_still"),
        "frozenset": frozenset,
    }
    exec(text[start:end], ns)
    return ns


def test_is_hdr_detects_iphone_hlg():
    ns = _load_color_helpers()
    assert ns["is_hdr_color"]({
        "color_transfer": "arib-std-b67",
        "color_primaries": "bt2020",
        "color_space": "bt2020nc",
        "color_range": "tv",
    })
    assert ns["is_hdr_color"]({"color_transfer": "smpte2084"})
    assert not ns["is_hdr_color"]({
        "color_transfer": "bt709",
        "color_primaries": "bt709",
        "color_range": "tv",
    })
    assert not ns["is_hdr_color"](None)
    assert not ns["is_hdr_color"]({})


def test_frame_still_vf_hdr_uses_tonemap_not_range_only():
    ns = _load_color_helpers()
    hlg = {
        "color_transfer": "arib-std-b67",
        "color_primaries": "bt2020",
        "color_space": "bt2020nc",
        "color_range": "tv",
    }
    s = ns["frame_still_vf"](hlg)
    assert "tonemap=" in s
    assert "zscale=" in s
    assert "bt709" in s
    assert "yuvj420p" in s
    assert ":r=pc" in s
    # Must declare HLG + bt2020 on the INPUT zscale (cast fix)
    assert "tin=arib-std-b67" in s
    assert "pin=bt2020" in s
    assert "min=bt2020nc" in s
    assert "rin=tv" in s
    assert "eq=" not in s
    assert "curves=" not in s


def test_frame_still_vf_sdr_expands_limited_to_full_range():
    ns = _load_color_helpers()
    sdr = {"color_transfer": "bt709", "color_range": "tv"}
    s = ns["frame_still_vf"](sdr)
    assert "in_range=auto" in s
    assert "out_range=pc" in s
    assert "yuvj420p" in s
    assert "tonemap=" not in s
    assert "eq=" not in s


def test_color_prep_vf_hdr_on_publish_sdr_empty():
    ns = _load_color_helpers()
    prep = ns["color_prep_vf"]({
        "color_transfer": "arib-std-b67",
        "color_primaries": "bt2020",
        "color_space": "bt2020nc",
        "color_range": "tv",
    })
    assert "tonemap=" in prep
    assert "yuv420p" in prep
    assert ":r=tv" in prep
    assert "tin=arib-std-b67" in prep
    assert "pin=bt2020" in prep
    assert ns["color_prep_vf"]({"color_transfer": "bt709"}) == ""
    assert ns["color_prep_vf"](None) == ""


def test_frame_still_ffmpeg_cmd_hdr_path():
    ns = _load_color_helpers()
    hlg = {"color_transfer": "arib-std-b67"}
    cmd = ns["frame_still_ffmpeg_cmd"]("/tmp/clip.mov", 12.5, hlg)
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and "12.5" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "tonemap=" in vf
    assert "-color_range" in cmd and "pc" in cmd
    assert "-colorspace" in cmd and "bt709" in cmd
    assert cmd[-1] == "pipe:1"
    assert "eq=" not in " ".join(cmd)


def test_frame_still_ffmpeg_cmd_probes_when_no_color_info():
    text = VOICE_VIDEO.read_text()
    start = text.index("# HLG (iPhone HDR)")
    end = text.index('@router.get("/api/video/frame")')
    called = []

    def tracking_probe(path):
        called.append(path)
        return {
            "color_range": "tv",
            "color_space": "bt2020nc",
            "color_transfer": "arib-std-b67",
            "color_primaries": "bt2020",
            "pix_fmt": "yuv420p",
        }

    g = {
        "subprocess": __import__("subprocess"),
        "logger": __import__("logging").getLogger("t"),
        "frozenset": frozenset,
    }
    exec(text[start:end], g)
    g["probe_video_color"] = tracking_probe
    cmd = g["frame_still_ffmpeg_cmd"]("/tmp/hdr.mov", 1.0, None)
    assert called == ["/tmp/hdr.mov"]
    assert "tonemap=" in cmd[cmd.index("-vf") + 1]


def test_process_paths_wire_color_prep():
    text = VOICE_VIDEO.read_text()
    assert "vf_prep = color_prep_vf(color_info)" in text
    assert "frame_still_ffmpeg_cmd(video_path, t, probe_video_color(video_path))" in text
    assert text.count("vf_prep") >= 6
    assert "hdr→sdr" in text


def test_extract_frame_route_uses_helper():
    text = VOICE_VIDEO.read_text()
    assert "frame_still_ffmpeg_cmd(video_path, t, probe_video_color(video_path))" in text
    assert (
        '["ffmpeg", "-ss", str(t), "-i", video_path,\n'
        '             "-vframes", "1", "-q:v", "2", "-f", "image2", "pipe:1"]'
        not in text
    )
