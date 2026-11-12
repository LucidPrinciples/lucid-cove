"""N13-N18 locks from the 11-12 fresh-account walk."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/dashboard/static"
FLOW = STATIC / "js/tune-flow.js"
PANEL = STATIC / "js/tuning-panel.js"
HOUSE = STATIC / "tuner-app/house.js"


def _read(path):
    return path.read_text()


def test_receive_is_a_call_to_action_and_says_when_waiting():
    flow = _read(FLOW)
    show = flow[flow.index("function _tfRevealReceive"):flow.index("function _tfReceiveTuning")]
    assert "Tap to receive your tuning" in show
    assert "Your tuning is still on its way." in show
    assert "scrollIntoView" not in show
    assert "house-shell" in show


def test_play_echo_plays_on_the_first_press():
    flow = _read(FLOW)
    play = flow[flow.index("function _tfPlaySingleEcho"):flow.index("function _renderStep8")]
    assert "autoplay: true" in play
    step7 = flow[flow.index("function _renderStep7"):flow.index("function _tfPlaySingleEcho")]
    assert "tf-btn-complete" in step7
    assert "_renderStep8" in step7
    step8 = flow[flow.index("function _renderStep8"):flow.index("const LANDING_RESPONSE_MAP")]
    assert "Did the tuning land?" in step8


def test_badge_open_keeps_autoplay_off_and_places_todays_echo():
    flow = _read(FLOW)
    build = flow[flow.index("async function _tfBuildModalPlaylist"):flow.index("function _tfOpenFullHistory")]
    assert "autoplay: false" in build
    assert "otPlaceEcho" in build
    panel = _read(PANEL)
    assert "function otEchoTrack" in panel
    assert "function otPlaceEcho" in panel
    preview = panel[panel.index("function _otPaintPreview"):panel.index("function _otStartPreview")]
    assert "opts.startIndex" in preview
    assert "tracks[0]" not in preview


def test_coaching_asterisks_become_emphasis():
    flow = _read(FLOW)
    fn = flow[flow.index("function _tfReadCoaching"):flow.index("function _tfCoachingText")]
    node = shutil.which("node")
    assert node
    script = fn + """
