"""batch8 #3 / CF-110 — analysis-model selection + sovereignty gate."""
from src.dashboard.routes import pipeline_keys as pk
from src.dashboard.routes.pipeline_keys import analysis_model_allowed, get_analysis_model


def test_local_pick_always_allowed():
    # A local (ollama) model is honored in BOTH compute modes.
    assert analysis_model_allowed("ollama", "local") is True
    assert analysis_model_allowed("ollama", "cloud") is True


def test_paid_pick_blocked_in_local_mode():
    # A paid provider is blocked only when the Cove's compute mode is local.
    assert analysis_model_allowed("openrouter", "local") is False
    assert analysis_model_allowed("groq", "local") is False


def test_paid_pick_allowed_in_cloud_mode():
    assert analysis_model_allowed("openrouter", "cloud") is True
    assert analysis_model_allowed("openai", "") is True  # default mode = cloud


def test_get_analysis_model_reads_override(monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(cfg, "get_feature_flags", lambda: {"analysis_model": "kimi-k2.5"})
    assert get_analysis_model() == "kimi-k2.5"


def test_get_analysis_model_empty_when_unset(monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(cfg, "get_feature_flags", lambda: {})
    assert get_analysis_model() == ""


def test_options_come_from_registry(monkeypatch):
    import src.config as cfg
    monkeypatch.setattr(cfg, "load_models_registry",
                        lambda: [{"id": "kimi-k2.5", "name": "Kimi K2.5"}, {"id": "no-name"}])
    opts = pk._analysis_model_options()
    ids = {o["id"] for o in opts}
    assert "kimi-k2.5" in ids and "no-name" in ids
    kimi = next(o for o in opts if o["id"] == "kimi-k2.5")
    assert kimi["label"] == "Kimi K2.5"
