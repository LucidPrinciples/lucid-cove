"""Public Lucid Tuner shell — overlay habit chrome on Tuner Host only.

Does not replace Mission Control on app.lucidcove.org.
"""
from pathlib import Path

from src.dashboard.host_context import is_public_tuner_host

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dashboard" / "static"
SHELL = STATIC / "tuner-app"
APP_PY = ROOT / "src" / "dashboard" / "app.py"


def test_is_public_tuner_host_only_app_lucidtuner():
    assert is_public_tuner_host("app.lucidtuner.com")
    assert is_public_tuner_host("APP.LucidTuner.com:443")
    assert not is_public_tuner_host("app.lucidcove.org")
    assert not is_public_tuner_host("lucidtuner.com")
    assert not is_public_tuner_host("www.lucidtuner.com")
    assert not is_public_tuner_host("")


def test_tuner_app_folder_is_habit_floor_not_mc():
    html = (SHELL / "index.html").read_text()
    assert "Align Your Broadcast" in html
    assert 'id="pane-app"' in html
    assert 'id="pane-tuner"' in html
    assert 'id="panel-tune"' in html
    assert 'id="panel-playlists"' in html
    assert 'id="panel-deeper"' in html
    assert 'id="settings-account"' in html
    assert 'id="affiliates-content"' in html
    assert 'id="settings-username"' in html
    assert "house-free" in html
    assert "Mission Control" not in html
    assert 'id="pane-team"' not in html
    assert 'id="pane-work"' not in html
    assert 'id="pane-action"' in html
    assert 'data-nav="action"' not in html
    assert 'class="board-switch"' not in html
    assert ">Attention<" not in html
    assert 'href="/work"' not in html
    nav = html[html.index('class="top-nav"') : html.index('class="top-end"')]
    assert 'href="/action"' not in nav
    end = html[html.index('class="top-end"') : html.index('id="house-shell"')]
    assert 'href="/action"' not in end
    assert "LUCID_TUNER_APP" not in html
    assert "/static/js/tune-flow.js" in html
    assert "/static/js/playlists.js" in html
    assert "/static/tuner-app/adapter.js" in html


def test_cove_index_still_mission_control():
    html = (STATIC / "index.html").read_text()
    assert "Mission Control" in html or 'id="tab-bar"' in html
    assert "Align Your Broadcast" not in html


def test_house_js_stays_on_habit_paths():
    js = (SHELL / "house.js").read_text()
    assert 'path === "/" || path === "/app"' in js
    assert "classList.add(\"house-free\")" in js
    assert "/api/presence/me" in js
    assert "/api/account/affiliates" in js
    assert "app.lucidtuner.com" in js
    assert r"^[A-Za-z0-9_-]{4,40}$" in js
    assert 'return { area: "team" }' not in js
    assert 'path === "/action"' in js
    assert 'return { area: "action" }' in js
    assert "loadTunerAction" in js
    assert ".top-nav a, .board-switch a" in js
    assert "return { area: \"app\" }" in js


def test_adapter_uses_hub_presence_not_new_wizard():
    js = (SHELL / "adapter.js").read_text()
    assert "/api/presence/me" in js
    assert "TIER_LEVELS" in js
    assert "function loadTuneFlow" not in js


def test_tuner_freq_chrome_follows_latest_tuning():
    js = (SHELL / "tuner.js").read_text()
    action = (SHELL / "action.js").read_text()
    assert "lpFreqColor" in js
    assert "applyTunerFreqChrome" in js
    assert "_tfFetchLatestDropTuning" in js
    assert "applyDrop(pkg.frequency || \"Peace\")" not in js
    assert "applyTunerFreqChrome" in action
    assert "triad.frequency" in action


