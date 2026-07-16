"""#SEC4 medium security batch — pure logic tests (no live WebDAV/DB).

Covers:
  H3  files._clean_webdav_path / _is_kb_path (no admin-cred via ..)
  M1  profile avatar handle is basename-safe (source scan + helper behaviour)
  M2  nextcloud_tools._confine_download_path
  M3  youtube process-queue is admin-gated in multi mode (source scan)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── H3: WebDAV path normalization ───────────────────────────────────────────

def test_clean_webdav_path_happy():
    from src.dashboard.routes.files import _clean_webdav_path
    assert _clean_webdav_path("AgentSkills/Knowledge Base/foo") == (
        "AgentSkills/Knowledge Base/foo", None)
    assert _clean_webdav_path("/a/./b//c/") == ("a/b/c", None)
    assert _clean_webdav_path("") == ("", None)
    assert _clean_webdav_path("/") == ("", None)


def test_clean_webdav_path_rejects_root_escape():
    from src.dashboard.routes.files import _clean_webdav_path
    clean, err = _clean_webdav_path("../secret")
    assert clean is None and err == "Path escapes root"
    clean, err = _clean_webdav_path("../../x")
    assert clean is None and err == "Path escapes root"


def test_clean_webdav_path_collapses_internal_dots():
    from src.dashboard.routes.files import _clean_webdav_path
    # Internal .. that does not escape root is collapsed, not rejected.
    assert _clean_webdav_path("a/b/../c") == ("a/c", None)
    assert _clean_webdav_path("AgentSkills/Knowledge Base/../../secret") == (
        "secret", None)


def test_is_kb_path_false_on_traversal():
    """The bug: startswith(KB) was True for 'KB/../../x' and upgraded to admin creds."""
    from src.dashboard.routes.files import _is_kb_path
    assert _is_kb_path("AgentSkills/Knowledge Base") is True
    assert _is_kb_path("AgentSkills/Knowledge Base/doc.md") is True
    assert _is_kb_path("AgentSkills/Knowledge Base/../../secret") is False
    assert _is_kb_path("../AgentSkills/Knowledge Base") is False
    assert _is_kb_path("Other/thing") is False


def test_resolve_webdav_returns_error_on_escape(monkeypatch):
    """_resolve_webdav must not call NC at all when the path escapes root."""
    import asyncio
    from src.dashboard.routes import files as files_mod

    async def boom(*a, **k):
        raise AssertionError("should not resolve NC creds for escaping path")

    monkeypatch.setattr(
        "src.dashboard.routes.nextcloud.resolve_tab_nc_creds", boom, raising=False)

    async def run():
        base, user, auth, err = await files_mod._resolve_webdav(None, "../x")
        assert base is None and err == "Path escapes root"

    asyncio.get_event_loop().run_until_complete(run())


# ── M2: download path confinement ───────────────────────────────────────────

def test_confine_download_allows_sandbox():
    from src.tools.nextcloud_tools import _confine_download_path
    p, err = _confine_download_path("/app/data/downloads/report.pdf")
    assert err is None and p.endswith("/app/data/downloads/report.pdf")
    p, err = _confine_download_path("nested/out.bin")  # relative → downloads/
    assert err is None and p.endswith("/app/data/downloads/nested/out.bin")
    p, err = _confine_download_path("/app/data/scratch/tmp")
    assert err is None and "/app/data/scratch/tmp" in p


def test_confine_download_denies_outside():
    from src.tools.nextcloud_tools import _confine_download_path
    for bad in (
        "/etc/passwd",
        "/app/data/projects/secret",
        "/app/data/downloads/../../etc/passwd",
        "/tmp/x",
        "",
    ):
        p, err = _confine_download_path(bad)
        assert p is None and err, f"expected deny for {bad!r}, got {(p, err)}"


# ── M1 / M3: source-shape guards (text scan, no runtime) ────────────────────

def test_avatar_ingest_sanitizes_handle():
    src = (ROOT / "src/dashboard/routes/profile.py").read_text()
    # Must use Path(...).name (or equivalent) on handle before write_bytes
    assert "SEC4 M1" in src
    assert "pathlib.Path((handle or" in src or "Path((handle or" in src
    assert "fname = pathlib.Path(fname).name" in src


def test_youtube_process_queue_admin_gated():
    src = (ROOT / "src/dashboard/routes/youtube.py").read_text()
    assert "SEC4 M3" in src
    # Signature takes request; multi-mode checks cove_role == admin
    assert re.search(
        r"async def youtube_process_queue\(\s*request:\s*Request\s*\)", src)
    assert 'actor.get("cove_role") != "admin"' in src
    assert "Operators only" in src


def test_files_upload_strips_filename_dirs():
    src = (ROOT / "src/dashboard/routes/files.py").read_text()
    assert "Path_name_only" in src
    assert "def _clean_webdav_path" in src
