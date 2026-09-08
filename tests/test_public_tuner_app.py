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