def test_app_py_host_gates_tuner_shell_and_paths():
    src = APP_PY.read_text()
    assert "is_public_tuner_host" in src
    assert 'tuner-app" / "index.html"' in src or "tuner-app" in src
    assert '"/app"' in src
    assert '"/tune"' in src
    assert '"/playlists"' in src
    assert '"/deeper"' in src
    assert '"/action"' in src


def test_copied_assets_exist():
    for name in (
        "index.html",
        "adapter.js",
        "house.js",
        "tuner.js",
        "free-tuner.js",
        "free-tuner.css",
        "house.css",
        "chrome.css",
        "tuner.css",
        "tuner-mount.css",
        "work.css",
        "action.js",
        "action.css",
    ):
        assert (SHELL / name).is_file(), name


def test_tuner_home_is_square_two_by_two_not_cove_stack():
    html = (SHELL / "index.html").read_text()
    css = (SHELL / "free-tuner.css").read_text()
    assert 'class="home-grid"' in html
    assert ">The Field<" in html
    assert ">Tune<" in html
    assert "pro-badge" in html
    assert "Signal &amp; genre streams" in html or "Signal & genre streams" in html
    assert "grid-template-columns: 1fr 1fr" in css or "grid-template-columns:1fr 1fr" in css
    assert "#pane-app .home-grid" in css
    assert "#pane-app .button-stack" not in css


def test_tuner_host_pro_modal_is_unlimited_tune_not_operator():
    js = (SHELL / "adapter.js").read_text()
    assert "function showUpgradeModal" in js
    assert "window.showUpgradeModal = window.showUpgradeModal || function () {};" not in js
    assert "$9" in js
    assert "Start Pro" in js
    assert "pro_monthly" in js
    assert "Become an Operator" not in js
    assert "Creation Flows" not in js
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    assert "free: 1" in flow
    assert "pro: -1" in flow


def test_tuner_home_field_is_drop_not_countdown():
    js = (SHELL / "free-tuner.js").read_text()
    html = (SHELL / "index.html").read_text()
    assert 'id="fieldDesc">today\'s tuning for everyone</' in html
    assert 'id="fieldDesc">Trust the Field</' not in html
    assert 'id="fieldDesc">Latest Tuning</' not in html
    assert "_ltFieldCountdown" not in js
    assert "_tfCountdownStr" not in js
    assert 'lchGoto("/tune")' in js
    assert "showUpgradeModal" not in js


def test_tuner_pro_badge_only_when_upgraded():
    html = (SHELL / "index.html").read_text()
    js = (SHELL / "adapter.js").read_text()
    assert 'id="tuneProBadge" hidden' in html
    assert "badge.hidden = level < 5" in js
    assert "badge.hidden = level >= 5" not in js


def test_tuner_host_connect_key_without_this_house():
    html = (SHELL / "index.html").read_text()
    js = (SHELL / "house.js").read_text()
    assert 'id="settings-get-connect-key"' in html
    assert "Get my connect key" in html
    assert "https://hermes.cove.lucidcove.org" not in html
    assert 'id="settings-open-hermes"' not in html
    assert "Open Lucid Cove on Hermes" not in html
    assert "Same Lucid Tuner account. Choose which house runs your agents." in html
    assert 'name="settings-house-kind"' in html
    assert 'value="cove"' in html
    assert 'value="hermes"' in html
    assert "Original Lucid Cove on a computer you own." in html
    assert "Lucid Cove on Hermes" in html
    assert "github.com/LucidPrinciples/lucid-cove" in html
    assert "github.com/LucidPrinciples/lucid-cove-hermes" not in html
    assert "settings-steps-cove" in html
    assert "settings-steps-hermes" in html
    assert "host us" not in html.lower()
    assert "Operator" not in html
    assert "Haven" not in html
    assert "house-hermes-steps" in html
    assert "syncHouseKind" in js
    assert "kind !== \"cove\"" in js
    assert "kind !== \"hermes\"" in js
    assert "this house" not in html
    assert "same registry" not in html.lower()
    assert "overlay stays private" not in html
    assert 'id="settings-username"' in html
    assert "readonly" in html
    assert "Set at signup" in html
    assert "settings-copy-row" in js
    assert "settings-copy-btn" in js
    assert "handle.readOnly = locked" in js
    assert "if (!handleLocked) payload.username" in js
    css = (SHELL / "house.css").read_text()
    assert ".settings-copy-row" in css
    assert ".settings-kind-list" in css
    assert ".settings-input {" in css and "width: 100%" in css
    assert "/api/account/self-host-token" in js
    assert "settings-connect-key-value" in js
    assert "That slice comes after this door walks" not in html
    presence = (ROOT / "src" / "dashboard" / "routes" / "presence.py").read_text()
    assert 'HTTPException(403, "Handle is locked")' in presence
    assert "handle_locked" in presence


