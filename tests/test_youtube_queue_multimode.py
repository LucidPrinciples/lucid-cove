"""Video pipeline on the CENTRALIZED stack — the three silent failure modes
found 2026-07-09 (post-flip assessment) must never come back:

1. The 15-min YouTube queue processor gated on env("YOUTUBE_CLIENT_ID") — but
   centralized Coves save the OAuth app via Posting Accounts into feature
   flags, so the processor skipped silently forever.
2. The uploader looked up the legacy GLOBAL 'youtube' token, while the OAuth
   callback stores per-presence 'youtube:{owner_id}' — the connected channel
   was never found ("won't post").
3. The calendar writer read NEXTCLOUD_USER/PASSWORD env at import time — unset
   on centralized stacks (only NEXTCLOUD_ADMIN_* exists) — and returned
   silently while the UI showed "Scheduled ✓".

Text scans (no app import), same pattern as test_cove_tune_time.py.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHED = (ROOT / "src" / "utils" / "scheduler.py").read_text()
CAL = (ROOT / "src" / "dashboard" / "routes" / "youtube_calendar.py").read_text()
BOARD = (ROOT / "src" / "dashboard" / "routes" / "action_board.py").read_text()
FETCH = (ROOT / "src" / "utils" / "content_fetch.py").read_text()


def test_processor_gate_reads_real_oauth_config():
    # Gate must consult _get_oauth_config (flags OR env), not env alone.
    assert "_get_oauth_config" in SCHED, \
        "YouTube queue processor no longer checks the real OAuth config"
    assert 'if not env("YOUTUBE_CLIENT_ID"):' not in SCHED, \
        "env-only YouTube gate is back — centralized (flags-based) configs skip silently"


def test_uploader_resolves_per_presence_token():
    assert "yt_service_key(post.get(\"presence_id\"))" in SCHED, \
        "uploader no longer resolves the per-presence 'youtube:{owner_id}' token"
    # Legacy fallback for NULL-owner rows must remain.
    assert re.search(r'get_valid_access_token\("youtube"\)', SCHED), \
        "legacy global-token fallback removed — single-mode uploads would break"


def test_uploader_fetches_content_with_webdav_fallback():
    assert "fetch_content_file" in SCHED, \
        "uploader resolves /content only — centralized stacks have no mount"
    assert "resolve_content_path" in FETCH and "remote.php/dav/files" in FETCH


def test_calendar_creds_resolve_at_call_time():
    # No module-level creds constants; call-time resolver with presence support.
    assert re.search(r'^_NC_USER\s*=', CAL, re.M) is None, \
        "module-level NEXTCLOUD_USER read is back (import-time, unset on centralized)"
    assert "_nc_calendar_creds" in CAL and "presence_id" in CAL
    assert "get_nc_admin_user" in CAL, "admin-cred fallback missing"


def test_calendar_failure_is_surfaced_not_swallowed():
    # create returns bool; the board PATCH response reports it.
    # Signature grew with #VP-CAL status/url kwargs — allow a wider window.
    assert re.search(r"def create_youtube_calendar_event\([\s\S]{0,900}\)\s*->\s*bool", CAL), \
        "create_youtube_calendar_event no longer reports success/failure"
    assert '"calendar"' in BOARD and '"failed"' in BOARD, \
        "schedule PATCH response no longer surfaces calendar failure"


def test_x_processor_not_gated_on_env_only():
    assert 'if not env("X_API_KEY"):\n            return' not in SCHED, \
        "env-only X gate is back — per-presence X creds would be skipped silently"
