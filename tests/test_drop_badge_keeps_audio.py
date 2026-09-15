"""Drop badge modal must not rewind or kill the shared player."""

from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "src/dashboard/static/js/tune-flow.js"


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


def test_modal_playlist_skips_set_when_already_playing():
    flow = FLOW.read_text()
    build = flow[flow.index("async function _tfBuildModalPlaylist") :]
    assert "otAudio && !otAudio.paused" in build
    assert "otSetPlaylist(tracks" in build
