"""Drop badge modal must not rewind or kill the shared player."""

from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "src/dashboard/static/js/tune-flow.js"
PANEL = Path(__file__).resolve().parents[1] / "src/dashboard/static/js/tuning-panel.js"


def test_show_detail_does_not_rewind():
    flow = FLOW.read_text()
    show = flow[flow.index("async function _tfShowTuningDetail") : flow.index("function _tfCloseDetailModal")]
    assert "otAudio.currentTime = 0" not in show
    assert "Stop any playing audio" not in show


def test_close_detail_keeps_mini_player():
    flow = FLOW.read_text()
    close = flow[flow.index("function _tfCloseDetailModal") : flow.index("async function _tfBuildModalPlaylist")]
    assert "otAudio.pause()" not in close
    assert "hideMiniPlayer" not in close
    assert "showMiniPlayer()" in close


def test_live_autoplay_false_paints_preview_not_takeover():
    panel = PANEL.read_text()
    setter = panel[panel.index("function otSetPlaylist") : panel.index("function _otDisplayTrackInfo")]
    assert "live && opts.autoplay !== true" in setter
    assert "_otPaintPreview(opts.mountId, tracks, opts)" in setter
    assert "_otPendingTracks" not in panel
    assert "function _otStartPreview" in panel


def test_genre_cdn_does_not_map_to_raw_signal():
    text = PANEL.read_text()
    cover = text[text.index("function otGetCoverUrl") : text.index("function otFmtTime")]
    assert "cdnBase ? String(folder || '')" in cover
    assert "otSignalToFolder(folder)" in cover
    audio = text[text.index("function otGetAudioUrl") : text.index("function otGetCoverUrl")]
    assert "t.cdnBase" in audio
    assert "otSignalToFolder(t && t.folder)" in audio


def test_error_does_not_skip_next():
    text = PANEL.read_text()
    assert "not skipping" in text
    assert "otNext(); }, 1500)" not in text