def test_tune_page_locks_tune_now_keeps_last_tuning():
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    assert "function _tfTuneNowTopHTML" in flow
    assert "tf-btn-tune-now-locked" in flow
    assert "Next free Tune at midnight" in flow
    assert "_tfUpgrade()" in flow
    assert "Upgrade for unlimited" in flow
    assert "tf-btn-tune-now-cta" in flow
    assert "Get unlimited tunings" not in flow
    assert 'id="tfUnlimitedCta"' not in flow
    assert "function _tfBlockIfLocked" in flow
    assert "function _tfDateIsToday" in flow
    assert "function _tfTuneAgainHTML" not in flow
    css = (ROOT / "src/dashboard/static/css/tune-flow.css").read_text()
    assert "tf-btn-tune-now-locked" in css
    assert "tf-btn-tune-now-cta" in css


def test_settings_signal_sliders_and_ordered_mirrors():
    html = (SHELL / "index.html").read_text()
    js = (SHELL / "house.js").read_text()
    css = (SHELL / "house.css").read_text()
    assert "Off signals stay out of Tune Now" in html
    assert "Field-selected Drop is unchanged" in html
    assert "Checked signals stay out of Field-selected Tune" not in html
    assert 'class="settings-slider-list" id="settings-signal-filters"' in html
    assert 'class="settings-toggle" role="switch"' in html
    assert 'data-signal="Ground"' in html
    assert 'name="excluded-signal"' not in html
    assert "settings-toggle" in css
    assert "collectExcludedSignals" in js
    assert "SIGNAL_COLORS" in js
    assert "ALLOWED_SIGNALS.length" in js
    assert "Drag to reorder" in html
    assert "collectMirrorsInOrder" in js
    assert "settings-mirror-row" in html
    assert "mirror-drag-handle" in html
    assert 'draggable="true"' in html
    assert "initMirrorDrag" in js
    assert "settings-ltp-model" not in html
    assert "ltp-model" not in html


