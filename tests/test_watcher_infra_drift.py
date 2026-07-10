"""#D33 — infra-drift watcher checks (proxy-config drift, TLS cert expiry). Pure logic
+ the filesystem helpers' no-op-when-absent contract (they run in-container where the
Caddy paths may not be mounted, so they must never raise/spam)."""
import os
from datetime import datetime, timezone, timedelta

from src.utils.watcher import (
    max_mtime, certs_expiring_within, _read_caddy_certs, _caddy_config_paths,
    CERT_EXPIRY_DAYS,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


# ── max_mtime ────────────────────────────────────────────────────────────────

def test_max_mtime_none_when_nothing_exists():
    assert max_mtime(["/does/not/exist/at/all", "/nor/this"]) is None


def test_max_mtime_picks_newest(tmp_path):
    a = tmp_path / "a"; a.write_text("1")
    b = tmp_path / "b"; b.write_text("2")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    assert max_mtime([str(a), str(b)]) == 2000.0


def test_max_mtime_scans_dir_children(tmp_path):
    d = tmp_path / "conf.d"; d.mkdir()
    snip = d / "smith.caddy"; snip.write_text("x")
    os.utime(d, (1000, 1000))
    os.utime(snip, (5000, 5000))
    # a new/changed snippet inside the dir must be seen even if the dir mtime is old
    assert max_mtime([str(d)]) == 5000.0


# ── certs_expiring_within ────────────────────────────────────────────────────

def test_cert_within_window_flagged():
    certs = [("cove.example.org", NOW + timedelta(days=10))]
    out = certs_expiring_within(certs, NOW, CERT_EXPIRY_DAYS)
    assert out == [("cove.example.org", 10)]


def test_cert_outside_window_ignored():
    certs = [("cove.example.org", NOW + timedelta(days=30))]
    assert certs_expiring_within(certs, NOW, CERT_EXPIRY_DAYS) == []


def test_expired_cert_has_negative_days_and_sorts_first():
    certs = [
        ("ok.example.org", NOW + timedelta(days=12)),
        ("dead.example.org", NOW - timedelta(days=2)),
    ]
    out = certs_expiring_within(certs, NOW, CERT_EXPIRY_DAYS)
    assert out[0][0] == "dead.example.org"
    assert out[0][1] < 0
    assert ("ok.example.org", 12) in out


def test_certs_expiring_handles_empty_and_none():
    assert certs_expiring_within([], NOW, 14) == []
    assert certs_expiring_within([("x", None)], NOW, 14) == []


# ── no-op-when-absent contract ───────────────────────────────────────────────

def test_read_caddy_certs_noop_when_dir_absent():
    assert _read_caddy_certs("/no/such/cert/dir") == []


def test_config_paths_env_override(monkeypatch):
    monkeypatch.setenv("LP_WATCHER_CADDY_PATHS", "/a:/b:/c")
    assert _caddy_config_paths() == ["/a", "/b", "/c"]
