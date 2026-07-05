"""batch8 #11 / CF-106 — local fallback resolves from installed tags, never a
hardcoded id, and fails LOUD when nothing is installed."""
import pytest

from src.models import local_fallback as lf
from src.models.local_fallback import (
    resolve_local_fallback_model, reset_local_fallback_cache, LocalModelUnavailable,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_local_fallback_cache()
    yield
    reset_local_fallback_cache()


def test_zero_installed_fails_loud(monkeypatch):
    # Nothing installed anywhere → LOUD error, never a hardcoded 404 model.
    monkeypatch.setattr(lf, "_probe_installed_sync",
                        lambda: [{"id": "ollama", "reachable": True, "models": []}])
    monkeypatch.setattr(lf, "gpu_from_config", lambda: {"present": False})
    with pytest.raises(LocalModelUnavailable):
        resolve_local_fallback_model()


def test_uses_the_installed_model(monkeypatch):
    # One model installed → that model is chosen (not the qwen3:30b seed).
    monkeypatch.setattr(lf, "_probe_installed_sync", lambda: [{
        "id": "ollama", "reachable": True,
        "models": [{"name": "llama3.2:3b", "size_bytes": 2_000_000_000, "chat": True}],
    }])
    monkeypatch.setattr(lf, "gpu_from_config", lambda: {"present": False})
    assert resolve_local_fallback_model() == "llama3.2:3b"


def test_embedding_only_box_fails_loud(monkeypatch):
    # An embedding model is not a brain — a box with only one must fail loud.
    monkeypatch.setattr(lf, "_probe_installed_sync", lambda: [{
        "id": "ollama", "reachable": True,
        "models": [{"name": "nomic-embed-text", "size_bytes": 300_000_000, "chat": False}],
    }])
    monkeypatch.setattr(lf, "gpu_from_config", lambda: {"present": False})
    with pytest.raises(LocalModelUnavailable):
        resolve_local_fallback_model()


def test_result_is_cached(monkeypatch):
    calls = {"n": 0}

    def _probe():
        calls["n"] += 1
        return [{"id": "ollama", "reachable": True,
                 "models": [{"name": "qwen3:8b", "size_bytes": 5_000_000_000, "chat": True}]}]

    monkeypatch.setattr(lf, "_probe_installed_sync", _probe)
    monkeypatch.setattr(lf, "gpu_from_config", lambda: {"present": False})
    assert resolve_local_fallback_model() == "qwen3:8b"
    assert resolve_local_fallback_model() == "qwen3:8b"
    assert calls["n"] == 1  # second call served from cache