def test_batch1_words_live_in_one_place():
    """Home, Tune state, streak, practice titles, welcome, and Help read LT_COPY."""
    html = (SHELL / "index.html").read_text()
    house = (SHELL / "house.js").read_text()
    tuner = (SHELL / "tuner.js").read_text()
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    css = (ROOT / "src/dashboard/static/css/tune-flow.css").read_text()

    assert 'id="home-streak"' in html
    assert 'id="welcome-overlay"' in html
    assert 'id="welcome-text"' in html
    assert 'id="help-welcome"' in html
    assert 'id="help-batch1-entries"' in html
    assert ">you, right now<" in html

    copy = flow[flow.index("const LT_COPY"):flow.index("window.LT_COPY")]
    assert "—" not in copy and "–" not in copy
    for line in (
        "The Field",
        "today's tuning for everyone",
        "you, right now",
        "Your free Tune is available now",
        "Used today. Next free Tune at midnight",
        "Unlimited Tune",
        "No personal Tune yet.",
        "your last tuning",
        "Your streak",
        "Start your streak today",
        "Your Tune history",
        "The Field's history",
        "This is always available in Help.",
        "The player",
    ):
        assert line in copy
    streak = flow[flow.index("function _tfStreakText"):flow.index("window._tfStreakText")]
    assert "n < 1" in streak
    assert "streakZero" in streak
    coaching = flow[flow.index("const TUNE_COACHING"):flow.index("const TUNE_LIMITS")]
    assert "—" not in coaching and "–" not in coaching
    assert "The truth was always there. You just needed to retune the decoder." in coaching

    assert "window.LT_COPY" in house
    assert "help-batch1-entries" in house
    assert "lt_welcome_seen" in house
    assert "paintHomeStreak" in house
    assert "_tfNoteFieldDay" in tuner
    assert "function _tfPracticeTrio" in flow
    assert "function _tfFreeTuneUsed" in flow
    assert "function _tfNoteFieldDay" in flow
    assert "lt_field_days" in flow
    trio = flow[flow.index("function _tfPracticeTrio"):flow.index("function _tfPracticeCompactHTML")]
    assert trio.index("practice_steps") < trio.index("universal_practice")
    assert "Your Tune</span>" not in flow
    assert "Today's Tune Now is used" not in flow
    assert "text-transform: none" in css
    assert ".tf-complete-date" in css
    assert ".tf-tune-state" in css
    assert ".tf-shaped" not in css
    assert "tuneShaped" not in flow
    assert "shaped by your answers" not in flow


def test_tune_shaped_copy_and_loading_lines():
    """A personal Tune says the answers set the frequency. The feeling step stays a reading."""
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    messages = flow[flow.index("const QUANTUM_MESSAGES"):flow.index("function _renderStep3")]
    for line in (
        "Your answers set the frequency. The field selects the song inside it.",
        "What you are doing names the broadcast. The field selects the song inside that frequency.",
        "The frequency you want to broadcast is set. The field selects the Signal inside it.",
        "Your decoder holds the frequency you want to broadcast. The field selects the song inside it.",
        "Alignment begins with your answers. The field selects the song inside the frequency.",
    ):
        assert line in messages
    assert "Your answer chose" not in flow
    assert "selects your frequency" not in flow
    assert "\u2014" not in messages and "\u2013" not in messages
    assert "I am a Broadcast Frequency" in flow[flow.index("const FIELD_INTENTION"):flow.index("const QUANTUM_MESSAGES")]
    step3 = flow[flow.index("function _renderStep3"):flow.index("function _tfSetE")]
    assert "Heavy / Stuck" in step3
    assert "Steady / Okay" in step3
    assert "Clear / Energized" in step3
    step4 = flow[flow.index("function _renderStep4"):flow.index("function _renderStep5")]
    assert "tuneShaped" not in step4
    assert "tf-shaped" not in step4
    assert "_tfReadCoaching(d.coaching || TUNE_COACHING[freq]" in step4


def test_feeling_answer_does_not_feed_selection():
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    start = flow.index("async function _tfRequestTuning")
    body = flow[start:flow.index("if (!resp.ok)", start)]
    assert "frequency: tuneFlow.mode.frequency" in body
    assert "signal_before" not in body
    assert "eStart" not in body
    saved = flow[flow.index("tuneFlow.tuningData = data"):flow.index("function _renderStep4")]
    assert "_tfSaveSignalBefore()" in saved


def test_signal_before_is_saved_when_answered():
    """Leaving after the feeling step still PATCHes signal_before. Step 8 is not required."""
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    choose = flow[flow.index("function _tfSetE"):flow.index("function _tfSaveSignalBefore")]
    assert "_tfSaveSignalBefore()" in choose
    save = flow[flow.index("function _tfSaveSignalBefore"):flow.index("function _tfShowReceiveButton")]
    assert "method: 'PATCH'" in save
    assert "signal_before: value" in save
    assert "user_feedback" not in save
    assert "signal_after" not in save


