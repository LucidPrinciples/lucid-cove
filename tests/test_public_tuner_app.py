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
    assert 'href="/action"' in html
    assert 'data-nav="action"' in html
    assert 'class="board-switch"' in html
    assert 'aria-label="Action"' in html
    assert ">Attention<" not in html
    assert 'href="/work"' not in html
    nav = html[html.index('class="top-nav"') : html.index('class="top-end"')]
    assert 'href="/action"' not in nav
    end = html[html.index('class="top-end"') : html.index("id=\"house-shell\"")]
    assert 'href="/action"' in end
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
    assert 'area: "action"' in js
    assert "loadTunerAction" in js
    assert ".top-nav a, .board-switch a" in js


def test_adapter_uses_hub_presence_not_new_wizard():
    js = (SHELL / "adapter.js").read_text()
    assert "/api/presence/me" in js
    assert "TIER_LEVELS" in js
    assert "function loadTuneFlow" not in js


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
    assert ">Field<" in html
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
    assert 'id="fieldDesc">Latest Tuning</' in html
    assert 'id="fieldDesc">Trust the Field</' not in html
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
    assert "Next free tune in" in flow
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
    assert "s.universal_coaching" in detail
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
    assert "Flows create" in html
    assert "lt-tuner-daily-actions-v1" in js
    assert "FLOW_PRACTICE" in js
    assert "FLOW_GRATITUDE" in js
    assert "_tfFetchLatestDropTuning" in js
    assert "tuning_key" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert ".ta-tab" in css
    assert "/static/tuner-app/action.js" in html
    assert "/static/tuner-app/action.css" in html
    css_house = (SHELL / "house.css").read_text()
    assert "body.house-free .board-switch {" not in css_house
    assert 'body.house-free .board-switch a[href="/work"]' in css_house
    assert "body.house .top-end" in css_house
    assert "@media (max-width: 800px)" in css_house

