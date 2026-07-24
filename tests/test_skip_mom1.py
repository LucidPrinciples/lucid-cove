# #SKIP-MOM1 — skip-moments = one full via process-moments; leave active pipeline.
import pathlib

STATIC = pathlib.Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static" / "action-board"
ROUTES = pathlib.Path(__file__).resolve().parents[1] / "src" / "dashboard" / "routes"
VOICE = pathlib.Path(__file__).resolve().parents[1] / "voice" / "src" / "routes"

PIPELINE = (STATIC / "full-video-pipeline.html").read_text()
CROP = (STATIC / "video-crop-position.html").read_text()
VPIPE = (ROUTES / "video_pipeline.py").read_text()
VPROC = (VOICE / "video.py").read_text()


def test_whole_mode_hides_caption_full_checkbox():
    assert "wholeVideo ? ''" in CROP or "${wholeVideo ? ''" in CROP
    assert "Also generate captioned full-length video" in CROP
    # checkbox markup only rendered when not wholeVideo
    assert "wholeVideo ? '' : `<label" in CROP or "wholeVideo ? '' : `<label" in CROP.replace("\n", "")


def test_whole_mode_process_button_label():
    assert "Process full video" in CROP


def test_whole_mode_graduates_not_caption_full():
    assert "/api/video/graduate-stem" in CROP
    # whole path returns before caption-full fire
    idx_g = CROP.index("if (wholeVideo)")
    idx_cf = CROP.index("const captionFull = document.getElementById('chk-caption-full')")
    assert idx_g < idx_cf


def test_pipeline_hides_has_processed():
    assert "has_processed" in PIPELINE
    assert "t.has_processed" in PIPELINE


def test_transcripts_api_exposes_has_processed():
    assert "has_processed" in VPIPE
    assert 'f"{stem}-moments-processed.json"' in VPIPE or "moments-processed.json" in VPIPE


def test_graduate_stem_routes_exist():
    assert '@router.post("/graduate-stem")' in VPIPE
    assert '@router.post("/api/video/graduate-stem")' in VPROC


def test_process_moments_graduates_whole_video():
    assert "whole_video process-moments" in VPROC or "whole_done" in VPROC
    assert "graduate_processing_to_raw" in VPROC


def test_legacy_skip_moments_still_wired():
    assert "function skipMoments(" in PIPELINE
    assert "whole=1" in PIPELINE
    assert "params.get('whole') === '1'" in CROP