def test_api_coaching_dashes_are_cleaned_for_reading():
    """The model still writes em dashes. Step 4, the card, and the detail read a cleaned line."""
    import json
    import shutil
    import subprocess

    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    fn = flow[flow.index("function _tfReadCoaching"):flow.index("function _tfCoachingText")]
    assert "\\u2014" in fn and "\\u2013" in fn
    step4 = flow[flow.index("function _renderStep4"):flow.index("function _renderStep5")]
    assert "_tfReadCoaching(" in step4
    detail = flow[flow.index("async function _tfShowTuningDetail"):flow.index("function _tfCloseDetailModal")]
    assert "_tfCoachingText(s," in detail
    node = shutil.which("node")
    assert node, "node is required to run the coaching cleaner"
    cases = [
        ("locked on the flow signal \u2014 this is the Passing Tide",
         "locked on the flow signal. This is the Passing Tide"),
        ("Joy is not a reward \u2014 it's the frequency.",
         "Joy is not a reward. It's the frequency."),
        ("done. \u2014 next stays a sentence",
         "done. Next stays a sentence"),
        ("already there\u2014you just needed to retune",
         "already there. You just needed to retune"),
    ]
    script = fn + "\nconst cases = " + json.dumps(cases) + """;
const bad = [];
for (const [inp, exp] of cases) {
  const got = _tfReadCoaching(inp);
  if (got !== exp) bad.push({ got, exp });
}
if (bad.length) {
  console.error(JSON.stringify(bad));
  process.exit(1);
}
console.log("OK");
"""
    ran = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout
    assert "OK" in ran.stdout


def test_personal_card_shows_request_coaching_and_sent_answers():
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    helper = flow[flow.index("function _tfCoachingText"):flow.index("function _tfIsPersonalSession")]
    personal = helper[helper.index("} else {"):helper.index("if (!coaching) coaching = TUNE_COACHING")]
    assert personal.index("data.coaching") < personal.index("universal_coaching")
    field = helper[helper.index("if (fieldRecord)"):helper.index("} else {")]
    assert "universal_coaching" in field
    assert "data.coaching" not in field
    assert "TUNE_COACHING[freq]" in helper
    complete = flow[flow.index("async function _renderCompletedTuning"):flow.index("function _tfCheckedMirrorSources")]
    assert "data.context" in complete
    assert "data.initial_state" in complete
    assert "entry_mode" not in complete
    assert "entryMode" not in complete
    assert "tuneShaped" not in complete
    assert "tf-shaped" not in complete
    assert "answerLine" in complete
    assert "_tfCoachingText(data, freq, fieldRecord)" in complete
    assert "return _tfReadCoaching(coaching)" in helper


def test_signal_readings_migration_is_idempotent():
    """051 widens SMALLINT once. A later startup sees REAL and does not alter again."""
    sql = (ROOT / "docker/migrations/051_signal_readings_real.sql").read_text()
    init = (ROOT / "docker/init-base.sql").read_text()
    assert sql.count("data_type = 'smallint'") == 2
    assert sql.count("ALTER COLUMN signal_before TYPE REAL") == 1
    assert sql.count("ALTER COLUMN signal_after TYPE REAL") == 1
    before_alter = sql.split("ALTER COLUMN signal_before")[0]
    after_alter = sql.split("ALTER COLUMN signal_after")[0]
    assert before_alter.count("data_type = 'smallint'") == 1
    assert after_alter.count("data_type = 'smallint'") == 2
    assert "signal_before       REAL" in init
    assert "signal_after        REAL" in init


def test_help_sends_contact_to_haven_inbox():
    html = (SHELL / "index.html").read_text()
    js = (SHELL / "house.js").read_text()
    css = (SHELL / "house.css").read_text()
    assert 'id="help-contact-form"' in html
    assert 'id="help-contact-message"' in html
    assert 'id="help-contact-email"' not in html
    assert "/api/contact/submit" in js
    assert 'product: "lucid-tuner"' in js
    assert "sendFeedback" not in js
    assert "LUCID_TUNER_APP" not in html
    assert "help-contact-form" in css


