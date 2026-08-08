"""
Morning open v1 — latest Drop cold-start + morning alert prefs.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DROP = ROOT / "src" / "tuning" / "public_drop.py"
TUNING_ROUTES = ROOT / "src" / "dashboard" / "routes" / "tuning.py"
TUNING_SETTINGS = ROOT / "src" / "dashboard" / "routes" / "tuning.py"
TUNE_FLOW = ROOT / "src" / "dashboard" / "static" / "js" / "tune-flow.js"
CORE_JS = ROOT / "src" / "dashboard" / "static" / "js" / "core.js"
MORNING_JS = ROOT / "src" / "dashboard" / "static" / "js" / "morning-alert.js"
SETTINGS_TUNING = ROOT / "src" / "dashboard" / "static" / "js" / "settings-tuning.js"
PANELS = ROOT / "src" / "dashboard" / "static" / "js" / "panels.js"


def test_get_latest_available_drop_exists():
    src = PUBLIC_DROP.read_text()
    assert "def get_latest_available_drop" in src
    # parses
    ast.parse(src)


def test_latest_api_route_registered():
    src = TUNING_ROUTES.read_text()
    assert '/api/tuning/latest' in src
    assert "get_latest_available_drop" in src
    assert "is_latest" in src
    ast.parse(src)


def test_morning_alert_settings_routes():
    src = TUNING_ROUTES.read_text()
    assert '/api/tuning/morning-alert' in src
    assert "_normalize_morning_alert" in src
    assert "local_time" in src
    ast.parse(src)


def test_tune_flow_opens_latest_view():
    src = TUNE_FLOW.read_text()
    assert "view') === 'latest'" in src or 'view") === "latest"' in src or "view') === 'latest'" in src
    assert "_tfFetchLatestDropTuning" in src
    assert "/api/tuning/latest" in src
    assert "mc_open_latest_tuning" in src


def test_core_boot_prefers_tune_for_latest():
    src = CORE_JS.read_text()
    assert "view') === 'latest'" in src or 'view") === "latest"' in src
    assert "mc_open_latest_tuning" in src
    assert "firstTab = 'tune'" in src
    assert "morning-alert" in src


def test_morning_alert_client_and_settings_ui():
    assert MORNING_JS.is_file()
    m = MORNING_JS.read_text()
    assert "tab=tune" in m or "set('tab', 'tune')" in m
    assert "view" in m and "latest" in m
    st = SETTINGS_TUNING.read_text()
    assert "loadSettingsMorningAlert" in st
    assert "morning-alert" in st
    panels = PANELS.read_text()
    assert "settings-morning-alert" in panels


def test_normalize_morning_alert_unit():
    # Import without full app: exec the helper by reading source patterns
    src = TUNING_ROUTES.read_text()
    # Extract function body via exec isolated
    ns = {}
    # Pull defaults + normalize only
    chunk = re.search(
        r"_DEFAULT_MORNING_ALERT = \{.*?\n\}\n\n\ndef _normalize_morning_alert\(raw\) -> dict:.*?\n    return out\n",
        src,
        re.S,
    )
    assert chunk, "normalize helper missing"
    exec(chunk.group(0), ns)
    assert ns["_normalize_morning_alert"](None)["local_time"] == "07:00"
    assert ns["_normalize_morning_alert"]({"enabled": 1, "local_time": "6:30"})["local_time"] == "06:30"
    assert ns["_normalize_morning_alert"]({"enabled": True, "local_time": "25:99"})["enabled"] is True
    # invalid time keeps default hour clamp — 25 -> 23
    assert ns["_normalize_morning_alert"]({"local_time": "25:99"})["local_time"] == "23:59"
