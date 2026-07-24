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
YT_ROUTES = (ROOT / "src" / "dashboard" / "routes" / "youtube.py").read_text()


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


def test_publish_now_process_queue_selects_presence_id():
    """#YT-PUBNOW1 — manual process-queue must load presence_id like the scheduler.

    Publish Now / POST /api/youtube/process-queue re-SELECTs the full row then
    calls _upload_youtube_post. If presence_id is missing, yt_service_key(None)
    hits legacy 'youtube' while OAuth lives at youtube:{presence} — scheduled
    path works, Publish Now fails with 'No youtube tokens stored'.
    """
    # Scheduler already locked by test_uploader_resolves_per_presence_token.
    assert "presence_id" in SCHED
    # Route file must include presence_id on the per-id full-row fetch used
    # inside youtube_process_queue (not only on insert/list paths).
    assert "async def youtube_process_queue" in YT_ROUTES
    # The full-row SELECT that feeds _upload_youtube_post:
    assert re.search(
        r"FROM youtube_queue WHERE id = %s"
        r"[\s\S]{0,200}?_upload_youtube_post"
        r"|"
        r"playlist_id, upload_date, publish_date, series, presence_id"
        r"\s+FROM youtube_queue WHERE id = %s",
        YT_ROUTES,
    ) or (
        "playlist_id, upload_date, publish_date, series, presence_id" in YT_ROUTES
        and "youtube_process_queue" in YT_ROUTES
    ), (
        "process-queue full-row SELECT missing presence_id/upload_date — "
        "Publish Now will drop per-presence YouTube tokens again"
    )
    # Stronger: the exact column list the scheduler uses must appear in routes.
    assert (
        "playlist_id, upload_date, publish_date, series, presence_id" in YT_ROUTES
    ), "youtube.py process-queue SELECT must match scheduler column list"
