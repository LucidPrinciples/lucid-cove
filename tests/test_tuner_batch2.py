"""APP2: one player, Tune load state, pill placeholder, Tuning Key line breaks."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/dashboard/static"
SHELL = STATIC / "tuner-app"
FLOW = STATIC / "js/tune-flow.js"
PANEL = STATIC / "js/tuning-panel.js"


def _read(path):
    return path.read_text()


def test_pill_is_empty_until_the_day_loads():
    html = _read(SHELL / "index.html")
    tuner = _read(SHELL / "tuner.js")
    assert 'id="freq-text"></span>' in html or 'id="freq-text"></span>' in html.replace(" ", "")
    assert ">Peace<" not in html
    assert 'data-frequency="Peace"' not in html
    assert 'freq || "Peace"' not in tuner
    assert "if (freq) applyDrop(freq, colors)" in tuner


def test_tune_page_does_not_wait_on_presence_or_history():
    tuner = _read(SHELL / "tuner.js")
    flow = _read(FLOW)
    assert "loadTuneFlow()" in tuner
    show = tuner[tuner.index("function showPanel"):tuner.index("window.showPanel")]
    assert "MCReady" not in show.split("loadTuneFlow()")[0].split("which === \"tune\"")[-1]
    assert "function _tfFetchPersonalHistory" in flow
    assert "AbortController" in flow
    assert "Your last tuning is still loading." in flow
    assert "Couldn't load your tunings. You can still tune." in flow
    assert "/api/tuning/history?limit=100" in flow


def test_key_slash_becomes_a_line_break():
    flow = _read(FLOW)
    panel = _read(PANEL)
    action = _read(SHELL / "action.js")
    team = _read(STATIC / "js/team.js")
    assert flow.count("split(' / ').join('<br>')") >= 3
    assert "split(' / ').join('\\n')" in panel
    assert "whiteSpace = 'pre-line'" in panel
    assert 'function keyBreak' in action
    assert "keyBreak(triad.tuning_key)" in action
    assert "split(' / ').join('<br>')" in team
    css = _read(SHELL / "action.css")
    assert "white-space: pre-line" in css


def test_one_player_keeps_place_and_starts_at_random():
    panel = _read(PANEL)
    lists = _read(STATIC / "js/playlists.js")
    house = _read(SHELL / "house.js")
    css = _read(SHELL / "house.css")
    assert "function _otTrackIsLoaded" in panel
    assert "function _otKeepLoaded" in panel
    assert "function _otStableStart" in panel
    assert "function _otAudible" in panel
    setter = panel[panel.index("function otSetPlaylist"):panel.index("function _otDisplayTrackInfo")]
    assert "live && opts.autoplay !== true" in setter
    assert "_otPaintPreview(opts.mountId, tracks, opts)" in setter
    assert "_otHaltBuffers()" in setter
    assert "_otTrackIsLoaded" in setter
    icons = panel[panel.index("function otUpdateIcons"):panel.index("function otStartProgress")]
    assert "_otAudible()" in icons
    toggle = panel[panel.index("function otTogglePlay"):panel.index("function otNext")]
    assert "_otAudible()" in toggle
    assert "[tracks[i], tracks[j]]" not in lists
    assert "startIndex:" in lists
    assert "Math.random() * tracks.length" in lists
    assert "showMiniPlayer()" in house
    assert "grid-row: 1 / -1" in css


def test_new_tune_copy_has_no_dash():
    flow = _read(FLOW)
    copy = flow[flow.index("const LT_COPY"):flow.index("window.LT_COPY")]
    assert "—" not in copy and "–" not in copy
    assert "tuneSlow" in copy
    assert "tuneLoadFail" in copy
