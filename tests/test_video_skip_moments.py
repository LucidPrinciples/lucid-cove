# #D5 — "Skip moments — use whole video". A short video should go transcribe →
# caption + crop → schedule directly, bypassing moment-finding. This is a UI-flow
# change (no new backend: /caption-full and /process-moments already exist), so
# assert the two pages are wired: the pipeline page offers the shortcut and sends
# whole=1; the crop page turns whole=1 into a single full-video clip and skips the
# redirect to moments review.
import pathlib

STATIC = pathlib.Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static" / "action-board"
PIPELINE = (STATIC / "full-video-pipeline.html").read_text()
CROP = (STATIC / "video-crop-position.html").read_text()


def test_pipeline_offers_skip_moments_for_transcribed():
    # a skipMoments() handler exists and is only offered once a transcript exists
    assert "function skipMoments(" in PIPELINE
    assert "skip-moments" in PIPELINE
    assert "canSkipMoments" in PIPELINE
    assert "v.status === 'transcribed' || v.has_moments" in PIPELINE


def test_pipeline_skip_navigates_with_whole_flag():
    assert "video-crop-position.html?stem=${stem}&whole=1" in PIPELINE


def test_skip_action_does_not_trigger_card_click():
    # the shortcut must stop the card's openVideo() from also firing
    assert "event.stopPropagation(); skipMoments(" in PIPELINE


def test_crop_reads_whole_param():
    assert "params.get('whole') === '1'" in CROP
    assert "wholeVideo" in CROP


def test_crop_skips_moments_redirect_in_whole_mode():
    # the "no clips -> go pick moments" redirect must be bypassed for whole video
    assert "clips.length === 0 && stem && !wholeVideo" in CROP


def test_crop_synthesizes_full_video_clip():
    # one clip spanning 0 -> full duration, dropped into the normal flow
    assert "wholeVideo && clips.length === 0" in CROP
    assert "clip_label: 'Full video'" in CROP
    assert "start_seconds: 0" in CROP
    assert "end_seconds: dur" in CROP


def test_crop_back_link_returns_to_pipeline_in_whole_mode():
    assert "full-video-pipeline.html?stem=${stem}" in CROP
