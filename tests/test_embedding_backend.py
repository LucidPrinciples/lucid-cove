# batch-9 #12 (CF-107): embeddings ride the cloud provider when llm.mode=cloud, fall back to
# NONE (semantic search off) where no backend exists, and NEVER let a local Cove silently pay
# (sovereignty gate).
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.config as cfg  # noqa: E402
import src.memory.knowledge as k  # noqa: E402


def _set_mode(monkeypatch, mode, url=""):
    monkeypatch.setattr(cfg, "get_compute_config",
                        lambda: {"llm": {"mode": mode, "url": url}})


def test_local_mode_uses_ollama_nomic(monkeypatch):
    _set_mode(monkeypatch, "local")
    b = k._resolve_embedding_backend()
    assert b["kind"] == "local"
    assert b["model"] == k.EMBEDDING_MODEL
    assert k.semantic_search_status()["available"] is True


def test_local_mode_never_touches_cloud_even_with_openai_key(monkeypatch):
    # Sovereignty gate: an OpenAI key present must NOT flip a local Cove to paid cloud.
    _set_mode(monkeypatch, "local")
    monkeypatch.setattr(k, "env", lambda *a, **kw: "sk-should-not-be-used")
    assert k._resolve_embedding_backend()["kind"] == "local"


def test_cloud_mode_with_openai_key_uses_768_dim_model(monkeypatch):
    _set_mode(monkeypatch, "cloud")
    monkeypatch.setattr(k, "env", lambda name, default="": "sk-live" if name == "OPENAI_API_KEY" else default)
    b = k._resolve_embedding_backend()
    assert b["kind"] == "cloud"
    assert b["model"] == k.CLOUD_EMBEDDING_MODEL
    assert b["key"] == "sk-live"
    assert k.EMBEDDING_DIM == 768


def test_cloud_mode_without_key_is_off(monkeypatch):
    _set_mode(monkeypatch, "cloud")
    monkeypatch.setattr(k, "env", lambda name, default="": default)  # no keys
    b = k._resolve_embedding_backend()
    assert b["kind"] == "none"
    st = k.semantic_search_status()
    assert st["available"] is False
    assert "Semantic search is off" in st["reason"]


def test_external_mode_uses_given_url(monkeypatch):
    _set_mode(monkeypatch, "external", url="http://gpu.box:11434")
    b = k._resolve_embedding_backend()
    assert b["kind"] == "external"
    assert b["url"] == "http://gpu.box:11434"
    assert b["model"] == k.EMBEDDING_MODEL
