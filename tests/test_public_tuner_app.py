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
    assert "A connect key lets Lucid Cove on Hermes use this Lucid Tuner account" in html
    assert "house-hermes-steps" in html
    assert "github.com/LucidPrinciples/lucid-cove-hermes" in html
    assert "this house" not in html
    assert "same registry" not in html
    assert "overlay stays private" not in html
    assert "/api/account/self-host-token" in js
    assert "settings-connect-key-value" in js
    assert "That slice comes after this door walks" not in html


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
