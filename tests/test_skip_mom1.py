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


def _section_between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i + len(start))
    return src[i:j]


def test_pipeline_keeps_processing_masters_with_shorts():
    """Partial stems must stay listed while master is still in processing/."""
    proc = _section_between(
        PIPELINE,
        "for (const f of processingFiles) {\n        const ext = f.filename.lastIndexOf('.');\n        const stem = ext > 0 ? f.filename.substring(0, ext) : f.filename;\n        const t = transcriptMap[stem];",
        "// Inbox only if not already",
    )
    # Must not skip the row because shorts already exist
    assert "if (t && t.has_processed)" not in proc
    assert "continue;" not in proc or "has_processed" not in proc.split("continue;")[0][-80:]
    assert "folder: 'processing'" in proc
    assert "videos.push" in proc


def test_pipeline_keeps_inbox_masters_with_shorts():
    inbox = _section_between(
        PIPELINE,
        "// Inbox only if not already",
        "// Transcript-only",
    )
    assert "if (t && t.has_processed)" not in inbox
    assert "folder: 'inbox'" in inbox


def test_pipeline_keeps_incomplete_transcript_only():
    """has_processed alone must not drop transcript-only stems with work left."""
    only = _section_between(
        PIPELINE,
        "// Transcript-only",
        "const bannerHtml",
    )
    assert "if (t.has_processed) continue" not in only
    assert "moments_complete" in only
    assert "needs plan" in PIPELINE or "clips_left" in only


def test_transcripts_api_exposes_has_processed():
    assert "has_processed" in VPIPE
    assert 'f"{stem}-moments-processed.json"' in VPIPE or "moments-processed.json" in VPIPE


def test_graduate_stem_routes_exist():
    assert '@router.post("/graduate-stem")' in VPIPE
    assert '@router.post("/api/video/graduate-stem")' in VPROC
    assert "try_graduate_session" in VPIPE


def test_process_moments_does_not_auto_graduate_in_voice():
    """#VP-SESS-LIFE2 — voice process-moments no longer moves processing→raw."""
    assert "whole_done and processed" not in VPROC
    # Graduate helper still exists for the gated app path
    assert "graduate_processing_to_raw" in VPROC or "graduate-stem" in VPROC


def test_crop_whole_uses_gated_graduate_flag():
    assert "skip_moments: true" in CROP or "skip_moments:true" in CROP
    assert "gd.graduated" in CROP


def test_legacy_skip_moments_still_wired():
    assert "function skipMoments(" in PIPELINE
    assert "whole=1" in PIPELINE
    assert "params.get('whole') === '1'" in CROP