function ESC(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
const got = _tfCoachingHTML("what you *do*, not what you *say*");
if (got !== "what you <em>do</em>, not what you <em>say</em>") {
  console.error(got);
  process.exit(1);
}
const dashed = _tfCoachingHTML("locked on the flow signal \\u2014 this is the Passing Tide");
if (dashed.indexOf("\\u2014") !== -1) process.exit(1);
console.log("OK");
"""
    ran = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout
    assert "OK" in ran.stdout


def _echo_helpers():
    panel = _read(PANEL)
    return panel[panel.index("function otSignalToFolder"):panel.index("function _otKeepLoaded")]


def test_day_echo_is_the_drop_file_at_the_top_without_a_duplicate():
    node = shutil.which("node")
    assert node
    data = {
        "frequency": "Trust",
        "principle": "The Passing Tide",
        "signal_type": "Raw",
        "audio_url": "https://audio.lucidtuner.com/Lucid_Tuner/Raw_Signal/The_Passing_Tide_Raw_Echo.mp3",
    }
    script = _echo_helpers() + "\nconst data = " + json.dumps(data) + """;
const stream = [
  { filename: "Pattern_Ground_Echo.mp3", folder: "Ground_Signal", principle: "Pattern", title: "Pattern" },
  { filename: "The_Passing_Tide_Ground_Echo.mp3", folder: "Ground_Signal", principle: "The Passing Tide", title: "Ground" },
  { filename: "The_Passing_Tide_Raw_Echo.mp3", folder: "Raw_Signal", principle: "The Passing Tide", title: "Raw row" },
  { filename: "The_Passing_Tide_Raw_Echo.mp3", folder: "Raw_Signal", principle: "The Passing Tide", title: "Raw copy" },
  { filename: "The_Passing_Tide_Open_Echo.mp3", folder: "Open_Signal", principle: "The Passing Tide", title: "Open" },
];
const placed = otPlaceEcho(stream, data);
if (placed.index !== 0 || placed.tracks[0].title !== "Raw row") process.exit(1);
if (placed.tracks[0].filename !== "The_Passing_Tide_Raw_Echo.mp3") process.exit(1);
if (placed.tracks.length !== 4) process.exit(1);
const names = placed.tracks.map(t => t.filename);
if (names.filter(n => n === "The_Passing_Tide_Raw_Echo.mp3").length !== 1) process.exit(1);
const missing = otPlaceEcho(stream.filter(t => t.filename.indexOf("Raw") < 0), data);
if (missing.index !== 0) process.exit(1);
if (missing.tracks[0].filename !== "The_Passing_Tide_Raw_Echo.mp3") process.exit(1);
if (missing.tracks.length !== 4) process.exit(1);
if (missing.tracks[1].filename !== "Pattern_Ground_Echo.mp3") process.exit(1);
console.log("OK");
"""
    ran = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_after_the_echo_the_stream_keeps_playing_at_random():
    panel = _read(PANEL)
    pick = panel[panel.index("function _otPickNextIndex"):panel.index("function _otFileAt")]
    assert "otStreamNextIndex(otTracks.length, otIndex)" in pick
    node = shutil.which("node")
    assert node
    data = {
        "frequency": "Trust",
        "principle": "The Passing Tide",
        "signal_type": "Raw",
        "audio_url": "https://audio.lucidtuner.com/Lucid_Tuner/Raw_Signal/The_Passing_Tide_Raw_Echo.mp3",
    }
    script = _echo_helpers() + "\nconst data = " + json.dumps(data) + """;
const stream = [
  { filename: "Pattern_Ground_Echo.mp3", title: "Pattern" },
  { filename: "The_Passing_Tide_Ground_Echo.mp3", title: "Ground" },
  { filename: "Listen_Raw_Echo.mp3", title: "Listen" },
  { filename: "Love_Song_Raw_Echo.mp3", title: "Love" },
];
const placed = otPlaceEcho(stream, data);
if (placed.tracks[0].filename !== "The_Passing_Tide_Raw_Echo.mp3") process.exit(1);
const rolls = [0.0, 0.9, 0.4, 0.2, 0.7];
let roll = 0;
function rnd() { return rolls[roll++ % rolls.length]; }
let current = 0;
const heard = [placed.tracks[0].filename];
for (let n = 0; n < 8; n++) {
  const next = otStreamNextIndex(placed.tracks.length, current, rnd);
  if (next === current || next < 0 || next >= placed.tracks.length) process.exit(1);
  heard.push(placed.tracks[next].filename);
  current = next;
}
if (heard[1] === "Pattern_Ground_Echo.mp3") process.exit(1);
const later = heard.slice(1);
if (new Set(later).size < 2) process.exit(1);
console.log("OK");
"""
    ran = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_player_shows_the_echo_before_play_and_play_echo_starts_it():
    flow = _read(FLOW)
    panel = _read(PANEL)
    build = flow[flow.index("async function _tfBuildModalPlaylist"):flow.index("function _tfOpenFullHistory")]
    assert "autoplay: false" in build
    assert "startIndex: placed.index" in build
    stream = flow[flow.index("async function _tfsInit"):]
    assert "autoplay: false" in stream
    assert "startIndex: startIndex" in stream
    preview = panel[panel.index("function _otPaintPreview"):panel.index("function _otStartPreview")]
    assert "tracks[start]" in preview
    play = flow[flow.index("function _tfPlaySingleEcho"):flow.index("function _renderStep8")]
    fetch_at = play.index("await fetch")
    assert fetch_at > 0
    first_press = play[:fetch_at]
    assert "otEchoTrack(data)" in first_press
    assert "autoplay: true" in first_press
    assert "otSetPlaylist([track]" in first_press
    assert "await " not in first_press
    assert "otPlaceEcho(tracks, data)" in play[fetch_at:]


def test_welcome_is_per_account_and_placeholder_stays_off_tune_screens():
    flow = _read(FLOW)
    house = _read(HOUSE)
    copy = flow[flow.index("const LT_COPY"):flow.index("window.LT_COPY")]
    assert "PLACEHOLDER. Chords writes the one-time line on the Field versus Tune." in copy
    assert "\u2014" not in copy and "\u2013" not in copy
    steps = flow[flow.index("function _renderStep1"):flow.index("function _renderCompletedTuning")]
    assert "PLACEHOLDER. Chords writes" not in steps
    welcome = house[house.index("async function maybeOpenWelcome"):house.index("async function paintHomeStreak")]
    assert "MCReady" in welcome
    assert "ltAccountKey(WELCOME_SEEN_KEY)" in welcome
    assert "lt_welcome_seen" in house
    panel = _read(PANEL)
    assert "lt_player_log" in panel
    assert "ltAccountKey('lt_player_log')" not in panel
    assert 'ltAccountKey("lt_player_log")' not in panel
