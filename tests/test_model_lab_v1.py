"""#MODELLAB1 v1 — Soren Model Lab unit tests (no live Ollama required)."""

from src.dashboard.routes import model_lab as ml


def test_valid_tag_accepts_ollama_names():
    ok, val = ml._valid_tag("qwen3:8b")
    assert ok is True
    assert val == "qwen3:8b"
    ok, val = ml._valid_tag("ltp-tuner-v2:latest")
    assert ok is True
    # Ollama HF-style pulls (ollama pull hf.co/org/name)
    ok, val = ml._valid_tag(
        "hf.co/mishmashly/Neo-Dolphin-Mistral-7B-GGUF:latest"
    )
    assert ok is True
    assert val.startswith("hf.co/")


def test_valid_tag_rejects_empty_and_junk():
    ok, err = ml._valid_tag("")
    assert ok is False
    ok, err = ml._valid_tag("bad tag with spaces")
    assert ok is False
    ok, err = ml._valid_tag("../escape")
    assert ok is False
    ok, err = ml._valid_tag("/absolute")
    assert ok is False


def test_clamp_temp():
    assert ml._clamp_temp(0.3) == 0.3
    assert ml._clamp_temp(-1) == 0.0
    assert ml._clamp_temp(9) == 2.0
    assert ml._clamp_temp("nope", 0.7) == 0.7


def test_presence_filter_shapes():
    where, params = ml._presence_filter(None)
    assert "NULL" in where or "presence_id" in where
    assert params == ()
    where, params = ml._presence_filter("abc")
    assert "%s" in where
    assert params == ("abc",)


def test_row_iso_dates():
    from datetime import datetime, timezone
    r = ml._row({"id": 1, "created_at": datetime(2026, 8, 11, tzinfo=timezone.utc)})
    assert r["id"] == 1
    assert isinstance(r["created_at"], str)


def test_router_has_expected_paths():
    paths = {getattr(r, "path", None) for r in ml.router.routes}
    assert "/model-lab" in paths
    assert "/api/model-lab/models" in paths
    assert "/api/model-lab/sessions" in paths
    assert "/api/model-lab/runs" in paths
    assert "/api/model-lab/packs" in paths


def test_load_lab_packs_has_baseline_set():
    data = ml._load_lab_packs()
    ids = {p.get("id") for p in data.get("packs") or []}
    assert "blank" in ids
    assert "general-chat" in ids
    assert "summarize" in ids
    assert "code-review" in ids
    general = next(p for p in data["packs"] if p["id"] == "general-chat")
    assert general.get("system_prompt")
    assert "session" in (general.get("applies_to") or [])






def test_split_model_output_keeps_thinking():
    raw = (
        "<think>\nprivate monologue about Lucidworks\n</think>\n"
        "The **Lucid Principles** are a framework for coherence."
    )
    answer, thinking = ml._split_model_output(raw)
    assert "Lucidworks" in thinking
    assert "private monologue" in thinking
    assert "Lucid Principles" in answer
    assert "<think" not in answer.lower()
    assert ml._strip_think_blocks(raw) == answer


def test_split_think_unclosed():
    raw = "<think> only reasoning, no close"
    answer, thinking = ml._split_model_output(raw)
    assert answer == ""
    assert "only reasoning" in thinking


def test_message_text_list_blocks():
    assert "hello" in ml._message_text([{"type": "text", "text": "hello"}])


def test_bulk_storage_route_registered():
    from src.dashboard.routes import settings as settings_routes

    paths = {getattr(r, "path", None) for r in settings_routes.router.routes}
    assert "/api/settings/bulk-storage" in paths


def test_lab_ollama_client_unloads_after_use(monkeypatch):
    """Lab must not leave models resident (P620 heat) — keep_alive=0."""
    captured = {}

    class _FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "langchain_ollama.ChatOllama",
        _FakeChat,
    )
    monkeypatch.setattr(
        "src.models.provider._ollama_base_url",
        lambda: "http://ollama.test:11434",
    )
    client = ml._lab_ollama_client("qwen3:8b", 0.5)
    assert isinstance(client, _FakeChat)
    assert captured.get("keep_alive") == "0"
    assert captured.get("model") == "qwen3:8b"
    assert int(captured.get("num_ctx") or 0) <= 8192
