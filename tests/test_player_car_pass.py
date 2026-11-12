"""Car player: pause flag, locked retry, previous history, now playing, player log."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "src/dashboard/static/js/tuning-panel.js"
HTML = ROOT / "src/dashboard/static/tuner-app/index.html"
HARNESS = Path(__file__).resolve().parent / "player_car_harness.js"

FAIL_STATUS = "The stream failed three times. Playback stopped."
LOG_BLURB = "Recent player events on this phone. Copy and send them after a drive if playback stops."


def _panel():
    return PANEL.read_text()


def test_pause_flag_cleared_when_buffer_goes_live():
    text = _panel()
    hand = text[text.index("function _otHandoffToPreloaded") : text.index("function _otIsMobile")]
    assert "retiring._otIgnorePause = true" in hand
    assert hand.index("otAudio._otIgnorePause = false") > hand.index("otAudio = incoming")
    # Rebinding stacks pause/ended/error listeners each time a buffer is recycled.
    assert "otAudio._otEventsBound = false" not in hand
    err = text[text.index("addEventListener('error'") : text.index("addEventListener('playing'")]
    assert "code === 1" in err
    bind = text[text.index("function _otBindCurrentAudioEvents") : text.index("function _otEnsureAudio")]
    assert "otAudio._otIgnorePause = false" in bind


def test_previous_uses_played_history():
    text = _panel()
    prev = text[text.index("function otPrev") : text.index("function otSetVolume")]
    assert "_otPlayHistory" in prev
    assert "(otIndex - 1 + otTracks.length) % otTracks.length" not in prev
    assert "otAudio.currentTime > 3" in prev


def test_parked_check_runs_before_keep_loaded():
    text = _panel()
    setter = text[text.index("function otSetPlaylist") : text.index("function _otDisplayTrackInfo")]
    assert setter.index("const live") < setter.index("_otTrackIsLoaded")
    assert setter.index("const parked") < setter.index("_otTrackIsLoaded")
    assert "live && opts.autoplay !== true" in setter
    assert "_otHaltBuffers()" in setter
    assert "_otPaintPreview(opts.mountId, tracks, opts)" in setter
    assert "_otPendingTracks" not in text


def test_now_playing_uses_cdn_base_and_position():
    text = _panel()
    setup = text[text.index("function otSetupMediaSession") : text.index("function otRenderPlaylist")]
    assert "otGetCoverUrl(track.folder, track.cdnBase)" in setup
    assert "otGetCoverUrl(track.folder)" not in setup.replace(
        "otGetCoverUrl(track.folder, track.cdnBase)", ""
    )
    assert "function _otPublishPosition" in text
    assert "LUCID TUNER" in setup


def test_stall_does_not_spend_the_error_cap():
    text = _panel()
    watch = text[text.index("function _otArmStallWatch") : text.index("function _otRetrySameTrack")]
    assert "otNext(" not in watch
    assert "_otConsecutiveErrors" not in watch
    assert "OT_STALL_QUIET_MS" in watch
    waiting = text[text.index("addEventListener('waiting'") : text.index("addEventListener('stalled'")]
    assert "_otWatchStall('waiting')" in waiting
    assert "_otScheduleError" not in waiting
    stalled = text[text.index("addEventListener('stalled'") : text.index("addEventListener('stalled'") + 180]
    assert "_otWatchStall('stalled')" in stalled
    err = text[text.index("function _otResolveError") : text.index("function _otResumeAfterStop")]
    assert "OT_FAIL_CAP" in err
    assert "otNext()" in err


def test_mobile_skips_preload_and_src_pause_is_marked():
    text = _panel()
    sched = text[text.index("function _otSchedulePreload") : text.index("function _otHandoffToPreloaded")]
    assert "_otIsMobile()" in sched
    assert "OT_PRELOAD_DELAY_MS" in sched
    assert "800" not in sched
    assert "readyState < 2" in sched
    play = text[text.index("addEventListener('play'") : text.index("addEventListener('pause'")]
    assert "_otSchedulePreload()" not in play
    playing = text[text.index("addEventListener('playing'") : text.index("addEventListener('waiting'")]
    assert "_otSchedulePreload()" in playing
    pause = text[text.index("addEventListener('pause'") : text.index("addEventListener('error'")]
    assert "_otHoldUserPause" in pause
    assert "function _otWithSrcChange" in text
    src_change = text[text.index("function _otWithSrcChange") : text.index("function _otReleaseSrcPause")]
    assert "setTimeout" not in src_change
    assert "_otHoldUserPause = false" not in src_change
    release = text[text.index("function _otReleaseSrcPause") : text.index("function _otLogPlayer")]
    assert "_otHoldUserPause = false" in release
    for name in ("loadstart", "loadedmetadata", "canplay", "suspend", "abort", "emptied", "play-blocked"):
        assert name in text


def test_up_next_is_warmed_with_a_ranged_fetch():
    text = _panel()
    assert "const OT_WARM_LEAD_S = 25" in text
    assert "bytes=0-1048575" in text
    assert "function _otEnsureUpNext" in text
    maybe = text[text.index("function _otMaybeWarm") : text.index("function _otLogWarm")]
    assert "_otLiveLoadInProgress" in maybe
    live = text[text.index("function _otLiveLoadInProgress") : text.index("function _otFullyBuffered")]
    assert "readyState < 4" in live
    assert "_otBuffering" in live
    start = text[text.index("function _otStartWarm") : text.index("function _otPreloadIndexTrack")]
    assert "new Audio" not in start
    tick = text[text.index("addEventListener('timeupdate'") : text.index("function _otEnsureAudio")]
    assert "_otMaybeWarm()" in tick


def test_silent_play_is_loading_not_playing():
    text = _panel()
    audible = text[text.index("function _otAudible") : text.index("function _otLoadingSound")]
    assert "_otSounding" in audible
    assert "_otBuffering" in audible
    play = text[text.index("addEventListener('play'") : text.index("addEventListener('pause'")]
    assert "playbackState = 'playing'" not in play
    assert "_otPublishPlaybackState()" in play
    playing = text[text.index("addEventListener('playing'") : text.index("addEventListener('waiting'")]
    assert "_otSounding = true" in playing
    assert "_otSchedulePreload()" in playing
    assert "_otPublishPlaybackState()" in playing
    icons = text[text.index("function otUpdateIcons") : text.index("function otStartProgress")]
    assert "_otLoadingSound()" in icons
    assert "is-loading" in icons
    assert "Loading" in text


def test_player_log_in_help_with_copy():
    text = _panel()
    html = HTML.read_text()
    assert "const OT_PLAYER_LOG_MAX = 200" in text
    assert "lt_player_log" in text
    assert "function otCopyPlayerLog" in text
    assert "getElementById('player-log-copy')" in text
    assert 'id="player-log-copy"' in html
    assert ">Copy</button>" in html
    assert "Player log" in html
    assert LOG_BLURB in html
    assert FAIL_STATUS in text
    for line in (FAIL_STATUS, LOG_BLURB, "No player events yet.", "Could not copy.", "Player log"):
        assert "\u2014" not in line and "\u2013" not in line


def test_player_car_behavior():
    proc = subprocess.run(
        ["node", str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PANEL": str(PANEL)},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["log"] == 200
