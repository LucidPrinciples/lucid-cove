"""#PERF-MC1 follow-up: gzip + colder Attention shell (source guards)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src/dashboard/app.py").read_text(encoding="utf-8")
CORE = (ROOT / "src/dashboard/static/js/core.js").read_text(encoding="utf-8")


def test_gzip_middleware_registered():
    assert "GZipMiddleware" in APP
    assert "minimum_size=500" in APP


def test_cold_shell_skips_action_board_by_default():
    # Attention cold path should not always pull ~127KB action-board.js
    assert "const shell = ['quick-list']" in CORE or 'const shell = ["quick-list"]' in CORE
    assert "action-board.js is no longer on cold shell" in CORE or "no longer on cold shell" in CORE


def test_idle_prefetch_does_not_fan_out_all_tabs():
    assert "setTimeout(warm, 12000)" in CORE
    assert "do NOT prefetch the full tab roster" in CORE
    # old aggressive idle path must stay gone
    assert "requestIdleCallback(run, { timeout: 4000 })" not in CORE


def test_switch_board_loads_action_board():
    assert "async function switchBoard" in CORE
    assert "await loadScriptBasenames(['action-board'])" in CORE or 'await loadScriptBasenames(["action-board"])' in CORE
