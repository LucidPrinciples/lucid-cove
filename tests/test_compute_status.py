# CF-96 (batch7 #1) — the ONE compute resolver. Pure-logic unit tests: the
# resolver folds mode + endpoint/token + cloud-key presence into a ready/why
# verdict, and NEVER emits a token. These cover the video_asr readiness matrix
# that every surface reads.
import sys
import types

import src.compute_status as cs


def _fake_pipeline_keys(provider):
    """Inject a stand-in for the (FastAPI-importing) pipeline_keys module so the
    resolver's lazy import resolves without pulling FastAPI (absent in the sandbox,
    present in prod). Returns a cleanup callable."""
    name = "src.dashboard.routes.pipeline_keys"
    saved = sys.modules.get(name)
    m = types.ModuleType(name)
    m.first_asr_provider_key = lambda: (provider, "k") if provider else ("", "")
    m.any_asr_key = lambda: bool(provider)
    sys.modules[name] = m

    def restore():
        if saved is not None:
            sys.modules[name] = saved
        else:
            sys.modules.pop(name, None)
    return restore


def _va(cfg):
    return cs._video_asr_status(cfg)


def test_external_ready_with_url_and_token():
    r = _va({"video_asr": {"mode": "external", "url": "https://voice.friend.example", "token": "gpugrant_x"}})
    assert r["ready"] is True
    assert r["backend"] == "gpu"
    assert r["path"] == "gpu-rented"
    assert r["host"] == "voice.friend.example"


def test_external_missing_token_not_ready():
    r = _va({"video_asr": {"mode": "external", "url": "https://voice.friend.example", "token": ""}})
    assert r["ready"] is False
    assert r["backend"] == "none"
    assert r["path"] == "off"
    assert "grant token" in r["why"]


def test_external_missing_url_not_ready():
    r = _va({"video_asr": {"mode": "external", "url": "", "token": "gpugrant_x"}})
    assert r["ready"] is False
    assert "endpoint" in r["why"]


def test_local_is_ready_gpu():
    r = _va({"video_asr": {"mode": "local"}})
    assert r["ready"] is True
    assert r["backend"] == "gpu"
    assert r["path"] == "gpu-local"


def test_cloud_without_key_not_ready():
    restore = _fake_pipeline_keys("")  # no cloud key resolvable
    try:
        r = _va({"video_asr": {"mode": "cloud"}})
        assert r["ready"] is False
        assert r["backend"] == "none"
    finally:
        restore()


def test_cloud_with_key_ready():
    restore = _fake_pipeline_keys("groq")
    try:
        r = _va({"video_asr": {"mode": "cloud"}})
        assert r["ready"] is True
        assert r["backend"] == "cloud"
        assert "Groq" in r["label"]
    finally:
        restore()


def test_host_of_strips_scheme_and_path():
    assert cs._host_of("https://voice.example.com/api/stt") == "voice.example.com"
    assert cs._host_of("wss://box:8300/ws") == "box:8300"
    assert cs._host_of("") == ""


def test_compute_status_never_emits_token(monkeypatch):
    import src.config as config
    monkeypatch.setattr(config, "get_compute_config", lambda: {
        "llm": {"mode": "cloud", "url": ""},
        "voice": {"mode": "local", "url": ""},
        "video_asr": {"mode": "external", "url": "https://x.example", "token": "SECRET_TOKEN"},
    })
    out = cs.compute_status()
    assert "SECRET_TOKEN" not in repr(out)
    assert out["video_asr"]["mode"] == "external"