def test_tune_detail_module_mounts_player_like_drop():
    """Recent tunings and the header badge open the same in-app module + player.

    drop.lucidprinciples.com still works because it never referenced an undefined
    `data` binding. The Tuner history modal did — that throw skipped the player.
    """
    flow = (ROOT / "src/dashboard/static/js/tune-flow.js").read_text()
    start = flow.index("async function _tfShowTuningDetail")
    end = flow.index("function _tfCloseDetailModal")
    detail = flow[start:end]
    assert "data.universal_coaching" not in detail
    assert "_tfCoachingText(s," in detail
    assert 'id="tfModalPlayer"' in detail
    assert "${audioUrl ?" not in detail
    assert "_tfBuildModalPlaylist" in detail
    assert 'class="drop-header"' in detail
    assert "tf-modal-drop" in detail
    assert "Consecutive Tuning" in detail
    assert "_tfFullDropDate" in flow
    css = (ROOT / "src/dashboard/static/css/tune-flow.css").read_text()
    assert "tf-modal-drop" in css
    assert "max-width: 640px" in css

    tuner = (SHELL / "tuner.js").read_text()
    assert "_tfShowTuningDetail" in tuner
    assert "_tfFetchLatestDropTuning" in tuner

    house = (SHELL / "house.js").read_text()
    assert 'frame.src = "about:blank"' not in house

    panel = (STATIC / "js" / "tuning-panel.js").read_text()
    assert "signal_type: d.signal_type" in panel
    # Official Drop order: Tuning Key, then Coaching, then Practice.
    markup = detail[detail.index("drop-header") :]
    assert markup.index("Tuning Key") < markup.index("Coaching") < markup.index("Practice")
    complete = flow[flow.index("async function _renderCompletedTuning") : flow.index("function _tfCloseDetailModal")]
    complete_html = complete[complete.index("tune-complete") :]
    assert complete_html.index("Tuning Key") < complete_html.index(">Coaching<")
    # Drop archive belongs on Field/Hub, not on the personal Tune page.
    assert 'id="otRecentDrops"' not in complete
    assert 'id="thHistory"' not in complete
    assert 'id="tfHistory"' in complete
    assert "otLoadRecentDrops" not in complete
    # Badge / Drop module still carries the public archive + today's mirrors.
    assert 'id="tfDropRecent"' in detail
    assert 'id="tfDropMirrors"' in detail
    assert "_tfLoadDropArchive" in flow
    assert "_tfLoadDropMirrors" in flow
    # Checked sources, in settings order, on every player — not the cascade default.
    assert "function _tfFetchCheckedMirrors" in flow
    assert "function _tfCheckedMirrorSources" in flow
    assert "function _tfAppendMirrorCards" in flow
    assert "_tfFetchCheckedMirrors" in flow[flow.index("async function _tfLoadDropMirrors") :]
    # Re-opening Tune or the badge replaces mirror cards — it must not stack another copy.
    assert 'id="tfTuneMirrors"' in complete
    assert "mount.replaceChildren()" in flow[flow.index("function _tfAppendMirrorCards") :]
    assert "_tfTuneMirrorsGen++" in complete
    assert "_tfDropMirrorsGen++" in detail
    assert "if (gen !== _tfTuneMirrorsGen) return" in flow
    assert "if (gen !== _tfDropMirrorsGen) return" in flow
    assert "s._dropHub" in detail
    assert "if (!s._fromArchive)" not in detail
    tuner = (SHELL / "tuner.js").read_text()
    assert "_dropHub: true" in tuner
    house = (SHELL / "house.js").read_text()
    assert "window.applyHouseSettingsToMC = applyHouseSettingsToMC" in house
    css_key = css[css.index(".tf-modal-drop .ot-card.ot-key") :]
    assert "text-align: center" in css_key
    assert "1.05rem" in css_key
    # CDN playlists are {tracks:[{signalType, album, filename}]} — map to Clear_Signal.
    assert "function otPlaylistRows" in panel
    assert "function otMapCdnTrack" in panel
    assert "t.signalType" in panel
    assert "otTracks = rows.map(t => otMapCdnTrack(t, signalFolder))" in panel
    assert "otPlaylistRows(await res.json())" in flow
    assert "otMapCdnTrack(t, signalFolder)" in flow
    assert "function _tfPracticeStep" in flow
    assert "_tfPracticeStepHTML" in flow
    assert 'title: numbered ? \'\' : title' in flow or 'title: numbered ? "" : title' in flow


