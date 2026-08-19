"""Publish encodes must mix source channels to mono.

iPhone / Bluetooth captures often put talk on the left and leave the right
nearly empty. Speakers fold L+R so it sounds fine; one remaining earbud
only hears the empty side. process-moments and caption-full used to run
highpass/loudnorm per channel and encode stereo AAC with no -ac 1.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PY = (ROOT / "voice" / "src" / "routes" / "video.py").read_text()


def test_publish_audio_helper_downmixes_before_treat():
    start = VIDEO_PY.index("PUBLISH_AF = (")
    end = VIDEO_PY.index("def join_vf")
    ns = {}
    exec(VIDEO_PY[VIDEO_PY.index("def encode_fps_args"):end], ns)
    af = ns["PUBLISH_AF"]
    assert af.startswith("aformat=channel_layouts=mono,")
    assert "loudnorm=" in af
    args = ns["publish_audio_args"]()
    assert args[0] == "-af" and args[1] == af
    assert "-ac" in args and args[args.index("-ac") + 1] == "1"
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"


def test_process_moments_and_caption_full_use_publish_audio():
    proc = VIDEO_PY.split("async def process_moments", 1)[1].split(
        "async def heal_inbox_processing", 1
    )[0]
    cap = VIDEO_PY.split("async def caption_full_video", 1)[1]
    assert "*publish_audio_args()" in proc
    assert "*publish_audio_args()" in cap
    # Inline stereo AAC (no -ac) must not remain on either publish path.
    assert '"-c:a", "aac", "-b:a", "192k"' not in proc
    assert '"-c:a", "aac", "-b:a", "192k"' not in cap
    # Preview stills can stay cheap stereo or mono; publish must force mix.
    assert "aformat=channel_layouts=mono" in VIDEO_PY
