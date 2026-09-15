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


def test_badge_while_playing_paints_drop_preview():
    flow = FLOW.read_text()
    build = flow[flow.index("async function _tfBuildModalPlaylist") : flow.index("function _tfOpenFullHistory")]
    assert "autoplay: false" in build
    assert "otSetPlaylist(tracks" in build


def test_ot_set_playlist_matches_hermes_takeover():
    panel = PANEL.read_text()
    setter = panel[panel.index("function otSetPlaylist") : panel.index("function _otDisplayTrackInfo")]
    assert "_otHaltBuffers()" in setter
    assert "live && opts.autoplay !== true" in setter
    assert "_otPaintPreview(opts.mountId, tracks, opts)" in setter
    assert "_otPendingTracks" not in panel


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


def test_takeover_halts_both_buffers():
    text = PANEL.read_text()
    assert "function _otHaltBuffers" in text
    setter = text[text.index("function otSetPlaylist") : text.index("function _otDisplayTrackInfo")]
    assert "_otHaltBuffers()" in setter


def test_badge_does_not_load_drop_iframe():
    tuner = Path(__file__).resolve().parents[1] / "src/dashboard/static/tuner-app/tuner.js"
    text = tuner.read_text()
    assert "dropFrame.src = dropPlayerUrl" not in text
    assert "_tfShowTuningDetail" in text


def test_house_css_does_not_clip_mini_player():
    css = (Path(__file__).resolve().parents[1] / "src/dashboard/static/tuner-app/house.css").read_text()
    assert "html, body.house" not in css
    shell = css[css.index("#house-shell {") : css.index(".house-pane[hidden]")]
    assert "overflow-y: auto" in shell
    assert "grid-template-rows: auto minmax(0, 1fr)" in css
    assert "z-index: 5000" in css
    panes = css[css.index("#pane-team") : css.index("#pane-work {")]
    assert "overflow: visible" in panes
    mount = (Path(__file__).resolve().parents[1] / "src/dashboard/static/tuner-app/tuner-mount.css").read_text()
    assert "body.has-mini-player .mini-player { display: flex !important; }" in mount


def test_playlists_do_not_scrollintoview():
    text = (Path(__file__).resolve().parents[1] / "src/dashboard/static/js/playlists.js").read_text()
    assert ".scrollIntoView(" not in text
    assert "function _plRevealPlayerMount" in text
    assert "house-shell" in text
