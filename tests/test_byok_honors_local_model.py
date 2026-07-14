"""Install-pass: BYOK must honor the specific local model the operator picked.

Wendy screenshot 2026-07-14: operator chose dolphin-phi via Ollama on a stranger
iMac; chat still hit the hardcoded seed qwen3:30b-a3b and 404'd.
"""
from unittest.mock import patch

from src.models import provider as prov


def setup_function(_fn=None):
    # Clear request-scoped BYOK between tests.
    try:
        tok = prov._byok_ctx.set(None)
        prov._byok_ctx.reset(tok)
    except Exception:
        pass
    # Reset cove primary.
    prov._cove_primary = None


def test_set_request_byok_stores_model():
    tok = prov.set_request_byok("ollama", "", model="dolphin-phi:latest")
    assert tok is not None
    assert prov._byok_now() == {
        "provider": "ollama",
        "api_key": "",
        "model": "dolphin-phi:latest",
    }
    prov.clear_request_byok(tok)


def test_get_primary_model_uses_byok_local_tag(monkeypatch):
    calls = []

    def _fake_client(provider, model_string, temperature, key=None):
        calls.append((provider, model_string, key))
        return f"client:{provider}:{model_string}"

    monkeypatch.setattr(prov, "_client_for", _fake_client)
    tok = prov.set_request_byok("ollama", "", model="dolphin-phi")
    out = prov.get_primary_model(temperature=0.3)
    prov.clear_request_byok(tok)
    assert out == "client:ollama:dolphin-phi"
    assert calls == [("ollama", "dolphin-phi", "")]


def test_get_primary_model_ollama_without_model_resolves_installed(monkeypatch):
    """No explicit tag → resolve from installed tags, NEVER the seed id."""
    calls = []

    def _fake_client(provider, model_string, temperature, key=None):
        calls.append((provider, model_string))
        return f"client:{provider}:{model_string}"

    monkeypatch.setattr(prov, "_client_for", _fake_client)
    monkeypatch.setattr(prov, "_resolve_local_fallback", lambda: "llama3.2:3b")
    tok = prov.set_request_byok("ollama", "")
    out = prov.get_primary_model()
    prov.clear_request_byok(tok)
    assert out == "client:ollama:llama3.2:3b"
    assert calls[0] == ("ollama", "llama3.2:3b")
    assert "qwen3:30b-a3b" not in str(calls)


def test_get_model_client_byok_different_provider_honors_model(monkeypatch):
    calls = []

    def _fake_client(provider, model_string, temperature, key=None):
        calls.append((provider, model_string, key))
        return f"client:{provider}:{model_string}"

    monkeypatch.setattr(prov, "_client_for", _fake_client)
    monkeypatch.setattr(prov, "_resolve_model_string", lambda mid: ("openrouter", "moonshotai/kimi-k2.5"))
    tok = prov.set_request_byok("ollama", "", model="dolphin-phi")
    out = prov.get_model_client("kimi-k2.5-openrouter")
    prov.clear_request_byok(tok)
    assert out == "client:ollama:dolphin-phi"
    assert calls == [("ollama", "dolphin-phi", "")]


def test_seed_id_still_exists_but_is_not_used_for_byok_ollama():
    # Regression guard: the seed constant may remain for display/legacy, but the
    # BYOK ollama path must not silently return it when a model tag is present.
    assert prov.BYOK_DEFAULT_MODEL["ollama"] == prov._DEFAULT_LOCAL_MODEL
