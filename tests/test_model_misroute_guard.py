# #D38 — cloud model ids must NEVER route to the Ollama provider. The founder's
# instance config carried 'kimi-k2.5' (a typo for the registry id
# 'kimi-k2.5-openrouter'); the old provider inference (no slash -> ollama) sent it
# to Ollama, a guaranteed 404 on EVERY video-moments run before the fallback
# rescued it. The guard recovers the misrouted id to its real registry entry, and
# never lets a recognisably-cloud id land on Ollama.
from src.models import provider
from src.models.provider import (
    _looks_like_cloud_id,
    _recover_cloud_model,
    _resolve_model_string,
)

REGISTRY = [
    {"id": "kimi-k2.5-openrouter", "provider": "openrouter",
     "model_string": "moonshotai/kimi-k2.5", "type": "cloud"},
    {"id": "gemini-flash", "provider": "google",
     "model_string": "gemini-2.5-flash", "type": "cloud"},
    {"id": "qwen3-8b", "provider": "ollama",
     "model_string": "qwen3:8b", "type": "local"},
]


def test_cloud_family_detection():
    assert _looks_like_cloud_id("kimi-k2.5")
    assert _looks_like_cloud_id("claude-sonnet-4.6")
    assert _looks_like_cloud_id("gemini-2.5-flash")
    # a genuine local Ollama tag is NOT a cloud family
    assert not _looks_like_cloud_id("qwen3:8b")
    assert not _looks_like_cloud_id("llama3.2:3b")


def test_recover_matches_desuffixed_registry_id():
    # 'kimi-k2.5' -> registry id 'kimi-k2.5-openrouter' (suffix stripped)
    assert _recover_cloud_model("kimi-k2.5", REGISTRY) == (
        "openrouter", "moonshotai/kimi-k2.5")


def test_recover_matches_model_string_last_segment():
    # 'gemini-2.5-flash' == the model_string exactly
    assert _recover_cloud_model("gemini-2.5-flash", REGISTRY) == (
        "google", "gemini-2.5-flash")


def test_recover_ignores_local_entries():
    # never resolve a cloud lookup onto a local (ollama) entry
    assert _recover_cloud_model("qwen3:8b", REGISTRY) is None
    assert _recover_cloud_model("nonesuch", REGISTRY) is None


def test_resolve_the_founder_typo_never_hits_ollama(monkeypatch):
    # end-to-end: 'kimi-k2.5' resolves to the cloud entry, provider != ollama
    monkeypatch.setattr(provider, "get_model_from_registry", lambda mid: None)
    monkeypatch.setattr(provider, "load_models_registry", lambda: REGISTRY)
    provider._WARNED_UNKNOWN_MODELS.clear()
    prov, mstr = _resolve_model_string("kimi-k2.5")
    assert prov == "openrouter"
    assert mstr == "moonshotai/kimi-k2.5"
    assert prov != "ollama"


def test_unknown_cloud_family_routes_to_openrouter_not_ollama(monkeypatch):
    # a cloud family name with no registry match still avoids ollama
    monkeypatch.setattr(provider, "get_model_from_registry", lambda mid: None)
    monkeypatch.setattr(provider, "load_models_registry", lambda: REGISTRY)
    provider._WARNED_UNKNOWN_MODELS.clear()
    prov, mstr = _resolve_model_string("grok-3-mini")
    assert prov == "openrouter"


def test_genuine_local_tag_still_routes_to_ollama(monkeypatch):
    # self-host flexibility preserved: an unknown non-cloud tag is a local model
    monkeypatch.setattr(provider, "get_model_from_registry", lambda mid: None)
    monkeypatch.setattr(provider, "load_models_registry", lambda: REGISTRY)
    provider._WARNED_UNKNOWN_MODELS.clear()
    prov, mstr = _resolve_model_string("phi4:14b")
    assert prov == "ollama"
    assert mstr == "phi4:14b"


def test_misroute_warns_once_not_per_call(monkeypatch, capsys):
    monkeypatch.setattr(provider, "get_model_from_registry", lambda mid: None)
    monkeypatch.setattr(provider, "load_models_registry", lambda: REGISTRY)
    provider._WARNED_UNKNOWN_MODELS.clear()
    for _ in range(5):
        _resolve_model_string("kimi-k2.5")
    # exactly one recovery line for five resolves — no per-run spam
    out = capsys.readouterr().out
    assert out.count("recovered misrouted cloud id 'kimi-k2.5'") == 1
