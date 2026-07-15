# Install hard-stop: host must resolve mesh A records (or repair) before set_domain says live.
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "provision"))
import set_domain as sd  # noqa: E402


def test_is_mesh_ip_range():
    assert sd._is_mesh_ip("100.64.0.9") is True
    assert sd._is_mesh_ip("100.127.1.1") is True
    assert sd._is_mesh_ip("100.63.0.1") is False
    assert sd._is_mesh_ip("8.8.8.8") is False
    assert sd._is_mesh_ip("") is False


def test_ensure_host_resolves_ok_when_system_already_has_mesh(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_a_system", lambda h, timeout=3.0: "100.64.0.9")
    monkeypatch.setattr(sd, "_resolve_a_doh", lambda h, timeout=5.0: "100.64.0.9")
    monkeypatch.setattr(sd, "_detect_mesh_ip_host", lambda: "100.64.0.9")
    r = sd.ensure_host_resolves("withers.lucidcove.org", "100.64.0.9")
    assert r["ok"] is True
    assert r["method"] == "system"
    assert r["system_ip"] == "100.64.0.9"


def test_ensure_host_resolves_pins_hosts_when_system_nxdomain(monkeypatch, tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(sd, "_hosts_path", lambda: hosts)
    monkeypatch.setattr(sd, "_detect_mesh_ip_host", lambda: "100.64.0.9")
    monkeypatch.setattr(sd, "_resolve_a_doh", lambda h, timeout=5.0: "100.64.0.9")
    monkeypatch.setattr(sd, "_tailscale_accept_dns", lambda: {"ok": True})
    monkeypatch.setattr(sd, "_flush_host_dns_cache", lambda: {"ok": True, "actions": []})

    state = {"n": 0}

    def _sys(h, timeout=3.0):
        state["n"] += 1
        # Fail until hosts pin written
        if "withers.lucidcove.org" in hosts.read_text():
            return "100.64.0.9"
        return ""

    monkeypatch.setattr(sd, "_resolve_a_system", _sys)
    r = sd.ensure_host_resolves("withers.lucidcove.org", "100.64.0.9")
    assert r["ok"] is True
    assert r["method"] == "hosts"
    text = hosts.read_text()
    assert "100.64.0.9 withers.lucidcove.org" in text
    assert "lucidcove-set-domain withers.lucidcove.org" in text


def test_ensure_hosts_pin_idempotent(tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(sd, "_hosts_path", lambda: hosts)
    a = sd._ensure_hosts_pin("demo.lucidcove.org", "100.64.0.5")
    b = sd._ensure_hosts_pin("demo.lucidcove.org", "100.64.0.5")
    assert a["ok"] and b["ok"]
    # single pin line
    lines = [ln for ln in hosts.read_text().splitlines() if "demo.lucidcove.org" in ln and not ln.strip().startswith("#")]
    assert len(lines) == 1
    # update IP
    c = sd._ensure_hosts_pin("demo.lucidcove.org", "100.64.0.6")
    assert c["ok"]
    text = hosts.read_text()
    assert "100.64.0.6 demo.lucidcove.org" in text
    assert "100.64.0.5 demo.lucidcove.org" not in text


def test_ensure_host_resolves_fails_without_ip(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_a_system", lambda h, timeout=3.0: "")
    monkeypatch.setattr(sd, "_resolve_a_doh", lambda h, timeout=5.0: "")
    monkeypatch.setattr(sd, "_detect_mesh_ip_host", lambda: "")
    monkeypatch.setattr(sd, "_tailscale_accept_dns", lambda: {"ok": False, "skipped": True})
    monkeypatch.setattr(sd, "_flush_host_dns_cache", lambda: {"ok": True, "actions": []})
    r = sd.ensure_host_resolves("ghost.lucidcove.org", "")
    assert r["ok"] is False
    assert "Cannot resolve" in (r.get("message") or "") or "cannot resolve" in (r.get("message") or "").lower()
