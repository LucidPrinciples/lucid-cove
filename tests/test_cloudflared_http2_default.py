"""Cove cloudflared connectors default to HTTP/2 edge transport."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_enable_tunnel():
    root = Path(__file__).resolve().parents[1]
    path = root.joinpath("provision", "enable_tunnel.py")
    spec = importlib.util.spec_from_file_location("enable_tunnel_mod", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(root))
    spec.loader.exec_module(mod)
    return mod


def test_default_transport_is_http2(monkeypatch):
    mod = _load_enable_tunnel()
    monkeypatch.delenv("CLOUDFLARED_PROTOCOL", raising=False)
    monkeypatch.delenv("TUNNEL_TRANSPORT_PROTOCOL", raising=False)
    assert mod._transport_protocol() == "http2"
    assert mod.DEFAULT_TRANSPORT_PROTOCOL == "http2"


def test_env_override_quic(monkeypatch):
    mod = _load_enable_tunnel()
    monkeypatch.setenv("CLOUDFLARED_PROTOCOL", "quic")
    assert mod._transport_protocol() == "quic"


def test_run_args_include_protocol():
    mod = _load_enable_tunnel()
    args = mod.cloudflared_run_args("tok", "http2")
    assert "--protocol" in args
    assert args[args.index("--protocol") + 1] == "http2"
    assert args[-2:] == ["--token", "tok"]


def test_compose_fragment_defaults_http2():
    root = Path(__file__).resolve().parents[1]
    text = root.joinpath("docker", "cloudflared.compose.fragment.yml").read_text()
    assert "TUNNEL_TRANSPORT_PROTOCOL: ${TUNNEL_TRANSPORT_PROTOCOL:-http2}" in text
    assert "--protocol" in text