def test_tuner_action_is_actions_and_flows_only():
    html = (SHELL / "index.html").read_text()
    js = (SHELL / "action.js").read_text()
    css = (SHELL / "action.css").read_text()
    assert 'data-tab="actions"' in html
    assert 'data-tab="flows"' in html
    assert 'data-tab="links"' not in html
    assert 'data-tab="tools"' not in html
    assert 'id="ab-links-list"' not in html
    assert 'id="ab-tools-list"' not in html
    assert "Action Board — Lucid Cove on Hermes" not in html
    assert "Jules" not in html
    assert "Tune Now" not in js
    assert "loadTuneFlow" not in js
    assert 'lchGoto("/tune")' not in js
    assert "Does not start a Tune" in js
    assert "Flows you turn on mint" in html
    assert "lt-tuner-daily-actions-v1" in js
    assert "lt-tuner-flow-active-v1" in js
    assert "FLOW_WALK" in js
    assert "walk-lucid-path" in js
    assert "Choose your Walk" in js
    assert "FLOW_GRATITUDE" in js
    assert "todays-practice" not in js
    assert "Today’s practice" not in js
    assert "Ready to run" in js
    assert '"Run"' in js or "\"Run\"" in js
    assert "Read again" in js
    assert "Turn on" in js
    assert "Personalized Walk" in js
    assert "_tfFetchLatestDropTuning" in js
    assert "tuning_key" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert ".ta-tab" in css
    assert ".ta-walk-pick" in css
    assert ".ta-choice-list" in css
    assert "/static/tuner-app/action.js" in html
    assert "/static/tuner-app/action.css" in html
    css_house = (SHELL / "house.css").read_text()
    css_chrome = (SHELL / "chrome.css").read_text()
    safe_top = "padding-top: calc(1rem + env(safe-area-inset-top, 0px))"
    assert "body.house-free .board-switch {" not in css_house
    assert 'body.house-free .board-switch a[href="/work"]' in css_house
    assert "body.house .top-end" in css_house
    assert "@media (max-width: 800px)" in css_house
    assert "grid-template-rows: auto minmax(0, 1fr)" in css_house
    assert "position: sticky" in css_house
    assert safe_top in css_chrome
    assert safe_top in css_house
    assert "env(safe-area-inset-left, 0px)" in css_chrome
    assert "env(safe-area-inset-right, 0px)" in css_chrome
    assert "env(safe-area-inset-left, 0px)" in css_house
    assert "env(safe-area-inset-right, 0px)" in css_house
    assert "padding-bottom: 0.7rem" in css_house


def test_tuning_chrome_does_not_paint_alignment_label():
    panel = (STATIC / "js" / "tuning-panel.js").read_text()
    flow = (STATIC / "js" / "tune-flow.js").read_text()
    assert "freq + ' ALIGNMENT'" not in panel
    assert "Alignment found." not in flow
    assert "ALIGNMENT</div>" not in flow
    assert "Tuning received." in flow
    assert "alignEl.textContent = freq || ''" in panel
    assert "alignEl.hidden = true" not in panel

